"""クソフィジカル AI: 人間の体を AI に貸す MCP サーバー。

AI が道具（move_body / look）を呼ぶ → 人間のブラウザに依頼が出る → 人間が動いて、成功か失敗かと原因を返す → AI に返る。

  uv run server.py   （ふつうは .mcp.json 経由で Claude Code が起動する）

  BODY_PORT         依頼を見るページのポート（既定 8765）
  BODY_HOST         既定 127.0.0.1。スマホから見るなら 0.0.0.0（認証なし。家の Wi-Fi の中だけで）
  BODY_TIMEOUT_SEC  体の返事を待つ秒数（既定 86400 = 1 日）
  BODY_NOTIFY       1 なら macOS の通知を出す（既定 1）
  DISCORD_BOT_TOKEN / DISCORD_CHANNEL_ID / DISCORD_USER_ID   あれば Discord にも依頼を送る（discord_body.py）

設定は、同じフォルダの .env にも書ける（環境変数が優先）。
"""
import asyncio, html, os, subprocess, threading, time, uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

from mcp.server.mcpserver import MCPServer

HERE = os.path.dirname(os.path.abspath(__file__))
if os.path.exists(os.path.join(HERE, ".env")):   # KEY=VALUE の行だけ読む
    for line in open(os.path.join(HERE, ".env"), encoding="utf-8"):
        k, eq, v = line.strip().partition("=")
        if eq and not k.startswith("#"):
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

PORT = int(os.environ.get("BODY_PORT", "8765"))
HOST = os.environ.get("BODY_HOST", "127.0.0.1")
TIMEOUT = float(os.environ.get("BODY_TIMEOUT_SEC", "86400"))
NOTIFY = os.environ.get("BODY_NOTIFY", "1") == "1"

mcp = MCPServer("kuso-physical-ai")
jobs = {}   # 依頼 ID → {"kind", "text", "reason", "answer"}
done = []   # 済んだ依頼（新しい順・10 件まで）


def answer(jid: str, ok: bool, note: str) -> bool:
    """体の返事を受け取る（ページからも Discord からも）。もう終わった依頼なら False。"""
    job = jobs.get(jid)
    if not job or job["answer"] is not None:
        return False
    job["answer"] = (ok, note)
    return True


DISCORD = None
if os.environ.get("DISCORD_BOT_TOKEN") and os.environ.get("DISCORD_CHANNEL_ID"):
    from discord_body import DiscordBody
    DISCORD = DiscordBody(os.environ["DISCORD_BOT_TOKEN"], int(os.environ["DISCORD_CHANNEL_ID"]),
                          int(os.environ.get("DISCORD_USER_ID") or 0), answer)


async def ask_body(kind: str, text: str, reason: str) -> tuple[bool, str, int]:
    """依頼を出して、人間の返事を待つ。返すのは（成功したか, 体が書いた一言, かかった秒数）。"""
    jid, t0 = uuid.uuid4().hex[:8], time.time()
    jobs[jid] = {"kind": kind, "text": text, "reason": reason, "answer": None}
    if NOTIFY:
        try:
            subprocess.Popen(["osascript", "-e", 'display notification "AI が体を呼んでいます" with title "クソフィジカル AI"'])
        except OSError:
            pass
    if DISCORD:
        DISCORD.send(jid, kind, text, reason)
    while jobs[jid]["answer"] is None:   # 人間が答えるまで、ひたすら待つ
        if time.time() - t0 > TIMEOUT:
            del jobs[jid]
            note = "タイムアウト: 体から返事がありません（寝ているのかもしれません）"
            if DISCORD:
                DISCORD.close(jid, False, note)
            return False, note, int(time.time() - t0)
        await asyncio.sleep(0.5)
    ok, note = jobs.pop(jid)["answer"]
    if DISCORD:
        DISCORD.close(jid, ok, note)
    done.insert(0, (kind, text, ok, note))
    del done[10:]
    return ok, note, int(time.time() - t0)


@mcp.tool()
async def move_body(action: str, reason: str) -> dict:
    """人間の体を動かす。1 回に頼めるのは、小さな動作 1 つだけ（例: 「右手を前に 20cm 伸ばす」）。
    体は気まぐれで、遅いし、失敗もする。返ってきた success と cause を読んでから、次の動作を決めること。

    action: してほしい動作。reason: なぜその動作が要るのか（体が納得するように）。
    返り値: success（成功したか）・cause（失敗の原因や補足）・seconds（かかった秒数）。
    """
    ok, note, sec = await ask_body("動かす", action, reason)
    return {"success": ok, "cause": note, "seconds": sec}


@mcp.tool()
async def look(target: str) -> dict:
    """人間の目で見る。何を見てほしいかを書くと、体が見えたものを言葉で返す。
    カメラではないので、返事は雑なことがある。

    返り値: success（見られたか）・seen（見えたもの）・cause（見られなかった原因）・seconds（かかった秒数）。
    """
    ok, note, sec = await ask_body("見る", target, "")
    return {"success": ok, "seen": note if ok else "", "cause": "" if ok else note, "seconds": sec}


PAGE = """<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>クソフィジカル AI</title>
<style>body{{font-family:sans-serif;max-width:560px;margin:24px auto;padding:0 16px}}
.job{{border:2px solid #222;border-radius:12px;padding:16px;margin:16px 0}}.kind{{font-size:12px;color:#666}}
.text{{font-size:22px;font-weight:bold;margin:8px 0}}textarea{{width:100%;height:64px;font-size:16px;box-sizing:border-box}}
button{{font-size:16px;padding:8px 14px;margin:8px 8px 0 0}}li{{color:#666;font-size:14px}}</style>
<h1>あなたは AI の体です</h1>{jobs}<h2>済んだ依頼</h2><ul>{done}</ul>
<script>const ids="{ids}";setInterval(async()=>{{const t=await (await fetch("/ids")).text();
if(t!==ids&&![...document.querySelectorAll("textarea")].some(e=>e.value))location.reload()}},2000)</script>"""

JOB = """<form class="job" method="post" action="/answer"><input type="hidden" name="id" value="{id}">
<div class="kind">{kind}</div><div class="text">{text}</div><div>{reason}</div>
<textarea name="note" placeholder="失敗の原因・見えたもの・言い訳"></textarea>
<button name="success" value="1">成功</button><button name="success" value="0">失敗</button></form>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):   # 標準出力は MCP の通信に使うので、何も書かない
        pass

    def send(self, body, code=200, ctype="text/html; charset=utf-8", headers=()):
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        pending = {k: j for k, j in jobs.items() if j["answer"] is None}
        if self.path == "/ids":
            return self.send(",".join(pending), ctype="text/plain")
        e = html.escape
        body = "".join(JOB.format(id=k, kind=j["kind"], text=e(j["text"]), reason=e(j["reason"] and "理由: " + j["reason"]))
                       for k, j in pending.items()) or "<p>依頼はありません。体は自由です。</p>"
        hist = "".join(f"<li>[{k}] {e(t)} → {'成功' if ok else '失敗'}: {e(n)}</li>" for k, t, ok, n in done)
        self.send(PAGE.format(jobs=body, done=hist, ids=",".join(pending)))

    def do_POST(self):
        form = parse_qs(self.rfile.read(int(self.headers.get("Content-Length", 0))).decode())
        answer(form.get("id", [""])[0], form.get("success", ["1"])[0] == "1", form.get("note", [""])[0].strip())
        self.send("", 303, headers=[("Location", "/")])


if __name__ == "__main__":
    web = ThreadingHTTPServer((HOST, PORT), Handler)
    threading.Thread(target=web.serve_forever, daemon=True).start()
    if DISCORD:
        DISCORD.start()
    mcp.run()   # stdio
