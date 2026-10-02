"""Rebuild a job's notation at a corrected BPM.

For the common case this exists to fix — a tempo-octave error, where the
detected BPM is an exact multiple/fraction of the real one (e.g. 132
detected, 66 actual) — re-running the full pipeline (Demucs separation +
Basic Pitch transcription) would be wasteful: none of that depends on
tempo, only how the *already correct* absolute-time note data gets
quantized into beats/measures. So instead we recover that absolute-time
data from the already-built score (inverting the old BPM's quarter-length
scale) and feed it back through the normal notation-building path at the
new BPM — the same function the initial pipeline run uses, so measure/
beam structure comes out exactly as correct as a fresh transcription
would, unlike naively rescaling the already-built Measures' offsets
in place (tried first; `Stream.augmentOrDiminish` scales note timing but
not the Measure containers around it, leaving every measure over/under-
full — see README for the Runde this was built in).
"""

from __future__ import annotations

from pathlib import Path

import librosa
from music21 import converter, note, stream

from app.core.job_store import job_store
from app.models.job import InstrumentTrackType, Job, TrackResult
from app.pipeline import chords, export, notation
from app.pipeline.key_detection import detect_key
from app.pipeline.lyrics import transcribe_lyrics
from app.pipeline.provider import NoteEvent, StemTranscription
from app.pipeline.tempo import italian_term_for_bpm
from app.pipeline.transcription.drums import GM_CLOSED_HIHAT, GM_KICK, GM_SNARE

# Inverse of notation._DRUM_MIDI_TO_DISPLAY. Lossy for hi-hat (open vs.
# closed collapse to the same notehead, see notation.py) — acceptable,
# since the written notation is identical either way and this only
# affects which exact GM percussion key the resulting MIDI/synth-audio
# hit uses, not anything visible in the score.
_DISPLAY_TO_DRUM_MIDI: dict[tuple[str, int, str], int] = {
    ("F", 4, "normal"): GM_KICK,
    ("C", 5, "normal"): GM_SNARE,
    ("G", 5, "x"): GM_CLOSED_HIHAT,
}


def _extract_note_events(part: stream.Part, old_bpm: int, is_drum: bool) -> list[NoteEvent]:
    sec_per_ql = 60.0 / old_bpm
    events: list[NoteEvent] = []
    for n in part.recurse().notesAndRests:
        if isinstance(n, note.Rest):
            continue
        start_sec = n.getOffsetInHierarchy(part) * sec_per_ql
        end_sec = start_sec + n.duration.quarterLength * sec_per_ql
        velocity = n.volume.velocity or 90
        if is_drum and isinstance(n, note.Unpitched):
            key = (n.displayStep, n.displayOctave, n.notehead)
            gm_key = _DISPLAY_TO_DRUM_MIDI.get(key, GM_SNARE)
            events.append(
                NoteEvent(
                    pitch_midi=gm_key,
                    start_sec=start_sec,
                    end_sec=end_sec,
                    velocity=velocity,
                    gm_drum_key=gm_key,
                )
            )
        elif isinstance(n, note.Note):
            events.append(
                NoteEvent(
                    pitch_midi=n.pitch.midi,
                    start_sec=start_sec,
                    end_sec=end_sec,
                    velocity=velocity,
                )
            )
    events.sort(key=lambda e: e.start_sec)
    return events


def rebuild_with_bpm(job: Job, new_bpm: int) -> None:
    """Re-notate `job` at `new_bpm`, overwriting its combined/per-track
    MusicXML+MIDI in place and updating job.bpm/tempo_italian_term. Any
    cached derived variants (measures-per-system, transpose, PDF, synth
    audio — see jobs.py) are deleted since they're stale once the
    underlying notation has changed; they'll be regenerated on next
    request same as after a fresh pipeline run."""
    if not job.combined_musicxml_path:
        raise ValueError("Job has no MusicXML to rebuild from")
    old_bpm = job.bpm or 120
    old_score = converter.parse(job.combined_musicxml_path)
    work_dir = Path(job.combined_musicxml_path).parent
    title = Path(job.original_filename).stem or "Transkription"

    label_to_track = {t.label: t for t in job.tracks}
    stems: list[StemTranscription] = []
    for part in old_score.parts:
        track = label_to_track.get(part.partName)
        if track is None:
            continue
        is_drum = track.instrument == InstrumentTrackType.DRUMS
        events = _extract_note_events(part, old_bpm, is_drum)
        stems.append(
            StemTranscription(
                instrument=track.instrument,
                label=track.label,
                stem_audio_path=Path(track.stem_audio_path or ""),
                notes=events,
            )
        )

    key_obj = detect_key(stems)

    # Chord labels are tied to the measure grid, which shifts with tempo
    # (half the BPM -> half as many measures for the same audio) — so the
    # old score's per-measure labels don't line up with the new grid.
    # Re-detecting fresh from the source audio (cheap: chroma + template
    # matching, no Demucs/Basic Pitch involved) is both simpler and more
    # correct than trying to reindex the old ones.
    y, sr = librosa.load(str(job.source_audio_path), sr=None, mono=True)
    measure_sec = (60.0 / new_bpm) * 4
    num_measures = max(1, int(len(y) / sr / measure_sec) + 1)
    chord_labels = chords.detect_chords_per_measure(y, sr, new_bpm, num_measures)

    lyric_words = []
    vocals_track = next(
        (t for t in job.tracks if t.instrument == InstrumentTrackType.VOCALS), None
    )
    if vocals_track and vocals_track.stem_audio_path:
        try:
            lyric_words = transcribe_lyrics(Path(vocals_track.stem_audio_path))
        except Exception:
            lyric_words = []

    score = notation.build_score(
        stems,
        new_bpm,
        key_obj,
        title,
        chord_labels_per_measure=chord_labels,
        lyric_words=lyric_words,
    )

    combined_musicxml = work_dir / "combined.musicxml"
    combined_midi = work_dir / "combined.mid"
    export.export_musicxml(score, combined_musicxml)
    export.export_midi(score, combined_midi)

    # Stale cached variants — measures-per-system/transpose/for_musescore
    # MusicXML, rendered PDFs, transposed MIDI, synth WAV — all derived
    # from the combined files just overwritten above.
    for pattern in ("combined_*.musicxml", "combined_*.mid", "*.synth.wav", "score_*.pdf"):
        for stale in work_dir.glob(pattern):
            stale.unlink(missing_ok=True)

    new_tracks: list[TrackResult] = []
    for stem in stems:
        if not stem.notes:
            continue
        old_track = label_to_track[stem.label]
        track_dir = work_dir / stem.instrument.value
        track_dir.mkdir(parents=True, exist_ok=True)
        single_part_score = notation.build_score(
            [stem], new_bpm, key_obj, f"{title} – {stem.label}", lyric_words=lyric_words
        )
        xml_path = track_dir / "part.musicxml"
        midi_path = track_dir / "part.mid"
        export.export_musicxml(single_part_score, xml_path)
        export.export_midi(single_part_score, midi_path)
        new_tracks.append(
            TrackResult(
                track_id=old_track.track_id,
                instrument=stem.instrument,
                label=stem.label,
                stem_audio_path=old_track.stem_audio_path,
                musicxml_path=str(xml_path),
                midi_path=str(midi_path),
            )
        )

    job.tracks = new_tracks
    job.bpm = new_bpm
    job.tempo_italian_term = italian_term_for_bpm(new_bpm)
    job.combined_musicxml_path = str(combined_musicxml)
    job.combined_midi_path = str(combined_midi)
    job_store.save(job)
