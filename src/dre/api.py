"""FastAPI entrypoint. Not yet wired to the "Analyze Changes" button in the
Lovable app (deliberately deferred) — run standalone with:

    uvicorn dre.api:app --reload
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException

from dre import service

app = FastAPI(title="Drawing Revision Engine")


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
