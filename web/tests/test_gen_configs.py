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
    "gateway": {"base_url": "https://gw.test/v1"},
    "hosts": {"box1": {"hardware": "DGX Spark"}},
    "models": [
        {
            "id": "fast_one",
            "host": "box1",
            "name": "Fast One",
            "served_model": "Test/Fast",
            "group": "test-fast",
            "tier": "core",
            "base_url": "http://box1:8000/v1",
            "api_key": "sk-local",
            "context": 131072,
            "max_output": 4096,
            "tool_call": True,
            "reasoning": False,
            "status": "running",
        },
        {
            "id": "direct_one",
            "host": "box1",
            "name": "Direct One",
            "served_model": "Test/Direct",
            "group": "test-direct",
            "tier": "frontier",
            "base_url": "http://box1:8001/v1",
            "api_key": "none",
            "context": 8192,
            "status": "listed",
        },
        {
            "id": "planned_one",
            "host": "box1",
            "name": "Planned One",
            "served_model": "Test/Planned",
            "group": "test-planned",
            "tier": "bulk",
            "base_url": "http://box1:8002/v1",
            "context": 8192,
            "status": "planned",
        },
    ],
}


def test_metered_models_collapse_into_gateway_provider():
    cfg = gen_configs.opencode_config(CATALOG)
    assert list(cfg["provider"]) == ["octo-gateway", "octo-direct-one"]
    gw = cfg["provider"]["octo-gateway"]
    assert gw["options"]["baseURL"] == "https://gw.test/v1"
    assert gw["options"]["apiKey"] == "{env:OCTO_KEY}"
    prov = gw["models"]["test-fast"]
    assert prov["limit"] == {"context": 131072, "output": 4096}
    assert prov["tool_call"] is True
    assert prov["reasoning"] is False


def test_listed_models_stay_direct():
    cfg = gen_configs.opencode_config(CATALOG)
    direct = cfg["provider"]["octo-direct-one"]
    assert direct["options"]["baseURL"] == "http://box1:8001/v1"
    assert direct["options"]["apiKey"] == "none"
    assert "VPN" in direct["name"]
    assert "test-direct" not in cfg["provider"]["octo-gateway"]["models"]


def test_model_defaults_use_tiers_via_gateway():
    catalog = json.loads(json.dumps(CATALOG))
    catalog["models"][2]["status"] = "running"
    cfg = gen_configs.opencode_config(catalog)
    assert cfg["model"] == "octo-gateway/test-fast"
    assert cfg["small_model"] == "octo-gateway/test-planned"


def test_schema_key_present():
    cfg = gen_configs.opencode_config(CATALOG)
    assert cfg["$schema"] == "https://opencode.ai/config.json"


def test_open_webui_env_gateway_first_with_direct_notes():
    env = gen_configs.open_webui_env(CATALOG)
    assert "https://gw.test/v1" in env
    assert "Model ID     : test-fast" in env
    assert "http://box1:8001/v1" in env  # listed shown as direct note
    assert "http://box1:8002/v1" not in env  # planned excluded


def test_generated_configs_match_shipped_catalog():
    root = Path(__file__).resolve().parents[2]
    cfg_path = root / "configs" / "opencode.json"
    if not cfg_path.exists():
        pytest.skip("run scripts/gen_configs.py first")
    shipped = json.loads(cfg_path.read_text())
    expected = gen_configs.opencode_config(gen_configs.load_catalog(root / "catalog.yaml"))
    assert shipped == expected


def test_byo_key_models_stay_out_of_shipped_configs():
    root = Path(__file__).resolve().parents[2]
    oc = (root / "configs" / "opencode.json").read_text()
    env = (root / "configs" / "open-webui.env").read_text()
    assert "mc-qwen38-27b" not in oc and "models-corp" not in oc
    assert "mc-qwen38-27b" not in env and "apicast" not in env
