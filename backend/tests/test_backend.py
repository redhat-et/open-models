import time

import pytest
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


def test_catalog_splits_metered_and_direct(client):
    cat = client.get("/api/catalog").json()
    assert cat["gateway_url"].startswith("https://")
    by_id = {m["id"]: m for m in cat["models"]}
    assert by_id["ocp-flash-next"]["metered"] is True
    assert by_id["ocp-flash-next"]["gateway_model"] == "qwen38-flash-next"
    assert by_id["spark2-smart"]["metered"] is False
    assert by_id["spark2-smart"]["direct_url"].startswith("http://octo-et-spark")
    assert "hotaisle" not in by_id  # planned models excluded


def test_index_serves_ui(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "OCTO Open Models" in resp.text
