#!/usr/bin/env bash
# Run the backend natively on macOS with Apple GPU acceleration (MPS for
# Demucs, CoreML/Neural Engine for Basic Pitch) instead of in Docker.
# First-time setup: uv venv --python 3.11 .venv && uv pip install -p .venv/bin/python -r requirements.txt -r requirements-native-macos.txt
set -euo pipefail
cd "$(dirname "$0")"

export DEVICE=mps
export TRANSCRIPTION_PROVIDER=local

# Only needed for PDF export. The mis-layout that made us disable this
# ("1 Takt pro Zeile", measures randomly stretched across the full page
# width) turned out to be caused by our own forced system-break markers
# fighting MuseScore's line-casting — not something specific to MuseScore
# 4 — and is fixed in export.py/notation.py (strip_system_breaks) for
# both renderers. MuseScore 4's documented occasional CLI crash ("Bekannte
# Einschränkung: MuseScore 4 CLI") is still possible; that still surfaces
# as a clear HTTP 502, not bad output, so it's an acceptable trade-off for
# getting PDF export natively too. Comment out (or leave unset) if MuseScore
# isn't installed or its CLI misbehaves in your shell.
if [ -x "/Applications/MuseScore 4.app/Contents/MacOS/mscore" ]; then
  export MUSESCORE_BINARY="/Applications/MuseScore 4.app/Contents/MacOS/mscore"
fi


# Port 8001, not 8000: keeps this independent of the Docker backend (which
# may also be running on 8000) instead of the two ambiguously sharing it.
#
# No --reload: this project directory lives inside OneDrive, which touches
# files across the whole tree (including inside .venv/) as it syncs. Even
# with --reload-dir scoped to app/, OneDrive's churn elsewhere still
# triggered reload storms that killed jobs mid-run (in-memory job store
# gone on restart) — see the "Docker + OneDrive" note in ARCHITECTURE.md
# for the same family of issue. Restart this script manually after editing
# backend code.
# --host 0.0.0.0 (not the uvicorn default 127.0.0.1): lets a frontend
# running in a Docker container reach this native process via
# host.docker.internal, e.g. for VITE_API_BASE_URL=http://host.docker.internal:8001.
exec .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8001
