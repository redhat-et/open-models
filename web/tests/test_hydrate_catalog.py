import importlib.util
from pathlib import Path

import yaml

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "hydrate_catalog.py"
spec = importlib.util.spec_from_file_location("hydrate_catalog", SCRIPT)
hydrate_catalog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hydrate_catalog)


def slim_catalog():
    return {
        "models": [
            {
                "id": "reachable-with-cap",
                "host": "box1",
                "base_url": "http://box1:8000/v1",
                "api_key": "sk-local",
                "served_model": "Old/Stale",
                "context": 32768,  # e.g. deliberately capped below the box's native max
                "status": "planned",
            },
            {
                "id": "reachable-no-context-yet",
                "host": "box1",
                "base_url": "http://box1:8000/v1",
                "status": "planned",
            },
            {
                "id": "unreachable-with-fallback",
                "host": "box1",
                "base_url": "http://box2:8000/v1",
                "served_model": "Hand/Fallback",
                "context": 4096,
                "status": "planned",
            },
            {
                "id": "no-base-url-yet",
                "host": "box1",
                "served_model": "Placeholder/Model",
                "context": 8192,
                "status": "planned",
            },
        ]
    }


def fake_fetch(reachable_url, response):
    def _fetch(base_url, api_key):
        if base_url != reachable_url:
            raise OSError("connection refused")
        assert api_key in ("sk-local", None)
        return response
    return _fetch


def test_hydrate_always_overwrites_served_model_but_never_an_existing_context_cap(monkeypatch):
    monkeypatch.setattr(hydrate_catalog, "fetch_model_info",
                         fake_fetch("http://box1:8000/v1", {"id": "Live/Model", "max_model_len": 75264}))

    catalog = slim_catalog()
    hydrate_catalog.hydrate(catalog)

    capped = catalog["models"][0]
    assert capped["served_model"] == "Live/Model"
    assert capped["context"] == 32768, "an existing context is a deliberate cap, never raised automatically"


def test_hydrate_fills_context_only_when_missing(monkeypatch):
    monkeypatch.setattr(hydrate_catalog, "fetch_model_info",
                         fake_fetch("http://box1:8000/v1", {"id": "Live/Model", "max_model_len": 75264}))

    catalog = slim_catalog()
    hydrate_catalog.hydrate(catalog)

    fresh = catalog["models"][1]
    assert fresh["served_model"] == "Live/Model"
    assert fresh["context"] == 75264


def test_hydrate_falls_back_to_slim_values_when_unreachable(monkeypatch):
    monkeypatch.setattr(hydrate_catalog, "fetch_model_info",
                         fake_fetch("http://box1:8000/v1", {"id": "Live/Model", "max_model_len": 131072}))

    catalog = slim_catalog()
    failed = hydrate_catalog.hydrate(catalog)

    unreachable = catalog["models"][2]
    assert unreachable["served_model"] == "Hand/Fallback"
    assert unreachable["context"] == 4096
    assert len(failed) == 1
    assert "unreachable-with-fallback" in failed[0]


def test_hydrate_skips_models_without_a_base_url():
    def fail_if_called(base_url, api_key):
        raise AssertionError("should not query a model with no base_url")

    catalog = {"models": [{
        "id": "no-base-url-yet",
        "host": "box1",
        "served_model": "Placeholder/Model",
        "context": 8192,
        "status": "planned",
    }]}
    orig_fetch = hydrate_catalog.fetch_model_info
    hydrate_catalog.fetch_model_info = fail_if_called
    try:
        failed = hydrate_catalog.hydrate(catalog)
    finally:
        hydrate_catalog.fetch_model_info = orig_fetch

    untouched = catalog["models"][0]
    assert untouched["served_model"] == "Placeholder/Model"
    assert untouched["context"] == 8192
    assert failed == []


def minimal_slim_yaml(model_lines: str) -> str:
    return (
        "# hand-written comment\n"
        "platform: {name: Test}\n"
        "gateway: {base_url: https://gw.test/v1}\n"
        "hosts:\n  box1: {hardware: test box}\n"
        "models:\n"
        f"  - {model_lines}\n"
    )


def test_main_writes_a_distinct_generated_output_file(tmp_path, monkeypatch):
    monkeypatch.setattr(hydrate_catalog, "fetch_model_info",
                         lambda base_url, api_key: {"id": "Fetched/Model", "max_model_len": 8192})

    slim_path = tmp_path / "catalog.slim.yaml"
    out_path = tmp_path / "catalog.yaml"
    slim_path.write_text(minimal_slim_yaml(
        "id: new\n    host: box1\n    name: New\n    tier: lab\n    status: planned\n"
        "    base_url: http://box1:8000/v1"
    ))

    rc = hydrate_catalog.main(["hydrate_catalog.py", str(slim_path), str(out_path)])

    assert rc == 0
    assert slim_path.read_text().startswith("# hand-written comment\n")
    written = out_path.read_text()
    assert written.startswith("# generated by scripts/hydrate_catalog.py")
    data = yaml.safe_load(written)
    model = data["models"][0]
    assert model["served_model"] == "Fetched/Model"
    assert model["context"] == 8192
    # defaults materialized from the schema, not spelled out in slim:
    assert model["tool_call"] is True
    assert model["reasoning"] is True
    assert model["max_output"] == 8192
    assert model["group"] == "new"


def test_main_succeeds_when_a_box_is_unreachable_but_slim_has_a_fallback(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(hydrate_catalog, "fetch_model_info",
                         lambda base_url, api_key: (_ for _ in ()).throw(OSError("connection refused")))

    slim_path = tmp_path / "catalog.slim.yaml"
    out_path = tmp_path / "catalog.yaml"
    slim_path.write_text(minimal_slim_yaml(
        "id: paused-box\n    host: box1\n    name: Paused\n    tier: lab\n    status: planned\n"
        "    base_url: http://box1:8000/v1\n    served_model: Fallback/Model\n    context: 4096"
    ))

    rc = hydrate_catalog.main(["hydrate_catalog.py", str(slim_path), str(out_path)])

    # a box being routinely powered off must not break `make gen`/`make deploy`
    assert rc == 0
    assert out_path.exists()
    assert "connection refused" in capsys.readouterr().err


def test_main_fails_without_writing_when_slim_is_invalid(tmp_path):
    slim_path = tmp_path / "catalog.slim.yaml"
    out_path = tmp_path / "catalog.yaml"
    # no base_url, no way to hydrate served_model/context -> invalid
    slim_path.write_text(minimal_slim_yaml("id: broken\n    host: box1\n    name: Broken\n    tier: lab\n    status: planned"))

    rc = hydrate_catalog.main(["hydrate_catalog.py", str(slim_path), str(out_path)])

    assert rc == 1
    assert not out_path.exists()


def test_byo_key_models_never_probed(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("byo_key models must not be probed")
    monkeypatch.setattr(hydrate_catalog, "fetch_model_info", boom)
    catalog = {"models": [{"id": "x", "tier": "external", "byo_key": True,
                           "base_url": "https://example.invalid/v1",
                           "served_model": "keep/me", "context": 123}]}
    assert hydrate_catalog.hydrate(catalog) == []
