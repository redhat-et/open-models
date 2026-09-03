from app.catalog import load_catalog, probeable_models, validate_catalog


def test_shipped_catalog_is_valid():
    catalog = load_catalog()
    assert validate_catalog(catalog) == []


def test_catalog_has_platform_and_models():
    catalog = load_catalog()
    assert catalog["platform"]["name"]
    assert len(catalog["models"]) >= 3


def test_model_ids_unique():
    catalog = load_catalog()
    ids = [m["id"] for m in catalog["models"]]
    assert len(ids) == len(set(ids))


def test_probeable_models_excludes_planned():
    catalog = load_catalog()
    for model in probeable_models(catalog):
        assert model["status"] in {"running", "live", "candidate"}


def test_validate_catalog_flags_bad_host():
    catalog = load_catalog()
    catalog["models"][0]["host"] = "nonexistent-host"
    errors = validate_catalog(catalog)
    assert any("unknown host" in e for e in errors)
