"""Lyrics via speech-to-text on the isolated vocals stem.

Runs faster-whisper (CTranslate2-based, no torch dependency of its own —
lighter than openai-whisper) on the vocals stem Demucs already separated
out, with word-level timestamps so each word can be attached to the
nearest transcribed note in notation.py. Purely instrumental songs (no
vocals stem content, or nothing whisper is confident enough to transcribe)
just yield an empty list — lyrics are best-effort, not required.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from faster_whisper import WhisperModel

_model: WhisperModel | None = None


def _get_model() -> WhisperModel:
    # Loaded once per process and reused — model init (reading weights) is
    # the slow part (~seconds), not worth repeating per job.
    global _model
    if _model is None:
        _model = WhisperModel("base", device="cpu", compute_type="int8")
    return _model


@dataclass
class LyricWord:
    text: str
    start_sec: float
    end_sec: float


def transcribe_lyrics(vocals_audio_path: Path) -> list[LyricWord]:
    model = _get_model()
    # No `language=`: auto-detect rather than assume German, so this also
    # works for songs in other languages.
    segments, _info = model.transcribe(str(vocals_audio_path), word_timestamps=True)

    words: list[LyricWord] = []
    for segment in segments:
        for w in segment.words or []:
            text = w.word.strip()
            if text:
                words.append(LyricWord(text=text, start_sec=w.start, end_sec=w.end))
    return words
