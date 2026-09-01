# -*- coding: utf-8 -*-
"""Record the simulator by talking to Chrome directly (CDP screencast).

Capturing the desktop would sweep in whatever else is on screen. This attaches
to a headless Chrome over the DevTools protocol and pulls frames straight out
of the page, so the recording contains the simulator and nothing else.

    python demo/capture.py            # -> demo/screen_raw.mp4
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import socket
import subprocess
import tempfile
import sys
import time
import urllib.request
from pathlib import Path

import websocket  # websocket-client

HERE = Path(__file__).resolve().parent
FRAMES = HERE / "frames"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
URL = os.environ.get("DEMO_URL", "http://127.0.0.1:8391/?demo=1")
SECONDS = int(os.environ.get("DEMO_SECONDS", "46"))
W, H = 1280, 860
FPS = 12


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main() -> int:
    if FRAMES.exists():
        shutil.rmtree(FRAMES)
    FRAMES.mkdir(parents=True)

    port = free_port()
    profile = Path(tempfile.mkdtemp(prefix="radar-cdp-"))  # fresh each run: a locked profile silently kills the launch
    proc = subprocess.Popen([
        CHROME, "--headless=new", f"--remote-debugging-port={port}",
        f"--user-data-dir={profile}", f"--window-size={W},{H}",
        "--hide-scrollbars", "--force-device-scale-factor=1",
        "--disable-gpu", "--no-first-run", "--autoplay-policy=no-user-gesture-required",
        "--remote-allow-origins=*",
        URL,
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    ws_url = None
    for _ in range(40):
        try:
            targets = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=2))
            page = next((t for t in targets if t.get("type") == "page"), None)
            if page:
                ws_url = page["webSocketDebuggerUrl"]
                break
        except Exception:  # noqa: BLE001 - chrome is still starting
            pass
        time.sleep(0.5)
    if not ws_url:
        proc.kill()
        print("could not attach to chrome", file=sys.stderr)
        return 1

    ws = websocket.create_connection(ws_url, timeout=30)
    mid = [0]

    def send(method, params=None):
        mid[0] += 1
        ws.send(json.dumps({"id": mid[0], "method": method, "params": params or {}}))

    send("Page.enable")
    send("Page.startScreencast", {"format": "jpeg", "quality": 85,
                                  "maxWidth": W, "maxHeight": H, "everyNthFrame": 1})

    n, deadline, last = 0, time.time() + SECONDS, 0.0
    stamps: list[float] = []
    started = time.time()
    interval = 1.0 / FPS
    while time.time() < deadline:
        try:
            msg = json.loads(ws.recv())
        except Exception:  # noqa: BLE001 - socket timeout during a still moment
            continue
        if msg.get("method") != "Page.screencastFrame":
            continue
        p = msg["params"]
        send("Page.screencastFrameAck", {"sessionId": p["sessionId"]})
        now = time.time()
        if now - last < interval:      # keep an even cadence for ffmpeg
            continue
        last = now
        (FRAMES / f"f{n:05d}.jpg").write_bytes(base64.b64decode(p["data"]))
        stamps.append(now)
        n += 1

    send("Page.stopScreencast")
    ws.close()
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
    time.sleep(1)
    shutil.rmtree(profile, ignore_errors=True)

    if n < 10:
        print(f"only {n} frames captured", file=sys.stderr)
        return 1

    # Hold each frame for as long as it was actually on screen, so a pause in
    # the demo stays a pause instead of being compressed away.
    listing = FRAMES / "frames.txt"
    with listing.open("w", encoding="utf-8") as fh:
        for i, ts in enumerate(stamps):
            nxt = stamps[i + 1] if i + 1 < len(stamps) else started + SECONDS
            hold = max(0.02, nxt - ts)
            fh.write("file 'f{:05d}.jpg'\n".format(i))
            fh.write("duration {:.3f}\n".format(hold))
        # concat needs the final frame listed twice to honour its duration
        fh.write("file 'f{:05d}.jpg'\n".format(len(stamps) - 1))

    import imageio_ffmpeg
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    out = HERE / "screen_raw.mp4"
    subprocess.run([ff, "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "concat", "-safe", "0", "-i", str(listing),
                    "-fps_mode", "vfr",
                    "-vf", f"scale={W}:-2:flags=lanczos,format=yuv420p",
                    "-c:v", "libx264", "-preset", "fast", "-crf", "20", str(out)], check=True, cwd=str(FRAMES))
    print(f"captured {n} frames -> {out} ({out.stat().st_size/1048576:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
