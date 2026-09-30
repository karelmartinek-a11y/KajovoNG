"""Diskové a procesní hranice PHOTO; síť nahrazena na klientské hranici."""

import base64
import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from kajovo.core import photo_batch
from test_photo_refresh_isolation import _setup
from test_photo_studio import _image_bytes


def result_client(tmp_path, *, invalid=False):
    log, source, job, client, _state = _setup(tmp_path)
    client.file_content.return_value = (json.dumps({
        "custom_id": job.items[0].custom_id,
        "response": {"status_code": 200, "body": {"data": [{
            "b64_json": "neplatné" if invalid else base64.b64encode(_image_bytes()).decode(),
        }]}},
        "error": None,
    }) + "\n").encode()
    return log, source, job, client


def test_download_then_refresh_keeps_local_state_and_exact_bytes(tmp_path):
    log, _source, job, client = result_client(tmp_path)
    photo_batch.download_results(client, job, log)
    target = Path(job.items[0].output_path)
    before = target.read_bytes()
    photo_batch.refresh_job(client, job, log)
    stored = photo_batch.load_jobs(log)[0]
    assert stored.status == "downloaded"
    assert stored.items[0].status == "downloaded"
    assert target.read_bytes() == before == _image_bytes()
    client.create_image_batch.assert_not_called()
    client.upload_file.assert_not_called()


def test_invalid_image_keeps_provider_counts_separate_from_local_failure(tmp_path):
    log, _source, job, client = result_client(tmp_path, invalid=True)
    photo_batch.download_results(client, job, log)
    assert job.status == "failed" and job.items[0].status == "failed"
    counts = getattr(job, "provider_request_counts", {
        "total": job.request_total, "completed": job.request_completed, "failed": job.request_failed,
    })
    assert counts == {"total": 1, "completed": 1, "failed": 0}
    photo_batch.refresh_job(client, job, log)
    assert photo_batch.load_jobs(log)[0].status == "failed"


@pytest.mark.parametrize("value", [True, 1.0, "1", -1])
def test_photo_counts_reject_coercion_before_mutating_job(tmp_path, value):
    _log, _source, job, client = result_client(tmp_path)
    payload = copy.deepcopy(client.retrieve_batch.return_value)
    payload["request_counts"]["completed"] = value
    before = copy.deepcopy(job)
    with pytest.raises(ValueError, match="request_counts"):
        photo_batch.apply_batch_status(job, payload)
    assert job == before


@pytest.mark.parametrize("corruption", ["json", "identity", "hash"])
def test_photo_corrupt_work_order_blocks_before_provider_and_preserves_bytes(tmp_path, corruption):
    log, _source, job, client = result_client(tmp_path)
    path = log / "PHOTO" / job.job_id / "work_order_v2.json"
    data = json.loads(path.read_text())
    if corruption == "identity":
        data["run_id"] = "photojob_foreign"
    elif corruption == "hash":
        data["order_hash"] = "f" * 64
    path.write_text("{" if corruption == "json" else json.dumps(data), encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="WorkOrder|WORK_ORDER"):
        photo_batch.refresh_job(client, job, log)
    assert path.read_bytes() == before
    client.retrieve_batch.assert_not_called()
    client.create_image_batch.assert_not_called()


def test_refresh_reloads_current_state_instead_of_overwriting_stale_snapshot(tmp_path):
    log, _source, job, client = result_client(tmp_path)
    stale = copy.deepcopy(job)
    photo_batch.download_results(client, job, log)
    photo_batch.refresh_job(client, stale, log)
    stored = photo_batch.load_jobs(log)[0]
    assert stored.status == "downloaded"
    assert stored.items[0].output_sha256 == job.items[0].output_sha256
    assert Path(stored.items[0].output_path).read_bytes() == _image_bytes()


@pytest.mark.parametrize("operation", ["refresh", "download", "delete"])
def test_photo_operations_share_real_process_lock(tmp_path, operation):
    log, _source, job, client = result_client(tmp_path)
    root = log / "PHOTO" / job.job_id
    code = """
import sys
from pathlib import Path
from kajovo.core.photo_batch import _photo_job_lock
with _photo_job_lock(Path(sys.argv[1])):
    print('locked', flush=True)
    sys.stdin.readline()
"""
    process = subprocess.Popen([sys.executable, "-c", code, str(root)],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True)
    try:
        assert process.stdout.readline().strip() == "locked"
        with pytest.raises(BlockingIOError):
            if operation == "refresh":
                photo_batch.refresh_job(client, job, log)
            elif operation == "download":
                photo_batch.download_results(client, job, log)
            else:
                photo_batch.delete_job(job, log)
        assert (root / "photo_job.json").is_file()
        client.retrieve_batch.assert_not_called()
    finally:
        _out, error = process.communicate("release\n", timeout=10)
        assert process.returncode == 0, error


def test_deleted_stale_job_cannot_be_resurrected_by_refresh(tmp_path):
    log, _source, job, client = result_client(tmp_path)
    photo_batch.delete_job(job, log)
    with pytest.raises(ValueError):
        photo_batch.refresh_job(client, job, log)
    assert not (log / "PHOTO" / job.job_id).exists()
    client.retrieve_batch.assert_not_called()
