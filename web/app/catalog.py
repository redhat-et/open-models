from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

REPO_ROOT = Path(__file__).resolve().parents[2]
CATALOG_PATH = REPO_ROOT / "catalog.yaml"
CONFIGS_DIR = REPO_ROOT / "configs"

PROBE_STATUSES = {"running", "live", "candidate", "listed"}
UNMETERED_STATUSES = {"listed"}


class Host(BaseModel):
    # forbid, not ignore: an unmodeled field must fail validation, not
    # silently vanish from the generated catalog.yaml on model_dump().
    model_config = ConfigDict(extra="forbid")

    hardware: str
    notes: str | None = None
    wake_note: str | None = None
    compute: dict[str, str] | None = None
    idle_off: bool = False


class ModelEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    host: str
    name: str
    base_url: str
    tier: str
    status: str
    # scripts/hydrate_catalog.py fills these from the box's live /v1/models
    # if catalog.slim.yaml leaves them out - always required in the generated
    # catalog.yaml though, even for `planned` entries (placeholder values are
    # fine - consumers like the portal card assume every model has both).
    served_model: str
    context: int
    group: str | None = None
    price: dict[str, float] | None = None
    engine: dict[str, Any] | None = None
    api_key: str | None = None
    scrape_url: str | None = None
    max_output: int = 8192
    # all recent models support these - only set False for one that doesn't.
    tool_call: bool = True
    reasoning: bool = True
    quant: str | None = None
    seats: int | None = None
    # external tier: the serving side hands out its own tokens - we link, never store
    byo_key: bool = False
    signup_url: str | None = None
    docs_url: str | None = None
    notes: str | None = None
    previous_status: str | None = None

    @model_validator(mode="after")
    def _group_defaults_to_id(self) -> ModelEntry:
        if self.group is None:
            self.group = self.id
        return self

    @model_validator(mode="after")
    def _base_url_is_a_url(self) -> ModelEntry:
        if not self.base_url.startswith("http"):
            raise ValueError(f"model {self.id} base_url is not a URL: {self.base_url}")
        return self


class Catalog(BaseModel):
    platform: dict[str, Any]
    defaults: dict[str, Any] | None = None
    gateway: dict[str, Any]
    hosts: dict[str, Host]
    models: list[ModelEntry]

    @model_validator(mode="after")
    def _cross_reference_hosts_and_ids(self) -> Catalog:
        ids = [m.id for m in self.models]
        dupes = {i for i in ids if ids.count(i) > 1}
        if dupes:
            raise ValueError(f"duplicate model ids in catalog: {sorted(dupes)}")
        for m in self.models:
            if m.host not in self.hosts:
                raise ValueError(f"model {m.id} references unknown host: {m.host}")
        return self


def load_catalog(path: Path | None = None) -> dict:
    with (path or CATALOG_PATH).open() as fh:
        return yaml.safe_load(fh)


def format_validation_errors(e: ValidationError) -> list[str]:
    errors = []
    for err in e.errors():
        if err["type"] == "value_error":
            errors.append(err["msg"].removeprefix("Value error, "))
        else:
            loc = ".".join(str(p) for p in err["loc"])
            errors.append(f"{loc}: {err['msg']}")
    return errors


def validate_catalog(catalog: dict) -> list[str]:
    try:
        Catalog.model_validate(catalog)
    except ValidationError as e:
        return format_validation_errors(e)
    return []


def probeable_models(catalog: dict) -> list[dict]:
    return [m for m in catalog.get("models", []) if m.get("status") in PROBE_STATUSES]
