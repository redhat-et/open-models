from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CATALOG_PATH = REPO_ROOT / "catalog.yaml"
CONFIGS_DIR = REPO_ROOT / "configs"

REQUIRED_MODEL_KEYS = (
    "id",
    "host",
    "name",
    "served_model",
    "tier",
    "base_url",
    "context",
    "status",
)

PROBE_STATUSES = {"running", "live", "candidate"}


def load_catalog(path: Path | None = None) -> dict:
    with (path or CATALOG_PATH).open() as fh:
        return yaml.safe_load(fh)


def validate_catalog(catalog: dict) -> list[str]:
    errors: list[str] = []
    models = catalog.get("models") or []
    hosts = catalog.get("hosts") or {}
    ids = [m.get("id") for m in models]
    if len(ids) != len(set(ids)):
        errors.append("duplicate model ids in catalog")
    for model in models:
        for key in REQUIRED_MODEL_KEYS:
            if key not in model:
                errors.append(f"model {model.get('id', '<missing id>')} missing key: {key}")
        if not str(model.get("base_url", "")).startswith("http"):
            errors.append(f"model {model.get('id')} base_url is not a URL: {model.get('base_url')}")
        if model.get("host") not in hosts:
            errors.append(f"model {model.get('id')} references unknown host: {model.get('host')}")
    return errors


def probeable_models(catalog: dict) -> list[dict]:
    return [m for m in catalog.get("models", []) if m.get("status") in PROBE_STATUSES]
