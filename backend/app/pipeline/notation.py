"""Turn raw NoteEvents per stem into a music21 Score (MusicXML/MIDI-ready)."""

from __future__ import annotations

from music21 import clef as m21clef
from music21 import harmony, layout, metadata
from music21 import instrument as m21instrument
from music21 import stream, tempo as m21tempo, meter, key as m21key, note, chord

from app.models.job import InstrumentTrackType
from app.pipeline.lyrics import LyricWord
from app.pipeline.provider import NoteEvent, StemTranscription
from app.pipeline.tempo import italian_term_for_bpm
from app.pipeline.transcription.drums import GM_CLOSED_HIHAT, GM_KICK, GM_OPEN_HIHAT, GM_SNARE

# Measures per system/line — one measure per line (the music21/MuseScore
# default when packing is very dense) makes even short pieces sprawl over
# many pages; force a readable, klang.io-like layout instead.
DEFAULT_MEASURES_PER_SYSTEM = 4

_INSTRUMENT_M21: dict[InstrumentTrackType, type[m21instrument.Instrument]] = {
    InstrumentTrackType.PIANO: m21instrument.Piano,
    InstrumentTrackType.GUITAR: m21instrument.AcousticGuitar,
    InstrumentTrackType.BASS: m21instrument.ElectricBass,
    InstrumentTrackType.VOCALS: m21instrument.Vocalist,
    InstrumentTrackType.STRINGS: m21instrument.StringInstrument,
    InstrumentTrackType.WINDS: m21instrument.WoodwindInstrument,
    InstrumentTrackType.DRUMS: m21instrument.Woodblock,  # placeholder; drum staff handled separately
    InstrumentTrackType.OTHER: m21instrument.Piano,
}

_BASS_CLEF_INSTRUMENTS = {InstrumentTrackType.BASS}


def _clef_for(instrument: InstrumentTrackType) -> m21clef.Clef:
    if instrument == InstrumentTrackType.DRUMS:
        return m21clef.PercussionClef()
    if instrument in _BASS_CLEF_INSTRUMENTS:
        return m21clef.BassClef()
    return m21clef.TrebleClef()


# Standard 5-line drum-staff positions (e.g. MuseScore's default kit):
# kick on the bottom line, snare on the middle line, hi-hat above the
# staff. (step, octave, notehead).
#
# These must be written as <unpitched><display-step>/<display-octave>
# elements (music21 note.Unpitched), not regular <pitch> notes: with a
# percussion clef, OSMD (confirmed by a minimal isolated test — four
# regular Note objects at C4/F4/C5/G5 under a percussion clef all render
# at the *same* position, ignoring pitch entirely) only positions notes
# on the staff correctly when they're Unpitched with an explicit display
# position. A regular pitched Note is apparently just given some default
# fallback placement regardless of its actual pitch when the clef is
# percussion — which is what put every drum note several ledger lines
# below the staff.
_DRUM_MIDI_TO_DISPLAY: dict[int, tuple[str, int, str]] = {
    GM_KICK: ("F", 4, "normal"),
    GM_SNARE: ("C", 5, "normal"),
    GM_CLOSED_HIHAT: ("G", 5, "x"),
    GM_OPEN_HIHAT: ("G", 5, "x"),
}


def build_part(
    stem: StemTranscription,
    bpm: int,
    quarter_length_sec: float,
    key_obj: m21key.Key,
    num_measures: int,
    show_tempo: bool = True,
    chord_labels_per_measure: list[str | None] | None = None,
    lyric_words: list[LyricWord] | None = None,
) -> stream.Part:
    part = stream.Part(id=stem.instrument.value)
    part.partName = stem.label

    is_drum = stem.instrument == InstrumentTrackType.DRUMS
    # (offset_ql, note) pairs for lyric-word alignment further down — only
    # populated for the Vocals part.
    vocal_note_offsets: list[tuple[float, note.NotRest]] = []

    groups = _merge_simultaneous(stem.notes)
    # (offset_ql, raw_duration_ql) per group, computed up front so each
    # duration can be trimmed against the *next* group's start — Basic
    # Pitch occasionally emits two near (but not simultaneous-enough to
    # merge) events for the same pitch, which otherwise overlap. An
    # overlapping same-pitch note in a single voice makes music21 treat it
    # as a tie between two musically unrelated notes when writing/measuring
    # the stream (a real tie, not a bug in music21 — but one that then
    # crashes OpenSheetMusicDisplay 1.9's tie-graphics code, since the two
    # "tied" notes aren't actually adjacent).
    offsets_ql = [_quantize(g[0].start_sec / quarter_length_sec) for g in groups]
    groups, offsets_ql = _merge_by_quantized_offset(groups, offsets_ql)

    # Build Measure objects explicitly instead of inserting notes into a
    # flat Part and calling part.makeMeasures(): on real (noisy, densely
    # packed) transcriptions, makeMeasures()'s own heuristics sometimes
    # failed to split at every 4.0-quarterLength boundary — measures with
    # e.g. 6.25 quarterLengths of content ended up as a single "measure",
    # which is what made whole systems render as one giant overwide
    # measure ("always just 1 measure per line") regardless of layout/zoom
    # settings. Assigning each note to its measure ourselves (by
    # offset_ql // 4.0) is slower to write but can't misfire that way.
    #
    # Bucket notes by measure *before* creating/appending any Measure
    # objects (see below for why).
    notes_by_measure: list[list[tuple[float, note.NotRest]]] = [[] for _ in range(num_measures)]
    for i, evt in enumerate(groups):
        offset_ql = offsets_ql[i]
        raw_duration_ql = (evt[0].end_sec - evt[0].start_sec) / quarter_length_sec
        duration_ql = max(_GRID_QL, _quantize(raw_duration_ql))

        # Trim so this note never reaches the next note's start.
        if i + 1 < len(offsets_ql):
            duration_ql = min(duration_ql, offsets_ql[i + 1] - offset_ql)

        # Cap at the end of the current measure (4/4 -> 4 quarter lengths).
        # A longer duration would otherwise land partly in the next
        # measure, which we can't represent (each note lives in exactly
        # one Measure object here) — and would anyway hit the same
        # cross-system tie crash risk described above.
        room_in_measure = 4.0 - (offset_ql % 4.0)
        duration_ql = min(duration_ql, room_in_measure if room_in_measure > 0 else 4.0)
        duration_ql = max(_GRID_QL, duration_ql)

        m_idx = min(int(offset_ql // 4.0), num_measures - 1)
        local_offset_ql = offset_ql - m_idx * 4.0

        if is_drum:
            # Unpitched notes (see _DRUM_MIDI_TO_DISPLAY) can't be combined
            # into a Chord (that expects Pitches) — insert simultaneous
            # hits (e.g. kick+hihat) as separate elements at the same
            # offset instead of merging them into one object.
            for e in evt:
                n = _to_note(e, is_drum=True)
                n.duration.quarterLength = duration_ql
                notes_by_measure[m_idx].append((local_offset_ql, n))
        else:
            if len(evt) == 1:
                n = _to_note(evt[0], is_drum=False)
            else:
                n = chord.Chord([_to_note(e, is_drum=False) for e in evt])
            n.duration.quarterLength = duration_ql
            notes_by_measure[m_idx].append((local_offset_ql, n))
            if stem.instrument == InstrumentTrackType.VOCALS:
                vocal_note_offsets.append((offset_ql, n))

    if lyric_words and vocal_note_offsets:
        _attach_lyrics(vocal_note_offsets, lyric_words, quarter_length_sec)

    # Fully build each Measure (metadata, notes, gap-filling rests) *before*
    # appending it to the Part. `Stream.append()` positions the appended
    # element at the Part's *current* highestTime, evaluated once, at call
    # time — it does not retroactively track later mutations to an already
    # -appended Measure. Appending empty measure shells first and filling
    # them in afterwards (the previous structure of this function) meant
    # every append() saw the Part's highestTime still at ~0 (the previous
    # measures being empty at that point), so *every* measure landed at
    # offset 0 instead of 0, 4, 8, 12, ... — inaudible in the notation
    # views (OSMD/MuseScore both re-lay-out from the Measure structure
    # itself) but fatal for the MIDI export, which collapsed the entire
    # piece into ~2 seconds of simultaneous notes.
    for m_idx in range(num_measures):
        m = stream.Measure(number=m_idx + 1)
        if m_idx == 0:
            inst_cls = _INSTRUMENT_M21.get(stem.instrument, m21instrument.Piano)
            inst = inst_cls()
            # Override music21's built-in (often cryptic, e.g. "Wd Bl"/
            # "Elec b") name/abbreviation so the score matches the UI label.
            inst.instrumentName = stem.label
            inst.instrumentAbbreviation = stem.label
            m.insert(0, inst)
            if show_tempo:
                # Only the top part gets a MetronomeMark — one per part in a
                # combined Score renders as N stacked "♩ = x" markings.
                italian_term = italian_term_for_bpm(bpm)
                m.insert(0, m21tempo.MetronomeMark(text=italian_term, number=bpm))
            m.insert(0, meter.TimeSignature("4/4"))
            m.insert(0, key_obj)
            m.insert(0, _clef_for(stem.instrument))
        if chord_labels_per_measure and m_idx < len(chord_labels_per_measure):
            label = chord_labels_per_measure[m_idx]
            if label:
                m.insert(0, harmony.ChordSymbol(label))

        for local_offset_ql, n in notes_by_measure[m_idx]:
            m.insert(local_offset_ql, n)
        _fill_measure_gaps(m)

        part.append(m)

    return part


def _attach_lyrics(
    vocal_note_offsets: list[tuple[float, note.NotRest]],
    lyric_words: list[LyricWord],
    quarter_length_sec: float,
) -> None:
    """Attach each transcribed word to its nearest Vocals note by timing.

    Word timestamps come from Whisper on the isolated vocals stem; note
    timing comes from Basic Pitch's pitch detection on the same stem — two
    independent, imperfect estimates, so this is nearest-offset matching,
    not exact alignment. Multiple close words landing on the same note
    (e.g. a run of short words under one sustained note) are concatenated
    rather than overwriting each other, so no transcribed word is
    silently dropped.
    """
    note_offsets_ql = [o for o, _ in vocal_note_offsets]
    for word in lyric_words:
        word_offset_ql = word.start_sec / quarter_length_sec
        idx = min(range(len(note_offsets_ql)), key=lambda i: abs(note_offsets_ql[i] - word_offset_ql))
        target = vocal_note_offsets[idx][1]
        existing = target.lyric
        target.lyric = f"{existing} {word.text}" if existing else word.text


def _fill_measure_gaps(m: stream.Measure) -> None:
    """Insert Rests so `m`'s contents add up to exactly 4.0 quarterLengths.

    music21's own `Stream.makeRests(fillGaps=True, timeRangeFromBarDuration=
    True)` was tried here first, but on real transcriptions it sometimes
    inserted a rest starting *at* offset 4.0 (i.e. past the end of the
    measure) rather than filling the gap before it, silently pushing that
    one measure's total to 4.25/4.5 — which is exactly the kind of
    misaligned-barline bug this whole explicit-Measure-building approach
    was meant to eliminate. Filling gaps by hand removes any ambiguity.
    """
    existing = sorted(m.notesAndRests, key=lambda n: n.offset)
    cursor = 0.0
    for n in existing:
        if n.offset > cursor:
            m.insert(cursor, note.Rest(quarterLength=n.offset - cursor))
        cursor = max(cursor, n.offset + n.duration.quarterLength)
    if cursor < 4.0:
        m.insert(cursor, note.Rest(quarterLength=4.0 - cursor))


# Raw onset/offset timestamps (in seconds, converted to quarter-note lengths)
# are continuous floats and cannot be expressed as MusicXML note durations
# as-is (music21 raises "Cannot convert inexpressible durations"). Snap both
# note offsets and durations to a 16th-note grid before inserting into the
# Stream. A finer/adaptive grid (e.g. triplet-aware) is a later refinement.
_GRID_QL = 0.25


def _quantize(quarter_length: float, grid: float = _GRID_QL) -> float:
    return round(quarter_length / grid) * grid


def _to_note(evt: NoteEvent, is_drum: bool) -> note.NotRest:
    if is_drum:
        step, octave, notehead = _DRUM_MIDI_TO_DISPLAY.get(
            evt.gm_drum_key or evt.pitch_midi, ("C", 5, "normal")
        )
        n = note.Unpitched()
        n.displayStep = step
        n.displayOctave = octave
        n.notehead = notehead
    else:
        n = note.Note()
        n.pitch.midi = evt.pitch_midi
    n.volume.velocity = evt.velocity
    return n


def _merge_simultaneous(
    notes: list[NoteEvent], tolerance_sec: float = 0.03
) -> list[list[NoteEvent]]:
    """Group notes starting within `tolerance_sec` of each other into chords."""
    if not notes:
        return []
    ordered = sorted(notes, key=lambda e: e.start_sec)
    groups: list[list[NoteEvent]] = [[ordered[0]]]
    for evt in ordered[1:]:
        if evt.start_sec - groups[-1][0].start_sec <= tolerance_sec:
            groups[-1].append(evt)
        else:
            groups.append([evt])
    return groups


def _merge_by_quantized_offset(
    groups: list[list[NoteEvent]], offsets_ql: list[float]
) -> tuple[list[list[NoteEvent]], list[float]]:
    """Merge consecutive groups that landed on the identical offset after
    16th-note quantization — even though `_merge_simultaneous` (working on
    un-quantized start_sec) judged them too far apart to be one chord.
    Without this, two such groups both get offset_ql=X, and the "trim
    duration to the next group's start" logic computes a 0-length gap
    between them, which the `_GRID_QL` floor then re-inflates into two
    *overlapping* 0.25-quarterLength notes at the same offset — silently
    pushing that measure's total past 4.0 quarterLengths (the root cause of
    a whole system rendering as a single overwide "measure")."""
    if not groups:
        return groups, offsets_ql
    merged_groups = [groups[0]]
    merged_offsets = [offsets_ql[0]]
    for g, off in zip(groups[1:], offsets_ql[1:]):
        if off == merged_offsets[-1]:
            merged_groups[-1] = merged_groups[-1] + g
        else:
            merged_groups.append(g)
            merged_offsets.append(off)
    return merged_groups, merged_offsets


def build_score(
    stems: list[StemTranscription],
    bpm: int,
    key_obj: m21key.Key,
    title: str,
    chord_labels_per_measure: list[str | None] | None = None,
    lyric_words: list[LyricWord] | None = None,
) -> stream.Score:
    score = stream.Score()
    md = metadata.Metadata()
    md.title = title
    # Otherwise music21 defaults the composer field to "Music21" on export.
    md.composer = "Music Transcriber (automatische Transkription)"
    score.insert(0, md)

    quarter_length_sec = 60.0 / bpm

    # Every part must agree on the same number of measures (shorter stems
    # get trailing rest measures) — otherwise barlines across staves in a
    # system wouldn't line up.
    def _stem_end_ql(stem: StemTranscription) -> float:
        if not stem.notes:
            return 0.0
        return max(n.end_sec for n in stem.notes) / quarter_length_sec

    num_measures = max(
        (int(_stem_end_ql(stem) // 4.0) + 1 for stem in stems if stem.notes),
        default=1,
    )

    is_first = True
    built_parts: dict[InstrumentTrackType, stream.Part] = {}
    for stem in stems:
        if not stem.notes:
            continue
        part = build_part(
            stem,
            bpm,
            quarter_length_sec,
            key_obj,
            num_measures,
            show_tempo=is_first,
            # Chord symbols only make sense once, above the top staff.
            chord_labels_per_measure=chord_labels_per_measure if is_first else None,
            lyric_words=lyric_words,
        )
        score.insert(0, part)
        built_parts[stem.instrument] = part
        is_first = False

    # Piano + Bass are conventionally read as one grand staff (right/left
    # hand) — brace them together like a real piano part, rather than
    # rendering as two unrelated single-staff instruments.
    if InstrumentTrackType.PIANO in built_parts and InstrumentTrackType.BASS in built_parts:
        score.insert(
            0,
            layout.StaffGroup(
                [built_parts[InstrumentTrackType.PIANO], built_parts[InstrumentTrackType.BASS]],
                symbol="brace",
                barTogether=True,
            ),
        )

    set_system_breaks(score, DEFAULT_MEASURES_PER_SYSTEM)
    return score


def set_system_breaks(score: stream.Score, measures_per_system: int) -> None:
    """(Re-)write the system-break layout markers across every part of an
    already-built Score. Used both for the pipeline's default density and
    to let the frontend/PDF-export re-render an existing job at a
    different measures-per-line density (the "Takte pro Zeile" slider)
    without re-running transcription."""
    for part in score.parts:
        measures = list(part.getElementsByClass(stream.Measure))
        for idx, m in enumerate(measures):
            for sl in list(m.getElementsByClass(layout.SystemLayout)):
                m.remove(sl)
            if idx % measures_per_system == 0:
                m.insert(0, layout.SystemLayout(isNew=True))


def strip_system_breaks(score: stream.Score) -> None:
    """Remove all forced system-break markers, letting the renderer's own
    casting-off decide line breaks purely from page/container width and
    each measure's actual content width.

    Used for the PDF export: forcing a fixed measures-per-line count
    (set_system_breaks) fights MuseScore's own layout once measure width
    varies a lot across the piece (dense 16th-note passages vs. sparse
    ones) — MuseScore still inserts *additional* breaks where a forced
    system would overflow the page, which can strand a single sparse
    measure alone in its system and then justify/stretch it across the
    full page width. Letting MuseScore fully own the casting-off avoids
    that; scale_mm (export.py) remains the lever for roughly how dense a
    line is. OSMD's web view does not have this problem — it doesn't
    stretch under-full systems to fill the line — so it keeps its fixed
    breaks via set_system_breaks()."""
    for part in score.parts:
        for m in part.getElementsByClass(stream.Measure):
            for sl in list(m.getElementsByClass(layout.SystemLayout)):
                m.remove(sl)


# Standard concert-pitch -> written-pitch offsets for common transposing
# instruments, in semitones (positive = the written note is higher than
# the sounding/concert pitch — the usual convention notation software
# uses, e.g. MuseScore's own "Transponierende Instrumente"). Mirrored on
# the frontend (TRANSPOSING_INSTRUMENT_PRESETS in api.ts) for its preset
# dropdown; kept here too as the authoritative reference/documentation.
# The underlying operation is the same plain semitone shift as the manual
# stepper — this just fills in a musically-standard value (and a proper
# instrument name, see rename_parts()) instead of the user having to
# know/count it themselves.
TRANSPOSING_INSTRUMENT_PRESETS: list[tuple[str, int]] = [
    ("Konzerttonhöhe (C)", 0),
    ("Trompete", 2),
    ("Klarinette", 2),
    ("Tenor-Sax", 2),
    ("Sopran-Sax", 2),
    ("Alt-Sax", 9),
    ("Bariton-Sax", 9),
    ("Horn", 7),
    ("A-Klarinette", 3),
]


def transpose_score(score: stream.Score, transpositions: dict[str, int]) -> None:
    """Shift selected parts by a number of semitones, in place.
    `transpositions` maps Part.partName (the stem label set in
    build_part(), e.g. "Vocals") to a semitone offset; parts absent from
    the dict, or mapped to 0, are left untouched. music21's Part.transpose
    walks the whole part, so pitches, the key signature *and* any
    ChordSymbol harmony all move together and stay musically consistent."""
    for part in score.parts:
        semitones = transpositions.get(part.partName, 0)
        if semitones:
            part.transpose(semitones, inPlace=True)


def rename_parts(score: stream.Score, renames: dict[str, str]) -> None:
    """Rename selected parts — Part.partName/partAbbreviation and their
    Instrument object's instrumentName/instrumentAbbreviation — so a track
    transposed "as" e.g. Alto-Sax is also *labelled* Alto-Sax in the
    score, not still "Vocals". Keyed by the part's current partName, same
    as transpose_score(), and meant to be called together with it (the
    frontend's instrument-preset dropdown sets both together)."""
    for part in score.parts:
        new_name = renames.get(part.partName)
        if not new_name:
            continue
        part.partName = new_name
        part.partAbbreviation = new_name
        for inst in part.recurse().getElementsByClass("Instrument"):
            inst.instrumentName = new_name
            inst.instrumentAbbreviation = new_name
