"""Clip moment identification via the Claude Code CLI.

Requires `claude` on PATH and an active Claude Code login (OAuth).
No ANTHROPIC_API_KEY needed — the CLI uses its own session auth.
"""

import json
import os
import re
import subprocess


def fmt_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _build_prompt(transcript: str, min_dur: int, max_dur: int, language: str) -> str:
    ar = language == "ar"

    if ar:
        header = (
            "You are a viral Arabic content editor. Your job is to find the moments in this "
            "transcript that would make someone stop scrolling and watch to the end — then share it.\n\n"
            "The video is Arabic educational/technical content (may contain English technical terms). "
            "Speaker labels like [S1], [S2] indicate different speakers — use this to spot Q&A moments "
            "or audience interactions."
        )
        ts_instruction = "List every major topic shift. Titles in Arabic. English tech terms stay in English."
        cold_open = (
            "1. COLD OPEN: The clip's first sentence must grab without any setup.\n"
            '   Bad start: "كما قلنا في البداية..." / "يعني..." / "المهم..."\n'
            "   Good start: A bold claim, a surprising fact, a question, a relatable analogy."
        )
        viral = (
            "3. VIRAL SIGNAL — pick clips with at least one of:\n"
            '   - A moment that makes you say "ما كنت أعرف هذا" (didn\'t know that)\n'
            "   - A relatable analogy using everyday Arabic life/culture\n"
            "   - A demonstration where something visibly works or fails\n"
            "   - A counterintuitive or controversial claim\n"
            "   - A speaker genuinely laughing or the audience reacting"
        )
        title_format = (
            "TITLE FORMAT (Arabic):\n"
            "- Max 7 words\n"
            '- Curiosity gap: make the viewer need to watch ("لماذا...", "ما لا تعرفه عن...", "الخطأ الذي...")\n'
            "- English tech terms stay in English"
        )
        title_example = '"عنوان فضولي بالعربي"'
    else:
        header = (
            "You are a viral content editor. Your job is to find the moments in this "
            "transcript that would make someone stop scrolling and watch to the end — then share it.\n\n"
            "Speaker labels like [S1], [S2] indicate different speakers — use this to spot Q&A moments "
            "or audience interactions."
        )
        ts_instruction = "List every major topic shift. Titles in English."
        cold_open = (
            "1. COLD OPEN: The clip's first sentence must grab without any setup.\n"
            '   Bad start: "As I was saying..." / "So basically..." / "The thing is..."\n'
            "   Good start: A bold claim, a surprising fact, a question, a relatable analogy."
        )
        viral = (
            "3. VIRAL SIGNAL — pick clips with at least one of:\n"
            "   - A counterintuitive or surprising fact\n"
            "   - A relatable analogy from everyday life\n"
            "   - A demonstration where something visibly works or fails\n"
            "   - A bold or controversial claim\n"
            "   - A speaker genuinely laughing or the audience reacting"
        )
        title_format = (
            "TITLE FORMAT (English):\n"
            "- Max 7 words\n"
            '- Curiosity gap: make the viewer need to watch ("Why...", "What nobody tells you about...", "The mistake...")'
        )
        title_example = '"A Curiosity-Gap Title"'

    return f"""\
{header}

TRANSCRIPT:
{transcript}

━━━ TASK 1: YouTube chapter timestamps ━━━
{ts_instruction}

━━━ TASK 2: Select {min_dur}–{max_dur}s clips for YouTube / TikTok ━━━

WHAT MAKES A GREAT CLIP — ranked by importance:
{cold_open}
2. SELF-CONTAINED: A viewer who never saw the full video understands and feels satisfied.
{viral}
4. CLEAN END: Ends on a complete thought, a punchline, or a resolved point — not mid-sentence.

HARD RULES:
- Duration: end - start must be between {min_dur} and {max_dur} seconds exactly
- No overlapping clips
- 3–6 clips maximum
- Use EXACT float seconds from the transcript (the number after | on each line)
- Do NOT invent or round timestamps
- Skip clips that are pure Q&A unless the answer is genuinely standalone and punchy

{title_format}

Return ONLY this JSON, nothing else:
{{
  "timestamps": [
    {{"time": "0:00", "title": "Chapter title"}},
    {{"time": "3:45", "title": "..."}}
  ],
  "clips": [
    {{
      "start": 42.0,
      "end": 108.5,
      "title": {title_example},
      "opening_line": "first sentence of the clip verbatim",
      "hook": "why a stranger would stop scrolling at this exact moment (English)"
    }}
  ]
}}
"""


def parse_llm_json(raw: str) -> dict:
    cleaned = raw.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"\s*```$", "", cleaned, flags=re.MULTILINE)
    return json.loads(cleaned.strip())


def _call_claude(prompt: str) -> dict:
    try:
        result = subprocess.run(
            ["claude", "--print"],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=900,
            env=os.environ,
        )
    except FileNotFoundError:
        raise RuntimeError(
            "Claude Code CLI not found. Install it from https://claude.ai/code "
            "and run `claude` once to log in."
        )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "no output"
        raise RuntimeError(f"claude CLI exited with code {result.returncode}: {detail}")
    return parse_llm_json(result.stdout)


def identify_moments(
    segments: list,
    min_duration: float = 60.0,
    max_duration: float = 120.0,
    language: str = "ar",
    chunk_size: int = 180_000,
) -> dict:
    lines = []
    for seg in segments:
        s = seg.get("start", 0)
        e = seg.get("end", 0)
        text = seg.get("text", "").strip()
        speaker = seg.get("speaker", "")
        prefix = f"[{speaker}] " if speaker else ""
        if text:
            lines.append(f"[{fmt_time(s)} | {s:.1f}s - {e:.1f}s] {prefix}{text}")
    full_text = "\n".join(lines)
    total_chars = len(full_text)

    CHUNK_SIZE = chunk_size  # default 180k chars (~45k tokens), well within Claude's 200k context

    all_clips: list = []
    all_timestamps: list = []
    chunk_num = 0
    pos = 0

    while pos < total_chars:
        chunk_num += 1
        chunk = full_text[pos: pos + CHUNK_SIZE]
        if pos + CHUNK_SIZE < total_chars:
            last_nl = chunk.rfind("\n")
            if last_nl > 0:
                chunk = chunk[:last_nl]

        next_pos = pos + len(chunk)
        pct = int(100 * next_pos / total_chars)
        label = "full transcript" if total_chars <= CHUNK_SIZE else f"chunk {chunk_num}, {pct}%"
        print(f"  Claude CLI → {label}...")

        prompt = _build_prompt(chunk, int(min_duration), int(max_duration), language)

        try:
            data = _call_claude(prompt)
            all_timestamps.extend(data.get("timestamps", []))
            all_clips.extend(data.get("clips", []))
        except Exception as e:
            print(f"  Warning: chunk {chunk_num} failed — {e}")

        pos = next_pos

    all_clips.sort(key=lambda c: c["start"])
    deduped = []
    for clip in all_clips:
        dur = clip["end"] - clip["start"]
        if not (min_duration <= dur <= max_duration):
            print(f"  Skipped '{clip['title']}' (duration {dur:.0f}s out of range)")
            continue
        if deduped and clip["start"] < deduped[-1]["end"]:
            print(f"  Skipped '{clip['title']}' (overlaps with previous clip)")
            continue
        deduped.append(clip)

    def ts_to_secs(t: str) -> int:
        parts = t.split(":")
        return sum(int(p) * 60 ** i for i, p in enumerate(reversed(parts)))

    seen: set = set()
    unique_ts = []
    for ts in sorted(all_timestamps, key=lambda t: ts_to_secs(t["time"])):
        if ts["time"] not in seen:
            seen.add(ts["time"])
            unique_ts.append(ts)

    return {"timestamps": unique_ts, "clips": deduped}
