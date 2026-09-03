import json

from fastapi.testclient import TestClient

from app import main
from app.catalog import CONFIGS_DIR, load_catalog

client = TestClient(main.app)


def test_health():
    assert client.get("/api/health").json() == {"status": "ok"}


def test_models_endpoint_matches_catalog():
    payload = client.get("/api/models").json()
    catalog = load_catalog()
    assert payload["platform"] == catalog["platform"]
    assert len(payload["models"]) == len(catalog["models"])


def test_index_renders_model_names():
    html = client.get("/").text
    for model in load_catalog()["models"]:
        assert model["name"] in html


def test_index_shows_curl_for_running_models():
    html = client.get("/").text
    running = [m for m in load_catalog()["models"] if m["status"] in ("running", "live")]
    assert running, "catalog should ship with at least one running model"
    assert "chat/completions" in html


def test_opencode_config_downloadable():
    path = CONFIGS_DIR / "opencode.json"
    if not path.exists():
        import pytest

        pytest.skip("run scripts/gen_configs.py first")
    resp = client.get("/configs/opencode.json")
    assert resp.status_code == 200
    cfg = resp.json()
    assert cfg["$schema"] == "https://opencode.ai/config.json"
