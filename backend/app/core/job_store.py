"""Job store: in-memory dict, mirrored to a JSON file per job on disk.

The in-memory dict alone loses every job whenever the backend process
restarts (Docker recreate, native server restart, or just a crash) — which
also meant the frontend couldn't resume a job across a page reload, since it
has nothing else to ask for. Persisting each job's metadata next to its
already-on-disk stems/exports (storage/jobs/<id>/job.json) and reloading
them at startup fixes both: a restart no longer wipes the job list, and the
frontend can list/resume previously analyzed songs instead of re-uploading.

Still file-per-job rather than a real database — fine for the current
single-process MVP; swap for Redis/Postgres if this needs to scale to
multiple backend workers (see ARCHITECTURE.md "Offene Punkte").
"""

from __future__ import annotations

import json
import threading
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from app.core.config import settings
from app.models.job import InstrumentTrackType, Job, JobStatus, TrackResult

_JOB_FILENAME = "job.json"


def _job_to_dict(job: Job) -> dict:
    d = asdict(job)
    d["created_at"] = job.created_at.isoformat()
    d["updated_at"] = job.updated_at.isoformat()
    return d


def _job_from_dict(d: dict) -> Job:
    tracks = [TrackResult(**{**t, "instrument": InstrumentTrackType(t["instrument"])}) for t in d.get("tracks", [])]
    return Job(
        id=d["id"],
        status=JobStatus(d["status"]),
        original_filename=d.get("original_filename", ""),
        source_audio_path=d.get("source_audio_path", ""),
        created_at=datetime.fromisoformat(d["created_at"]),
        updated_at=datetime.fromisoformat(d["updated_at"]),
        progress=d.get("progress", 0.0),
        error=d.get("error"),
        bpm=d.get("bpm"),
        tempo_italian_term=d.get("tempo_italian_term"),
        key_signature=d.get("key_signature"),
        time_signature=d.get("time_signature"),
        tracks=tracks,
        combined_musicxml_path=d.get("combined_musicxml_path"),
        combined_midi_path=d.get("combined_midi_path"),
    )


class JobStore:
    def __init__(self, jobs_dir: Path | None = None) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._jobs_dir = jobs_dir

    def create(self, job: Job) -> Job:
        with self._lock:
            self._jobs[job.id] = job
        self._persist(job)
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def save(self, job: Job) -> None:
        job.touch()
        with self._lock:
            self._jobs[job.id] = job
        self._persist(job)

    def list_all(self) -> list[Job]:
        with self._lock:
            jobs = list(self._jobs.values())
        return sorted(jobs, key=lambda j: j.created_at, reverse=True)

    def _persist(self, job: Job) -> None:
        if self._jobs_dir is None:
            return
        job_dir = self._jobs_dir / job.id
        job_dir.mkdir(parents=True, exist_ok=True)
        (job_dir / _JOB_FILENAME).write_text(json.dumps(_job_to_dict(job), indent=2))

    def load_from_disk(self) -> None:
        if self._jobs_dir is None or not self._jobs_dir.exists():
            return
        for job_file in self._jobs_dir.glob(f"*/{_JOB_FILENAME}"):
            try:
                job = _job_from_dict(json.loads(job_file.read_text()))
            except Exception:
                continue  # skip corrupt/partial job.json rather than fail startup
            with self._lock:
                self._jobs[job.id] = job


job_store = JobStore(jobs_dir=settings.jobs_dir)
job_store.load_from_disk()
