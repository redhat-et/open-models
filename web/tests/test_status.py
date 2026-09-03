from fastapi.testclient import TestClient

from app import main
from app.catalog import load_catalog, probeable_models

client = TestClient(main.app)


def test_status_probes_only_live_and_candidate(monkeypatch):
    calls = []

    def fake_probe(model):
        calls.append(model["id"])
        return {"id": model["id"], "status": "up", "detail": "ok"}

    monkeypatch.setattr(main, "probe_model", fake_probe)
    result = client.get("/api/status").json()

    expected = {m["id"] for m in probeable_models(load_catalog())}
    assert set(calls) == expected
    statuses = {r["id"]: r["status"] for r in result}
    assert all(statuses[i] == "up" for i in expected)


def test_planned_models_reported_as_planned(monkeypatch):
    monkeypatch.setattr(
        main, "probe_model", lambda m: {"id": m["id"], "status": "up", "detail": ""}
    )
    result = client.get("/api/status").json()
    planned = {m["id"] for m in load_catalog()["models"] if m["status"] not in ("running", "live", "candidate")}
    statuses = {r["id"]: r["status"] for r in result}
    assert all(statuses[i] == "planned" for i in planned)


def test_probe_model_down_on_network_error(monkeypatch):
    import urllib.error

    def boom(*args, **kwargs):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(main.urllib.request, "urlopen", boom)
    result = main.probe_model({"id": "x", "base_url": "http://nowhere.invalid/v1"})
    assert result == {"id": "x", "status": "down", "detail": "<urlopen error connection refused>"}
