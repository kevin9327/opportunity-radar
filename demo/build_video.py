# -*- coding: utf-8 -*-
"""Assemble the submission video: cards + real screen capture + narration."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import imageio_ffmpeg
from PIL import Image

FF = imageio_ffmpeg.get_ffmpeg_exe()
HERE = Path(__file__).resolve().parent
BUILD = HERE / "build"
W, H = 1280, 776


def dur(p: Path) -> float:
    err = subprocess.run([FF, "-i", str(p)], capture_output=True, text=True).stderr
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", err)
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))


def still(img: Path, seconds: float, out: Path, zoom: bool = False):
    """A card or screenshot held for N seconds, optionally with a slow push-in."""
    im = Image.open(img).convert("RGB")
    if im.size != (W, H):
        # fit the shot into frame without distortion (screenshots are taller)
        scale = W / im.width
        im = im.resize((W, int(im.height * scale)), Image.LANCZOS)
        canvas = Image.new("RGB", (W, H), (8, 17, 28))
        canvas.paste(im, (0, 0))
        im = canvas
    tmp = BUILD / (out.stem + "_src.png")
    im.save(tmp)
    frames = int(seconds * 30)
    vf = (f"zoompan=z='min(zoom+0.00035,1.06)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
          f":d={frames}:s={W}x{H}:fps=30,format=yuv420p") if zoom else \
         f"scale={W}:{H},fps=30,format=yuv420p"
    subprocess.run([FF, "-y", "-loglevel", "error", "-loop", "1", "-i", str(tmp),
                    "-t", f"{seconds:.2f}", "-vf", vf,
                    "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-an", str(out)], check=True)


def clip(src: Path, start: float, seconds: float, out: Path, speed: float = 1.0):
    """A slice of the real screen capture, optionally sped up through a wait."""
    vf = f"scale={W}:{H},fps=30,format=yuv420p"
    if speed != 1.0:
        vf = f"setpts=PTS/{speed}," + vf
    subprocess.run([FF, "-y", "-loglevel", "error", "-ss", f"{start:.2f}", "-i", str(src),
                    "-t", f"{seconds * speed:.2f}", "-vf", vf,
                    "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-an", str(out)], check=True)


def main():
    BUILD.mkdir(exist_ok=True)
    a = HERE / "audio"
    narr = {n: dur(a / f"{n}.mp3") for n in [f"n{i}" for i in range(1, 9)]}

    part1, part2 = HERE / "part1.mp4", HERE / "part2.mp4"
    segments = []   # (path, narration key or None, seconds)

    def add(path: Path, key: str | None, seconds: float):
        segments.append((path, key, seconds))

    # 1 problem  2 title/what it is  3 live demo Q1  4 live demo Q2
    # 5 ranking  6 honesty  7 honesty(tests)  8 end
    still(HERE / "cards/problem.png", narr["n1"] + 0.6, BUILD / "s1.mp4", zoom=True)
    add(BUILD / "s1.mp4", "n1", narr["n1"] + 0.6)

    still(HERE / "cards/title.png", narr["n2"] + 0.5, BUILD / "s2.mp4", zoom=True)
    add(BUILD / "s2.mp4", "n2", narr["n2"] + 0.5)

    # real capture: first question typing through to its answer
    clip(part1, 1.0, narr["n3"] + 0.4, BUILD / "s3.mp4", speed=1.25)
    add(BUILD / "s3.mp4", "n3", narr["n3"] + 0.4)

    # real capture: second question (deadline filter)
    clip(part1, 26.0, narr["n4"] + 0.4, BUILD / "s4.mp4", speed=1.35)
    add(BUILD / "s4.mp4", "n4", narr["n4"] + 0.4)

    # ranking: the live "thinking" moment, then the finished screen
    clip(part2, 2.0, 3.2, BUILD / "s5a.mp4", speed=2.2)
    add(BUILD / "s5a.mp4", "n5", 3.2)
    still(HERE / "rank_result.png", narr["n5"] - 3.2 + 1.0, BUILD / "s5b.mp4")
    add(BUILD / "s5b.mp4", None, narr["n5"] - 3.2 + 1.0)

    still(HERE / "cards/honesty.png", narr["n6"] + narr["n7"] + 0.8, BUILD / "s6.mp4")
    add(BUILD / "s6.mp4", "n6", narr["n6"])
    add(None, "n7", narr["n7"] + 0.8)          # same card keeps playing under n7

    still(HERE / "cards/stack.png", narr["n8"] * 0.55, BUILD / "s7.mp4", zoom=True)
    add(BUILD / "s7.mp4", "n8", narr["n8"] * 0.55)
    still(HERE / "cards/end.png", narr["n8"] * 0.45 + 1.6, BUILD / "s8.mp4")
    add(BUILD / "s8.mp4", None, narr["n8"] * 0.45 + 1.6)

    # --- video: straight concat (cuts, not fades: this is a technical demo) ---
    vids = [p for p, _, _ in segments if p is not None]
    listing = BUILD / "concat.txt"
    listing.write_text("".join(f"file '{p.name}'\n" for p in vids), encoding="utf-8")
    video = BUILD / "video.mp4"
    subprocess.run([FF, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
                    "-i", str(listing), "-c", "copy", str(video)], check=True, cwd=str(BUILD))

    # --- audio: each narration line starts where its segment starts ---
    ain, filters, labels, t = [], [], [], 0.0
    idx = 0
    for path, key, seconds in segments:
        if key:
            ain += ["-i", str(a / f"{key}.mp3")]
            idx += 1
            filters.append(f"[{idx}:a]adelay={int(t*1000)}|{int(t*1000)},volume=1.6[v{idx}]")
            labels.append(f"[v{idx}]")
        t += seconds
    total = dur(video)
    fc = ";".join(filters) + ";" + "".join(labels) + f"amix=inputs={len(labels)}:duration=longest:normalize=0[a]"
    out = HERE / "opportunity_radar_demo.mp4"
    subprocess.run([FF, "-y", "-loglevel", "error", "-i", str(video), *ain,
                    "-filter_complex", fc, "-map", "0:v", "-map", "[a]",
                    "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-shortest", str(out)], check=True)
    print(f"video {total:.1f}s -> {out.name} ({out.stat().st_size/1048576:.1f} MB)")


if __name__ == "__main__":
    main()
