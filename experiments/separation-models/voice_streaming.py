"""Prototype: split a list of (possibly overlapping) transcribed notes into
separate monophonic "voices" by pitch-proximity + temporal continuity —
the classic multi-pitch streaming approach (Duan, Han & Pardo, "Multi-pitch
Streaming of Harmonic Sound Mixtures").

Built while investigating whether we could split the two simultaneous
saxophones in a Gerry Mulligan jazz quintet recording ("In A Sentimental
Mood") into two separate parts. See README.md in this directory for the
full writeup — short version: this two-stage algorithm (tight-threshold
greedy streaming, then merge compatible fragments) works correctly as an
algorithm, but on our actual test data it kept finding 4+ long-lived
simultaneous voices even at a very loose pitch-jump threshold, not 2 —
meaning the bottleneck had moved from "algorithm too naive" to "the
upstream note data itself is too noisy" (residual bleed from other brass
in the BS-Roformer "saxophone" stem, and/or vibrato being split into
spurious extra notes by Basic Pitch). Kept here, not wired into the main
app, in case a cleaner upstream signal (better separation model, or
noise-robust note merging) makes this worth revisiting.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# A note as basic_pitch.inference.predict() returns it: (start_sec, end_sec,
# pitch_midi, amplitude, pitch_bends).
RawNote = tuple[float, float, int, float, object]


@dataclass
class VoiceFragment:
    notes: list[tuple[float, float, int]] = field(default_factory=list)

    @property
    def first_start(self) -> float:
        return self.notes[0][0]

    @property
    def last_end(self) -> float:
        return max(n[1] for n in self.notes)

    @property
    def last_pitch(self) -> int:
        return self.notes[-1][2]


def stream_notes(
    notes: list[RawNote],
    tight_jump_semitones: int = 9,
) -> list[VoiceFragment]:
    """Stage 1: greedy streaming. Walk notes in time order; attach each one
    to whichever currently-free voice has the closest last pitch, as long
    as the jump isn't implausibly large for a single continuous melodic
    line; otherwise start a new fragment. "Free" = that voice's previous
    note has already ended (voices can't play two notes at once).

    `tight_jump_semitones` trades off two failure modes: too tight and a
    single real voice gets fragmented every time it takes a normal melodic
    leap (we saw this at 5 semitones — way too strict for jazz phrasing);
    too loose and unrelated notes get glued into one fragment. 9 semitones
    (a sixth) was the least-bad value we found, but see the module
    docstring — on our actual test data even very loose values (20
    semitones) still left 4+ fragments spanning most of the clip, meaning
    this parameter wasn't actually the bottleneck."""
    fragments: list[VoiceFragment] = []
    for start, end, pitch, _amp, _bends in sorted(notes, key=lambda n: n[0]):
        free = [f for f in fragments if f.last_end <= start + 0.05]
        if free:
            best = min(free, key=lambda f: abs(f.last_pitch - pitch))
            if abs(best.last_pitch - pitch) <= tight_jump_semitones:
                best.notes.append((start, end, pitch))
                continue
        fragments.append(VoiceFragment(notes=[(start, end, pitch)]))
    return fragments


def merge_compatible_fragments(
    fragments: list[VoiceFragment],
    target_voice_count: int,
    max_merge_jump_semitones: int = 7,
    max_gap_sec: float = 3.0,
) -> list[VoiceFragment]:
    """Stage 2: two fragments are candidates for merging only if they don't
    overlap in time and the pitch/time gap between them is small enough to
    plausibly be the same player continuing. Repeatedly merges the
    cheapest compatible pair until `target_voice_count` is reached or no
    compatible pair remains (the latter is the actual failure mode we hit
    — see module docstring)."""

    def compatible_cost(a: VoiceFragment, b: VoiceFragment) -> float | None:
        if a.last_end <= b.first_start + 0.05:
            earlier, later = a, b
        elif b.last_end <= a.first_start + 0.05:
            earlier, later = b, a
        else:
            return None  # they overlap in time — can't be the same voice
        gap = later.first_start - earlier.last_end
        jump = abs(earlier.last_pitch - later.notes[0][2])
        if gap > max_gap_sec or jump > max_merge_jump_semitones:
            return None
        return gap + jump * 0.3

    fragments = list(fragments)
    while len(fragments) > target_voice_count:
        best_pair, best_cost = None, None
        for i in range(len(fragments)):
            for j in range(i + 1, len(fragments)):
                cost = compatible_cost(fragments[i], fragments[j])
                if cost is not None and (best_cost is None or cost < best_cost):
                    best_cost, best_pair = cost, (i, j)
        if best_pair is None:
            break  # no compatible pair left — can't reach target_voice_count
        i, j = best_pair
        merged = VoiceFragment(
            notes=sorted(fragments[i].notes + fragments[j].notes, key=lambda n: n[0])
        )
        fragments = [f for k, f in enumerate(fragments) if k not in (i, j)] + [merged]
    return fragments
