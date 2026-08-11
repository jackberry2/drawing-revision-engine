"""FastAPI entrypoint. Not yet wired to the "Analyze Changes" button in the
Lovable app (deliberately deferred) — run standalone with:

    uvicorn dre.api:app --reload
"""

from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from dre import service

app = FastAPI(title="Drawing Revision Engine")

# Only needed if the Lovable frontend calls this API directly from the
# browser (client-side fetch) rather than through a server-side proxy. Set
# ALLOWED_ORIGINS to a comma-separated list of exact origins (e.g.
# "https://your-app.lovable.app,https://yourdomain.com") to lock this down;
# defaults to allow-all since this endpoint is already gated by knowing a
# real analysis_request_id and by the service role key server-side, not by
# origin.
_allowed_origins = os.environ.get("ALLOWED_ORIGINS", "*")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if _allowed_origins == "*" else _allowed_origins.split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/analyze/{analysis_request_id}")
def analyze(analysis_request_id: str, dry_run: bool = False) -> dict:
    """dry_run=true runs the real pipeline against the real request/images
    but skips every database write — use it to preview output first."""
    try:
        return service.analyze_request(analysis_request_id, dry_run=dry_run)
    except Exception as exc:  # noqa: BLE001 - surfaced to the caller as a 500 with detail
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
