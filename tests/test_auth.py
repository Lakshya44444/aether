"""The /api surface is a trust boundary.

/api/traces returns decision records for every session the gateway has seen, so an
unauthenticated deployment is an exfiltration endpoint. These pin that the key is
actually required when one is configured, and that the static UI is not gated behind it.
"""
import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def keyed(monkeypatch):
    """Reimports the app with keys configured; the key set is read at import time."""
    monkeypatch.setenv("AETHER_API_KEYS", "key-one, key-two")
    import src.config
    import src.main
    importlib.reload(src.config)
    app_module = importlib.reload(src.main)
    with TestClient(app_module.app) as client:
        yield client
    monkeypatch.delenv("AETHER_API_KEYS", raising=False)
    importlib.reload(src.config)
    importlib.reload(src.main)


BODY = {"input_text": "hello", "output_text": "hi", "use_case": "customer_support",
        "action": "generate_text"}


def test_missing_key_is_rejected(keyed):
    assert keyed.post("/api/evaluate", json=BODY).status_code == 401


def test_wrong_key_is_rejected(keyed):
    r = keyed.post("/api/evaluate", json=BODY, headers={"X-API-Key": "nope"})
    assert r.status_code == 401


def test_either_configured_key_is_accepted(keyed):
    for key in ("key-one", "key-two"):
        r = keyed.post("/api/evaluate", json=BODY, headers={"X-API-Key": key})
        assert r.status_code == 200, key


def test_read_endpoints_are_gated_too(keyed):
    """The write path is not the one that leaks."""
    assert keyed.get("/api/traces").status_code == 401
    assert keyed.get("/api/stats").status_code == 401


def test_unauthenticated_by_default(client_without_keys):
    assert client_without_keys.post("/api/evaluate", json=BODY).status_code == 200


@pytest.fixture
def client_without_keys():
    import src.main
    with TestClient(src.main.app) as client:
        yield client


# ── Operational endpoints ────────────────────────────────────────────────────

def test_health_and_metrics_answer_without_a_key(keyed):
    """A probe and a scraper are infrastructure, not callers. Neither reveals
    anything about traffic content, and gating them means a liveness check that
    fails whenever the key rotates."""
    assert keyed.get("/api/health").status_code == 200
    assert keyed.get("/api/metrics").status_code == 200


def test_health_reports_its_backends(client_without_keys):
    body = client_without_keys.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["checks"]["audit"] == "ok"
    assert body["checks"]["state"] == "ok"
    assert "loaded" in body["checks"]["policies"]


def test_metrics_counts_a_decision(client_without_keys):
    client_without_keys.post("/api/evaluate", json=BODY)
    body = client_without_keys.get("/api/metrics").text
    assert 'aether_decisions_total{use_case="customer_support"' in body
    assert "aether_evaluate_latency_ms_bucket" in body
    assert "aether_detector_failures_total" in body


def test_policy_reload_needs_the_key(keyed):
    assert keyed.post("/api/policies/reload").status_code == 401
    ok = keyed.post("/api/policies/reload", headers={"X-API-Key": "key-one"})
    assert ok.status_code == 200
    assert set(ok.json()["loaded"]) == {"customer_support", "internal_copilot", "finance_agent"}
    assert ok.json()["added"] == [] and ok.json()["removed"] == []


def test_rate_limit_rejects_a_burst(monkeypatch):
    """Authentication answers who, not how much. Detection is linear in input length,
    so an authenticated caller can still exhaust a worker."""
    import importlib
    from fastapi.testclient import TestClient
    monkeypatch.setenv("AETHER_RATE_LIMIT_PER_MINUTE", "60")
    monkeypatch.setenv("AETHER_RATE_LIMIT_BURST", "3")
    import src.config, src.main
    importlib.reload(src.config)
    app_module = importlib.reload(src.main)

    with TestClient(app_module.app) as client:
        codes = [client.post("/api/evaluate", json=BODY).status_code for _ in range(8)]
    assert codes.count(200) == 3, codes
    assert codes.count(429) == 5, codes

    monkeypatch.undo()
    importlib.reload(src.config)
    importlib.reload(src.main)


def test_rate_limited_response_says_when_to_retry(monkeypatch):
    import importlib
    from fastapi.testclient import TestClient
    monkeypatch.setenv("AETHER_RATE_LIMIT_PER_MINUTE", "60")
    monkeypatch.setenv("AETHER_RATE_LIMIT_BURST", "1")
    import src.config, src.main
    importlib.reload(src.config)
    app_module = importlib.reload(src.main)

    with TestClient(app_module.app) as client:
        client.post("/api/evaluate", json=BODY)
        blocked = client.post("/api/evaluate", json=BODY)
    assert blocked.status_code == 429
    assert int(blocked.headers["Retry-After"]) >= 1

    monkeypatch.undo()
    importlib.reload(src.config)
    importlib.reload(src.main)
