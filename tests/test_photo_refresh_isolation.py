"""Skutečný řadič a diskový PHOTO backend; bez předstírání spuštěného Qt UI.

Pouze metody řadiče se načtou přes AST, protože nevyžadují widgety. Síť je
nahrazena na hranici klienta. Nativní vykreslení a vlákna ověřují Qt testy.
"""

import ast
import base64
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from kajovo.core import photo_batch
from test_photo_studio import _frozen_job, _image_bytes, _prepare_import_job


ROOT = Path(__file__).resolve().parents[1]


def _controller_method(name):
    source = ROOT / "kajovo/studio/photos.py"
    module = ast.parse(source.read_text(encoding="utf-8"))
    page = next(node for node in module.body if isinstance(node, ast.ClassDef) and node.name == "PhotosPage")
    method = next(node for node in page.body if isinstance(node, ast.FunctionDef) and node.name == name)

    def friendly_error(error, operation="Operaci"):
        # Textový překladač a widgety patří do samostatné Qt sady. Tato
        # jednotka ověřuje předání chyby a dokončení nezávislé diskové operace.
        return f"{operation}: {type(error).__name__}."

    namespace = {"photo_batch": photo_batch, "friendly_error": friendly_error, "deepcopy": deepcopy}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])), str(source), "exec"), namespace)
    return namespace[name]


def _setup(tmp_path):
    log_dir = tmp_path / "LOG"
    good_dir = tmp_path / "good"
    good_dir.mkdir()
    source, good = _frozen_job(good_dir)
    good.batch_id = "batch_good"
    good.status = "in_progress"
    _prepare_import_job(good, log_dir)
    photo_batch.save_job(good, log_dir)
    payload = {
        "id": good.batch_id, "input_file_id": good.input_file_id,
        "endpoint": "/v1/images/edits", "status": "completed",
        "output_file_id": "file_ready", "error_file_id": None,
        "request_counts": {"total": 1, "completed": 1, "failed": 0},
    }
    client = Mock()
    client.list_batches.return_value = [payload]
    client.retrieve_batch.return_value = payload
    state = SimpleNamespace(
        context=SimpleNamespace(api_key="offline-test", settings=SimpleNamespace(log_dir=str(log_dir))),
        jobs=photo_batch.load_jobs(log_dir), notice=Mock(), _render_jobs=Mock(),
    )
    task = SimpleNamespace(progress_event=Mock(), logline=Mock())

    def execute(title, function, receive, **kwargs):
        receive(function(client, task))

    state.execute = execute
    return log_dir, source, good, client, state


@pytest.mark.parametrize("fault", ["interrupted_preparation", "unknown_without_order", "corrupt_evidence", "bad_remote_identity", "array_evidence", "null_evidence"])
def test_one_broken_photo_job_cannot_hide_other_completed_downloads(tmp_path, fault):
    log_dir, source, good, client, state = _setup(tmp_path)
    bad_dir = tmp_path / "bad"
    bad_dir.mkdir()
    _, bad = _frozen_job(bad_dir)
    bad.input_file_id = "file_interrupted"
    if fault == "unknown_without_order":
        bad.status = "submission_unknown"
    elif fault == "bad_remote_identity":
        bad.batch_id = "batch_bad"
        bad.status = "in_progress"
        remote_bad = deepcopy(client.list_batches.return_value[0])
        remote_bad.update(id=bad.batch_id, input_file_id="file_different")
        client.list_batches.return_value.insert(0, remote_bad)
    path = photo_batch.save_job(bad, log_dir) / "photo_job.json"
    if fault == "corrupt_evidence":
        path.write_text("{not valid JSON", encoding="utf-8")
    elif fault == "array_evidence":
        path.write_text("[]", encoding="utf-8")
    elif fault == "null_evidence":
        path.write_text("null", encoding="utf-8")
    before = path.read_bytes()
    original = source.read_bytes()
    _controller_method("refresh_jobs")(state)
    state._render_jobs.assert_called_once()
    received = next(job for job in state.jobs if job.job_id == good.job_id)
    assert received.status == "completed"
    assert received.output_file_id == "file_ready"
    assert path.read_bytes() == before
    if fault != "interrupted_preparation":
        notice = state.notice.setText.call_args.args[0]
        assert bad.job_id in notice
        assert "nepodařilo" in notice

    # Konec scénáře je skutečný dekódovaný PNG na disku, nikoli jen stav completed.
    generated = _image_bytes()
    line = {
        "custom_id": good.items[0].custom_id,
        "response": {"status_code": 200, "body": {"data": [{"b64_json": base64.b64encode(generated).decode()}]}},
        "error": None,
    }
    client.file_content.return_value = (json.dumps(line) + "\n").encode()
    result = photo_batch.download_results(client, received, log_dir)
    assert result.items[0].status == "downloaded"
    assert Path(result.items[0].output_path).read_bytes() == generated
    assert source.read_bytes() == original
    client.upload_file.assert_not_called()
    client.create_image_batch.assert_not_called()
    client._req.assert_not_called()


def test_tolerant_loader_reports_corruption_without_changing_strict_loader(tmp_path):
    log_dir, source, good, client, state = _setup(tmp_path)
    damaged = log_dir / "PHOTO/photojob_corrupt/photo_job.json"
    damaged.parent.mkdir()
    damaged.write_text("{}", encoding="utf-8")
    before = damaged.read_bytes()
    with pytest.raises(ValueError, match="poškozená"):
        photo_batch.load_jobs(log_dir)
    errors = []
    loaded = photo_batch.load_jobs(log_dir, errors=errors)
    assert [job.job_id for job in loaded] == [good.job_id]
    assert len(errors) == 1 and "photojob_corrupt" in errors[0]
    assert damaged.read_bytes() == before


def test_page_load_reports_damaged_job_but_keeps_healthy_job_visible(tmp_path):
    log_dir, source, good, client, state = _setup(tmp_path)
    damaged = log_dir / "PHOTO/photojob_corrupt/photo_job.json"
    damaged.parent.mkdir()
    damaged.write_text("{}", encoding="utf-8")
    _controller_method("load_jobs")(state)
    assert [job.job_id for job in state.jobs] == [good.job_id]
    state._render_jobs.assert_called_once()
    assert "photojob_corrupt" in state.notice.setText.call_args.args[0]


def test_missing_work_order_still_blocks_that_jobs_download(tmp_path):
    source, job = _frozen_job(tmp_path)
    job.batch_id = "batch_missing_order"
    job.input_file_id = "file_input"
    job.status = "completed"
    job.output_file_id = "file_output"
    client = Mock()
    with pytest.raises(ValueError, match="WorkOrder"):
        photo_batch.download_results(client, job, tmp_path / "LOG")
    client.file_content.assert_not_called()
    client.create_image_batch.assert_not_called()


def test_valid_photo_refresh_has_no_false_warning(tmp_path):
    log_dir, source, good, client, state = _setup(tmp_path)
    _controller_method("refresh_jobs")(state)
    assert state.jobs[0].status == "completed"
    assert "nepodařilo" not in state.notice.setText.call_args.args[0]


@pytest.mark.parametrize("failure", [PermissionError, FileNotFoundError])
def test_photo_job_metadata_failure_is_isolated(tmp_path, monkeypatch, failure):
    log_dir, source, good, client, state = _setup(tmp_path)
    bad_dir = tmp_path / "bad"
    bad_dir.mkdir()
    _, bad = _frozen_job(bad_dir)
    damaged = photo_batch.save_job(bad, log_dir) / "photo_job.json"
    original = damaged.read_bytes()
    original_stat = Path.stat

    def stat(path, *args, **kwargs):
        if path == damaged:
            raise failure("Nedostupná metadata jedné úlohy.")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", stat)
    with pytest.raises(ValueError, match="poškozená"):
        photo_batch.load_jobs(log_dir)
    _controller_method("refresh_jobs")(state)
    assert [job.job_id for job in state.jobs] == [good.job_id]
    assert state.jobs[0].status == "completed"
    assert state.jobs[0].output_file_id == "file_ready"
    assert bad.job_id in state.notice.setText.call_args.args[0]
    assert damaged.read_bytes() == original
    client.create_image_batch.assert_not_called()
    client.upload_file.assert_not_called()
