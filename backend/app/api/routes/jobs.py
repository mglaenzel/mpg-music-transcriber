from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from app.core.config import settings
from app.core.job_store import job_store
from app.models.job import Job, JobStatus
from app.pipeline import export as export_module
from app.pipeline import notation
from app.pipeline.runner import run_pipeline

router = APIRouter(prefix="/api/jobs", tags=["jobs"])

_ALLOWED_SUFFIXES = {".mp3", ".wav", ".mp4", ".m4a", ".flac", ".mid", ".midi"}


def _parse_transpose_param(
    transpose: str | None, tracks: list
) -> tuple[dict[str, int], dict[str, str]]:
    """`transpose` is `track_id:semitones[:name],...`, e.g.
    `?transpose=vocals:9:Alt-Sax,guitar:0:Tenor-Sax`. Resolves each
    track_id to its label (Part.partName, same lookup the `instruments`
    PDF filter uses) since that's what notation.transpose_score()/
    rename_parts() match parts by. The optional third field renames the
    part (e.g. so a track transposed "as" Alto-Sax is labelled that in
    the score, not still "Vocals" — see rename_parts()); a rename works
    independently of the semitone shift, including with `0` semitones
    (a relabel-only use, e.g. correcting a misidentified instrument like
    Demucs's "guitar" stem actually being a bled-through saxophone, with
    no pitch change intended). Returns (transpositions, renames), both
    keyed by label. Unknown track_ids and malformed pairs are silently
    dropped — nothing to apply for those anyway."""
    if not transpose:
        return {}, {}
    label_by_id = {t.track_id: t.label for t in tracks}
    transpositions: dict[str, int] = {}
    renames: dict[str, str] = {}
    for pair in transpose.split(","):
        track_id, _, rest = pair.partition(":")
        label = label_by_id.get(track_id)
        if not label:
            continue
        semis, _, name = rest.partition(":")
        try:
            semitones = int(semis)
        except ValueError:
            continue
        if semitones:
            transpositions[label] = semitones
        if name:
            renames[label] = name
    return transpositions, renames


def _transpose_suffix(transpose: str | None) -> str:
    """Short, filename-safe signature for a raw `transpose` query value,
    used to key cached file variants so different transpositions (or none)
    don't collide on disk. Hash-based rather than reconstructed from the
    parsed dicts — stays correct automatically if the param format grows
    further, and side-steps needing to sanitize instrument names for use
    in a filename."""
    if not transpose:
        return ""
    import hashlib

    return "_tp" + hashlib.sha1(transpose.encode()).hexdigest()[:10]


def _score_metadata(title: str):
    # converter.parse() doesn't reliably repopulate Score.metadata from the
    # <work-title>/<movement-title> tags even though they're in the file —
    # so a re-parsed score.metadata.title comes back None and MuseScore
    # falls back to its own default "Music21 Fragment" title. Build it
    # fresh instead, matching notation.build_score()'s original values.
    from music21 import metadata as m21_metadata

    md = m21_metadata.Metadata()
    md.title = title
    md.composer = "Music Transcriber (automatische Transkription)"
    return md


@router.post("")
async def create_job(
    file: UploadFile, background_tasks: BackgroundTasks, genre: str | None = Form(None)
) -> dict:
    """`genre` (one of notation... see tempo.GENRE_START_BPM — "auto",
    "ballad", "pop_rock", "dance", "fast") seeds tempo detection toward
    that genre's typical BPM range. Not cosmetic: librosa's beat tracker
    has a well-documented "octave error" failure mode — reporting an
    exact double/half of the true tempo — and for genuinely ambiguous
    audio (sparse, rubato jazz ballads especially) the detector's own
    confidence in the right vs. wrong answer can be under 1% apart, too
    close for any purely acoustic heuristic to resolve reliably. See
    README for the measurements behind this."""
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in _ALLOWED_SUFFIXES:
        raise HTTPException(400, f"Unsupported file type: {suffix or 'unknown'}")

    job = Job(original_filename=file.filename or "upload")
    job_store.create(job)

    dest = settings.uploads_dir / f"{job.id}{suffix}"
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    job.source_audio_path = str(dest)
    job_store.save(job)

    background_tasks.add_task(run_pipeline, job.id, genre)
    return {"job_id": job.id, "status": job.status}


@router.get("")
async def list_jobs() -> list[dict]:
    """Library of previously analyzed songs — lets the frontend resume a
    job after a page reload instead of re-uploading/re-analyzing."""
    return [
        {
            "job_id": job.id,
            "status": job.status,
            "original_filename": job.original_filename,
            "created_at": job.created_at.isoformat(),
            "bpm": job.bpm,
            "tempo_italian_term": job.tempo_italian_term,
        }
        for job in job_store.list_all()
    ]


@router.get("/{job_id}")
async def get_job(job_id: str) -> dict:
    job = job_store.get(job_id)
    if job is None:
        raise HTTPException(404, "Job not found")

    return {
        "job_id": job.id,
        "status": job.status,
        "progress": job.progress,
        "error": job.error,
        "bpm": job.bpm,
        "tempo_italian_term": job.tempo_italian_term,
        "time_signature": job.time_signature,
        "tracks": [
            {
                "track_id": t.track_id,
                "instrument": t.instrument,
                "label": t.label,
            }
            for t in job.tracks
        ],
    }


def _require_done_job(job_id: str) -> Job:
    job = job_store.get(job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    if job.status != JobStatus.DONE:
        raise HTTPException(409, f"Job is not finished yet (status={job.status})")
    return job


@router.post("/{job_id}/bpm")
async def correct_bpm(job_id: str, bpm: int = Query(..., ge=20, le=400)) -> dict:
    """Manually correct a mis-detected tempo — most commonly an exact
    octave error (half/double the real BPM; see tempo.GENRE_START_BPM's
    docstring for why automatic detection can't always get this right).
    Unlike the transpose/measures-per-system query params elsewhere in
    this file, this is NOT a transient per-request view: BPM determines
    how absolute-time note data gets quantized into beats/measures, so
    this re-notates and overwrites the job's stored MusicXML/MIDI (see
    retempo.rebuild_with_bpm) — the fix sticks across reloads, same as if
    the pipeline had detected it correctly the first time."""
    job = _require_done_job(job_id)

    from app.pipeline.retempo import rebuild_with_bpm

    try:
        rebuild_with_bpm(job, bpm)
    except Exception as exc:
        raise HTTPException(500, f"Tempo-Korrektur fehlgeschlagen: {exc}") from exc
    return {"bpm": job.bpm, "tempo_italian_term": job.tempo_italian_term}


@router.get("/{job_id}/musicxml")
async def download_musicxml(
    job_id: str,
    measures_per_system: int | None = None,
    for_musescore: bool = False,
    transpose: str | None = None,
) -> FileResponse:
    """`measures_per_system` (the "Takte pro Zeile" slider in the frontend)
    re-renders the stored MusicXML with different system-break markers on
    the fly. Omit for the pipeline's default density.

    `for_musescore=true` is for the "MusicXML herunterladen" link, whose
    file is meant to be opened directly in the MuseScore desktop app —
    not fed to OSMD. It gets the same treatment as our own PDF export
    (strip_system_breaks + scale_mm, see export_pdf): MuseScore's desktop
    app justifies/stretches an under-full system exactly like its CLI
    does, so the forced, fixed-N-per-line breaks that work fine for OSMD's
    web view (which doesn't stretch) show the same "1 Takt pro Zeile,
    riesig auseinandergezogen" collapse there too.

    `transpose` shifts selected tracks by semitones, see
    _parse_transpose_param() for the format."""
    job = _require_done_job(job_id)
    if not job.combined_musicxml_path:
        raise HTTPException(404, "No MusicXML for this job")

    transpositions, renames = _parse_transpose_param(transpose, job.tracks)

    if not for_musescore and not transpositions and not renames and (
        measures_per_system is None or measures_per_system == notation.DEFAULT_MEASURES_PER_SYSTEM
    ):
        return FileResponse(
            job.combined_musicxml_path, filename=f"{job.original_filename}.musicxml"
        )

    from music21 import converter

    work_dir = Path(job.combined_musicxml_path).parent
    title = Path(job.original_filename).stem or "Transkription"
    tp_suffix = _transpose_suffix(transpose)

    if for_musescore:
        variant_path = work_dir / f"combined_for_musescore{tp_suffix}.musicxml"
        if not variant_path.exists():
            score = converter.parse(job.combined_musicxml_path)
            score.metadata = _score_metadata(title)
            notation.transpose_score(score, transpositions)
            notation.rename_parts(score, renames)
            notation.strip_system_breaks(score)
            export_module.export_musicxml(score, variant_path)
            export_module.apply_scale_mm(variant_path)
        return FileResponse(variant_path, filename=f"{job.original_filename}.musicxml")

    mps = measures_per_system or notation.DEFAULT_MEASURES_PER_SYSTEM
    variant_path = work_dir / f"combined_mps{mps}{tp_suffix}.musicxml"
    if not variant_path.exists():
        score = converter.parse(job.combined_musicxml_path)
        score.metadata = _score_metadata(title)
        notation.transpose_score(score, transpositions)
        notation.rename_parts(score, renames)
        notation.set_system_breaks(score, mps)
        export_module.export_musicxml(score, variant_path)
    return FileResponse(variant_path, filename=f"{job.original_filename}.musicxml")


def _get_transposed_midi_path(job: Job, transpose: str | None) -> Path:
    """Returns the stored combined MIDI as-is when nothing is transposed,
    otherwise builds (and caches on disk) a transposed variant. Shared by
    /midi and /synth-audio, which both need "the MIDI for this
    transposition" — the synth WAV is rendered from whatever this
    returns, so a transposed track is audible in "Synth. Wiedergabe" too,
    not just in the notation."""
    if not job.combined_midi_path:
        raise HTTPException(404, "No MIDI for this job")

    transpositions, renames = _parse_transpose_param(transpose, job.tracks)
    if not transpositions:
        return Path(job.combined_midi_path)

    from music21 import converter

    work_dir = Path(job.combined_midi_path).parent
    tp_suffix = _transpose_suffix(transpose)
    variant_path = work_dir / f"combined{tp_suffix}.mid"
    if not variant_path.exists():
        score = converter.parse(job.combined_musicxml_path)
        notation.transpose_score(score, transpositions)
        notation.rename_parts(score, renames)
        export_module.export_midi(score, variant_path)
    return variant_path


@router.get("/{job_id}/midi")
async def download_midi(job_id: str, transpose: str | None = None) -> FileResponse:
    job = _require_done_job(job_id)
    midi_path = _get_transposed_midi_path(job, transpose)
    return FileResponse(midi_path, filename=f"{job.original_filename}.mid")


@router.get("/{job_id}/synth-audio")
async def synth_audio(job_id: str, transpose: str | None = None) -> FileResponse:
    """A playable rendering of the combined MIDI (see pipeline/synth.py) —
    browsers can't play a raw .mid file directly via <audio>."""
    job = _require_done_job(job_id)
    midi_path = _get_transposed_midi_path(job, transpose)

    from app.pipeline.synth import render_midi_to_wav

    wav_path = midi_path.with_suffix(".synth.wav")
    if not wav_path.exists():
        render_midi_to_wav(midi_path, wav_path)
    return FileResponse(wav_path, filename=f"{job.original_filename}.synth.wav")


@router.get("/{job_id}/pdf")
async def download_pdf(
    job_id: str,
    instruments: str | None = None,
    measures_per_system: int | None = None,
    transpose: str | None = None,
) -> FileResponse:
    """`instruments` is a comma-separated list of track_ids (InstrumentTrackType
    values), e.g. `?instruments=piano,bass`. Omit for the full score.
    `measures_per_system` mirrors the web view's "Takte pro Zeile" slider —
    the note scaling is adjusted proportionally so the chosen density
    still fits the page, the same idea as OSMD's zoom on the frontend.
    `transpose` shifts selected tracks by semitones, see
    _parse_transpose_param() for the format."""
    job = _require_done_job(job_id)

    import subprocess

    from music21 import converter

    score = converter.parse(job.combined_musicxml_path)
    title = Path(job.original_filename).stem or "Transkription"
    score.metadata = _score_metadata(title)

    transpositions, renames = _parse_transpose_param(transpose, job.tracks)
    notation.transpose_score(score, transpositions)

    mps = measures_per_system or notation.DEFAULT_MEASURES_PER_SYSTEM
    # No forced system breaks here (unlike the web view / OSMD) — MuseScore
    # justifies/stretches an under-full system to fill the page width,
    # which turns a forced break that collides with a dense measure into a
    # single sparse measure stretched across the whole line. Letting
    # MuseScore's own casting-off decide line breaks avoids that; scale_mm
    # below is what actually controls the target density instead.
    notation.strip_system_breaks(score)
    scale_mm = str(
        round(
            float(export_module.DEFAULT_PDF_SCALE_MILLIMETERS)
            * notation.DEFAULT_MEASURES_PER_SYSTEM
            / mps,
            2,
        )
    )

    part_names = None
    if instruments:
        wanted_ids = set(instruments.split(","))
        part_names = [t.label for t in job.tracks if t.track_id in wanted_ids]

    work_dir = Path(job.combined_musicxml_path).parent
    suffix_bits = [instruments or "full", f"mps{mps}"]
    suffix = "_" + "_".join(suffix_bits) + _transpose_suffix(transpose)
    pdf_path = work_dir / f"score{suffix}.pdf"
    try:
        export_module.export_pdf(
            score, pdf_path, part_names=part_names, scale_mm=scale_mm, renames=renames
        )
    except subprocess.CalledProcessError as exc:
        raise HTTPException(
            502,
            "PDF-Export fehlgeschlagen: MuseScore-Kommandozeile ist abgestürzt "
            f"(Exit-Code {exc.returncode}). MusicXML/MIDI sind davon nicht "
            "betroffen. Falls das nativ (nicht Docker) passiert: bekannter "
            "MuseScore-4-CLI-Absturz auf diesem System, siehe README "
            "'Bekannte Einschränkung — MuseScore 4 CLI'; PDF-Export läuft "
            "zuverlässig über Docker (Variante A).",
        ) from exc
    except FileNotFoundError as exc:
        raise HTTPException(
            502,
            "PDF-Export nicht verfügbar: MuseScore-Kommandozeile "
            f"('{settings.musescore_binary}') wurde nicht gefunden. Das ist "
            "hier auf dem nativen Backend erwartet (MuseScore 4 auf macOS "
            "erzeugte unzuverlässiges Layout, siehe README 'Bekannte "
            "Einschränkung — MuseScore 4 CLI'); PDF-Export läuft zuverlässig "
            "über Docker (Variante A).",
        ) from exc

    return FileResponse(pdf_path, filename=f"{job.original_filename}{suffix}.pdf")


@router.get("/{job_id}/tracks/{track_id}/audio")
async def download_track_audio(job_id: str, track_id: str) -> FileResponse:
    job = _require_done_job(job_id)
    track = next((t for t in job.tracks if t.track_id == track_id), None)
    if track is None or not track.stem_audio_path:
        raise HTTPException(404, "Track not found")
    return FileResponse(track.stem_audio_path)


@router.get("/{job_id}/tracks/{track_id}/musicxml")
async def download_track_musicxml(job_id: str, track_id: str) -> FileResponse:
    job = _require_done_job(job_id)
    track = next((t for t in job.tracks if t.track_id == track_id), None)
    if track is None or not track.musicxml_path:
        raise HTTPException(404, "Track not found")
    return FileResponse(track.musicxml_path)


@router.get("/{job_id}/source-audio")
async def download_source_audio(job_id: str) -> FileResponse:
    job = job_store.get(job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    return FileResponse(job.source_audio_path)
