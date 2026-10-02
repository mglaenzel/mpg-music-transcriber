"""TranscriptionProvider interface.

Decouples the rest of the app from the concrete transcription engine, so the
local open-source pipeline (Demucs + Basic Pitch) and a future commercial API
provider can be swapped via `settings.transcription_provider` without
touching callers. See docs/ARCHITECTURE.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.models.job import InstrumentTrackType


@dataclass
class NoteEvent:
    pitch_midi: int  # MIDI note number; ignored for percussion (use gm_drum_key instead)
    start_sec: float
    end_sec: float
    velocity: int = 90
    gm_drum_key: int | None = None  # General MIDI percussion key, drums only


@dataclass
class StemTranscription:
    instrument: InstrumentTrackType
    label: str
    stem_audio_path: Path
    notes: list[NoteEvent]


class TranscriptionProvider(Protocol):
    def separate_and_transcribe(
        self, audio_path: Path, work_dir: Path
    ) -> list[StemTranscription]:
        """Run source separation + per-stem note transcription."""
        ...


def get_provider() -> TranscriptionProvider:
    from app.core.config import settings

    if settings.transcription_provider == "local":
        from app.pipeline.local_provider import LocalTranscriptionProvider

        return LocalTranscriptionProvider()

    raise NotImplementedError(
        f"Transcription provider '{settings.transcription_provider}' is not "
        "implemented yet. Only 'local' is available right now."
    )
