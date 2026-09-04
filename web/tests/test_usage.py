from fastapi.testclient import TestClient

from app import main

client = TestClient(main.app)


def test_usage_reports_rates_from_prometheus(monkeypatch):
    def fake_prom(query):
        assert "generation_tokens_total" in query
        model = query.split('model="')[1].split('"')[0]
        value = "0.5" if model == "rtx5090-fast" else ""
        result = [{"value": ["0", "0.5"]}] if value else []
        return {"data": {"result": result}}

    monkeypatch.setattr(main, "prom_query", fake_prom)
    payload = client.get("/api/usage").json()
    assert payload["models"]["rtx5090-fast"]["gen_tokens_per_hour"] == 1800


def test_usage_survives_prometheus_down(monkeypatch):
    def boom(query):
        raise OSError("connection refused")

    monkeypatch.setattr(main, "prom_query", boom)
    resp = client.get("/api/usage")
    assert resp.status_code == 200
    entry = next(iter(resp.json()["models"].values()))
    assert "error" in entry
