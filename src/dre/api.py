"""FastAPI entrypoint. Not yet wired to the "Analyze Changes" button in the
Lovable app (deliberately deferred) — run standalone with:

    uvicorn dre.api:app --reload
"""

from __future__ import annotations

import os
import secrets

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from dre import config, service
from dre.supa import repository as repo

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


def require_api_key(x_api_key: str = Header(default="")) -> None:
    if not config.DRE_API_KEY:
        # Fail closed: an unset key must never mean "let everyone through".
        raise HTTPException(status_code=500, detail="DRE_API_KEY is not configured on the server")
    if not secrets.compare_digest(x_api_key, config.DRE_API_KEY):
        raise HTTPException(status_code=401, detail="Missing or invalid X-API-Key")


@app.post("/analyze/{analysis_request_id}", dependencies=[Depends(require_api_key)])
def analyze(analysis_request_id: str, dry_run: bool = False) -> dict:
    """dry_run=true runs the real pipeline against the real request/images
    but skips every database write — use it to preview output first.

    Mode-agnostic: dispatches to the two-image or single-sheet pipeline based
    on `analysis_requests.mode`, so this same route already serves both. See
    `/analyze-single` below for a mode-checked entrypoint under the name the
    Lovable frontend's single-sheet button actually calls.
    """
    try:
        return service.analyze_request(analysis_request_id, dry_run=dry_run)
    except Exception as exc:  # noqa: BLE001 - surfaced to the caller as a 500 with detail
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/analyze-single/{analysis_request_id}", dependencies=[Depends(require_api_key)])
def analyze_single(analysis_request_id: str, dry_run: bool = False) -> dict:
    """Dedicated entrypoint for the "Analyze Sheet (Single Revision)" button.

    `service.analyze_request` already dispatches on `analysis_requests.mode`
    (see `/analyze` above), so this is a thin wrapper around the same
    function — it exists as its own named route because that's what the
    frontend calls, not because the underlying pipeline differs. It fails
    loudly with a 400 if the request isn't actually set up for single-sheet
    mode, rather than silently running whatever `/analyze` would have run.
    """
    try:
        analysis_request = repo.get_analysis_request(analysis_request_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=404, detail=f"analysis_request {analysis_request_id!r} not found"
        ) from exc

    mode = analysis_request.get("mode") or "two_image"
    if mode != "single_sheet":
        raise HTTPException(
            status_code=400,
            detail=(
                f"analysis_request {analysis_request_id} has mode={mode!r}, not "
                "'single_sheet'. Use POST /analyze/{id} for two-image requests, "
                "or set mode='single_sheet' before calling this route."
            ),
        )

    try:
        return service.analyze_request(analysis_request_id, dry_run=dry_run)
    except Exception as exc:  # noqa: BLE001 - surfaced to the caller as a 500 with detail
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
