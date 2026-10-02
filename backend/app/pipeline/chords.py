"""Baseline per-measure chord recognition via chroma template matching.

Computes a chroma vector (energy per pitch class, octave-independent) for
each measure of the original mix and matches it against the 24 major/minor
triad templates by cosine similarity. This is a standard lightweight MIR
approach — not as robust as a trained chord-recognition model (no
7ths/sus/inversions), but enough to annotate chord symbols above the staff
like klang.io does. Flagged as an area to upgrade later in ARCHITECTURE.md.
"""

from __future__ import annotations

import librosa
import numpy as np

_PITCH_CLASSES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def _major_template(root: int) -> np.ndarray:
    t = np.zeros(12)
    t[[root, (root + 4) % 12, (root + 7) % 12]] = 1.0
    return t


def _minor_template(root: int) -> np.ndarray:
    t = np.zeros(12)
    t[[root, (root + 3) % 12, (root + 7) % 12]] = 1.0
    return t


def _build_templates() -> dict[str, np.ndarray]:
    templates: dict[str, np.ndarray] = {}
    for i, pc in enumerate(_PITCH_CLASSES):
        templates[pc] = _major_template(i) / np.linalg.norm(_major_template(i))
        templates[f"{pc}m"] = _minor_template(i) / np.linalg.norm(_minor_template(i))
    return templates


_TEMPLATES = _build_templates()

_SILENCE_THRESHOLD = 1e-3


def detect_chords_per_measure(
    y: np.ndarray, sr: int, bpm: int, num_measures: int
) -> list[str | None]:
    """Returns one chord label (or None for near-silent measures) per measure,
    assuming a constant 4/4 tempo of `bpm` starting at t=0."""
    if num_measures <= 0 or y.size == 0:
        return []

    measure_sec = (60.0 / bpm) * 4
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
    times = librosa.frames_to_time(np.arange(chroma.shape[1]), sr=sr)

    labels: list[str | None] = []
    for m in range(num_measures):
        t0, t1 = m * measure_sec, (m + 1) * measure_sec
        mask = (times >= t0) & (times < t1)
        if not mask.any():
            labels.append(None)
            continue

        mean_chroma = chroma[:, mask].mean(axis=1)
        norm = np.linalg.norm(mean_chroma)
        if norm < _SILENCE_THRESHOLD:
            labels.append(None)
            continue
        mean_chroma = mean_chroma / norm

        best_label, best_score = None, -1.0
        for label, template in _TEMPLATES.items():
            score = float(np.dot(mean_chroma, template))
            if score > best_score:
                best_score, best_label = score, label
        labels.append(best_label)

    return labels
