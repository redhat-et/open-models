"""End-to-end tests for the offline lab (scripts/lab.sh). Stdlib only."""
import json
import os
import time
import urllib.error
import urllib.request

import pytest

GW = os.environ.get("LAB_GW", "http://localhost:4080")
APP = os.environ.get("LAB_APP", "http://localhost:4082")
PROM = os.environ.get("LAB_PROM", "http://localhost:4081")
MASTER = os.environ.get("LAB_MASTER", "sk-lab-master")
MOCKS = {"flash": 4083, "fast-a": 4084, "fast-b": 4085}

_state = {}


def req(method, url, key=None, body=None, timeout=90):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    if key:
        r.add_header("Authorization", f"Bearer {key}")
    if data:
        r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def chat(key, model):
    return req("POST", f"{GW}/v1/chat/completions", key,
               {"model": model, "messages": [{"role": "user", "content": "hello"}], "max_tokens": 50})


def make_key(**extra):
    alias = extra.get("key_alias")
    if alias:
        req("POST", f"{GW}/key/delete", MASTER, {"key_aliases": [alias]})
    code, j = req("POST", f"{GW}/key/generate", MASTER, {"user_id": "octo-shared", **extra})
    assert code == 200, j
    return j["key"]


def set_fail(mock, fail):
    code, j = req("POST", f"http://localhost:{MOCKS[mock]}/admin/fail", body={"fail": fail})
    assert code == 200, j


@pytest.fixture(scope="module", autouse=True)
def _wait_stack():
    deadline = time.time() + 240
    while time.time() < deadline:
        try:
            if req("GET", f"{GW}/health/liveliness")[0] == 200:
                break
        except Exception:
            time.sleep(2)
    else:
        pytest.fail("litellm never came up (./scripts/lab.sh up, then logs)")
    assert req("GET", f"{APP}/api/health")[0] == 200


def test_health():
    assert req("GET", f"{GW}/v1/models", MASTER)[0] == 200
    assert req("GET", f"{APP}/api/health")[0] == 200


def test_key_chat_flash():
    k = _state["key"] = make_key(key_alias="lab-e2e")
    code, j = chat(k, "qwen38-flash-next")
    assert code == 200, j
    assert "flash" in j["choices"][0]["message"]["content"]


def test_fast_fallback_when_a_down():
    set_fail("fast-a", True)
    try:
        served = []
        for _ in range(8):
            code, j = chat(_state["key"], "qwen38-27b-fast")
            if code == 503:  # transient: failure-recording race; next request must work
                continue
            assert code == 200, j
            served.append(j["choices"][0]["message"]["content"].split("]")[0])
        assert served and all(s == "[fast-b" for s in served), served
    finally:
        set_fail("fast-a", False)


def test_flash_group_fallback_to_fast():
    set_fail("flash", True)
    try:
        time.sleep(1)
        got_fast = False
        for _ in range(5):
            code, j = chat(_state["key"], "qwen38-flash-next")
            if code == 200 and "fast-" in j["choices"][0]["message"]["content"]:
                got_fast = True
                break
        assert got_fast, "flash->fast group fallback never served"
    finally:
        set_fail("flash", False)


def test_rate_limit_429():
    k = make_key(key_alias="lab-rpm", rpm_limit=2)
    codes = [chat(k, "qwen38-27b-fast")[0] for _ in range(12)]
    assert 429 in codes, codes


def test_spend_accumulates():
    deadline = time.time() + 120
    while time.time() < deadline:
        code, j = req("GET", f"{GW}/key/info?key={_state['key']}", MASTER)
        assert code == 200, j
        if j["info"]["spend"] > 0:
            break
        time.sleep(5)
    else:
        pytest.fail(f"spend never accumulated: {j['info']}")


def test_delete_key_then_401():
    code, j = req("POST", f"{GW}/key/delete", MASTER, {"keys": [_state["key"]]})
    assert code == 200, j
    code, _ = chat(_state["key"], "qwen38-flash-next")
    assert code == 401


def test_prom_targets_and_metrics():
    deadline = time.time() + 60
    while time.time() < deadline:
        code, j = req("GET", f"{PROM}/api/v1/targets")
        if code == 200:
            ups = [t for t in j["data"]["activeTargets"] if t["health"] == "up"]
            if len(ups) == 4:
                break
        time.sleep(3)
    else:
        pytest.fail("prometheus targets not all up")
    code, j = req("GET", f"{PROM}/api/v1/query?query=count(litellm_llm_api_latency_metric_last_total)")
    if code != 200 or not j["data"]["result"]:
        code, j = req("GET", f"{PROM}/api/v1/query?query=up")
    assert j["data"]["result"], "no prom metrics yet"
