"""依頼を Discord にも送り、ボタンで返事を受ける（任意）。

DISCORD_BOT_TOKEN と DISCORD_CHANNEL_ID があるときだけ動く。
ボットは Mac の側から Discord につなぎに行く（Gateway）ので、公開 URL もポート開放も要らない。
"""
import asyncio, threading

import discord


class AnswerModal(discord.ui.Modal):
    note = discord.ui.TextInput(label="原因・見えたもの・言い訳", style=discord.TextStyle.long, required=False, max_length=1000)

    def __init__(self, body, jid, ok):
        super().__init__(title="成功" if ok else "失敗")
        self.body, self.jid, self.ok = body, jid, ok

    async def on_submit(self, it: discord.Interaction):
        if self.body.on_answer(self.jid, self.ok, self.note.value.strip()):
            await it.response.send_message("AI に返しました", ephemeral=True)
        else:
            await it.response.send_message("この依頼はもう終わっています", ephemeral=True)


class AnswerView(discord.ui.View):
    def __init__(self, body, jid):
        super().__init__(timeout=None)
        self.body, self.jid = body, jid

    async def interaction_check(self, it: discord.Interaction) -> bool:
        if self.body.user_id and it.user.id != self.body.user_id:
            await it.response.send_message("あなたはこの体ではありません", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="成功", style=discord.ButtonStyle.success)
    async def ok(self, it: discord.Interaction, _):
        await it.response.send_modal(AnswerModal(self.body, self.jid, True))

    @discord.ui.button(label="失敗", style=discord.ButtonStyle.danger)
    async def ng(self, it: discord.Interaction, _):
        await it.response.send_modal(AnswerModal(self.body, self.jid, False))


class DiscordBody:
    def __init__(self, token: str, channel_id: int, user_id: int, on_answer):
        self.token, self.channel_id, self.user_id, self.on_answer = token, channel_id, user_id, on_answer
        self.client = discord.Client(intents=discord.Intents.default())
        self.loop = None
        self.msgs = {}   # 依頼 ID → 送ったメッセージ

    def start(self):
        """ボットを別スレッドで動かす（MCP サーバーとはイベントループを分ける）。"""
        def run():
            self.loop = asyncio.new_event_loop()
            self.loop.run_until_complete(self.client.start(self.token))
        threading.Thread(target=run, daemon=True).start()

    def send(self, jid, kind, text, reason):
        if self.loop:
            asyncio.run_coroutine_threadsafe(self._send(jid, kind, text, reason), self.loop)

    def close(self, jid, ok, note):
        if self.loop:
            asyncio.run_coroutine_threadsafe(self._close(jid, ok, note), self.loop)

    async def _send(self, jid, kind, text, reason):
        await self.client.wait_until_ready()
        ch = self.client.get_channel(self.channel_id) or await self.client.fetch_channel(self.channel_id)
        mention = f"<@{self.user_id}> " if self.user_id else ""
        body = f"**{kind}**: {text}" + (f"\n理由: {reason}" if reason else "")
        self.msgs[jid] = (await ch.send(mention + body, view=AnswerView(self, jid)), body)

    async def _close(self, jid, ok, note):
        sent = self.msgs.pop(jid, None)
        if sent:
            msg, body = sent
            await msg.edit(content=f"{body}\n→ **{'成功' if ok else '失敗'}**: {note or '（コメントなし）'}", view=None)
