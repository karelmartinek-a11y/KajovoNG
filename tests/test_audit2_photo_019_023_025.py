"""Regrese identity, kontraktů a procesových zámků PHOTO (A2-019, 023–025)."""
import base64
import copy
import json
import io
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest
from PIL import Image

from kajovo.core.photo_batch import (
    apply_batch_status, download_results, inspect_photo_bytes, refresh_job, save_job, _photo_job_lock,
)
from kajovo.core.orchestration.errors import OrchestrationError
from kajovo.core.orchestration.publish import TargetPublishLock
from test_photo_studio import _frozen_job, _image_bytes, _prepare_import_job


@pytest.mark.parametrize("field", ["id", "status", "input_file_id", "endpoint"])
@pytest.mark.parametrize("value", [None, "", 42])
def test_a2_019_photo_rejects_missing_or_invalid_identity(tmp_path, field, value):
    _, job = _frozen_job(tmp_path)
    job.input_file_id, job.batch_id = "file_input", "batch_expected"
    payload = {"id": job.batch_id, "status": "completed", "input_file_id": job.input_file_id,
               "endpoint": "/v1/images/edits", "output_file_id": "file_output"}
    payload[field] = value
    before = copy.deepcopy(job)
    with pytest.raises(ValueError):
        apply_batch_status(job, payload)
    assert job == before


@pytest.mark.parametrize("action", [refresh_job, download_results])
def test_a2_023_modern_missing_workorder_blocks_before_network(tmp_path, action):
    _, job = _frozen_job(tmp_path)
    job.batch_id = "batch_expected"
    client = Mock()
    with pytest.raises(ValueError, match="WorkOrder"):
        action(client, job, tmp_path / "LOG")
    assert not client.mock_calls


def test_a2_023_legacy_is_explicit(tmp_path):
    _, job = _frozen_job(tmp_path)
    job.schema_version = 1
    job.input_file_id, job.batch_id = "file_input", "batch_expected"
    client = Mock()
    client.retrieve_batch.return_value = {"id": job.batch_id, "status": "completed",
        "input_file_id": job.input_file_id, "endpoint": "/v1/images/edits"}
    refresh_job(client, job, tmp_path / "LOG")
    assert job.status == "completed"


@pytest.mark.parametrize("size,passed", [("auto", True), ("8x6", True), ("6x8", False)])
def test_a2_024_exact_decoded_dimensions(tmp_path, size, passed):
    if passed:
        assert inspect_photo_bytes(_image_bytes(), "png", size=size)["width"] == 8
    else:
        with pytest.raises(OrchestrationError, match="velikosti"):
            inspect_photo_bytes(_image_bytes(), "png", size=size)


def test_a2_024_wrong_size_never_reaches_out(tmp_path):
    _, job = _frozen_job(tmp_path)
    job.size, job.batch_id = "1024x1024", "batch_expected"
    log_dir = tmp_path / "LOG"
    _prepare_import_job(job, log_dir)
    client = Mock()
    client.retrieve_batch.return_value = {"id": job.batch_id, "status": "completed",
        "input_file_id": job.input_file_id, "endpoint": "/v1/images/edits", "output_file_id": "file_output"}
    client.file_content.return_value = json.dumps({"custom_id": job.items[0].custom_id,
        "response": {"status_code": 200, "body": {"data": [{"b64_json": base64.b64encode(_image_bytes()).decode()}]}}}).encode()
    download_results(client, job, log_dir)
    assert job.items[0].technical_validation == "failed"
    assert not job.items[0].output_path
    assert not list(Path(job.output_dir).iterdir())


@pytest.mark.parametrize("scope", ["job", "out"])
def test_a2_025_import_locks_are_shared_across_processes(tmp_path, scope):
    _, job = _frozen_job(tmp_path)
    log_dir = tmp_path / "LOG"
    job.batch_id = "batch_expected"
    _prepare_import_job(job, log_dir)
    root = save_job(job, log_dir)
    code = '''
import sys, json, base64
from pathlib import Path
from unittest.mock import Mock
from kajovo.core.photo_batch import load_jobs, download_results
from kajovo.core.orchestration.errors import OrchestrationError
job = load_jobs(sys.argv[1])[0]
client = Mock()
client.retrieve_batch.return_value = {"id": job.batch_id, "status": "completed",
    "input_file_id": job.input_file_id, "endpoint": "/v1/images/edits", "output_file_id": "file_output"}
client.file_content.return_value = json.dumps({"custom_id": job.items[0].custom_id,
    "response": {"status_code": 200, "body": {"data": [{"b64_json": base64.b64encode(Path(job.items[0].source_path).read_bytes()).decode()}]}}}).encode()
try:
    download_results(client, job, sys.argv[1])
except BlockingIOError:
    assert sys.argv[2] == "job"
    assert not client.mock_calls
except OrchestrationError as exc:
    assert sys.argv[2] == "out"
    assert exc.code == "PUBLISH_LOCKED", exc
    client.file_content.assert_called_once()
else:
    raise AssertionError("Import nezískal výhradní zámek")
'''
    with _photo_job_lock(root) if scope == "job" else TargetPublishLock(Path(job.output_dir)):
        result = subprocess.run([sys.executable, "-c", code, str(log_dir), scope], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    with _photo_job_lock(root), TargetPublishLock(Path(job.output_dir)):
        pass


def _result_client(job, binary):
    client = Mock()
    client.retrieve_batch.return_value = {"id": job.batch_id, "status": "completed",
        "input_file_id": job.input_file_id, "endpoint": "/v1/images/edits", "output_file_id": "file_output"}
    client.file_content.return_value = json.dumps({"custom_id": job.items[0].custom_id,
        "response": {"status_code": 200, "body": {"data": [{"b64_json": base64.b64encode(binary).decode()}]}}}).encode()
    return client


@pytest.mark.parametrize("alias", [False, True])
def test_a2_025_same_job_and_out_imports_without_self_lock_and_network_holds_only_job(tmp_path, alias):
    _, job = _frozen_job(tmp_path)
    log_dir = tmp_path / "LOG"
    root = log_dir / "PHOTO" / job.job_id
    root.mkdir(parents=True)
    (root / "unused").mkdir()
    job.output_dir = str(root / "unused" / ".." if alias else root)
    job.batch_id = "batch_expected"
    _prepare_import_job(job, log_dir)
    client = _result_client(job, _image_bytes())

    def network_result(result):
        with TargetPublishLock(Path(job.output_dir)):
            contender = _photo_job_lock(root)
            try:
                assert not contender.acquire()
            finally:
                contender.release()
        return result

    client.retrieve_batch.side_effect = lambda *args: network_result(client.retrieve_batch.return_value)
    client.file_content.side_effect = lambda *args: network_result(client.file_content.return_value)
    assert download_results(client, job, log_dir) is job
    assert Path(job.items[0].output_path).read_bytes() == _image_bytes()
    assert _photo_job_lock(root).path == _photo_job_lock(Path(job.output_dir)).path
    assert _photo_job_lock(root).path.parent.name == "kajovo-photo-job-locks"
    with _photo_job_lock(root), TargetPublishLock(root):
        pass


def test_a2_025_colliding_names_and_repeated_import_preserve_both_results(tmp_path):
    first_dir, second_dir = tmp_path / "first", tmp_path / "second"
    first_dir.mkdir()
    second_dir.mkdir()
    _, first = _frozen_job(first_dir)
    _, second = _frozen_job(second_dir)
    output = tmp_path / "OUT"
    log_dir = tmp_path / "LOG"
    first.output_dir = second.output_dir = str(output)
    image = io.BytesIO()
    Image.new("RGB", (8, 6), "red").save(image, "PNG")
    binaries = [_image_bytes(), image.getvalue()]
    clients = []
    for index, (job, binary) in enumerate(zip([first, second], binaries, strict=True)):
        job.batch_id = f"batch_{index}"
        _prepare_import_job(job, log_dir)
        client = _result_client(job, binary)
        clients.append(client)
        download_results(client, job, log_dir)
    paths = [Path(job.items[0].output_path) for job in [first, second]]
    assert paths[0] != paths[1]
    for job, client, path, binary in zip([first, second], clients, paths, binaries, strict=True):
        job.items[0].content_acceptance = "accepted"
        download_results(client, job, log_dir)
        assert Path(job.items[0].output_path) == path
        assert path.read_bytes() == binary
        assert job.items[0].content_acceptance == "accepted"
    assert set(output.iterdir()) == set(paths)
