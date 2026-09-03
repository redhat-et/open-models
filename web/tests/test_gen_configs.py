import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "gen_configs.py"
spec = importlib.util.spec_from_file_location("gen_configs", SCRIPT)
gen_configs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gen_configs)

CATALOG = {
    "platform": {"name": "Test", "dashboard_base": "http://g:3000"},
    "hosts": {"box1": {"hardware": "DGX Spark"}},
    "models": [
        {
            "id": "fast_one",
            "host": "box1",
            "name": "Fast One",
            "served_model": "Test/Fast",
            "tier": "fast",
            "base_url": "http://box1:8000/v1",
            "api_key": "sk-local",
            "context": 131072,
            "max_output": 4096,
            "tool_call": True,
            "reasoning": False,
            "status": "running",
        },
        {
            "id": "planned_one",
            "host": "box1",
            "name": "Planned One",
            "served_model": "Test/Planned",
            "tier": "balanced",
            "base_url": "http://box1:8001/v1",
            "context": 8192,
            "status": "planned",
        },
    ],
}


def test_only_live_models_become_providers():
    cfg = gen_configs.opencode_config(CATALOG)
    assert list(cfg["provider"]) == ["octo-fast-one"]
    prov = cfg["provider"]["octo-fast-one"]
    assert prov["npm"] == "@ai-sdk/openai-compatible"
    assert prov["options"]["baseURL"] == "http://box1:8000/v1"
    assert prov["options"]["apiKey"] == "sk-local"
    model = prov["models"]["Test/Fast"]
    assert model["limit"] == {"context": 131072, "output": 4096}
    assert model["tool_call"] is True
    assert model["reasoning"] is False


def test_model_defaults_use_tiers():
    catalog = json.loads(json.dumps(CATALOG))
    catalog["models"][1]["status"] = "running"
    cfg = gen_configs.opencode_config(catalog)
    assert cfg["small_model"] == "octo-fast-one/Test/Fast"
    assert cfg["model"] == "octo-planned-one/Test/Planned"


def test_schema_key_present():
    cfg = gen_configs.opencode_config(CATALOG)
    assert cfg["$schema"] == "https://opencode.ai/config.json"


def test_open_webui_env_lists_live_endpoints():
    env = gen_configs.open_webui_env(CATALOG)
    assert "http://box1:8000/v1" in env
    assert "http://box1:8001/v1" not in env


def test_generated_configs_match_shipped_catalog():
    root = Path(__file__).resolve().parents[2]
    cfg_path = root / "configs" / "opencode.json"
    if not cfg_path.exists():
        pytest.skip("run scripts/gen_configs.py first")
    shipped = json.loads(cfg_path.read_text())
    expected = gen_configs.opencode_config(gen_configs.load_catalog(root / "catalog.yaml"))
    assert shipped == expected
