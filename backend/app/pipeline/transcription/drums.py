"""Baseline drum transcription: onset detection + spectral-heuristic
classification into a small General MIDI percussion set.

This is intentionally a simple first pass (kick/snare/hihat/cymbal via
low/mid/high spectral-energy bands), not a trained drum-transcription model.
Flagged in ARCHITECTURE.md as an area to improve (e.g. swap in Omnizart or a
trained onset classifier) once the rest of the pipeline is end-to-end.
"""

from __future__ import annotations

from pathlib import Path

import librosa
import numpy as np

from app.pipeline.provider import NoteEvent

# General MIDI percussion key numbers (channel 10).
GM_KICK = 36
GM_SNARE = 38
GM_CLOSED_HIHAT = 42
GM_OPEN_HIHAT = 46
GM_CRASH = 49

_HIT_DURATION_SEC = 0.12


def transcribe_drum_stem(audio_path: Path) -> list[NoteEvent]:
    y, sr = librosa.load(str(audio_path), sr=None, mono=True)
    if y.size == 0:
        return []

    onset_frames = librosa.onset.onset_detect(y=y, sr=sr, units="frames")
    onset_times = librosa.frames_to_time(onset_frames, sr=sr)

    events: list[NoteEvent] = []
    hop = 2048
    for onset_sample, onset_time in zip(librosa.frames_to_samples(onset_frames), onset_times):
        window = y[onset_sample : onset_sample + hop]
        if window.size == 0:
            continue
        gm_key = _classify_hit(window, sr)
        events.append(
            NoteEvent(
                pitch_midi=gm_key,
                start_sec=float(onset_time),
                end_sec=float(onset_time) + _HIT_DURATION_SEC,
                velocity=100,
                gm_drum_key=gm_key,
            )
        )
    return events


def _classify_hit(window: np.ndarray, sr: int) -> int:
    spectrum = np.abs(np.fft.rfft(window))
    freqs = np.fft.rfftfreq(len(window), 1 / sr)
    if spectrum.sum() == 0:
        return GM_SNARE

    centroid = float(np.sum(freqs * spectrum) / spectrum.sum())
    low_energy = spectrum[(freqs < 150)].sum()
    high_energy = spectrum[(freqs > 6000)].sum()
    total = spectrum.sum()

    if low_energy / total > 0.5:
        return GM_KICK
    if high_energy / total > 0.35:
        return GM_OPEN_HIHAT if centroid > 9000 else GM_CLOSED_HIHAT
    return GM_SNARE
