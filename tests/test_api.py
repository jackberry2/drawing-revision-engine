from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from dre import api, config, credits


def _client() -> TestClient:
    return TestClient(api.app)


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """dre.api.limiter's in-memory storage is a module-level singleton that
    persists for the life of the process — without a reset, real calls one
    test makes against a route+API-key would count toward another test's
    budget for that same bucket, making tests order-dependent. Rate-limiting
    behavior itself is covered by its own dedicated tests below, which
    manage the reset explicitly around the burst they're triggering."""
    api.limiter.reset()
    yield


def test_analyze_rejects_missing_key(monkeypatch):
    monkeypatch.setattr(config, "DRE_API_KEY", "correct-key")
    resp = _client().post("/analyze/some-id")
    assert resp.status_code == 401


def test_analyze_rejects_wrong_key(monkeypatch):
    monkeypatch.setattr(config, "DRE_API_KEY", "correct-key")
    resp = _client().post("/analyze/some-id", headers={"X-API-Key": "wrong-key"})
    assert resp.status_code == 401


def test_analyze_fails_closed_when_server_key_unset(monkeypatch):
    monkeypatch.setattr(config, "DRE_API_KEY", "")
    resp = _client().post("/analyze/some-id", headers={"X-API-Key": "anything"})
    assert resp.status_code == 500


def test_analyze_accepts_correct_key_and_reaches_service(monkeypatch):
    monkeypatch.setattr(config, "DRE_API_KEY", "correct-key")
    with patch("dre.api.service.analyze_request", return_value={"ok": True}) as mock_analyze:
        resp = _client().post(
            "/analyze/some-id", headers={"X-API-Key": "correct-key"}
        )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    mock_analyze.assert_called_once_with("some-id", dry_run=False)


def test_analyze_returns_402_when_out_of_credits(monkeypatch):
    monkeypatch.setattr(config, "DRE_API_KEY", "correct-key")
    with patch(
        "dre.api.service.analyze_request",
        side_effect=credits.InsufficientCreditsError("user-1", 0),
    ):
        resp = _client().post("/analyze/some-id", headers={"X-API-Key": "correct-key"})
    assert resp.status_code == 402
    assert "user-1" in resp.json()["detail"]


def test_health_does_not_require_api_key(monkeypatch):
    monkeypatch.setattr(config, "DRE_API_KEY", "correct-key")
    resp = _client().get("/health")
    assert resp.status_code == 200


def test_analyze_single_rejects_missing_key(monkeypatch):
    monkeypatch.setattr(config, "DRE_API_KEY", "correct-key")
    resp = _client().post("/analyze-single/some-id")
    assert resp.status_code == 401


def test_analyze_single_404s_when_request_not_found(monkeypatch):
    monkeypatch.setattr(config, "DRE_API_KEY", "correct-key")
    with patch("dre.api.repo.get_analysis_request", side_effect=Exception("not found")):
        resp = _client().post(
            "/analyze-single/missing-id", headers={"X-API-Key": "correct-key"}
        )
    assert resp.status_code == 404


def test_analyze_single_rejects_two_image_mode_request(monkeypatch):
    monkeypatch.setattr(config, "DRE_API_KEY", "correct-key")
    with patch(
        "dre.api.repo.get_analysis_request",
        return_value={"id": "some-id", "mode": "two_image"},
    ):
        resp = _client().post(
            "/analyze-single/some-id", headers={"X-API-Key": "correct-key"}
        )
    assert resp.status_code == 400
    assert "single_sheet" in resp.json()["detail"]


def test_analyze_single_accepts_correct_key_and_reaches_service(monkeypatch):
    monkeypatch.setattr(config, "DRE_API_KEY", "correct-key")
    with patch(
        "dre.api.repo.get_analysis_request",
        return_value={"id": "some-id", "mode": "single_sheet"},
    ), patch(
        "dre.api.service.analyze_request", return_value={"ok": True}
    ) as mock_analyze:
        resp = _client().post(
            "/analyze-single/some-id", headers={"X-API-Key": "correct-key"}
        )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    mock_analyze.assert_called_once_with("some-id", dry_run=False)


def test_analyze_single_returns_402_when_out_of_credits(monkeypatch):
    monkeypatch.setattr(config, "DRE_API_KEY", "correct-key")
    with patch(
        "dre.api.repo.get_analysis_request",
        return_value={"id": "some-id", "mode": "single_sheet"},
    ), patch(
        "dre.api.service.analyze_request",
        side_effect=credits.InsufficientCreditsError("user-1", 0),
    ):
        resp = _client().post(
            "/analyze-single/some-id", headers={"X-API-Key": "correct-key"}
        )
    assert resp.status_code == 402
    assert "user-1" in resp.json()["detail"]


def test_duration_estimate_rejects_missing_key(monkeypatch):
    monkeypatch.setattr(config, "DRE_API_KEY", "correct-key")
    resp = _client().get("/analyze-single/some-id/duration-estimate")
    assert resp.status_code == 401


def test_duration_estimate_404s_when_request_not_found(monkeypatch):
    monkeypatch.setattr(config, "DRE_API_KEY", "correct-key")
    with patch("dre.api.repo.get_analysis_request", side_effect=Exception("not found")):
        resp = _client().get(
            "/analyze-single/missing-id/duration-estimate", headers={"X-API-Key": "correct-key"}
        )
    assert resp.status_code == 404


def test_duration_estimate_rejects_two_image_mode_request(monkeypatch):
    monkeypatch.setattr(config, "DRE_API_KEY", "correct-key")
    with patch(
        "dre.api.repo.get_analysis_request",
        return_value={"id": "some-id", "mode": "two_image"},
    ):
        resp = _client().get(
            "/analyze-single/some-id/duration-estimate", headers={"X-API-Key": "correct-key"}
        )
    assert resp.status_code == 400
    assert "single_sheet" in resp.json()["detail"]


def test_duration_estimate_accepts_correct_key_and_reaches_service(monkeypatch):
    monkeypatch.setattr(config, "DRE_API_KEY", "correct-key")
    fake_estimate = {
        "analysis_request_id": "some-id",
        "tiling_likely": True,
        "estimated_duration_seconds": 180,
        "reason": "n/a",
    }
    with patch(
        "dre.api.repo.get_analysis_request",
        return_value={"id": "some-id", "mode": "single_sheet"},
    ), patch(
        "dre.api.service.estimate_duration", return_value=fake_estimate
    ) as mock_estimate:
        resp = _client().get(
            "/analyze-single/some-id/duration-estimate", headers={"X-API-Key": "correct-key"}
        )
    assert resp.status_code == 200
    assert resp.json() == fake_estimate
    mock_estimate.assert_called_once_with("some-id")


def test_duration_estimate_does_not_require_dry_run_or_post():
    """A GET, unlike the real analyze routes - no side effects, safe to
    call speculatively/eagerly (docs/tiled_analysis_findings.md §3e)."""
    from dre import api as api_module

    route = next(
        r for r in api_module.app.routes if getattr(r, "path", None) == "/analyze-single/{analysis_request_id}/duration-estimate"
    )
    assert route.methods == {"GET"}


# ---- rate limiting (safety net against bugs/retry storms/a leaked key) ---


def test_analyze_normal_single_request_is_unaffected_by_rate_limiting(monkeypatch):
    """A lone, ordinary call must behave exactly as it did before rate
    limiting existed — same 200, same body, same call args."""
    monkeypatch.setattr(config, "DRE_API_KEY", "correct-key")
    with patch("dre.api.service.analyze_request", return_value={"ok": True}) as mock_analyze:
        resp = _client().post("/analyze/some-id", headers={"X-API-Key": "correct-key"})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    mock_analyze.assert_called_once_with("some-id", dry_run=False)


def test_analyze_burst_beyond_20_per_minute_returns_429(monkeypatch):
    """The 20/minute cap is a backstop against a retry loop or bug, not
    something real usage should ever brush against — this confirms it
    actually engages once a burst exceeds it, and that the response is a
    clearly-labeled 429, not a generic error."""
    monkeypatch.setattr(config, "DRE_API_KEY", "correct-key")
    client = _client()
    with patch("dre.api.service.analyze_request", return_value={"ok": True}):
        statuses = [
            client.post("/analyze/some-id", headers={"X-API-Key": "correct-key"}).status_code
            for _ in range(20)
        ]
        assert statuses == [200] * 20

        blocked = client.post("/analyze/some-id", headers={"X-API-Key": "correct-key"})

    assert blocked.status_code == 429
    assert "Rate limit exceeded" in blocked.json()["detail"]


def test_analyze_and_analyze_single_share_one_rate_limit_bucket(monkeypatch):
    """/analyze and /analyze-single both funnel into the same real,
    expensive service.analyze_request call, so they must share one budget
    — alternating between the two routes must not double the effective
    limit."""
    monkeypatch.setattr(config, "DRE_API_KEY", "correct-key")
    client = _client()
    with patch(
        "dre.api.repo.get_analysis_request",
        return_value={"id": "some-id", "mode": "single_sheet"},
    ), patch("dre.api.service.analyze_request", return_value={"ok": True}):
        for _ in range(10):
            assert (
                client.post("/analyze/some-id", headers={"X-API-Key": "correct-key"}).status_code
                == 200
            )
        for _ in range(10):
            assert (
                client.post(
                    "/analyze-single/some-id", headers={"X-API-Key": "correct-key"}
                ).status_code
                == 200
            )
        # 20 real calls already made across both routes combined - the 21st,
        # on either route, must be blocked by the shared bucket.
        blocked = client.post("/analyze/some-id", headers={"X-API-Key": "correct-key"})

    assert blocked.status_code == 429


def test_rate_limit_is_scoped_per_api_key(monkeypatch):
    """Exhausting one key's budget must never affect a different key -
    keying by API key (not IP) is exactly what lets this naturally extend
    to a second real caller later."""
    monkeypatch.setattr(config, "DRE_API_KEY", "correct-key")
    client = _client()
    with patch("dre.api.service.analyze_request", return_value={"ok": True}):
        for _ in range(20):
            assert (
                client.post("/analyze/some-id", headers={"X-API-Key": "correct-key"}).status_code
                == 200
            )
        exhausted = client.post("/analyze/some-id", headers={"X-API-Key": "correct-key"})
        assert exhausted.status_code == 429

        # A wrong/different key is a fresh bucket - and also still correctly
        # rejected on its own merits (401), never let through by a shared
        # rate-limit key collapsing distinct keys together.
        other_key = client.post("/analyze/some-id", headers={"X-API-Key": "some-other-key"})
    assert other_key.status_code == 401


def test_duration_estimate_normal_single_request_is_unaffected_by_rate_limiting(monkeypatch):
    monkeypatch.setattr(config, "DRE_API_KEY", "correct-key")
    fake_estimate = {
        "analysis_request_id": "some-id",
        "tiling_likely": False,
        "estimated_duration_seconds": 60,
        "reason": "n/a",
    }
    with patch(
        "dre.api.repo.get_analysis_request",
        return_value={"id": "some-id", "mode": "single_sheet"},
    ), patch("dre.api.service.estimate_duration", return_value=fake_estimate):
        resp = _client().get(
            "/analyze-single/some-id/duration-estimate", headers={"X-API-Key": "correct-key"}
        )
    assert resp.status_code == 200


def test_duration_estimate_burst_beyond_10_per_minute_returns_429(monkeypatch):
    """duration-estimate has no credit gate in front of it, so its own
    10/minute cap is the only protection - deliberately tighter than
    /analyze's 20/minute."""
    monkeypatch.setattr(config, "DRE_API_KEY", "correct-key")
    client = _client()
    fake_estimate = {
        "analysis_request_id": "some-id",
        "tiling_likely": False,
        "estimated_duration_seconds": 60,
        "reason": "n/a",
    }
    with patch(
        "dre.api.repo.get_analysis_request",
        return_value={"id": "some-id", "mode": "single_sheet"},
    ), patch("dre.api.service.estimate_duration", return_value=fake_estimate):
        statuses = [
            client.get(
                "/analyze-single/some-id/duration-estimate", headers={"X-API-Key": "correct-key"}
            ).status_code
            for _ in range(10)
        ]
        assert statuses == [200] * 10

        blocked = client.get(
            "/analyze-single/some-id/duration-estimate", headers={"X-API-Key": "correct-key"}
        )

    assert blocked.status_code == 429
    assert "Rate limit exceeded" in blocked.json()["detail"]


def test_duration_estimate_rate_limit_is_independent_of_analyze_bucket(monkeypatch):
    """Hammering /analyze must not eat into duration-estimate's separate
    budget, and vice versa - they're deliberately different buckets with
    different limits, not one shared limit across every route."""
    monkeypatch.setattr(config, "DRE_API_KEY", "correct-key")
    client = _client()
    fake_estimate = {
        "analysis_request_id": "some-id",
        "tiling_likely": False,
        "estimated_duration_seconds": 60,
        "reason": "n/a",
    }
    with patch(
        "dre.api.repo.get_analysis_request",
        return_value={"id": "some-id", "mode": "single_sheet"},
    ), patch("dre.api.service.analyze_request", return_value={"ok": True}), patch(
        "dre.api.service.estimate_duration", return_value=fake_estimate
    ):
        for _ in range(20):
            client.post("/analyze/some-id", headers={"X-API-Key": "correct-key"})
        # /analyze's bucket is now exhausted; duration-estimate's is untouched.
        resp = client.get(
            "/analyze-single/some-id/duration-estimate", headers={"X-API-Key": "correct-key"}
        )
    assert resp.status_code == 200
