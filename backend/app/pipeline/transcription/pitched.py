"""Note transcription for pitched (non-percussive) stems via Basic Pitch."""

from __future__ import annotations

from pathlib import Path

from basic_pitch import ICASSP_2022_MODEL_PATH
from basic_pitch.inference import predict

from app.pipeline.provider import NoteEvent


def transcribe_pitched_stem(audio_path: Path) -> list[NoteEvent]:
    _model_output, midi_data, note_events = predict(
        str(audio_path), model_or_model_path=ICASSP_2022_MODEL_PATH
    )

    events: list[NoteEvent] = []
    for start_sec, end_sec, pitch_midi, amplitude, _pitch_bends in note_events:
        velocity = max(1, min(127, round(amplitude * 127)))
        events.append(
            NoteEvent(
                pitch_midi=int(pitch_midi),
                start_sec=float(start_sec),
                end_sec=float(end_sec),
                velocity=velocity,
            )
        )
    events.sort(key=lambda e: e.start_sec)
    return events
