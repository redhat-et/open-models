#!/usr/bin/env python3
"""OCTO Open Models platform backend.

GitHub OAuth login (org membership or allowlist), then self-service API
keys minted through the LiteLLM admin API. No app-owned database: the
github user is stored in each key's metadata (`metadata.github_user`),
so litellm itself is the source of truth; sessions are signed cookies.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import time
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import yaml
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

REPO_ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = REPO_ROOT / "catalog.yaml"

GITHUB_CLIENT_ID = os.environ.get("GITHUB_CLIENT_ID", "")
GITHUB_CLIENT_SECRET = os.environ.get("GITHUB_CLIENT_SECRET", "")
SESSION_SECRET = os.environ.get("SESSION_SECRET", "dev-only-insecure-secret")
GITHUB_ORG = os.environ.get("GITHUB_ORG", "")
ALLOWED_USERS = {u.strip().lower() for u in os.environ.get("GITHUB_ALLOWLIST", "").split(",") if u.strip()}
SESSION_TTL = int(os.environ.get("SESSION_TTL", "2592000"))  # 30 days

LITELLM_URL = os.environ.get("LITELLM_URL", "").rstrip("/").removesuffix("/v1")
LITELLM_MASTER_KEY = os.environ.get("LITELLM_MASTER_KEY", "")
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "")
SSH_MOUNT = os.environ.get("SSH_MOUNT", "/etc/om-ssh")
SSH_HOME = os.environ.get("SSH_HOME", "/tmp/om-ssh-home")


def _bootstrap_ssh() -> None:
    # mounted secret files are root-owned world-readable; ssh needs an
    # owner-only copy in a HOME the running uid owns
    src = Path(SSH_MOUNT) / ".ssh"
    dst = Path(SSH_HOME) / ".ssh"
    if src.exists() and not (dst / "id_ed25519").exists():
        import shutil
        shutil.copytree(src, dst, dirs_exist_ok=True)
        dst.chmod(0o700)
        for f in dst.iterdir():
            f.chmod(0o600)


_bootstrap_ssh()
SSH_READY = Path(SSH_HOME) / ".ssh" / "id_ed25519"
IDLE_OFF_MINUTES = int(os.environ.get("IDLE_OFF_MINUTES", "45"))
COOKIE = "om_session"
GITHUB_API = "https://api.github.com"

app = FastAPI(title="octo-open-models backend", version="0.2.0")
templates = Jinja2Templates(directory=Path(__file__).resolve().parent / "templates")


def load_catalog() -> dict:
    with CATALOG_PATH.open() as fh:
        return yaml.safe_load(fh)


def load_gateway() -> dict:
    return load_catalog().get("gateway", {})


# ---------------------------------------------------------------- http utils

def http_json(url: str, token: str | None = None, method: str = "GET", body: dict | None = None, timeout: int = 20):
    headers = {"Accept": "application/json", "User-Agent": "octo-open-models"}
    data = None
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, headers=headers, data=data, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode()
            if url.startswith(GITHUB_API) and resp.status == 204:
                return None
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode()[:300]
        raise HTTPException(exc.code, detail) from exc


def litellm(path: str, method: str = "GET", body: dict | None = None):
    if not LITELLM_URL:
        raise HTTPException(503, "LITELLM_URL not configured")
    return http_json(f"{LITELLM_URL}{path}", LITELLM_MASTER_KEY, method, body)


# ---------------------------------------------------------------- sessions

def sign(payload: dict) -> str:
    raw = json.dumps(payload, sort_keys=True).encode()
    mac = hmac.new(SESSION_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    return mac + "." + urllib.parse.quote(raw.decode())


def verify(token: str) -> dict | None:
    try:
        mac, raw = token.split(".", 1)
        raw = urllib.parse.unquote(raw).encode()
        if not hmac.compare_digest(mac, hmac.new(SESSION_SECRET.encode(), raw, hashlib.sha256).hexdigest()):
            return None
        payload = json.loads(raw)
        if payload.get("exp", 0) < time.time():
            return None
        return payload
    except (ValueError, KeyError):
        return None


def current_user(request: Request) -> str:
    token = request.cookies.get(COOKIE, "")
    session = verify(token) if token else None
    if not session or not session.get("gh_user"):
        raise HTTPException(401, "not logged in")
    return session["gh_user"]


# ---------------------------------------------------------------- auth

@app.get("/auth/login")
def login(request: Request):
    if not GITHUB_CLIENT_ID:
        raise HTTPException(503, "GITHUB_CLIENT_ID not configured")
    state = secrets.token_hex(12)
    params = {
        "client_id": GITHUB_CLIENT_ID,
        "redirect_uri": str(request.url_for("callback")),
        "scope": "read:user read:org",
        "state": state,
    }
    resp = RedirectResponse("https://github.com/login/oauth/authorize?" + urllib.parse.urlencode(params))
    resp.set_cookie("om_oauth_state", state, httponly=True, samesite="lax", max_age=600)
    return resp


@app.get("/auth/callback")
def callback(request: Request, code: str = "", state: str = ""):
    if not code or state != request.cookies.get("om_oauth_state", "\0"):
        raise HTTPException(403, "state mismatch")
    tok = http_json(
        "https://github.com/login/oauth/access_token?" + urllib.parse.urlencode(
            {"client_id": GITHUB_CLIENT_ID, "client_secret": GITHUB_CLIENT_SECRET, "code": code}
        ),
        method="POST",
    )
    gh_token = (tok.get("access_token", "") if isinstance(tok, dict)
                else urllib.parse.parse_qs(tok or "").get("access_token", [""])[0])
    if not gh_token:
        raise HTTPException(403, "github token exchange failed")
    me = http_json(f"{GITHUB_API}/user", gh_token) or {}
    user = (me.get("login") or "").lower()
    if not user:
        raise HTTPException(403, "could not read github user")
    allowed = user in ALLOWED_USERS
    orgs = []
    if not allowed and GITHUB_ORG:
        orgs = http_json(f"{GITHUB_API}/user/orgs", gh_token) or []
        allowed = any((o.get("login") or "").lower() == GITHUB_ORG.lower() for o in orgs)
        if not allowed:
            print(f"[auth] {user} denied: token sees orgs {[o.get('login') for o in orgs]}")
    if not allowed:
        return HTMLResponse(
            f"""<!doctype html><html><head><title>OCTO Open Models - access needed</title></head>
<body style="font-family:sans-serif; max-width:38rem; margin:6rem auto; padding:0 1rem">
<h2>🐙 Almost there, {user}</h2>
<p>OCTO Open Models is gated on membership in the
<b><a href="https://github.com/{GITHUB_ORG}">{GITHUB_ORG}</a></b> GitHub org,
and your token doesn't see you as a member.</p>
<ul>
  <li>Are you in the org but private from API tokens? Visit
    <a href="https://github.com/settings/connections/applications">GitHub app
    settings</a> and grant <b>octo-models</b> (the OAuth app) access to the org,
    then log in again.</li>
  <li>Not a member yet? Ping <b>#octo-open-model-platform</b> in Slack and
    we'll get you invited - the models themselves just need an API key from
    this portal once you're in.</li>
</ul>
<p><a href="/">Back to the portal</a></p>
</body></html>""",
            status_code=403,
        )
    resp = RedirectResponse("/")
    resp.set_cookie(COOKIE, sign({"gh_user": user, "exp": time.time() + SESSION_TTL}),
                    httponly=True, samesite="lax", max_age=SESSION_TTL)
    resp.delete_cookie("om_oauth_state")
    return resp


@app.post("/auth/logout")
def logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(COOKIE)
    return resp


@app.get("/api/me")
def me(request: Request):
    return {"github_user": current_user(request), "org": GITHUB_ORG}


# ------------------------------------------------- health / power / savings

HEALTH: dict[str, dict] = {}
WAKE_TS: dict[str, float] = {}
BOOT_TS = time.time()
_STATS: dict = {}


def _notify(text: str) -> None:
    print(f"[alert] {text}")
    if not SLACK_WEBHOOK_URL:
        return
    try:
        http_json(SLACK_WEBHOOK_URL, method="POST", body={"text": text}, timeout=10)
    except Exception as exc:  # noqa: BLE001
        print(f"[alert] webhook failed: {exc}")


def _probe(model: dict) -> dict:
    root = (model.get("base_url") or "").rstrip("/").removesuffix("/v1")
    key = model.get("api_key") or "none"
    entry = {"status": "unknown", "latency_ms": None, "fails": HEALTH.get(model["id"], {}).get("fails", 0),
             "busy": HEALTH.get(model["id"], {}).get("busy") or time.time()}
    t0 = time.time()
    try:
        http_json(f"{root}/v1/models", key, timeout=8)
        entry["latency_ms"] = round((time.time() - t0) * 1000)
        entry["status"] = "slow" if entry["latency_ms"] > 2500 else "ok"
        entry["fails"] = 0
    except Exception:  # noqa: BLE001
        entry["status"] = "down"
        entry["fails"] += 1
    try:  # activity signal: vLLM /metrics -> running requests + completed counter
        req = urllib.request.Request(f"{root}/metrics", headers={"Authorization": f"Bearer {key}"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            body = resp.read().decode()
        running = sum(float(l.split()[-1]) for l in body.splitlines()
                      if l.startswith("vllm:num_requests_running"))
        done = sum(float(l.split()[-1]) for l in body.splitlines()
                   if l.startswith("vllm:request_success_total"))
        prev_done = HEALTH.get(model["id"], {}).get("done")
        if running > 0 or (prev_done is not None and done > prev_done):
            entry["busy"] = time.time()
        entry["done"] = done
        entry["running"] = int(running)
    except Exception:  # noqa: BLE001
        pass
    return entry


def _run_hook(cmd: str, timeout: int = 900) -> str:
    env = {**os.environ, "HOME": SSH_HOME if SSH_READY.exists() else os.environ.get("HOME", "/tmp")}
    try:
        r = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True,
                           timeout=timeout, cwd=REPO_ROOT, env=env)
        out = (r.stdout + r.stderr).strip()
        return f"rc={r.returncode} {out[-300:]}"
    except Exception as exc:  # noqa: BLE001
        return f"error: {exc}"


def _power_poll() -> None:
    time.sleep(5)
    while True:
        try:
            cat = load_catalog()
            hosts = cat.get("hosts") or {}
            for m in cat.get("models", []):
                if m.get("status") not in METERED_STATUSES or not m.get("base_url"):
                    continue
                prev = HEALTH.get(m["id"], {}).get("status")
                if prev == "sleeping":
                    continue
                entry = _probe(m)
                if prev == "waking" and entry["status"] in ("ok", "slow"):
                    _notify(f"OCTO: '{m['name']}' is awake and serving")
                HEALTH[m["id"]] = entry
                if prev == "ok" and entry["status"] == "down" and entry["fails"] >= 3:
                    _notify(f"OCTO: model '{m['name']}' ({m['id']}) is DOWN at {m['base_url']}")
                if prev == "down" and entry["status"] in ("ok", "slow"):
                    _notify(f"OCTO: model '{m['name']}' recovered")
                host = hosts.get(m.get("host") or "", {})
                if not host.get("idle_off") or not SSH_READY.exists() or not (host.get("compute") or {}).get("off"):
                    continue
                if time.time() - WAKE_TS.get(m.get("host", ""), 0) < IDLE_OFF_MINUTES * 60:
                    continue
                idle_for = time.time() - (entry.get("busy") or time.time())
                if time.time() - BOOT_TS < 3600:  # fresh pod: relearn busy history first
                    continue
                if entry["status"] in ("ok", "slow") and entry.get("done") is not None and idle_for > IDLE_OFF_MINUTES * 60:
                    _notify(f"OCTO: '{m['name']}' idle {int(idle_for // 60)}m -> powering off host {m['host']} (Wake button brings it back)")
                    print("[power] off:", _run_hook(f"python3 scripts/compute.py {m['id']} off", 120))
                    HEALTH[m["id"]] = {"status": "sleeping", "latency_ms": None, "fails": 0, "busy": 0}
        except Exception as exc:  # noqa: BLE001
            print(f"[power-poll] {exc}")
        time.sleep(60)


@app.on_event("startup")
def _start_poller() -> None:
    threading.Thread(target=_power_poll, daemon=True).start()


@app.get("/api/models/health")
def models_health():
    return HEALTH


class Power(BaseModel):
    model: str
    action: str  # "on" | "off"


@app.post("/api/power")
def power(request: Request, payload: Power):
    user = current_user(request)
    if payload.action not in ("on", "off"):
        raise HTTPException(422, "action must be on|off")
    cat = load_catalog()
    m = next((x for x in cat.get("models", []) if x["id"] == payload.model and x.get("host")), None)
    if not m:
        raise HTTPException(404, "no such model")
    host_cfg = (cat.get("hosts") or {}).get(m["host"], {})
    if not (host_cfg.get("compute") or {}).get(payload.action):
        raise HTTPException(422, "host has no compute hook")
    if not SSH_READY.exists():
        raise HTTPException(503, "backend has no ssh credentials mounted")
    if payload.action == "on":
        WAKE_TS[m["host"]] = time.time()
        HEALTH[m["id"]] = {"status": "waking", "latency_ms": None, "fails": 0,
                           "busy": time.time(), "done": None}
    else:
        HEALTH[m["id"]] = {"status": "sleeping", "latency_ms": None, "fails": 0, "busy": 0}
    def bg():
        print(f"[power] {payload.action} by {user}:", _run_hook(f"python3 scripts/compute.py {payload.model} {payload.action}"))
    threading.Thread(target=bg, daemon=True).start()
    return {"started": payload.action, "model": payload.model}


def _platform_spend() -> float:
    for path in ("/global/spend", "/global/spend/all"):
        try:
            data = litellm(path) or {}
            for k in ("total_spend", "spend"):
                if isinstance(data, dict) and k in data:
                    return float(data[k] or 0)
        except Exception:  # noqa: BLE001
            continue
    return float((litellm(f"/user/info?user_id={load_gateway().get('shared_litellm_user', 'octo-shared')}") or {})
                 .get("user_info", {}).get("spend", 0) or 0)


@app.get("/api/stats")
def stats():
    if _STATS and time.time() - _STATS.get("at", 0) < 120:
        return _STATS["body"]
    try:
        spend = _platform_spend()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(503, f"spend unavailable: {exc}") from exc
    mult = float(load_gateway().get("frontier_savings_multiplier", 8))
    body = {"spend_usd": round(spend, 4), "saved_usd": round(spend * mult, 2)}
    _STATS.clear(); _STATS.update(at=time.time(), body=body)
    return body


# ---------------------------------------------------------------- keys

class KeyCreate(BaseModel):
    name: str


ALIAS_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")


def owned_keys(user: str) -> list[dict]:
    # litellm >=1.90 /key/list returns hashed tokens only; hydrate each via
    # /key/info (cheap enough at our scale) and filter on our metadata tag.
    listing = litellm("/key/list?page_size=100") or {}
    out = []
    for token in listing.get("keys", []):
        if not isinstance(token, str):
            continue
        info = litellm(f"/key/info?key={token}") or {}
        data = info.get("info", info)
        if (data.get("metadata") or {}).get("github_user") == user:
            out.append(data)
    return out


@app.post("/api/keys")
def create_key(request: Request, payload: KeyCreate):
    user = current_user(request)
    alias = payload.name.lower().strip()
    if not ALIAS_RE.match(alias):
        raise HTTPException(422, "name must match [a-z0-9_-] (max 48 chars)")
    defaults = (load_gateway().get("key_defaults") or {})
    body = {
        "key_alias": alias,
        "user_id": load_gateway().get("shared_litellm_user", "octo-shared"),
        "metadata": {"github_user": user},
        "rpm_limit": defaults.get("rpm"),
        "tpm_limit": defaults.get("tpm"),
        "max_parallel_requests": defaults.get("max_parallel_requests"),
        "max_budget": defaults.get("max_budget"),
        "budget_duration": defaults.get("budget_duration"),
    }
    created = litellm("/key/generate", "POST", body)
    return {"key": created["key"], "key_alias": alias, "note": "copy it now; shown only once"}


@app.get("/api/keys")
def list_keys(request: Request):
    user = current_user(request)
    mult = float(load_gateway().get("frontier_savings_multiplier", 8))
    return [
        {
            "key_alias": k.get("key_alias"),
            "key_name": k.get("key_name") or k.get("token", "")[:10],
            "saved": round(float(k.get("spend", 0) or 0) * mult, 4),
            "created_at": k.get("created_at"),
        }
        for k in owned_keys(user)
    ]


@app.delete("/api/keys/{alias}")
def delete_key(request: Request, alias: str):
    user = current_user(request)
    if not any(k.get("key_alias") == alias for k in owned_keys(user)):
        raise HTTPException(404, "no such key")
    litellm("/key/delete", "POST", {"key_aliases": [alias]})
    return {"deleted": alias}


# ---------------------------------------------------------------- misc

METERED_STATUSES = {"running", "live"}


@app.get("/api/health")
def health():
    return {"status": "ok", "litellm": bool(LITELLM_URL), "github_oauth": bool(GITHUB_CLIENT_ID)}


@app.get("/api/catalog")
def catalog():
    cat = load_catalog()
    gw = cat.get("gateway", {})
    models = []
    for m in cat.get("models", []):
        if m.get("status") == "planned":
            continue
        models.append({
            "id": m["id"],
            "name": m["name"],
            "served_model": m.get("served_model"),
            "tier": m.get("tier"),
            "host": m.get("host"),
            "context": m.get("context"),
            "quant": m.get("quant"),
            "tool_call": bool(m.get("tool_call")),
            "reasoning": bool(m.get("reasoning")),
            "price": m.get("price") or {},
            "seats": m.get("seats"),
            "status": m.get("status"),
            "metered": m.get("status") in METERED_STATUSES,
            "gateway_model": m.get("group") if m.get("status") in METERED_STATUSES else None,
            "direct_url": None if m.get("status") in METERED_STATUSES or m.get("status") == "paused" else m.get("base_url"),
            "notes": m.get("notes", ""),
            "power": bool(((cat.get("hosts") or {}).get(m.get("host") or "") or {}).get("compute")),
        })
    order = {"frontier": 0, "core": 1, "bulk": 2, "lab": 3}
    models.sort(key=lambda m: order.get(m.get("tier"), 9))
    hosts = {k: {"hardware": v.get("hardware", ""), "wake_note": v.get("wake_note", "")}
             for k, v in (cat.get("hosts") or {}).items()}
    return {"gateway_url": gw.get("base_url", ""), "models": models, "hosts": hosts}


DOCS_DIR = REPO_ROOT / "docs"
DOC_PAGES = {
    "user-setup": ("Setup on your machine", "Install opencode + an OCTO key in one paste"),
    "model-selection": ("Choosing a model", "Which tier for which job"),
    "howto": ("Runbook", "How the platform is operated (also useful reading)"),
    "model-onboarding": ("Onboarding a model", "Hardware reqs, serving, catalog entry, verification"),
    "monitoring-howto": ("Monitoring a model", "Scrape targets, alerts, dashboards"),
    "contributing": ("Contributing", "Open to OCTO (and beyond) — models, providers, code; all the links"),
}


@app.get("/guide")
def docs_index():
    items = "".join(
        f'<li><a href="/guide/{k}"><b>{v[0]}</b></a><div class="sub">{v[1]}</div></li>'
        for k, v in DOC_PAGES.items() if (DOCS_DIR / f"{k}.md").exists())
    return HTMLResponse(f"<html><head><meta charset='utf-8'><title>OCTO docs</title><style>"
                        "body{font-family:sans-serif;max-width:46rem;margin:3rem auto;padding:0 1rem;color:#151515}"
                        "a{color:#0066cc}.sub{color:#6a6e73;font-size:.85rem;margin-bottom:1rem}</style></head>"
                        "<body><h2>🐙 OCTO Open Models - docs</h2><ul style='list-style:none;padding:0'>"
                        f"{items}</ul><p><a href='/'>← back to the portal</a></p></body></html>")


@app.get("/guide/{name}")
def docs_page(name: str):
    name = {"runbook": "howto"}.get(name, name)  # landing page calls it "runbook"
    if name not in DOC_PAGES or not (DOCS_DIR / f"{name}.md").exists():
        raise HTTPException(404, "no such doc")
    import markdown as md
    body = md.markdown((DOCS_DIR / f"{name}.md").read_text(),
                       extensions=["tables", "fenced_code"])
    return HTMLResponse("<html><head><meta charset='utf-8'><title>OCTO docs</title><style>"
                        "body{font-family:sans-serif;max-width:46rem;margin:3rem auto;padding:0 1.5rem;color:#151515;line-height:1.5}"
                        "a{color:#0066cc}pre{background:#151515;color:#e0e0e0;padding:.8rem;border-radius:6px;overflow-x:auto}"
                        "code{background:#f0f0f0;padding:.1rem .3rem;border-radius:3px}pre code{background:none;padding:0}"
                        "table{border-collapse:collapse}td,th{border:1px solid #d2d2d2;padding:.3rem .6rem}"
                        "h1,h2{border-bottom:1px solid #eee;padding-bottom:.2rem}</style></head>"
                        f"<body><p><a href='/docs'>← docs</a> · <a href='/'>portal</a></p>{body}</body></html>")


@app.get("/setup.sh")
def setup_sh():
    return FileResponse(REPO_ROOT / "scripts" / "setup-user.sh",
                        media_type="text/plain; charset=utf-8")


@app.get("/setup/models.json")
def setup_models():
    cat = load_catalog()
    live = [m for m in cat.get("models", []) if m.get("status") in METERED_STATUSES]
    groups: dict = {}
    tiers: dict = {}
    for m in live:
        g = m.get("group", m["id"])
        tiers[g] = m.get("tier")
        cand = {
            "name": m.get("name", g),
            "limit": {"context": m.get("context", 8192), "output": m.get("max_output", 8192)},
            "tool_call": bool(m.get("tool_call")),
            "reasoning": bool(m.get("reasoning")),
            "temperature": True,
        }
        if g not in groups or cand["limit"]["context"] > groups[g]["limit"]["context"]:
            groups[g] = cand
    pick = lambda tier: next((g for g, t in tiers.items() if t == tier), None)
    default = (pick("core") or pick("frontier") or pick("bulk")
               or (next(iter(groups), None)))
    hosts = cat.get("hosts") or {}
    lab = []
    for m in cat.get("models", []):
        if (m.get("tier") == "lab" and m.get("base_url")
                and m.get("status") in {"running", "live", "listed"}):
            served = m.get("served_model") or m["id"]
            lab.append({
                "provider": "octo-" + m["id"],
                "name": f"OCTO Open Models ({(hosts.get(m.get('host')) or {}).get('hardware') or m.get('host')} - direct/unmetered, VPN)",
                "base_url": m["base_url"],
                "api_key": m.get("api_key") or "none",
                "served_model": served,
                "model": {
                    "name": m.get("name", served),
                    "limit": {"context": m.get("context", 8192), "output": m.get("max_output", 8192)},
                    "tool_call": bool(m.get("tool_call")),
                    "reasoning": bool(m.get("reasoning")),
                    "temperature": True,
                },
            })
    return {"models": groups, "default": default, "lab": lab,
            "small": pick("bulk") or default}


@app.get("/")
def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {})


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8020)


if __name__ == "__main__":
    main()
