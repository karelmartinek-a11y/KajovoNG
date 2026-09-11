import json
from unittest.mock import patch

import pytest

from kajovo.core.config import load_settings
from kajovo.core.secret_store import get_secret, set_secret


@pytest.mark.parametrize("raw", [{"retry": {"max_attempts": 0}}, {"retry": {"jitter_s": -1}},
                                     {"smtp": {"port": 65536}}, {"default_temperature": 3},
                                     {"batch_timeout_s": float("nan")}, {"retry": []}])
def test_invalid_settings_rejected(tmp_path, raw):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError):
        load_settings(str(path))


def test_optional_security_lists_accept_null(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text('{"security":{"deny_extensions_in":null}}', encoding="utf-8")
    with patch("kajovo.core.config.get_secret", return_value=None):
        assert load_settings(str(path)).security.deny_extensions_in is None


def test_legacy_financial_settings_are_ignored_without_touching_database(tmp_path):
    from dataclasses import asdict
    database = tmp_path / "existing.sqlite"
    database.write_bytes(b"puvodni obsah")
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"db_path": str(database), "pricing": {"source_url": "invalid"},
                                "default_model": "gpt-4.1"}), encoding="utf-8")
    with patch("kajovo.core.config.get_secret", return_value=None):
        settings = load_settings(str(path))
    assert settings.default_model == "gpt-4.1"
    assert "pricing" not in asdict(settings) and "db_path" not in asdict(settings)
    assert database.read_bytes() == b"puvodni obsah"


def test_clearing_secret_removes_environment_fallback(monkeypatch):
    monkeypatch.setenv("KAJOVO_SECRET_SMTP_PASSWORD", "obsolete")
    with patch("keyring.delete_password"), patch("keyring.get_password", return_value=None):
        assert set_secret("smtp_password", "")
        assert get_secret("smtp_password") is None
