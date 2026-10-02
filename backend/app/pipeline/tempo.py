"""BPM detection and mapping to Italian tempo markings."""

from __future__ import annotations

from dataclasses import dataclass

import librosa
import numpy as np

# (name, min_bpm, max_bpm) — ranges as specified by the user, ascending order.
# Boundaries between adjacent entries are resolved by nearest-midpoint so every
# integer BPM maps to exactly one marking, including gaps/overlaps in the
# source table (e.g. Andante 73-77 vs Andantino 78-83 touch at 77/78; Grave's
# lower bound is a floor, Prestissimo's upper bound is open-ended).
_TEMPO_TABLE: list[tuple[str, int, int]] = [
    ("Grave", 25, 45),
    ("Largo", 40, 60),
    ("Larghetto", 60, 66),
    ("Adagio", 66, 76),
    ("Andante", 73, 77),
    ("Andantino", 78, 83),
    ("Moderato", 85, 108),
    ("Allegretto", 100, 120),
    ("Allegro", 120, 156),
    ("Vivace", 156, 176),
    ("Presto", 168, 200),
    ("Prestissimo", 200, 400),
]


# librosa.beat.beat_track's autocorrelation-based tempo detection is
# prone to "octave errors" — reporting an exact multiple/fraction of the
# true tempo — and its own internal default start_bpm of 120 biases that
# ambiguity toward mid-tempo pop/rock. Measured directly on a sparse,
# rubato jazz ballad ("In A Sentimental Mood"): the detector's confidence
# (autocorrelation strength) at the wrong tempo (132.5) was only 0.8%
# higher than at the correct one (66.3) — essentially a coin flip, and no
# purely acoustic heuristic we tried could separate that case from a
# genuinely fast song (jingle.bells) with a similarly close margin (3.5%)
# whose *fast* reading was the correct one. A genre hint breaks that tie
# by biasing the search starting point toward the tempo range that genre
# actually occupies, rather than guessing from the audio alone.
GENRE_START_BPM: dict[str, float] = {
    "auto": 120.0,  # librosa's own default — no bias
    "ballad": 70.0,
    "pop_rock": 115.0,
    "dance": 128.0,
    "fast": 160.0,
}


@dataclass(frozen=True)
class TempoInfo:
    bpm: int
    italian_term: str


def italian_term_for_bpm(bpm: int) -> str:
    """Map an integer BPM to the closest Italian tempo marking.

    Overlapping ranges in the reference table are resolved by picking the
    entry whose [min, max] interval contains the BPM; if several match (the
    table has intentional overlaps, e.g. Allegretto/Allegro), the one whose
    range midpoint is closest wins. If none contains it, the nearest range
    boundary wins.
    """
    matches = [(name, lo, hi) for name, lo, hi in _TEMPO_TABLE if lo <= bpm <= hi]
    if matches:
        def midpoint_distance(entry: tuple[str, int, int]) -> float:
            _, lo, hi = entry
            return abs((lo + hi) / 2 - bpm)

        return min(matches, key=midpoint_distance)[0]

    def boundary_distance(entry: tuple[str, int, int]) -> int:
        _, lo, hi = entry
        return min(abs(bpm - lo), abs(bpm - hi))

    return min(_TEMPO_TABLE, key=boundary_distance)[0]


def detect_tempo(y: np.ndarray, sr: int, start_bpm: float = 120.0) -> TempoInfo:
    """Detect integer BPM from an audio signal and resolve the Italian term.
    `start_bpm` seeds librosa's autocorrelation search (see GENRE_START_BPM
    above for why this matters) — pass a genre-appropriate value to steer
    around octave-ambiguous cases; omit for librosa's own default."""
    tempo, _ = librosa.beat.beat_track(y=y, sr=sr, start_bpm=start_bpm)
    bpm = int(round(float(np.atleast_1d(tempo)[0])))
    return TempoInfo(bpm=bpm, italian_term=italian_term_for_bpm(bpm))
