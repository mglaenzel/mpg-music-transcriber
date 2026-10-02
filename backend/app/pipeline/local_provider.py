from __future__ import annotations

from pathlib import Path

from app.models.job import InstrumentTrackType
from app.pipeline.separation import separate_stems
from app.pipeline.transcription.drums import transcribe_drum_stem
from app.pipeline.transcription.pitched import transcribe_pitched_stem
from app.pipeline.provider import NoteEvent, StemTranscription

# Plausible MIDI pitch range per instrument. Source-separation bleed (e.g.
# bell/cymbal transients leaking into the "vocals" stem) makes Basic Pitch
# occasionally report notes several octaves outside where the instrument can
# actually play — these render as walls of ledger lines and blow up measure
# width, which was part of why scores were cramming to ~1 measure/line.
# Dropping obvious outliers is a blunt but safe fix (better than clamping,
# which would just relabel noise as a wrong-but-plausible pitch).
_PLAUSIBLE_MIDI_RANGE: dict[InstrumentTrackType, tuple[int, int]] = {
    InstrumentTrackType.VOCALS: (43, 81),  # G2–A5
    InstrumentTrackType.GUITAR: (40, 88),  # E2–E6
    InstrumentTrackType.BASS: (28, 67),  # E1–G4
    InstrumentTrackType.PIANO: (21, 108),  # full piano range, effectively no filter
    InstrumentTrackType.OTHER: (36, 96),
    InstrumentTrackType.STRINGS: (36, 96),
    InstrumentTrackType.WINDS: (48, 96),
}


def _filter_outlier_pitches(
    notes: list[NoteEvent], instrument: InstrumentTrackType
) -> list[NoteEvent]:
    lo, hi = _PLAUSIBLE_MIDI_RANGE.get(instrument, (0, 127))
    return [n for n in notes if lo <= n.pitch_midi <= hi]

# Stem name -> (instrument, human label), from Demucs's htdemucs_6s model.
# `other` catches whatever is left (strings, winds, synths, ...) until a
# finer split for those exists, see ARCHITECTURE.md.
_STEM_INSTRUMENT: dict[str, tuple[InstrumentTrackType, str]] = {
    "vocals": (InstrumentTrackType.VOCALS, "Vocals"),
    "piano": (InstrumentTrackType.PIANO, "Piano"),
    "guitar": (InstrumentTrackType.GUITAR, "Gitarre"),
    "bass": (InstrumentTrackType.BASS, "Bass"),
    "drums": (InstrumentTrackType.DRUMS, "Drums"),
    "other": (InstrumentTrackType.OTHER, "Sonstige"),
}

# Presentation/staff order: Vocals first, then Piano directly above Bass
# (piano right hand / left hand reads naturally next to a bass line — the
# same reason a piano grand staff puts treble directly above bass), then
# the rest, drums last.
_TRACK_ORDER = ["vocals", "piano", "bass", "guitar", "other", "drums"]


class LocalTranscriptionProvider:
    def separate_and_transcribe(
        self, audio_path: Path, work_dir: Path
    ) -> list[StemTranscription]:
        stems = separate_stems(audio_path, work_dir)

        results: list[StemTranscription] = []
        for stem_name in _TRACK_ORDER:
            stem_path = stems[stem_name]
            instrument, label = _STEM_INSTRUMENT[stem_name]
            if stem_name == "drums":
                notes = transcribe_drum_stem(stem_path)
            else:
                notes = transcribe_pitched_stem(stem_path)
                notes = _filter_outlier_pitches(notes, instrument)

            results.append(
                StemTranscription(
                    instrument=instrument,
                    label=label,
                    stem_audio_path=stem_path,
                    notes=notes,
                )
            )
        return results
