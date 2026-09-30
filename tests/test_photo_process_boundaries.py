"""Skutečné ukončení procesu na PHOTO HTTP/disk hranicích bez živé sítě."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from test_runtime_end_to_end import ROOT, PhotoTransport, child


def photo_process(root, phase):
    from kajovo.core import photo_batch
    from kajovo.core.openai_client import OpenAIClient
    from test_photo_studio import _frozen_job, _image_bytes

    log = root / "LOG"
    completing = phase in {"remote_completed", "download_bytes", "image_bytes", "finish", "unknown"}
    transport = PhotoTransport(root, completing)
    client = OpenAIClient("offline-test", base_url="https://offline.invalid/v1")
    client._sdk = None

    def request(method, url, **kwargs):
        path = url.split("/v1", 1)[1]
        if phase == "before_request" and (method, path) == ("POST", "/batches"):
            os._exit(77)
        with (root / "calls.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"method": method, "path": path}) + "\n")
        result = transport.request(method, url, **kwargs)
        if phase == "after_dispatch" and (method, path) == ("POST", "/batches"):
            os._exit(77)
        if phase == "download_bytes" and path == "/files/file_output/content":
            os._exit(77)
        return result

    client._transport.session.request = request
    if phase in {"submit", "before_request", "after_dispatch", "after_id"}:
        _, job = _frozen_job(root)
        photo_batch.prepare_and_submit(client, job, log)
        if phase == "after_id":
            os._exit(77)
    else:
        job = photo_batch.load_jobs(log)[0]
        if phase == "unknown":
            # Absence potvrzeného ID nikdy není pokyn k opakování placeného submitu.
            assert not job.batch_id and job.status == "submission_unknown"
            with pytest.raises(ValueError):
                photo_batch.prepare_and_submit(client, job, log)
            assert not transport.calls
            return
        if phase == "remote_completed":
            photo_batch.refresh_job(client, job, log)
            assert job.status == "completed" and job.output_file_id == "file_output"
            os._exit(77)
        if phase == "image_bytes":
            original = photo_batch._atomic_bytes

            def write(path, data):
                original(path, data)
                if path.suffix == ".png":
                    assert path.read_bytes() == _image_bytes()
                    os._exit(77)

            photo_batch._atomic_bytes = write
        photo_batch.download_results(client, job, log)
        assert job.status == "downloaded"
        assert photo_batch.load_jobs(log)[0].items[0].output_path == job.items[0].output_path
        assert Path(job.items[0].output_path).read_bytes() == _image_bytes()
        assert not any(method == "POST" for method, _path in transport.calls)
        assert len(list(Path(job.output_dir).glob("*.png"))) == 1


@pytest.mark.parametrize("boundary", ["before_request", "after_dispatch", "after_id", "remote_completed", "download_bytes", "image_bytes"])
def test_photo_hard_process_exit_and_recovery_without_resubmit(tmp_path, boundary):
    if boundary in {"remote_completed", "download_bytes", "image_bytes"}:
        child("from pathlib import Path; import sys; from test_photo_process_boundaries import photo_process; photo_process(Path(sys.argv[1]), 'submit')", tmp_path)
    environment = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONPATH=os.pathsep.join([str(ROOT), str(ROOT / "tests")]))
    code = "from pathlib import Path; import sys; from test_photo_process_boundaries import photo_process; " + f"photo_process(Path(sys.argv[1]), {boundary!r})"
    result = subprocess.run([sys.executable, "-c", code, str(tmp_path)], env=environment, cwd=tmp_path,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 77, result.stdout + result.stderr
    before = [json.loads(row) for row in (tmp_path / "calls.jsonl").read_text().splitlines()]
    submitted = [row for row in before if row == {"method": "POST", "path": "/batches"}]
    assert len(submitted) == (0 if boundary == "before_request" else 1)
    phase = "unknown" if boundary in {"before_request", "after_dispatch"} else "finish"
    child("from pathlib import Path; import sys; from test_photo_process_boundaries import photo_process; " + f"photo_process(Path(sys.argv[1]), {phase!r})", tmp_path)
    after = [json.loads(row) for row in (tmp_path / "calls.jsonl").read_text().splitlines()]
    assert after[:len(before)] == before
    assert not any(row["method"] == "POST" for row in after[len(before):])
    if phase == "finish":
        child("from pathlib import Path; import sys; from test_photo_process_boundaries import photo_process; photo_process(Path(sys.argv[1]), 'finish')", tmp_path)
