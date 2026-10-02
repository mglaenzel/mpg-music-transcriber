"""Source separation via Demucs.

Splits the input mix into stems using htdemucs_6s, the 6-source Demucs
model: vocals / drums / bass / guitar / piano / other. This replaces the
4-stem default (vocals/drums/bass/other) specifically so guitar and piano
are no longer bundled into one noisy "other" stem (see ARCHITECTURE.md).
`other` still catches anything left over (strings, winds, synths, ...).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from app.core.config import settings

STEM_NAMES = ["vocals", "drums", "bass", "guitar", "piano", "other"]


def separate_stems(audio_path: Path, work_dir: Path) -> dict[str, Path]:
    """Run Demucs on `audio_path`, return {stem_name: wav_path} in work_dir."""
    out_dir = work_dir / "stems"
    out_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        # sys.executable, not a bare "python3": in a native venv (as opposed
        # to Docker, where "python3" on PATH happens to be the one true
        # interpreter) a hardcoded "python3" resolves to the system Python,
        # which doesn't have demucs installed.
        sys.executable,
        "-m",
        "demucs.separate",
        "-n",
        settings.demucs_model,
        "-d",
        settings.device,
        "-o",
        str(out_dir),
        str(audio_path),
    ]
    subprocess.run(cmd, check=True)

    model_dir = out_dir / settings.demucs_model / audio_path.stem
    stems = {name: model_dir / f"{name}.wav" for name in STEM_NAMES}
    missing = [name for name, path in stems.items() if not path.exists()]
    if missing:
        raise RuntimeError(f"Demucs did not produce expected stems: {missing}")
    return stems
