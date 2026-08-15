from unittest.mock import patch

from fastapi.testclient import TestClient

from dre import api, config


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
