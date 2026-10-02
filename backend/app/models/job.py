from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class JobStatus(str, Enum):
    PENDING = "pending"
    SEPARATING = "separating"
    TRANSCRIBING = "transcribing"
    NOTATING = "notating"
    EXPORTING = "exporting"
    DONE = "done"
    FAILED = "failed"


class InstrumentTrackType(str, Enum):
    PIANO = "piano"
    GUITAR = "guitar"
    BASS = "bass"
    DRUMS = "drums"
    VOCALS = "vocals"
    STRINGS = "strings"
    WINDS = "winds"
    OTHER = "other"


@dataclass
class TrackResult:
    track_id: str
    instrument: InstrumentTrackType
    label: str
    stem_audio_path: str | None = None
    musicxml_path: str | None = None
    midi_path: str | None = None


@dataclass
class Job:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    status: JobStatus = JobStatus.PENDING
    original_filename: str = ""
    source_audio_path: str = ""
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    progress: float = 0.0
    error: str | None = None

    bpm: int | None = None
    tempo_italian_term: str | None = None
    key_signature: str | None = None
    time_signature: str | None = None

    tracks: list[TrackResult] = field(default_factory=list)
    combined_musicxml_path: str | None = None
    combined_midi_path: str | None = None

    def touch(self) -> None:
        self.updated_at = datetime.utcnow()
