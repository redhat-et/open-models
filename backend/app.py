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
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import yaml
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
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
        raise HTTPException(403, f"{user} is not a member of {GITHUB_ORG or 'the allowlist'}")
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
            "status": m.get("status"),
            "metered": m.get("status") in METERED_STATUSES,
            "gateway_model": m.get("group") if m.get("status") in METERED_STATUSES else None,
            "direct_url": None if m.get("status") in METERED_STATUSES or m.get("status") == "paused" else m.get("base_url"),
            "notes": m.get("notes", ""),
        })
    order = {"frontier": 0, "core": 1, "bulk": 2, "lab": 3}
    models.sort(key=lambda m: order.get(m.get("tier"), 9))
    hosts = {k: v.get("hardware", "") for k, v in (cat.get("hosts") or {}).items()}
    return {"gateway_url": gw.get("base_url", ""), "models": models, "hosts": hosts}


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
    return {"models": groups, "default": default,
            "small": pick("bulk") or default}


@app.get("/")
def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {})


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8020)


if __name__ == "__main__":
    main()
