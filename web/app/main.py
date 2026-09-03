from __future__ import annotations

import json
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from .catalog import CONFIGS_DIR, load_catalog, probeable_models

WEB_DIR = Path(__file__).resolve().parents[1]

app = FastAPI(title="OCTO Open Models", version="0.1.0")
app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")
templates = Jinja2Templates(directory=WEB_DIR / "templates")

PROBE_TIMEOUT_SECONDS = 3


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/api/models")
def models() -> dict:
    return load_catalog()


def probe_model(model: dict) -> dict:
    url = model["base_url"].rstrip("/") + "/models"
    result = {"id": model["id"], "status": "down", "detail": ""}
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=PROBE_TIMEOUT_SECONDS) as resp:
            body = json.loads(resp.read().decode())
        served = [m.get("id") for m in body.get("data", [])]
        result["status"] = "up"
        result["detail"] = ", ".join(x for x in served if x)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        result["detail"] = str(exc)
    return result


@app.get("/api/status")
def status() -> list[dict]:
    catalog = load_catalog()
    targets = probeable_models(catalog)
    with ThreadPoolExecutor(max_workers=min(8, max(1, len(targets) or 1))) as pool:
        probed = list(pool.map(probe_model, targets))
    by_id = {p["id"]: p for p in probed}
    out = []
    for model in catalog.get("models", []):
        if model["id"] in by_id:
            out.append(by_id[model["id"]])
        else:
            out.append({"id": model["id"], "status": "planned", "detail": ""})
    return out


@app.get("/configs/opencode.json")
def opencode_config():
    path = CONFIGS_DIR / "opencode.json"
    if not path.exists():
        return {"error": "configs/opencode.json missing; run scripts/gen_configs.py"}
    return FileResponse(path, media_type="application/json", filename="opencode.json")


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    catalog = load_catalog()
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "platform": catalog.get("platform", {}),
            "models": catalog.get("models", []),
            "hosts": catalog.get("hosts", {}),
        },
    )


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
