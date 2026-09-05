import time

import pytest
import os
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from backend import app as backend


@pytest.fixture()
def client():
    return TestClient(backend.app)


@pytest.fixture()
def logged_in(client, monkeypatch):
    monkeypatch.setattr(backend, "current_user", lambda request: "octo-user")
    return client


def make_session(user: str, exp_offset: int = 3600) -> str:
    return backend.sign({"gh_user": user, "exp": time.time() + exp_offset})


def test_session_roundtrip_and_tamper():
    tok = make_session("alice")
    assert backend.verify(tok)["gh_user"] == "alice"
    assert backend.verify(tok[:-1] + ("0" if tok[-1] != "0" else "1")) is None
    assert backend.verify(make_session("alice", -1)) is None
    assert backend.verify("garbage") is None


def test_me_requires_login(client):
    assert client.get("/api/me").status_code == 401
    client.cookies.set(backend.COOKIE, make_session("alice"))
    assert client.get("/api/me").json()["github_user"] == "alice"


def test_create_key_uses_catalog_defaults(logged_in, monkeypatch):
    calls = []

    def fake_litellm(path, method="GET", body=None):
        calls.append((path, body))
        return {"key": "sk-new-key"}

    monkeypatch.setattr(backend, "litellm", fake_litellm)
    resp = logged_in.post("/api/keys", json={"name": "laptop"})
    assert resp.status_code == 200
    assert resp.json()["key"] == "sk-new-key"
    path, body = calls[0]
    assert path == "/key/generate"
    assert body["key_alias"] == "laptop"
    assert body["metadata"] == {"github_user": "octo-user"}
    defaults = backend.load_gateway()["key_defaults"]
    assert body["rpm_limit"] == defaults["rpm"]
    assert body["tpm_limit"] == defaults["tpm"]
    assert body["max_budget"] == defaults["max_budget"]


def test_create_key_rejects_bad_alias(logged_in):
    assert logged_in.post("/api/keys", json={"name": "Bad Name!"}).status_code == 422


def fake_key(alias, user):
    return {"key_alias": alias, "token": "sk-" + "x" * 40, "metadata": {"github_user": user}, "spend": 1.5}


def test_list_and_delete_only_own_keys(logged_in, monkeypatch):
    keys = {"h1": fake_key("mine", "octo-user"), "h2": fake_key("theirs", "someone-else")}
    deleted = []

    def fake_litellm(path, method="GET", body=None):
        if path.startswith("/key/list"):
            return {"keys": list(keys)}
        if path.startswith("/key/info"):
            return keys[path.split("key=")[1]]
        if path == "/key/delete":
            deleted.extend(body["key_aliases"])
            return {}
        raise AssertionError(path)

    monkeypatch.setattr(backend, "litellm", fake_litellm)
    listed = logged_in.get("/api/keys").json()
    assert [k["key_alias"] for k in listed] == ["mine"]
    assert listed[0]["spend"] == 1.5
    assert logged_in.delete("/api/keys/theirs").status_code == 404
    assert logged_in.delete("/api/keys/mine").json()["deleted"] == "mine"
    assert deleted == ["mine"]


def test_health(client):
    assert client.get("/api/health").json()["status"] == "ok"


def test_catalog_splits_metered_and_direct(client, monkeypatch):
    monkeypatch.setattr(backend, "load_catalog", lambda: {"gateway": {"base_url": "https://gw/v1"}, "models": [
        {"id": "ocp-flash-next", "name": "f", "status": "running", "group": "qwen38-flash-next", "base_url": "https://f/v1"},
        {"id": "spark2-smart", "name": "s", "status": "listed", "base_url": "http://octo-et-spark2/v1"},
        {"id": "hotaisle", "name": "h", "status": "paused", "group": "qwen38-27b-fast", "base_url": "http://h/v1"},
        {"id": "strix-halo", "name": "x", "status": "planned", "base_url": "http://x/v1"},
    ]})
    cat = client.get("/api/catalog").json()
    assert cat["gateway_url"].startswith("https://")
    by_id = {m["id"]: m for m in cat["models"]}
    assert by_id["ocp-flash-next"]["metered"] is True
    assert by_id["ocp-flash-next"]["gateway_model"] == "qwen38-flash-next"
    assert by_id["spark2-smart"]["metered"] is False
    assert by_id["spark2-smart"]["direct_url"].startswith("http://octo-et-spark")
    assert by_id["hotaisle"]["metered"] is False
    assert by_id["hotaisle"]["status"] == "paused" and by_id["hotaisle"]["direct_url"] is None
    assert "strix-halo" not in by_id  # planned models excluded


def test_index_serves_ui(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "OCTO Open Models" in resp.text


def test_catalog_marks_paused(client, monkeypatch):
    monkeypatch.setattr(backend, "load_catalog", lambda: {"gateway": {}, "models": [
        {"id": "x", "name": "X", "status": "paused", "group": "g", "base_url": "http://x/v1"},
        {"id": "y", "name": "Y", "status": "running", "group": "g", "base_url": "http://y/v1"},
    ]})
    models = {m["id"]: m for m in client.get("/api/catalog").json()["models"]}
    assert models["x"]["status"] == "paused"
    assert not models["x"]["metered"] and models["x"]["direct_url"] is None
    assert models["y"]["metered"] and models["y"]["gateway_model"] == "g"


def test_model_toggle_roundtrip(tmp_path):
    import subprocess
    script = Path(__file__).resolve().parents[2] / "scripts" / "model_toggle.py"
    cat = tmp_path / "catalog.yaml"
    cat.write_text(
        "models:\n"
        "  - id: a\n"
        "    group: g\n"
        "    status: running\n\n"
        "  - id: b\n"
        "    group: g\n"
        "    status: running\n"
    )
    env = dict(os.environ, OCTO_CATALOG=str(cat))
    run = lambda *a: subprocess.run([sys.executable, str(script), *a], env=env,
                                    capture_output=True, text=True, check=True).stdout.strip()
    assert run("a", "off") == "a: running -> paused (group g)"
    assert "    previous_status: running" in cat.read_text()
    assert run("a", "off") == "a: already paused"
    assert run("a", "on") == "a: paused -> running (group g)"
    assert "previous_status" not in cat.read_text()
    orig = cat.read_text()
    run("b", "off"); run("b", "on")
    assert "previous_status" not in cat.read_text() and cat.read_text() == orig


def test_compute_guard_and_hooks(tmp_path):
    import subprocess
    script = Path(__file__).resolve().parents[2] / "scripts" / "compute.py"
    cat = tmp_path / "catalog.yaml"
    cat.write_text(
        "hosts:\n"
        "  h1:\n"
        "    compute: {'off': 'echo stopped', 'on': 'echo started'}\n"
        "models:\n"
        "  - {id: a, host: h1, status: running}\n"
        "  - {id: b, host: h1, status: running}\n"
    )
    env = dict(os.environ, OCTO_CATALOG=str(cat))
    run = lambda *a: subprocess.run([sys.executable, str(script), *a], env=env,
                                    capture_output=True, text=True)
    r = run("a", "off")
    assert r.returncode != 0 and "still serves b" in r.stderr
    import re
    cat.write_text(re.sub(r"id: b, host: h1, status: running",
                          "id: b, host: h1, status: paused", cat.read_text()))
    r = run("a", "off")
    assert r.returncode == 0 and "stopped" in r.stdout
    r = run("a", "on")
    assert r.returncode == 0 and "started" in r.stdout
    cat.write_text(cat.read_text().replace(
        "    compute: {'off': 'echo stopped', 'on': 'echo started'}\n", ""))
    assert "no compute" in run("a", "off").stdout
