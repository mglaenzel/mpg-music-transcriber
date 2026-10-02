"""Key-signature detection from transcribed pitches.

Without this, every part defaulted to C major, so any note outside C needed
an accidental printed in front of it — cluttering the score. We instead
collect all pitched (non-drum) notes across stems and let music21's
Krumhansl-Schmuckler-based key analysis estimate the most likely key.
"""

from __future__ import annotations

from music21 import key as m21key
from music21 import note, stream

from app.models.job import InstrumentTrackType
from app.pipeline.provider import StemTranscription


def detect_key(stems: list[StemTranscription]) -> m21key.Key:
    pitch_stream = stream.Stream()
    for stem in stems:
        if stem.instrument == InstrumentTrackType.DRUMS:
            continue
        for evt in stem.notes:
            n = note.Note()
            n.pitch.midi = evt.pitch_midi
            pitch_stream.append(n)

    if len(pitch_stream) == 0:
        return m21key.Key("C")

    try:
        analyzed = pitch_stream.analyze("key")
    except Exception:
        return m21key.Key("C")

    if not isinstance(analyzed, m21key.Key):
        return m21key.Key("C")
    return analyzed
