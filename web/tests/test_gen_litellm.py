import importlib.util
from pathlib import Path

import pytest
import yaml

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "gen_litellm_config.py"
spec = importlib.util.spec_from_file_location("gen_litellm_config", SCRIPT)
gen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gen)

CATALOG = {
    "gateway": {
        "fallbacks": {"smart": ["fast"], "fast": ["smart"], "gone": ["fast"]},
    },
    "models": [
        {"id": "a", "served_model": "T/A", "group": "smart", "base_url": "http://a/v1",
         "api_key": "sk-local", "context": 262144, "max_output": 8192, "status": "running"},
        {"id": "b", "served_model": "T/B", "group": "fast", "base_url": "http://b/v1",
         "status": "running"},
        {"id": "c", "served_model": "T/C", "group": "fast", "base_url": "http://c/v1",
         "status": "running"},
        {"id": "d", "served_model": "T/D", "group": "smart", "base_url": "http://d/v1",
         "status": "planned"},
        {"id": "e", "served_model": "T/E", "group": "fast", "base_url": "http://e/v1",
         "status": "paused"},
    ],
}


def test_live_models_only_and_group_aliasing():
    cfg = gen.litellm_config(CATALOG)
    names = [m["model_name"] for m in cfg["model_list"]]
    assert names == ["smart", "fast", "fast"]  # planned skipped, aliasing kept
    first = cfg["model_list"][0]["litellm_params"]
    assert first["model"] == "openai/T/A"
    assert first["api_base"] == "http://a/v1"
    assert first["max_tokens"] == 8192


def test_fallbacks_only_reference_existing_groups():
    cfg = gen.litellm_config(CATALOG)
    fb = cfg["router_settings"]["fallbacks"]
    flat = {k for d in fb for k in d} | {t for d in fb for v in d.values() for t in v}
    assert "gone" not in flat
    assert {"smart": ["fast"]} in fb


def test_master_key_from_env():
    cfg = gen.litellm_config(CATALOG)
    assert cfg["litellm_settings"]["master_key"] == "os.environ/LITELLM_MASTER_KEY"
    assert cfg["general_settings"]["database_url"] == "os.environ/DATABASE_URL"


def test_generated_config_matches_shipped_catalog():
    root = Path(__file__).resolve().parents[2]
    shipped_path = root / "deployments" / "gateway" / "litellm-config.yaml"
    if not shipped_path.exists():
        pytest.skip("run scripts/gen_litellm_config.py first")
    shipped = yaml.safe_load(shipped_path.read_text())
    expected = gen.litellm_config(gen.load_catalog(root / "catalog.yaml"))
    assert shipped == expected


def test_shipped_config_groups_and_fallback_chain():
    root = Path(__file__).resolve().parents[2]
    shipped_path = root / "deployments" / "gateway" / "litellm-config.yaml"
    if not shipped_path.exists():
        pytest.skip("run scripts/gen_litellm_config.py first")
    cfg = yaml.safe_load(shipped_path.read_text())
    groups = {m["model_name"] for m in cfg["model_list"]}
    assert groups == {"qwen38-flash-next", "qwen38-27b-fast", "glm-45-air"}
    chain = {k: v for fb in cfg["router_settings"]["fallbacks"] for k, v in fb.items()}
    assert chain["qwen38-27b-fast"] == ["qwen38-flash-next"]
    assert chain["glm-45-air"] == ["qwen38-flash-next"]
    assert "qwen38-flash-next" not in chain  # acyclic: core is the floor


def test_paused_models_excluded():
    cfg = gen.litellm_config(CATALOG)
    ids = {m["model_info"]["id"] for m in cfg["model_list"]}
    assert "e" not in ids
    assert {"b", "c"} <= ids
