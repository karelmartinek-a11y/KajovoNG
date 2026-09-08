import json
import zipfile
from unittest.mock import Mock, patch

import pytest

from kajovo.core.config import load_settings
from kajovo.core.contracts import ContractError, validate_paths
from kajovo.core.cascade_pipeline import CascadeRunConfig, CascadeRunWorker
from kajovo.core.cascade_types import CascadeDefinition
from kajovo.core.config import AppSettings, RetryPolicy
from kajovo.core.openai_client import OpenAIClient, OpenAIError
from kajovo.core.pricing import PriceTable, PriceRow, compute_cost
from kajovo.core.retry import CircuitBreaker, with_retry
from kajovo.core.utils import atomic_write_text
from utf8nobom.app import (TargetSpec, RunLogger, build_scan_plan, copy_directory_for_backup,
                          rewrite_zip_if_needed, validate_input_paths)


@pytest.mark.parametrize("paths", [["a", "a/b"], ["A.txt", "a.txt"], ["x/y", "x"]])
def test_manifest_path_conflicts(paths):
    with pytest.raises(ContractError):
        validate_paths([{"path": path} for path in paths])


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


def test_atomic_write_failure_preserves_original(tmp_path):
    path = tmp_path / "file.txt"
    path.write_text("original", encoding="utf-8")
    with patch("kajovo.core.utils.os.replace", side_effect=OSError("disk failure")):
        with pytest.raises(OSError):
            atomic_write_text(str(path), "new")
    assert path.read_text(encoding="utf-8") == "original"
    assert list(tmp_path.iterdir()) == [path]


def test_paginated_files_are_complete():
    client = OpenAIClient("test")
    client._sdk = None
    with patch.object(client, "_req", side_effect=[
        {"data": [{"id": "a"}], "has_more": True, "last_id": "a"},
        {"data": [{"id": "b"}], "has_more": False},
    ]) as request:
        assert [item["id"] for item in client.list_files()] == ["a", "b"]
        assert "after=a" in request.call_args.args[1]


def test_sdk_mutation_failure_does_not_repeat_via_rest():
    client = OpenAIClient("test")
    client._sdk = Mock()
    client._sdk.responses.create.side_effect = RuntimeError("lost response")
    with patch.object(client, "_req") as request:
        with pytest.raises(OpenAIError):
            client.create_response({"model": "test", "input": "test"})
        request.assert_not_called()


def test_open_breaker_does_not_consume_request_attempt():
    breaker = CircuitBreaker(1, 10)
    breaker.on_failure()
    with patch("kajovo.core.retry.time.sleep"):
        assert with_retry(lambda: "ok", RetryPolicy(max_attempts=1), breaker) == "ok"


def test_http_400_containing_500_in_body_is_not_retried():
    request = Mock(side_effect=OpenAIError("Invalid limit 500", status_code=400))
    with pytest.raises(OpenAIError):
        with_retry(request, RetryPolicy())
    assert request.call_count == 1


def test_schema_cannot_retrieve_external_urls():
    worker = CascadeRunWorker(CascadeRunConfig("test", CascadeDefinition("test"), "", "out"), AppSettings(), "test")
    with pytest.raises(ValueError):
        worker._validate_schema_minimal({"type": "object", "properties": {"x": {"$ref": "https://example.com/schema"}}})


def test_schema_validates_nested_content():
    worker = CascadeRunWorker(CascadeRunConfig("test", CascadeDefinition("test"), "", "out"), AppSettings(), "test")
    import jsonschema
    with pytest.raises(jsonschema.ValidationError):
        worker._validate_json_output({"x": [3]}, {"type": "object", "properties": {"x": {"type": "array", "items": {"type": "string"}}}})


@pytest.mark.parametrize("price", [-1, float("inf"), float("nan")])
def test_invalid_prices_rejected(price):
    with pytest.raises(ValueError):
        PriceRow("model", price, 1)


def test_price_units_and_old_cache(tmp_path):
    row = PriceTable.builtin_fallback().get("gpt-4o-mini")
    assert compute_cost(row, 1_000_000, 1_000_000)[0] == pytest.approx(0.75)
    path = tmp_path / "prices.json"
    path.write_text('{"rows":[{"model":"gpt-4o-mini","input":150,"output":600}]}', encoding="utf-8")
    table = PriceTable(str(path))
    table.load_cache()
    assert not table.rows
    table.update_from_rows({row.model: row}, verified=False)
    reloaded = PriceTable(str(path))
    reloaded.load_cache()
    assert reloaded.get(row.model) == row


def test_utf8_backup_never_overwrites_existing_backup(tmp_path):
    source, backup = tmp_path / "source", tmp_path / "backup"
    source.mkdir()
    backup.mkdir()
    (backup / "original.txt").write_text("keep", encoding="utf-8")
    with pytest.raises(FileExistsError):
        copy_directory_for_backup(source, backup)
    assert (backup / "original.txt").read_text() == "keep"


def test_utf8_plan_skips_git_and_deduplicates_nested_targets(tmp_path):
    source, backup = tmp_path / "source", tmp_path / "backup"
    source.mkdir()
    backup.mkdir()
    nested = source / "nested"
    nested.mkdir()
    (source / ".git").mkdir()
    (source / ".git" / "config").write_text("metadata")
    (nested / "file.txt").write_text("content")
    targets, _ = validate_input_paths([str(nested), str(source)], str(backup))
    assert targets == [TargetSpec(source)]
    assert [task.path for task in build_scan_plan(targets).file_tasks] == [nested / "file.txt"]


def test_zip_preserves_comment_and_rejects_unsafe_paths_without_changes(tmp_path):
    path = tmp_path / "test.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.comment = b"important metadata"
        archive.writestr("file.txt", b"\xef\xbb\xbftext")
    rewrite_zip_if_needed(path, Mock(), RunLogger(tmp_path, "safe"))
    with zipfile.ZipFile(path) as archive:
        assert archive.comment == b"important metadata"
        assert archive.read("file.txt") == b"text"
    with zipfile.ZipFile(path, "a") as archive:
        archive.writestr("../unsafe.txt", b"data")
    original = path.read_bytes()
    with pytest.raises(ValueError):
        rewrite_zip_if_needed(path, Mock(), RunLogger(tmp_path, "unsafe"))
    assert path.read_bytes() == original


def test_logger_rejects_existing_run_and_distinguishes_truncated_names(tmp_path):
    from kajovo.core.runlog import RunLogger
    logger = RunLogger(str(tmp_path), "RUN_090920261200_TEST")
    first = logger.save_json("responses", "x" * 160 + "one", {"value": 1})
    second = logger.save_json("responses", "x" * 160 + "two", {"value": 2})
    assert first != second
    with pytest.raises(FileExistsError):
        RunLogger(str(tmp_path), logger.run_id)


@pytest.mark.parametrize("valid_pin", [True, False])
def test_ssh_pin_is_sha256_and_connection_closes(tmp_path, valid_pin):
    import hashlib
    import base64
    from kajovo.core.diagnostics.ssh import collect_ssh_diagnostics
    key = b"test public key"
    fingerprint = base64.b64encode(hashlib.sha256(key).digest()).decode().rstrip("=")
    client = Mock()
    client.get_transport.return_value.get_remote_server_key.return_value.asbytes.return_value = key
    client.exec_command.return_value = (Mock(), Mock(read=lambda: b"result"), Mock(read=lambda: b""))
    with patch("kajovo.core.diagnostics.ssh.paramiko.SSHClient", return_value=client):
        if valid_pin:
            _, files = collect_ssh_diagnostics(str(tmp_path), "host", "user", "", "", pin="SHA256:" + fingerprint, pin_required=True)
            assert len(files) == 1
        else:
            with pytest.raises(RuntimeError, match="SHA256"):
                collect_ssh_diagnostics(str(tmp_path), "host", "user", "", "", pin=fingerprint.swapcase(), pin_required=True)
            client.exec_command.assert_not_called()
    client.close.assert_called_once()
