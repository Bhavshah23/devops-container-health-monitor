import importlib
import os

import pytest


@pytest.fixture()
def module(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("WEBHOOK_SECRET", "test-secret")
    monkeypatch.setenv("START_MONITOR", "false")
    monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
    import monitor

    return importlib.reload(monitor)


def test_health_endpoint(module):
    response = module.app.test_client().get("/health")
    assert response.status_code == 200
    assert response.get_json()["service"] == "ok"


def test_events_endpoint_stores_and_returns_event(module):
    module.store.add("demo", "health_change", "unhealthy", {"previous": "healthy"}, False)
    response = module.app.test_client().get("/api/events?limit=1")
    assert response.status_code == 200
    assert response.get_json()[0]["container_name"] == "demo"


def test_webhook_rejects_wrong_secret(module):
    response = module.app.test_client().post(
        "/webhook/events", json={"container": "api", "status": "down"}
    )
    assert response.status_code == 401


def test_webhook_accepts_valid_payload(module, monkeypatch):
    monkeypatch.setattr(module.monitor, "record_and_notify", lambda *args: None)
    response = module.app.test_client().post(
        "/webhook/events",
        headers={"X-Webhook-Secret": "test-secret"},
        json={"container": "api", "status": "down", "details": {"code": 503}},
    )
    assert response.status_code == 202


def test_bad_limit_returns_400(module):
    response = module.app.test_client().get("/api/events?limit=abc")
    assert response.status_code == 400
