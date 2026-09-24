"""Regrese kanonických dat, diagnostiky a lokálních prostředků."""
import json
from dataclasses import replace
from pathlib import Path

import pytest
from PIL import Image

from kajovo.core import secret_store
from kajovo.core.safe_config import persist_evidence
from kajovo.core.project_git import allowed_file
from kajovo.core.runlog import RunLogger
from kajovo.core.orchestration.work_order import work_order_from_mapping
from test_orchestration_repository import _order


@pytest.mark.parametrize("value", [{"type": ["a"]}, {"type": {"a": 1}}])
def test_domain_type_is_not_assumed_to_be_schema(value):
    assert persist_evidence(value) == value


def test_canonical_order_and_cascade_values_roundtrip(tmp_path):
    order = replace(_order("RUN-1", "sk-abcdefgh123", "unused"), target_path="sk-abcdefgh123.txt")
    raw = {**order.to_dict(), "order_hash": order.order_hash}
    log = RunLogger(str(tmp_path), "RUN_TEST")
    log.save_json("manifests", "work_order", raw)
    saved = json.loads(Path(log.find_json("manifests", "work_order")).read_text("utf-8"))
    assert work_order_from_mapping(saved) == order
    values = {"x": {"kind": "json", "value": {"token": "domain-value", "type": ["a"]}}}
    assert persist_evidence({"values": values}) == {"values": values}
    snapshot = {"structure": {"files": [{"path": "sk-abcdefgh123.txt", "purpose": "token=domain"}]}}
    assert persist_evidence({"snapshot": snapshot}) == {"snapshot": snapshot}


def test_exception_redacts_all_event_fields(tmp_path):
    log = RunLogger(str(tmp_path), "RUN_TEST")
    try:
        raise RuntimeError("password=do-not-leak sk-abcdefgh123")
    except RuntimeError as error:
        log.exception("example", error)
    events = log.bundle.events_path.read_text("utf-8")
    assert "do-not-leak" not in events
    assert "sk-abcdefgh123" not in events


@pytest.mark.parametrize("path", [".ENV", "KAJOVO_SETTINGS.JSON", "log/a.txt", "Cache/a.txt", "build/LIB/a.py", "BUILD/kajovo/a.py", "build/BDIST.win/a.py"])
def test_git_filter_is_case_insensitive(path):
    assert not allowed_file(path)
    assert allowed_file("src/main.py")


@pytest.mark.parametrize("key", ["valid-key", secret_store._API_KEY_EMPTY_SENTINEL])
def test_valid_credential_survives_legacy_read_failure(monkeypatch, key):
    monkeypatch.setattr(secret_store, "_read_keyring_api_key_record", lambda: key)
    def inaccessible():
        raise secret_store.APIKeyStoreError("Nečitelný registr")
    monkeypatch.setattr(secret_store, "_read_persisted_api_key", inaccessible)
    monkeypatch.setenv("OPENAI_API_KEY", "stale")
    with pytest.warns(RuntimeWarning):
        assert secret_store.load_api_key() == ("" if key == secret_store._API_KEY_EMPTY_SENTINEL else key)


def test_retina_icon_has_1024_pixels(tmp_path, monkeypatch):
    from Build import generate_icons
    source = tmp_path / "source.png"
    Image.new("RGBA", (1024, 1024)).save(source)
    monkeypatch.setattr(generate_icons, "SOURCE_LOGO", source)
    monkeypatch.setattr(generate_icons, "BUILD_ASSETS", tmp_path / "assets")
    monkeypatch.setattr(generate_icons, "RESOURCES", tmp_path / "resources")
    generate_icons.main()
    with Image.open(tmp_path / "assets/app_icon_1024.png") as image:
        assert image.size == (1024, 1024)
