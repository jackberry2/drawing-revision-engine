"""FastAPI entrypoint. Not yet wired to the "Analyze Changes" button in the
Lovable app (deliberately deferred) — run standalone with:

    uvicorn dre.api:app --reload
"""

from __future__ import annotations

import os
import secrets

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded

from dre import config, credits, service
from dre.supa import repository as repo

app = FastAPI(title="Drawing Revision Engine")


def _rate_limit_key(request: Request) -> str:
    """Keyed by the caller's API key, not IP — this is a single backend
    service called by one real caller (Lovable's backend) on behalf of
    however many of its own end users, so IP-based limiting would just
    measure Lovable's own egress IP, not real per-caller behavior. Keying
    by API key means this naturally extends if a second real caller with
    its own key ever exists. Deliberately reads the raw header directly
    rather than depending on require_api_key's validated value — the rate
    limiter must be able to key *something* even for an invalid/missing
    key (that request still gets rejected with 401 by require_api_key
    before doing any real work; see the ordering note below)."""
    return request.headers.get("x-api-key", "unknown")


# In-memory limiter — a deliberate safety net against bugs, retry storms, or
# a leaked key, not a tool for managing normal traffic (see the per-route
# comments below for why each number was picked). In-memory is fine at this
# scale: this runs as a single Render worker process, so there's no
# multi-process/multi-instance state to share; Redis would be overkill.
#
# FastAPI resolves `dependencies=[Depends(require_api_key)]` before calling
# the (decorated) endpoint function body, and the @limiter.limit(...)/
# shared_limit(...) decorators wrap that function body — so an invalid or
# missing key is rejected with 401 *before* it ever consumes rate-limit
# budget, real or otherwise.
limiter = Limiter(key_func=_rate_limit_key)
app.state.limiter = limiter


@app.exception_handler(RateLimitExceeded)
def _rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    # Matches this API's existing {"detail": ...} error shape (see
    # require_api_key/HTTPException below) rather than slowapi's own
    # default {"error": ...} shape, so a 429 isn't a special case for
    # anything parsing this API's error responses.
    return JSONResponse(status_code=429, content={"detail": f"Rate limit exceeded: {exc.detail}"})

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
@limiter.shared_limit("20/minute", scope="analyze")
def analyze(request: Request, analysis_request_id: str, dry_run: bool = False) -> dict:
    """dry_run=true runs the real pipeline against the real request/images
    but skips every database write — use it to preview output first.

    Mode-agnostic: dispatches to the two-image or single-sheet pipeline based
    on `analysis_requests.mode`, so this same route already serves both. See
    `/analyze-single` below for a mode-checked entrypoint under the name the
    Lovable frontend's single-sheet button actually calls.

    Rate limit: shares one 20/minute-per-API-key bucket with /analyze-single
    (see the `scope="analyze"` on both — they both funnel into the same real,
    expensive service.analyze_request call, so a caller alternating between
    the two routes doesn't get double the budget). This is a backstop, not
    the primary throttle — real cost is already gated by dre.credits before
    any Claude call happens; a genuine end user never needs anywhere near
    20 of these in a minute, since each one takes 60-190s end to end. This
    catches a retry loop or bug hammering the endpoint, not real traffic.
    """
    try:
        return service.analyze_request(analysis_request_id, dry_run=dry_run)
    except credits.InsufficientCreditsError as exc:
        raise HTTPException(status_code=402, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - surfaced to the caller as a 500 with detail
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/analyze-single/{analysis_request_id}", dependencies=[Depends(require_api_key)])
@limiter.shared_limit("20/minute", scope="analyze")
def analyze_single(request: Request, analysis_request_id: str, dry_run: bool = False) -> dict:
    """Dedicated entrypoint for the "Analyze Sheet (Single Revision)" button.

    `service.analyze_request` already dispatches on `analysis_requests.mode`
    (see `/analyze` above), so this is a thin wrapper around the same
    function — it exists as its own named route because that's what the
    frontend calls, not because the underlying pipeline differs. It fails
    loudly with a 400 if the request isn't actually set up for single-sheet
    mode, rather than silently running whatever `/analyze` would have run.

    Rate limit: see /analyze's docstring — shares the same 20/minute-per-API-
    key bucket (scope="analyze").
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
    except credits.InsufficientCreditsError as exc:
        raise HTTPException(status_code=402, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - surfaced to the caller as a 500 with detail
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get(
    "/analyze-single/{analysis_request_id}/duration-estimate",
    dependencies=[Depends(require_api_key)],
)
@limiter.limit("10/minute")
def duration_estimate(request: Request, analysis_request_id: str) -> dict:
    """docs/tiled_analysis_findings.md §3e: call this before (or without
    waiting for) the real POST /analyze-single, to show a real "this may
    take a few minutes" hint instead of a bare spinner — a tiled sheet's
    longer processing time otherwise looks identical to a hang, the exact
    class of problem that already caused a real incident this session (the
    cold-start "Can't reach the server" investigation). No Claude call —
    just a Storage download and cheap page-size geometry, so it's safe to
    call speculatively/eagerly, unlike the real analyze call.

    single_sheet mode only, same scope as `/analyze-single` itself —
    two_image tiling doesn't exist (see docs/tiled_analysis_findings.md
    §5), so there's nothing this could estimate differently for it.

    Rate limit: its own, tighter 10/minute-per-API-key bucket, deliberately
    separate from /analyze's — this route has no credit gate in front of
    it (nothing here costs the caller anything), so unlike /analyze, this
    rate limit is the *only* protection against it being hit on a loop, not
    a backstop behind an economic one. It's meant to be called once or
    twice per real analysis decision (see above), so 10/minute is still
    generous for genuine multi-user traffic through the one shared API key.
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
                "'single_sheet' — duration estimates are only meaningful for the "
                "mode tiling applies to."
            ),
        )

    try:
        return service.estimate_duration(analysis_request_id)
    except Exception as exc:  # noqa: BLE001 - surfaced to the caller as a 500 with detail
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
