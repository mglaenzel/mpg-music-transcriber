"""Render a MIDI file to a playable WAV — the "Synth. Wiedergabe" audio.

The frontend originally pointed an <audio> element straight at the .mid
file. Browsers cannot decode raw MIDI as audio (it's not a supported
<audio>/<source> format — there's no sound in a MIDI file, only note
events), so playback silently failed. Rather than pull in a browser-side
MIDI synth (new JS dependency, more moving parts), we render actual audio
server-side once, in Python, and serve that like any other audio file — the
same approach already used for `source-audio`.

This is a simple additive sine-wave synth, not a real soundfont/sampler —
tonally it sounds like a cheap keyboard demo, not a piano/guitar/etc. That's
an intentional scope cut: the goal is "audibly correct pitches and rhythm to
check the transcription against", not concert-quality playback.
"""

from __future__ import annotations

from pathlib import Path

import mido
import numpy as np
import soundfile as sf

_SAMPLE_RATE = 22050
_ATTACK_SEC = 0.01
_RELEASE_SEC = 0.08
# General MIDI channel 10 (0-indexed: 9) is percussion by convention — the
# same convention our own notation.py/export uses for the Drums part.
_DRUM_CHANNEL = 9


def _midi_note_to_freq(note_number: int) -> float:
    return 440.0 * (2.0 ** ((note_number - 69) / 12.0))


def _add_tone(buffer: np.ndarray, sr: int, start_sec: float, end_sec: float, freq: float, velocity: int) -> None:
    start = max(0, int(start_sec * sr))
    end = min(len(buffer), int(end_sec * sr))
    n = end - start
    if n <= 0:
        return
    t = np.arange(n) / sr
    # A couple of harmonics instead of a pure sine so it doesn't sound like
    # a test-tone generator; still cheap to compute.
    wave = 0.6 * np.sin(2 * np.pi * freq * t) + 0.25 * np.sin(2 * np.pi * freq * 2 * t) + 0.15 * np.sin(2 * np.pi * freq * 3 * t)

    attack_n = min(n, int(_ATTACK_SEC * sr))
    release_n = min(n, int(_RELEASE_SEC * sr))
    envelope = np.ones(n)
    if attack_n > 0:
        envelope[:attack_n] = np.linspace(0, 1, attack_n)
    if release_n > 0:
        envelope[-release_n:] = np.minimum(envelope[-release_n:], np.linspace(1, 0, release_n))

    amplitude = (velocity / 127.0) * 0.25
    buffer[start:end] += wave * envelope * amplitude


def _add_drum_hit(buffer: np.ndarray, sr: int, start_sec: float, velocity: int) -> None:
    duration_sec = 0.12
    start = max(0, int(start_sec * sr))
    end = min(len(buffer), int((start_sec + duration_sec) * sr))
    n = end - start
    if n <= 0:
        return
    t = np.arange(n) / sr
    noise = np.random.default_rng(0).standard_normal(n)
    envelope = np.exp(-t / 0.04)
    amplitude = (velocity / 127.0) * 0.3
    buffer[start:end] += noise * envelope * amplitude


def render_midi_to_wav(midi_path: Path, wav_path: Path) -> Path:
    mid = mido.MidiFile(str(midi_path))
    total_sec = max(mid.length, 0.1) + 1.0
    buffer = np.zeros(int(total_sec * _SAMPLE_RATE), dtype=np.float64)

    active: dict[tuple[int, int], tuple[float, int]] = {}
    t = 0.0
    for msg in mid:
        t += msg.time
        if msg.type == "note_on" and msg.velocity > 0:
            active[(msg.channel, msg.note)] = (t, msg.velocity)
        elif msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0):
            key = (msg.channel, msg.note)
            if key not in active:
                continue
            start_t, velocity = active.pop(key)
            if msg.channel == _DRUM_CHANNEL:
                _add_drum_hit(buffer, _SAMPLE_RATE, start_t, velocity)
            else:
                _add_tone(buffer, _SAMPLE_RATE, start_t, t, _midi_note_to_freq(msg.note), velocity)

    peak = np.max(np.abs(buffer))
    if peak > 0:
        buffer = buffer / peak * 0.9

    sf.write(str(wav_path), buffer.astype(np.float32), _SAMPLE_RATE)
    return wav_path
