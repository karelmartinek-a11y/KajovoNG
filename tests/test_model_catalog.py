import json

from kajovo.core.model_catalog import ModelCatalogCache
from kajovo.core.model_registry import model_spec, models_for_usage, recommended_model


def test_account_catalog_persists_details_without_api_key(tmp_path):
    path = tmp_path / "model_catalog.json"
    cache = ModelCatalogCache(path)
    saved = cache.save("sk-test-secret", [{
        "id": "gpt-6-astra",
        "object": "model",
        "created": 123,
        "owned_by": "openai",
        "ignored": "not-persisted",
    }])
    assert "gpt-6-astra" in saved["models"]
    raw = path.read_text(encoding="utf-8")
    assert "sk-test-secret" not in raw
    assert "ignored" not in raw
    data = json.loads(raw)
    assert data["models"]["gpt-6-astra"]["capabilities"]["responses"] is True
    assert "generate_file" in data["models"]["gpt-6-astra"]["usages"]

    loaded = cache.load("sk-test-secret")
    assert loaded["models"]["gpt-6-astra"]["catalog"]["owned_by"] == "openai"
    assert loaded["models"]["gpt-6-astra"]["capabilities"]["responses"] is True
    assert cache.load("different-account")["models"] == {}


def test_usage_profiles_filter_account_models_and_choose_recommendation():
    available = ["gpt-5.6-luna", "gpt-6-astra", "gpt-image-2"]
    assert models_for_usage(available, "generate_file") == [
        "gpt-6-astra",
        "gpt-5.6-luna",
    ]
    assert recommended_model(available, "generate_file") == "gpt-6-astra"
    assert models_for_usage(available, "photo_edit_batch") == ["gpt-image-2"]
    assert recommended_model(available, "photo_edit_batch") == "gpt-image-2"


def test_model_matrix_accepts_documented_dated_alias_identity():
    spec = model_spec("computer-use-preview-2025-03-11")
    assert spec["canonical"] == "computer-use-preview"



def test_corrupt_or_ambiguous_catalog_cache_is_ignored(tmp_path):
    path = tmp_path / "model_catalog.json"
    path.write_text(
        '{"schema_version":1,"schema_version":1,"models":{}}',
        encoding="utf-8",
    )
    loaded = ModelCatalogCache(path).load("sk-test-secret")
    assert loaded["models"] == {}
    assert loaded["fetched_at"] == 0.0
