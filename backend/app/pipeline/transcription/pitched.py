"""Note transcription for pitched (non-percussive) stems via Basic Pitch."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from basic_pitch import ICASSP_2022_MODEL_PATH
from basic_pitch.inference import predict

from app.models.job import InstrumentTrackType
from app.pipeline.provider import NoteEvent


@dataclass(frozen=True)
class _BasicPitchProfile:
    # minimum/maximum_frequency constrain the model's own search space to
    # where the instrument can plausibly play (converted from
    # local_provider._PLAUSIBLE_MIDI_RANGE, which stays as a post-hoc
    # safety net for separation bleed) — narrowing this up front, rather
    # than only filtering after the fact, measurably reduces the kind of
    # octave/overtone misreads Basic Pitch is prone to on a bass line's
    # weak fundamental.
    minimum_frequency: float
    maximum_frequency: float
    onset_threshold: float = 0.5  # Basic Pitch's own default
    frame_threshold: float = 0.3  # Basic Pitch's own default
    # melodia_trick favors one continuous salient line over genuine
    # polyphony (confirmed empirically while investigating a two-saxophone
    # jazz recording — see experiments/separation-models/README.md on the
    # separation-model-experiments branch) — exactly what we want for an
    # instrument that's normally monophonic (vocals, bass: one note at a
    # time, and it helps pick the real fundamental over a spurious
    # overtone), and exactly what we must turn *off* for a chordal
    # instrument (piano, guitar) or it'll quietly drop real chord tones.
    melodia_trick: bool = True


# Keyed by InstrumentTrackType; stems not listed here (e.g. a future
# instrument type) fall back to _DEFAULT_PROFILE.
_PROFILES: dict[InstrumentTrackType, _BasicPitchProfile] = {
    InstrumentTrackType.VOCALS: _BasicPitchProfile(
        minimum_frequency=98.0,  # G2
        maximum_frequency=880.0,  # A5
        onset_threshold=0.4,  # sung onsets are often soft/legato, not a sharp attack
        melodia_trick=True,  # one voice at a time
    ),
    InstrumentTrackType.PIANO: _BasicPitchProfile(
        minimum_frequency=27.5,  # A0, practically the full keyboard
        maximum_frequency=4186.0,  # C8
        frame_threshold=0.25,  # catch quieter chord tones under the melody note
        melodia_trick=False,  # chords are the point — don't collapse to one line
    ),
    InstrumentTrackType.GUITAR: _BasicPitchProfile(
        minimum_frequency=82.4,  # E2
        maximum_frequency=1318.5,  # E6
        melodia_trick=False,  # chords/double-stops are common
    ),
    InstrumentTrackType.BASS: _BasicPitchProfile(
        minimum_frequency=41.2,  # E1
        maximum_frequency=392.0,  # G4
        melodia_trick=True,  # monophonic; also biases toward the real fundamental
    ),
    InstrumentTrackType.STRINGS: _BasicPitchProfile(
        minimum_frequency=65.4, maximum_frequency=2093.0, melodia_trick=True
    ),
    InstrumentTrackType.WINDS: _BasicPitchProfile(
        minimum_frequency=130.8, maximum_frequency=2093.0, melodia_trick=True
    ),
}
_DEFAULT_PROFILE = _BasicPitchProfile(
    minimum_frequency=65.4, maximum_frequency=2093.0, melodia_trick=True
)


def transcribe_pitched_stem(
    audio_path: Path, instrument: InstrumentTrackType | None = None
) -> list[NoteEvent]:
    profile = _PROFILES.get(instrument, _DEFAULT_PROFILE) if instrument else _DEFAULT_PROFILE
    _model_output, midi_data, note_events = predict(
        str(audio_path),
        model_or_model_path=ICASSP_2022_MODEL_PATH,
        onset_threshold=profile.onset_threshold,
        frame_threshold=profile.frame_threshold,
        minimum_frequency=profile.minimum_frequency,
        maximum_frequency=profile.maximum_frequency,
        melodia_trick=profile.melodia_trick,
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
