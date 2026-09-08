import io
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
import requests

from kajovo.core.config import AppSettings
from kajovo.core.contracts import ContractError
from kajovo.core.cascade_pipeline import CascadeRunConfig, CascadeRunWorker
from kajovo.core.cascade_types import CascadeDefinition, CascadeStep
from kajovo.core.openai_client import OpenAIClient
from kajovo.core.pipeline import RunWorker
from kajovo.core.pricing import PriceTable
from kajovo.core.secret_store import get_secret, set_secret


def test_multipart_header_and_retry_rewind():
    client = OpenAIClient("test")
    assert "Content-Type" not in client.session.headers
    stream = io.BytesIO(b"complete")
    bodies = []

    def request(*args, **kwargs):
        bodies.append(kwargs["files"]["file"][1].read())
        if len(bodies) == 1:
            raise requests.Timeout()
        return Mock(status_code=200, headers={"content-type": "application/json"}, json=lambda: {})

    with patch.object(client.session, "request", side_effect=request), patch("kajovo.core.openai_client.time.sleep"):
        client._req("POST", "/files", files={"file": ("x.txt", stream)})
    assert bodies == [b"complete", b"complete"]


def test_clearing_secret_removes_environment_fallback(monkeypatch):
    monkeypatch.setenv("KAJOVO_SECRET_SMTP_PASSWORD", "obsolete")
    with patch("keyring.delete_password"), patch("keyring.get_password", return_value=None):
        assert set_secret("smtp_password", "")
        assert get_secret("smtp_password") is None


def test_missing_expected_file_does_not_overwrite_output(tmp_path):
    target = tmp_path / "keep.txt"
    target.write_text("original", encoding="utf-8")
    worker = CascadeRunWorker(CascadeRunConfig("test", CascadeDefinition("test"), "", str(tmp_path)), AppSettings(), "test")
    worker.logger = Mock()
    with pytest.raises(RuntimeError):
        worker._process_expected_out_files(step=CascadeStep(expected_out_files=["missing.txt"]), idx=1,
            json_output={"files": [{"path": "keep.txt", "content": "replacement"}]}, context={}, client=Mock())
    assert target.read_text(encoding="utf-8") == "original"


@pytest.mark.parametrize("path", ["/absolute.txt", "../escape.txt", "C:/escape.txt"])
def test_cascade_rejects_invalid_paths(path):
    worker = CascadeRunWorker(CascadeRunConfig("test", CascadeDefinition("test"), "", "out"), AppSettings(), "test")
    with pytest.raises(ValueError):
        worker._normalize_expected_rel_path(path)


def test_invalid_generated_json_fails_instead_of_returning_empty_file():
    cfg = SimpleNamespace(attached_file_ids=[], model="test", model_caps={}, temperature=0.0,
                          use_file_search=False, mode="GENERATE", project="test", prompt="test")
    worker = RunWorker(cfg, AppSettings(), "test", Mock(), Mock(), PriceTable.builtin_fallback())
    client = Mock()
    client.create_response.return_value = {"id": "response", "output_text": "invalid"}
    with pytest.raises(ContractError):
        worker._gen_file_chunks(client, "previous", "A3_FILE", "keep.txt", None, [])
    assert client.create_response.call_count == 3
