"""End-to-end pipeline: audio in -> separated, transcribed, notated, exported."""

from __future__ import annotations

from pathlib import Path

import librosa

from app.core.config import settings
from app.core.job_store import job_store
from app.models.job import InstrumentTrackType, Job, JobStatus, TrackResult
from app.pipeline import chords, export, notation
from app.pipeline.key_detection import detect_key
from app.pipeline.lyrics import transcribe_lyrics
from app.pipeline.provider import get_provider
from app.pipeline.tempo import GENRE_START_BPM, detect_tempo


def run_pipeline(job_id: str, genre_hint: str | None = None) -> None:
    job = job_store.get(job_id)
    if job is None:
        return

    try:
        work_dir = settings.jobs_dir / job.id
        work_dir.mkdir(parents=True, exist_ok=True)
        audio_path = Path(job.source_audio_path)

        job.status = JobStatus.SEPARATING
        job.progress = 0.1
        job_store.save(job)

        y, sr = librosa.load(str(audio_path), sr=None, mono=True)
        start_bpm = GENRE_START_BPM.get(genre_hint or "auto", GENRE_START_BPM["auto"])
        tempo_info = detect_tempo(y, sr, start_bpm=start_bpm)
        job.bpm = tempo_info.bpm
        job.tempo_italian_term = tempo_info.italian_term
        job.time_signature = "4/4"  # TODO: real time-signature detection
        job.progress = 0.2
        job_store.save(job)

        provider = get_provider()

        job.status = JobStatus.TRANSCRIBING
        job_store.save(job)
        stem_transcriptions = provider.separate_and_transcribe(audio_path, work_dir)
        job.progress = 0.6
        job_store.save(job)

        job.status = JobStatus.NOTATING
        job_store.save(job)

        bpm = job.bpm or 120
        key_obj = detect_key(stem_transcriptions)
        title = Path(job.original_filename).stem or "Transkription"

        measure_sec = (60.0 / bpm) * 4
        num_measures = max(1, int(len(y) / sr / measure_sec) + 1)
        chord_labels = chords.detect_chords_per_measure(y, sr, bpm, num_measures)

        lyric_words = []
        vocals_stem = next(
            (s for s in stem_transcriptions if s.instrument == InstrumentTrackType.VOCALS and s.notes),
            None,
        )
        if vocals_stem is not None:
            try:
                lyric_words = transcribe_lyrics(vocals_stem.stem_audio_path)
            except Exception:
                # Lyrics are best-effort — a transcription failure here
                # shouldn't fail the whole job.
                lyric_words = []

        score = notation.build_score(
            stem_transcriptions,
            bpm,
            key_obj,
            title,
            chord_labels_per_measure=chord_labels,
            lyric_words=lyric_words,
        )
        job.progress = 0.75
        job_store.save(job)

        job.status = JobStatus.EXPORTING
        job_store.save(job)

        combined_musicxml = work_dir / "combined.musicxml"
        combined_midi = work_dir / "combined.mid"
        export.export_musicxml(score, combined_musicxml)
        export.export_midi(score, combined_midi)
        job.combined_musicxml_path = str(combined_musicxml)
        job.combined_midi_path = str(combined_midi)

        tracks: list[TrackResult] = []
        for stem in stem_transcriptions:
            if not stem.notes:
                continue
            track_dir = work_dir / stem.instrument.value
            track_dir.mkdir(parents=True, exist_ok=True)

            single_part_score = notation.build_score(
                [stem], bpm, key_obj, f"{title} – {stem.label}", lyric_words=lyric_words
            )
            xml_path = track_dir / "part.musicxml"
            midi_path = track_dir / "part.mid"
            export.export_musicxml(single_part_score, xml_path)
            export.export_midi(single_part_score, midi_path)

            tracks.append(
                TrackResult(
                    track_id=stem.instrument.value,
                    instrument=stem.instrument,
                    label=stem.label,
                    stem_audio_path=str(stem.stem_audio_path),
                    musicxml_path=str(xml_path),
                    midi_path=str(midi_path),
                )
            )
        job.tracks = tracks
        job.progress = 1.0
        job.status = JobStatus.DONE
        job_store.save(job)

    except Exception as exc:  # noqa: BLE001 — surface any pipeline failure on the job
        job.status = JobStatus.FAILED
        job.error = str(exc)
        job_store.save(job)
        raise
