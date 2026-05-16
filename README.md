# SNIP

Automated video clip pipeline for Arabic and English content. Feed it a long video, get YouTube chapter timestamps and ready-to-post vertical clips with burned-in subtitles.

## What it produces

Each video gets its own folder under `snip_out/`:

```
snip_out/
└── my_lecture/
    ├── youtube_timestamps.txt
    ├── transcript.json
    ├── 01_clip_title_original.mp4
    ├── 01_clip_title_shorts.mp4
    ├── 02_clip_title_original.mp4
    └── 02_clip_title_shorts.mp4
```

| Output | Description |
|---|---|
| `youtube_timestamps.txt` | Chapter markers to paste into the video description |
| `clip_N_original.mp4` | Clip at original aspect ratio (LinkedIn / archive) |
| `clip_N_shorts.mp4` | 9:16 vertical with burned-in subtitles (YouTube Shorts / TikTok) |
| `transcript.json` | Full transcript cached for fast re-runs |

## How it works

```
Video → Whisper → Claude → ffmpeg
        transcribe  clip     render
                    select
```

Claude reads the transcript and picks the 3–6 moments most likely to make someone stop scrolling — prioritizing cold opens, self-contained insights, and punchy endings. The output is a JSON with exact timestamps that ffmpeg uses to cut and render.

## Requirements

**System dependencies** (must be installed and on PATH):

| Dependency | Purpose |
|---|---|
| Python 3.11+ | Runtime |
| [ffmpeg](https://ffmpeg.org/download.html) | Video cutting and subtitle burning |
| [Claude Code CLI](https://claude.ai/code) | AI clip selection (authenticated) |
| [uv](https://docs.astral.sh/uv/) | Python package manager |

**Optional** (for cloud transcription instead of local Whisper):

| Service | Flag | Key needed |
|---|---|---|
| [Groq](https://console.groq.com/) | `--groq-transcribe` | `GROQ_API_KEY` |
| [Speechmatics](https://www.speechmatics.com/) | `--speechmatics` | `SPEECHMATICS_API_KEY` |

> **Note:** The default transcription runs Whisper locally — no API key required. Claude Code CLI uses your existing Claude login — no `ANTHROPIC_API_KEY` needed.

## Installation

```bash
git clone https://github.com/silvaxxx1/Snipyfy.git
cd Snipyfy

# Install Python dependencies
uv sync

# (Optional) For cloud transcription — copy and fill in only the keys you need
cp .env.example .env
```

Verify ffmpeg and Claude CLI are available:

```bash
ffmpeg -version
claude --version
```

## Usage

```bash
# Arabic video (default)
uv run python snip.py /path/to/video.mp4

# English video
uv run python snip.py /path/to/video.mp4 --language en

# Custom output dir and clip length
uv run python snip.py /path/to/video.mp4 -o ./clips --min-duration 45 --max-duration 90

# Skip re-transcription on follow-up runs (transcript.json already exists)
uv run python snip.py /path/to/video.mp4 --skip-transcribe

# Use a lighter Whisper model for quick tests
uv run python snip.py /path/to/video.mp4 --model medium

# Cloud transcription (faster, no local GPU needed)
uv run python snip.py /path/to/video.mp4 --groq-transcribe
uv run python snip.py /path/to/video.mp4 --speechmatics

# Just transcribe, skip clip selection and rendering
uv run python snip.py /path/to/video.mp4 --transcribe-only

# Start from an existing transcript
uv run python snip.py /path/to/video.mp4 --transcript ./transcript.json
```

## CLI flags

| Flag | Default | Description |
|---|---|---|
| `-o / --output` | `<video_dir>/snip_out/<video_name>/` | Output directory |
| `--model` | `large-v3` | Whisper model: `tiny`, `base`, `medium`, `large`, `large-v2`, `large-v3` |
| `--min-duration` | `60` | Minimum clip length in seconds |
| `--max-duration` | `120` | Maximum clip length in seconds |
| `--skip-transcribe` | off | Reuse existing `transcript.json` in output dir |
| `--language` | `ar` | Language code for transcription and clip selection (`ar`, `en`, or any Whisper code) |
| `--transcribe-only` | off | Run Whisper only, skip clip selection and rendering |
| `--transcript` | — | Path to an existing `transcript.json` — skips Whisper entirely |
| `--groq-transcribe` | off | Transcribe via Groq Whisper API (requires `GROQ_API_KEY`) |
| `--speechmatics` | off | Transcribe via Speechmatics Batch API (requires `SPEECHMATICS_API_KEY`) |
| `--audio` | — | Path to pre-extracted audio file (use with `--groq-transcribe`) |

## Notes

- **RTL subtitles** — Arabic right-to-left text is handled automatically by libass in ffmpeg; no extra config needed.
- **Language** — pass `--language ar` (default) for Arabic, `--language en` for English, or any [Whisper-supported code](https://github.com/openai/whisper#available-models-and-languages). The Claude prompt adapts accordingly.
- **Arabic dialects** — `large-v3` gives best accuracy for Sudanese and Gulf dialects; `medium` works for Modern Standard Arabic.
- **9:16 crop** — center crop for now. Face-following pan is a planned improvement.
- **Fast iteration** — use `--skip-transcribe` to re-run clip selection without re-running Whisper (saves several minutes).
- **Long videos** — the transcript is chunked automatically if it exceeds Claude's single-call limit.

## License

MIT
