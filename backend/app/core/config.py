from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "Music Transcriber"
    storage_dir: Path = Path("storage")
    uploads_dir: Path = Path("storage/uploads")
    jobs_dir: Path = Path("storage/jobs")

    # "local" = open-source pipeline (Demucs + Basic Pitch), "api" = external
    # commercial transcription provider (not implemented yet, see ARCHITECTURE.md).
    transcription_provider: str = "local"

    demucs_model: str = "htdemucs_6s"
    device: str = "cpu"  # "cpu" | "mps" | "cuda" — overridden by env DEVICE

    max_upload_mb: int = 100

    musescore_binary: str = "mscore"

    class Config:
        env_file = ".env"
        env_prefix = ""

    def ensure_dirs(self) -> None:
        self.uploads_dir.mkdir(parents=True, exist_ok=True)
        self.jobs_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
settings.ensure_dirs()
