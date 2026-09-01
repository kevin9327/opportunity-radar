# -*- coding: utf-8 -*-
"""Screenshot one finished answer (used for the ranking still in the video)."""
import base64, json, socket, subprocess, sys, tempfile, time, urllib.request
from pathlib import Path
import websocket

HERE = Path(__file__).resolve().parent
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
URL, WAIT, OUT = sys.argv[1], int(sys.argv[2]), HERE / sys.argv[3]

with socket.socket() as s:
    s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]
prof = Path(tempfile.mkdtemp(prefix="radar-shot-"))
proc = subprocess.Popen([CHROME, "--headless=new", f"--remote-debugging-port={port}",
                         f"--user-data-dir={prof}", "--window-size=1280,1400", "--hide-scrollbars",
                         "--disable-gpu", "--no-first-run", "--remote-allow-origins=*", URL],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
ws_url = None
for _ in range(40):
    try:
        t = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=2))
        p = next((x for x in t if x.get("type") == "page"), None)
        if p: ws_url = p["webSocketDebuggerUrl"]; break
    except Exception: pass
    time.sleep(0.5)
ws = websocket.create_connection(ws_url, timeout=WAIT + 60)
time.sleep(WAIT)
ws.send(json.dumps({"id": 1, "method": "Page.captureScreenshot", "params": {"format": "png"}}))
while True:
    m = json.loads(ws.recv())
    if m.get("id") == 1:
        OUT.write_bytes(base64.b64decode(m["result"]["data"])); break
ws.close(); proc.terminate()
print(f"saved {OUT} ({OUT.stat().st_size//1024} KB)")
