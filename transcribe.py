"""Whisper transcription — local, via Groq API, or via Speechmatics Batch API."""

import json
import os
import subprocess
import tempfile
from pathlib import Path


def extract_audio(video_path: str, audio_path: str) -> None:
    ext = Path(audio_path).suffix.lower()
    # FLAC: lossless, ~4x smaller than PCM WAV → faster uploads; WAV for Whisper/Groq compatibility
    codec = "flac" if ext == ".flac" else "pcm_s16le"
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", video_path,
            "-vn", "-acodec", codec,
            "-ar", "16000", "-ac", "1",
            audio_path,
        ],
        check=True,
        capture_output=True,
    )


def _audio_duration(audio_path: str) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
         "-of", "csv=p=0", audio_path],
        capture_output=True, text=True, check=True,
    )
    return float(result.stdout.strip())


def _extract_chunk_flac(audio_path: str, start: float, end: float, out_path: str) -> None:
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", audio_path,
            "-ss", str(start), "-to", str(end),
            "-acodec", "flac", "-ar", "16000", "-ac", "1",
            out_path,
        ],
        check=True,
        capture_output=True,
    )


def transcribe_groq(audio_path: str, language: str = "ar", chunk_secs: int = 1500) -> dict:
    """
    Transcribe audio using Groq Whisper large-v3-turbo.
    Chunks audio into segments to stay under 25MB / 30-min limits.
    """
    from openai import OpenAI

    client = OpenAI(
        api_key=os.environ["GROQ_API_KEY"],
        base_url="https://api.groq.com/openai/v1",
    )

    total = _audio_duration(audio_path)
    all_segments = []
    chunk_num = 0

    start = 0.0
    while start < total:
        end = min(start + chunk_secs, total)
        chunk_num += 1
        print(f"  Chunk {chunk_num}: {start/60:.1f}min → {end/60:.1f}min ...")

        with tempfile.NamedTemporaryFile(suffix=".flac", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            _extract_chunk_flac(audio_path, start, end, tmp_path)
            with open(tmp_path, "rb") as f:
                response = client.audio.transcriptions.create(
                    model="whisper-large-v3-turbo",
                    file=f,
                    language=language,
                    response_format="verbose_json",
                    timestamp_granularities=["word", "segment"],
                )
            for seg in response.segments:
                seg_dict = dict(seg) if not isinstance(seg, dict) else seg
                seg_dict["start"] = seg_dict.get("start", 0) + start
                seg_dict["end"] = seg_dict.get("end", 0) + start
                for word in seg_dict.get("words", []):
                    word["start"] = word.get("start", 0) + start
                    word["end"] = word.get("end", 0) + start
                all_segments.append(seg_dict)
        finally:
            Path(tmp_path).unlink(missing_ok=True)

        start = end

    return {"segments": all_segments}


def transcribe(video_path: str, tmp_dir: str, model_name: str = "large-v3", language: str = "ar") -> dict:
    """Transcribe video locally with Whisper."""
    import whisper

    video = Path(video_path)
    audio_path = str(Path(tmp_dir) / "audio.wav")

    print(f"  Extracting audio from {video.name}...")
    extract_audio(str(video), audio_path)

    print(f"  Loading Whisper model '{model_name}' on CPU...")
    model = whisper.load_model(model_name, device="cpu")

    print(f"  Transcribing (language={language})... this may take a while.")
    result = model.transcribe(
        audio_path,
        language=language,
        word_timestamps=True,
        verbose=False,
    )
    return result


def _sm_results_to_segments(results: list, gap_threshold: float = 1.5) -> list[dict]:
    """
    Convert Speechmatics flat RecognitionResult list → SNIP segment format.

    Groups consecutive words into segments, splitting on:
    - silence gaps wider than gap_threshold seconds
    - sentence-ending punctuation (. ? !)
    """
    SENTENCE_ENDS = {".", "?", "!"}

    segments: list[dict] = []
    current_words: list[dict] = []
    current_text_parts: list[str] = []
    pending_punct: str = ""

    def flush(seg_end: float) -> None:
        nonlocal pending_punct, current_words, current_text_parts
        if not current_words:
            return
        text = " ".join(current_text_parts)
        if pending_punct:
            text = text + pending_punct
            pending_punct = ""
        segments.append({
            "start": current_words[0]["start"],
            "end": seg_end,
            "text": text,
            "speaker": current_words[0].get("speaker", "S1"),
            "words": list(current_words),
        })
        current_words = []
        current_text_parts = []

    for r in results:
        if not r.alternatives:
            continue
        alt = r.alternatives[0]
        content = alt.content or ""

        if r.type == "word":
            start, end = r.start_time or 0.0, r.end_time or 0.0
            # Split on silence gap
            if current_words and (start - current_words[-1]["end"]) > gap_threshold:
                flush(current_words[-1]["end"])
            current_words.append({
                "word": content,
                "start": start,
                "end": end,
                "probability": alt.confidence if alt.confidence is not None else 1.0,
                "speaker": alt.speaker or "S1",
            })
            current_text_parts.append(content)

        elif r.type == "punctuation":
            if content in SENTENCE_ENDS and current_words:
                pending_punct = content
                flush(current_words[-1]["end"])
            # other punctuation (comma etc.) — attach to text but don't split
            elif current_text_parts:
                current_text_parts[-1] = current_text_parts[-1] + content

    # flush any remaining words
    if current_words:
        flush(current_words[-1]["end"])

    return segments


def transcribe_speechmatics(audio_path: str, language: str = "ar") -> dict:
    """Transcribe audio via Speechmatics Batch API with word-level timestamps."""
    import asyncio

    from speechmatics.batch import AsyncClient, TranscriptionConfig

    async def _run() -> list[dict]:
        # diarization="speaker" enables speaker labels ([S1], [S2]) used by the Claude prompt.
        # language_identification_config requires language="auto" which hurts accuracy, so we skip it.
        config = TranscriptionConfig(
            language=language,
            operating_point="enhanced",
            diarization="speaker",
        )
        async with AsyncClient(api_key=os.environ["SPEECHMATICS_API_KEY"]) as client:
            print("  Submitting job to Speechmatics...")
            job = await client.submit_job(audio_path, transcription_config=config)
            print(f"  Job ID: {job.id} — waiting for completion...")
            transcript = await client.wait_for_completion(job.id, polling_interval=5.0)
        return _sm_results_to_segments(transcript.results)

    segments = asyncio.run(_run())
    print(f"  Speechmatics: {len(segments)} segments.")
    return {"segments": segments}


def load_cached(transcript_path: str) -> dict:
    """Load a previously saved transcript JSON."""
    with open(transcript_path, "r", encoding="utf-8") as f:
        return {"segments": json.load(f)}
