from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import jobs
from app.core.config import settings

app = FastAPI(title=settings.app_name)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5174",
        "http://localhost:5175",  # frontend pointed at the native (port 8001) backend
        "http://localhost:3000",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
    # Response headers are NOT readable by frontend JS on a cross-origin
    # request unless explicitly exposed here — Content-Disposition isn't
    # one of the handful of headers browsers expose by default. Without
    # this, ScoreViewer.tsx's PDF download (fetch() + blob, needed to show
    # a server error inline instead of a silently-broken <a href download>)
    # could never read the server-suggested filename and always fell back
    # to a generic "transkription.pdf" regardless of the song's title.
    expose_headers=["Content-Disposition"],
)

app.include_router(jobs.router)


@app.get("/")
async def root() -> dict:
    # This backend is API-only — there is no page at "/". The actual UI is
    # the separate frontend (see README), which talks to this API.
    return {
        "message": "Music Transcriber backend (API only, no UI at '/').",
        "health": "/api/health",
        "docs": "/docs",
    }


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok"}
