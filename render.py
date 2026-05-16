"""ffmpeg-based video rendering: cut, reformat 9:16, burn Arabic subtitles."""

import json
import os
import shutil
import subprocess
import tempfile

import pysubs2


def get_video_info(video_path: str) -> dict:
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_streams", "-select_streams", "v:0",
        video_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    stream = json.loads(result.stdout)["streams"][0]
    return {"width": stream["width"], "height": stream["height"]}


def cut_original(video_path: str, start: float, end: float, output_path: str) -> None:
    """Stream-copy cut — instant, no re-encode, keeps original quality."""
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-ss", str(start),
            "-i", video_path,
            "-t", str(end - start),
            "-c", "copy",
            output_path,
        ],
        check=True,
        capture_output=True,
    )


def cut_vertical(
    video_path: str, start: float, end: float, output_path: str,
    src_width: int, src_height: int,
) -> None:
    """Cut clip and reformat to 9:16 via center crop → scale to 1080×1920."""
    crop_w = int(src_height * 9 / 16)
    crop_x = (src_width - crop_w) // 2

    subprocess.run(
        [
            "ffmpeg", "-y",
            "-ss", str(start),
            "-i", video_path,
            "-t", str(end - start),
            "-vf", f"crop={crop_w}:{src_height}:{crop_x}:0,scale=1080:1920:flags=lanczos",
            "-c:v", "libx264", "-preset", "fast", "-crf", "22",
            "-c:a", "aac", "-b:a", "128k",
            output_path,
        ],
        check=True,
        capture_output=True,
    )


def build_ass(
    segments: list, clip_start: float, clip_end: float,
    output_path: str, is_vertical: bool = True,
) -> None:
    """
    Build an ASS subtitle file for a clip from Whisper word-level timestamps.
    Groups words into chunks of ~5 for natural reading rhythm.
    Arabic text is handled correctly by libass (RTL auto-detected per Unicode bidi).
    """
    subs = pysubs2.SSAFile()
    subs.info["PlayResX"] = 1080
    subs.info["PlayResY"] = 1920 if is_vertical else 1080
    subs.info["WrapStyle"] = 0
    subs.info["ScaledBorderAndShadow"] = "yes"

    style = pysubs2.SSAStyle()
    style.fontname = "Arial"
    style.fontsize = 85 if is_vertical else 52
    style.bold = True
    style.primarycolor = pysubs2.Color(255, 255, 255, 0)
    style.outlinecolor = pysubs2.Color(0, 0, 0, 0)
    style.backcolor = pysubs2.Color(0, 0, 0, 120)
    style.outline = 3.0
    style.shadow = 1.5
    style.alignment = 2  # bottom center
    style.marginl = 60
    style.marginr = 60
    style.marginv = 100 if is_vertical else 60
    subs.styles["Default"] = style

    # Collect all words within this clip's time range
    words = []
    for seg in segments:
        for w in seg.get("words") or []:
            w_start = w.get("start")
            w_end = w.get("end")
            if w_start is None or w_end is None:
                continue
            if w_start >= clip_start and w_end <= clip_end + 0.5:
                words.append(w)

    if not words:
        # Fall back to segment-level subtitles if no word timestamps
        for seg in segments:
            seg_start = seg.get("start", 0)
            seg_end = seg.get("end", 0)
            if seg_end <= clip_start or seg_start >= clip_end:
                continue
            start_rel = max(0.0, seg_start - clip_start)
            end_rel = max(0.0, seg_end - clip_start)
            text = seg.get("text", "").strip()
            if text:
                subs.events.append(pysubs2.SSAEvent(
                    start=pysubs2.make_time(s=start_rel),
                    end=pysubs2.make_time(s=end_rel),
                    text=text,
                ))
    else:
        chunk_size = 5
        for i in range(0, len(words), chunk_size):
            chunk = words[i:i + chunk_size]
            start_rel = chunk[0]["start"] - clip_start
            end_rel = chunk[-1]["end"] - clip_start
            text = " ".join(w.get("word", "").strip() for w in chunk)
            subs.events.append(pysubs2.SSAEvent(
                start=pysubs2.make_time(s=max(0.0, start_rel)),
                end=pysubs2.make_time(s=max(0.0, end_rel)),
                text=text,
            ))

    subs.save(output_path)


def burn_subtitles(video_path: str, ass_path: str, output_path: str) -> None:
    # Copy ASS to /tmp with a plain filename — ffmpeg ass= filter breaks on paths
    # that contain spaces, apostrophes, or colons (common in Zoom folder names).
    with tempfile.NamedTemporaryFile(suffix=".ass", delete=False) as tmp:
        safe_ass = tmp.name
    shutil.copy2(ass_path, safe_ass)
    try:
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", video_path,
                "-vf", f"ass={safe_ass}",
                "-c:v", "libx264", "-preset", "fast", "-crf", "22",
                "-c:a", "copy",
                output_path,
            ],
            check=True,
            capture_output=True,
        )
    finally:
        os.unlink(safe_ass)
