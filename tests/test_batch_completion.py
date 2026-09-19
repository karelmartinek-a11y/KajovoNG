"""BATCH V3 completion: no duplicate submit, no direct OUT mutation."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from change_v2_fixtures import (
    batch_output_rows,
    raw_jsonl,
    run,
    scenario,
)
from kajovo.core.batch_completion import (
    complete_saved_batch,
    import_bundle,
    local_batches,
    pending_batch_ids,
    read_state,
)
from kajovo.core.contracts import ContractError


def prepared_run(tmp_path, mode="GENERATE"):
    worker, client, _responder = scenario(
        tmp_path, mode, batch=True, maximum_quality=False
    )
    results, errors = run(worker, client)
    assert errors == []
    assert results[0]["status"] == "batch_pending"
    state = read_state(worker.log.paths.run_dir)
    assert state["generate_batch"]["version"] == 3
    return worker, client, state


def terminal_batch(state, *, status="completed", output=True):
    value = {
        "id": "batch_work",
        "status": status,
        "input_file_id": state.get("batch_input_file_id"),
        "endpoint": "/v1/responses",
        "created_at": 123,
    }
    if output:
        value["output_file_id"] = "file_out"
    return value


@pytest.mark.parametrize("mode", ["GENERATE", "MODIFY"])
def test_completion_survives_restart_without_new_paid_calls(tmp_path, mode):
    worker, client, state = prepared_run(tmp_path, mode)
    manifest = state["generate_batch"]
    client.retrieve_batch.return_value = terminal_batch(state)
    client.file_content.return_value = raw_jsonl(batch_output_rows(manifest))
    client.create_response.reset_mock()
    client.create_batch.reset_mock()
    client.upload_file.reset_mock()

    result = complete_saved_batch(
        client,
        worker.log.paths.run_dir,
        "batch_work",
        worker.settings,
    )
    assert result["status"] == "files_complete_unverified"
    assert result["written"] == []
    restored = read_state(worker.log.paths.run_dir)
    assert pending_batch_ids(restored) == []
    assert restored["batch_records"]["batch_work"]["created_at"] == 123
    assert not (Path(worker.cfg.out_dir) / "hello.txt").exists()
    staged = (
        Path(worker.log.paths.run_dir)
        / result["staged_files"][0]["staged_path"]
    )
    assert staged.read_text(encoding="utf-8") == "content:hello.txt\n"
    client.create_response.assert_not_called()
    client.create_batch.assert_not_called()
    client.upload_file.assert_not_called()


def test_import_archives_staging_not_out_and_keeps_bundle_open_for_publish(tmp_path):
    worker, client, state = prepared_run(tmp_path)
    client.retrieve_batch.return_value = terminal_batch(state)
    client.file_content.return_value = raw_jsonl(
        batch_output_rows(state["generate_batch"])
    )
    result = complete_saved_batch(
        client,
        worker.log.paths.run_dir,
        "batch_work",
        worker.settings,
    )
    bundle = worker.log.bundle
    staged_artifacts = [
        row for row in bundle.artifacts() if row["role"] == "staged_output"
    ]
    assert len(staged_artifacts) == len(result["staged_files"])
    assert all(
        Path(row["original_path"]).is_relative_to(
            Path(worker.log.paths.run_dir)
        )
        for row in staged_artifacts
    )
    assert not list(Path(worker.cfg.out_dir).glob("**/*")) if Path(worker.cfg.out_dir).exists() else True
    assert bundle.verify_integrity()["valid"]
    assert bundle.run_record()["status"] == "files_complete_unverified"


@pytest.mark.parametrize(
    "status", ["validating", "in_progress", "finalizing", "cancelling"]
)
def test_running_batch_is_normal_wait_without_writes(tmp_path, status):
    worker, client, _state = prepared_run(tmp_path)
    before = Path(worker.log.state_path).read_bytes()
    client.retrieve_batch.return_value = {
        "id": "batch_work",
        "status": status,
    }
    result = complete_saved_batch(
        client,
        worker.log.paths.run_dir,
        "batch_work",
        worker.settings,
    )
    assert result["status"] == "batch_pending"
    assert "později" in result["detail"]
    # Remote polling may update Run Bundle evidence but not workflow run_state.
    assert Path(worker.log.state_path).read_bytes() == before
    client.file_content.assert_not_called()


@pytest.mark.parametrize("status", ["completed", "failed", "expired", "cancelled"])
def test_terminal_batch_without_result_files_is_partial(tmp_path, status):
    worker, client, state = prepared_run(tmp_path)
    client.retrieve_batch.return_value = terminal_batch(
        state, status=status, output=False
    )
    result = complete_saved_batch(
        client,
        worker.log.paths.run_dir,
        "batch_work",
        worker.settings,
    )
    assert result["status"] == "partial"
    assert pending_batch_ids(read_state(worker.log.paths.run_dir)) == [
        "batch_work"
    ]


def test_completion_preserves_workflow_state_when_download_fails(tmp_path):
    worker, client, state = prepared_run(tmp_path)
    before = Path(worker.log.state_path).read_bytes()
    client.retrieve_batch.return_value = terminal_batch(state)
    client.file_content.side_effect = RuntimeError("Výstup již není dostupný")
    with pytest.raises(RuntimeError):
        complete_saved_batch(
            client,
            worker.log.paths.run_dir,
            "batch_work",
            worker.settings,
        )
    assert Path(worker.log.state_path).read_bytes() == before
    with pytest.raises(ContractError):
        complete_saved_batch(
            client,
            worker.log.paths.run_dir,
            "batch_other",
            worker.settings,
        )


def test_second_known_batch_remains_pending_after_first_import(tmp_path):
    worker, client, state = prepared_run(tmp_path)
    second = copy.deepcopy(state["generate_batch"])
    state["generate_batches"] = {"batch_second": second}
    Path(worker.log.state_path).write_text(
        json.dumps(state, ensure_ascii=False), encoding="utf-8"
    )

    client.retrieve_batch.return_value = terminal_batch(state)
    client.file_content.return_value = raw_jsonl(
        batch_output_rows(state["generate_batch"])
    )
    result = complete_saved_batch(
        client,
        worker.log.paths.run_dir,
        "batch_work",
        worker.settings,
    )
    assert result["status"] == "batch_pending"
    assert pending_batch_ids(read_state(worker.log.paths.run_dir)) == [
        "batch_second"
    ]

    client.retrieve_batch.return_value = {
        **terminal_batch(state),
        "id": "batch_second",
    }
    complete_saved_batch(
        client,
        worker.log.paths.run_dir,
        "batch_second",
        worker.settings,
    )
    assert pending_batch_ids(read_state(worker.log.paths.run_dir)) == []
    assert (
        local_batches(Path(worker.settings.log_dir))["batch_second"]["run_id"]
        == worker.log.run_id
    )


def test_duplicate_or_unrelated_v3_result_ids_block_before_staging(tmp_path):
    worker, client, state = prepared_run(tmp_path)
    rows = batch_output_rows(state["generate_batch"])
    for bad_rows in (
        [rows[0], copy.deepcopy(rows[0])],
        [{**rows[0], "custom_id": "foreign"}],
    ):
        client.retrieve_batch.return_value = terminal_batch(state)
        client.file_content.return_value = raw_jsonl(bad_rows)
        with pytest.raises(ContractError):
            complete_saved_batch(
                client,
                worker.log.paths.run_dir,
                "batch_work",
                worker.settings,
            )
        staging = Path(worker.log.paths.run_dir) / "staging" / "batch"
        assert not staging.exists() or not any(staging.glob("**/*.txt"))


@pytest.mark.parametrize("bad", [b"[]", b"invalid", b"", b"null"])
def test_malformed_legacy_bundle_never_reports_success(tmp_path, bad):
    result = import_bundle(bad, str(tmp_path))
    assert result["status"] == "partial"
    assert not result["written"]


def test_corrupt_history_record_is_skipped(tmp_path):
    root = tmp_path / "LOG"
    run_dir = root / "RUN_110920261200_abcd"
    run_dir.mkdir(parents=True)
    (run_dir / "run_state.json").write_text(
        json.dumps(
            {
                "batch_id": "batch_work",
                "generate_batches": [],
            }
        ),
        encoding="utf-8",
    )
    assert local_batches(root) == {}
