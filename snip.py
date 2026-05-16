#!/usr/bin/env python3
"""
SNIP — Automated video clip pipeline for Arabic and English content.

Outputs per run:
  youtube_timestamps.txt     — chapter markers to paste in video description
  clip_N_original.mp4        — clip at original ratio with burned subtitles (LinkedIn / archive)
  clip_N_shorts.mp4          — 9:16 vertical with burned subtitles (TikTok / Shorts)
  transcript.json            — full transcript cached for fast re-runs
  media_team_analysis.txt    — AI analysis per clip; edit and pass back via --feedback
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

# Unbuffered output so progress prints appear immediately in pipes and logs
sys.stdout.reconfigure(line_buffering=True)


def _load_dotenv() -> None:
    env_file = Path(__file__).parent / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip()
            # Don't overwrite values already set in the shell environment
            if key and value and not value.startswith("your_"):
                os.environ.setdefault(key, value)


_load_dotenv()

from identify import fmt_time, identify_moments
from render import build_ass, burn_subtitles, cut_original, cut_vertical, get_video_info
from transcribe import load_cached, transcribe, transcribe_groq, transcribe_speechmatics


def generate_analysis_txt(clips: list, output_path: Path) -> None:
    lines = [
        "تحليل المقاطع — مخرجات الـ AI",
        "=" * 50,
        "",
        "هذا الملف يشرح سبب اختيار كل مقطع وطبيعة الـ Hook.",
        "يمكن للفريق تعديله وإضافة ملاحظاته ثم تمريره للنظام",
        "كـ feedback في الجلسة القادمة عبر: --feedback <path>",
        "",
        "=" * 50,
        "",
    ]
    for i, clip in enumerate(clips, 1):
        dur = clip["end"] - clip["start"]
        lines += [
            f"{i}. {clip['title']}",
            f"   الوقت: {fmt_time(clip['start'])} ← {fmt_time(clip['end'])}  ({dur:.0f}s)",
            f"   النوع: {clip.get('hook_type', '—')}",
            f"   الافتتاحية: \"{clip.get('opening_line', '—')}\"",
            f"   لماذا يتوقف المشاهد: {clip.get('hook', '—')}",
            f"   الجملة الأقوى: \"{clip.get('shareable_line', '—')}\"",
            "",
            "   [ ملاحظات الفريق ]",
            "   _______________________________________________",
            "",
        ]
    lines += [
        "=" * 50,
        "ملاحظات عامة للفريق:",
        "_______________________________________________",
        "",
    ]
    output_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"  Saved → {output_path}")


def save_timestamps(timestamps: list, output_path: Path) -> None:
    with open(output_path, "w", encoding="utf-8") as f:
        for ts in timestamps:
            f.write(f"{ts['time']} {ts['title']}\n")
    print(f"  Saved → {output_path}")


def slugify(text: str, max_len: int = 35) -> str:
    """Make a safe filename from Arabic/mixed text."""
    # Keep only ASCII alphanumeric + spaces, fall back to generic slug
    ascii_only = "".join(c for c in text if c.isascii() and (c.isalnum() or c == " "))
    slug = ascii_only.strip().replace(" ", "_")[:max_len]
    return slug or "clip"


def _hms(seconds: float) -> str:
    s = int(seconds)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def run(
    video_path: str,
    output_dir: str,
    model_name: str,
    min_dur: float,
    max_dur: float,
    skip_transcribe: bool,
    transcribe_only: bool = False,
    transcript_path: str | None = None,
    groq_transcribe: bool = False,
    speechmatics: bool = False,
    audio_path: str | None = None,
    language: str = "ar",
    chunk_size: int = 180_000,
    feedback_path: str | None = None,
) -> None:
    pipeline_start = time.time()

    video = Path(video_path).resolve() if video_path else None
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    tmp = out / "_tmp"
    tmp.mkdir(exist_ok=True)

    transcript_cache = out / "transcript.json"

    # ── Step 1: Transcribe ──────────────────────────────────────────────────
    t0 = time.time()
    if transcript_path:
        print(f"\n[1/3] Loading transcript from {transcript_path}")
        result = load_cached(transcript_path)
    elif skip_transcribe and transcript_cache.exists():
        print(f"\n[1/3] Loading cached transcript from {transcript_cache}")
        result = load_cached(str(transcript_cache))
    elif speechmatics:
        src_audio = audio_path or str(tmp / "audio.flac")
        if audio_path:
            print(f"\n[1/3] Transcribing with Speechmatics from {audio_path}...")
        else:
            print(f"\n[1/3] Extracting audio then transcribing with Speechmatics...")
            from transcribe import extract_audio
            extract_audio(str(video), src_audio)
        result = transcribe_speechmatics(src_audio, language=language)
        with open(transcript_cache, "w", encoding="utf-8") as f:
            json.dump(result["segments"], f, ensure_ascii=False, indent=2)
        print(f"  Transcript saved → {transcript_cache}")
    elif groq_transcribe:
        src_audio = audio_path or str(tmp / "audio.wav")
        if audio_path:
            print(f"\n[1/3] Transcribing with Groq Whisper from {audio_path}...")
        else:
            print(f"\n[1/3] Extracting audio then transcribing with Groq Whisper...")
            from transcribe import extract_audio
            extract_audio(str(video), src_audio)
        result = transcribe_groq(src_audio, language=language)
        with open(transcript_cache, "w", encoding="utf-8") as f:
            json.dump(result["segments"], f, ensure_ascii=False, indent=2)
        print(f"  Transcript saved → {transcript_cache}")
    else:
        print(f"\n[1/3] Transcribing with Whisper {model_name}...")
        result = transcribe(str(video), tmp_dir=str(tmp), model_name=model_name, language=language)
        with open(transcript_cache, "w", encoding="utf-8") as f:
            json.dump(result["segments"], f, ensure_ascii=False, indent=2)
        print(f"  Transcript saved → {transcript_cache}")
    print(f"  ⏱  Step 1 done in {_hms(time.time() - t0)}")

    if transcribe_only:
        print("\nTranscription complete. Run without --transcribe-only to finish.")
        return

    segments = result["segments"]

    # ── Step 2: Identify moments + timestamps ───────────────────────────────
    t0 = time.time()
    feedback = ""
    if feedback_path:
        feedback = Path(feedback_path).read_text(encoding="utf-8")
        print(f"  Loaded feedback from {feedback_path}")

    print(f"\n[2/3] Identifying clip moments ({min_dur:.0f}–{max_dur:.0f}s) with Claude...")
    data = identify_moments(segments, min_duration=min_dur, max_duration=max_dur, language=language, chunk_size=chunk_size, feedback=feedback)
    print(f"  ⏱  Step 2 done in {_hms(time.time() - t0)}")

    print(f"\n  YouTube timestamps:")
    for ts in data["timestamps"]:
        print(f"    {ts['time']}  {ts['title']}")
    save_timestamps(data["timestamps"], out / "youtube_timestamps.txt")

    clips = data["clips"]
    generate_analysis_txt(clips, out / "media_team_analysis.txt")
    print(f"\n  Found {len(clips)} clip(s):")
    for i, c in enumerate(clips, 1):
        dur = c["end"] - c["start"]
        print(f"    [{i}] {fmt_time(c['start'])} → {fmt_time(c['end'])} ({dur:.0f}s) — {c['title']}")
        print(f"         Hook: {c.get('hook', '')}")
        if c.get("opening_line"):
            print(f"         Open: \"{c['opening_line']}\"")

    if not clips:
        print("\nNo valid clips found. Done.")
        return

    # ── Step 3: Render ──────────────────────────────────────────────────────
    if not video:
        print("\nNo video file provided — skipping render. Pass the video path to render clips.")
        return

    t0 = time.time()
    print(f"\n[3/3] Rendering {len(clips)} clip(s)...")
    info = get_video_info(str(video))
    src_w, src_h = info["width"], info["height"]

    for i, clip in enumerate(clips, 1):
        start, end = clip["start"], clip["end"]
        slug = slugify(clip["title"]) or f"clip_{i:02d}"
        name = f"{i:02d}_{slug}"

        print(f"\n  [{i}/{len(clips)}] {clip['title']}")

        # Original — cut then burn horizontal subtitles
        orig_raw = tmp / f"{name}_orig_raw.mp4"
        cut_original(str(video), start, end, str(orig_raw))
        orig_ass = tmp / f"{name}_orig.ass"
        build_ass(segments, start, end, str(orig_ass), is_vertical=False)
        orig_path = out / f"{name}_original.mp4"
        burn_subtitles(str(orig_raw), str(orig_ass), str(orig_path))
        print(f"    original  → {orig_path.name}")

        # Vertical raw (no subtitles yet)
        vert_raw = tmp / f"{name}_vert.mp4"
        cut_vertical(str(video), start, end, str(vert_raw), src_w, src_h)

        # ASS subtitles (vertical)
        ass_path = tmp / f"{name}.ass"
        build_ass(segments, start, end, str(ass_path), is_vertical=True)

        # Burn subtitles → final shorts file
        shorts_path = out / f"{name}_shorts.mp4"
        burn_subtitles(str(vert_raw), str(ass_path), str(shorts_path))
        print(f"    shorts    → {shorts_path.name}")

    print(f"  ⏱  Step 3 done in {_hms(time.time() - t0)}")

    total = time.time() - pipeline_start
    print(f"\n✓ Done in {_hms(total)} — outputs in: {out}/")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="SNIP — Automated video clip pipeline for Arabic and English content",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("video", nargs="?", help="Path to input video file (optional if --transcript is used)")
    parser.add_argument(
        "-o", "--output", default=None,
        help="Output directory (default: <video_dir>/snip_out or <transcript_dir>)",
    )
    parser.add_argument(
        "--transcript", default=None,
        help="Path to existing transcript.json — skips Whisper entirely",
    )
    parser.add_argument(
        "--speechmatics", action="store_true",
        help="Use Speechmatics Batch API instead of local Whisper (requires SPEECHMATICS_API_KEY)",
    )
    parser.add_argument(
        "--groq-transcribe", action="store_true",
        help="Use Groq Whisper API instead of local Whisper (requires GROQ_API_KEY)",
    )
    parser.add_argument(
        "--audio", default=None,
        help="Path to existing audio.wav — skips audio extraction (use with --groq-transcribe)",
    )
    parser.add_argument(
        "--model", default="large-v3",
        choices=["tiny", "base", "medium", "large", "large-v2", "large-v3"],
        help="Whisper model (default: large-v3)",
    )
    parser.add_argument(
        "--min-duration", type=float, default=60.0,
        help="Minimum clip duration in seconds (default: 60)",
    )
    parser.add_argument(
        "--max-duration", type=float, default=120.0,
        help="Maximum clip duration in seconds (default: 120)",
    )
    parser.add_argument(
        "--skip-transcribe", action="store_true",
        help="Skip transcription if transcript.json already exists in output dir",
    )
    parser.add_argument(
        "--transcribe-only", action="store_true",
        help="Only run Whisper and save transcript.json, then stop",
    )
    parser.add_argument(
        "--language", default="ar",
        help="Whisper language code and Claude prompt language (default: ar). "
             "Use 'en' for English, or any Whisper-supported language code.",
    )
    parser.add_argument(
        "--feedback", default=None,
        help="Path to a media_team_analysis.txt edited by the team — "
             "injected into Claude's prompt to improve clip selection.",
    )
    parser.add_argument(
        "--chunk-size", type=int, default=180_000,
        help="Max transcript chars per Claude call (default: 180000 ≈ 45k tokens). "
             "Lower this if Claude times out; raise it to send more context per call.",
    )

    args = parser.parse_args()

    if not args.video and not args.transcript:
        parser.error("provide a video file or --transcript path")

    if args.video:
        video = Path(args.video)
        if not video.exists():
            print(f"Error: file not found: {video}", file=sys.stderr)
            sys.exit(1)
        video_path = str(video)
        default_out = str(video.parent / "snip_out" / video.stem)
    else:
        video_path = None
        default_out = str(Path(args.transcript).parent)

    output_dir = args.output or default_out

    run(
        video_path=video_path,
        output_dir=output_dir,
        model_name=args.model,
        min_dur=args.min_duration,
        max_dur=args.max_duration,
        skip_transcribe=args.skip_transcribe,
        transcribe_only=args.transcribe_only,
        transcript_path=args.transcript,
        groq_transcribe=args.groq_transcribe,
        speechmatics=args.speechmatics,
        audio_path=args.audio,
        language=args.language,
        chunk_size=args.chunk_size,
        feedback_path=args.feedback,
    )


if __name__ == "__main__":
    main()
