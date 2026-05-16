#!/usr/bin/env python3
"""
Burn subtitles into existing original clips using the cached ASS files.
Run this from the snip/ directory:

    uv run python fuse_subs.py <snip_out_dir>

Example:
    uv run python fuse_subs.py "/home/silva/Downloads/Telegram Desktop/snip_out/design_thinking"
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pysubs2


def get_video_size(path: Path) -> tuple[int, int]:
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    )
    w, h = map(int, result.stdout.strip().split(","))
    return w, h


def burn(video: Path, ass: Path, output: Path) -> None:
    with tempfile.NamedTemporaryFile(suffix=".ass", delete=False) as t:
        safe_ass = t.name
    shutil.copy2(ass, safe_ass)
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(video),
             "-vf", f"ass={safe_ass}",
             "-c:v", "libx264", "-preset", "fast", "-crf", "22",
             "-c:a", "copy", str(output)],
            check=True, capture_output=True,
        )
    finally:
        os.unlink(safe_ass)


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: uv run python fuse_subs.py <snip_out_dir>")
        sys.exit(1)

    out = Path(sys.argv[1])
    tmp = out / "_tmp"

    ass_files = sorted(tmp.glob("*.ass"))
    if not ass_files:
        print(f"No ASS files found in {tmp}")
        sys.exit(1)

    print(f"Found {len(ass_files)} ASS files — fusing into originals...\n")

    for ass_file in ass_files:
        name = ass_file.stem
        original = out / f"{name}_original.mp4"
        if not original.exists():
            print(f"  skip {name} — original not found")
            continue

        w, h = get_video_size(original)

        # Load ASS and patch for horizontal layout
        subs = pysubs2.load(str(ass_file), encoding="utf-8")
        subs.info["PlayResX"] = str(w)
        subs.info["PlayResY"] = str(h)
        style = subs.styles["Default"]
        style.fontsize = 52
        style.marginv = 60

        patched_ass = tmp / f"{name}_horiz.ass"
        subs.save(str(patched_ass), encoding="utf-8")

        subbed = out / f"{name}_original_sub.mp4"
        try:
            burn(original, patched_ass, subbed)
            print(f"  ✓ {name}_original_sub.mp4  ({w}x{h})")
        except subprocess.CalledProcessError as e:
            print(f"  ✗ {name}: ffmpeg failed — {e.stderr.decode()[:200]}")
        finally:
            patched_ass.unlink(missing_ok=True)

    print("\nDone.")


if __name__ == "__main__":
    main()
