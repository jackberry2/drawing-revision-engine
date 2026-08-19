from unittest.mock import patch

from fastapi.testclient import TestClient

from dre import api, config, credits


def _client() -> TestClient:
    return TestClient(api.app)


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
