"""CHANGE/V2 production workflow regression tests.

These tests intentionally verify the current product contract:
strict typed preparation -> V3 graph -> FILE_CONTENT_V1 -> immutable staging
-> explicit publication. Legacy A2/A3 chunk payloads are tested only by legacy
adapters elsewhere and must not define new-run behavior.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import pytest

from change_v2_fixtures import (
    batch_output_rows,
    default_files,
    format_names,
    make_client,
    raw_jsonl,
    run,
    scenario,
    staged_path,
)
from kajovo.core.contracts import ContractError
from kajovo.core.generate_batch import process_saved_batch
from kajovo.core.orchestration.errors import OrchestrationError
from kajovo.core.orchestration.publish import publish_staged_run


# Compatibility helpers imported by a few older non-contract test modules.
def _client(_values=None, mode="GENERATE"):
    client, responder = make_client(mode)
    return client, responder.calls


def _run(worker, client):
    return run(worker, client)


def _scenario(tmp_path, mode, batch, maximum_quality):
    worker, client, responder = scenario(
        tmp_path, mode, batch, maximum_quality
    )
    return worker, client, responder


@pytest.mark.parametrize("mode", ["GENERATE", "MODIFY"])
@pytest.mark.parametrize("batch", [False, True])
@pytest.mark.parametrize("maximum_quality", [False, True])
def test_delivery_eight_variants_use_v2_preparation_and_truthful_boundary(
    tmp_path, mode, batch, maximum_quality
):
    worker, client, responder = scenario(
        tmp_path, mode, batch, maximum_quality
    )
    results, errors = run(worker, client)

    assert errors == []
    assert len(results) == 1
    names = format_names(responder)
    prefix = "A" if mode == "GENERATE" else "B"
    assert names[:4] == [
        f"{prefix}0R_REQUIREMENTS_V2",
        f"{prefix}1_PLAN_V2",
        f"{prefix}2_SPINE_V1",
        f"{prefix}2_FILE_SPEC_V1",
    ]
    quality_name = f"{prefix}2Q_QUALITY_GATE_V2"
    assert (quality_name in names) is maximum_quality
    assert all("previous_response_id" not in call for call in responder.calls)

    snapshot = worker.cfg.preparation_snapshot
    assert snapshot["version"] == 2
    assert snapshot["graph"]["contract"] == "IMPLEMENTATION_GRAPH_V3"
    assert snapshot["graph"]["mode"] == mode
    assert snapshot["source_snapshot_hash"]
    assert snapshot["snapshot_hash"]

    out = Path(worker.cfg.out_dir) / "hello.txt"
    if batch:
        assert results[0]["status"] == "batch_pending"
        client.create_batch.assert_called_once()
        rows = client.create_batch.call_args.kwargs["_prevalidated_rows"]
        assert len(rows) == 1
        body = rows[0]["body"]
        assert body["text"]["format"]["name"] == "FILE_CONTENT_V1"
        assert set(body["text"]["format"]["schema"]["properties"]) == {"content"}
        for forbidden in (
            "previous_response_id",
            "conversation",
            "background",
            "tools",
            "service_tier",
        ):
            assert forbidden not in body
        assert not out.exists()
    else:
        assert results[0]["status"] == "files_complete_unverified"
        assert names[-1] == "FILE_CONTENT_V1"
        assert not out.exists()
        staged = staged_path(worker, "hello.txt")
        assert staged.read_text(encoding="utf-8") == "content:hello.txt\n"
        state = json.loads(Path(worker.log.state_path).read_text(encoding="utf-8"))
        assert state["publication_state"] == "awaiting_verification_or_explicit_take"
        assert state["verification_evidence"]["result"] == "needs_human"


@pytest.mark.parametrize("mode", ["GENERATE", "MODIFY"])
@pytest.mark.parametrize("maximum_quality", [False, True])
def test_stop_after_plan_finishes_after_optional_quality_gate_without_production(
    tmp_path, mode, maximum_quality
):
    worker, client, responder = scenario(
        tmp_path,
        mode,
        batch=True,
        maximum_quality=maximum_quality,
        stop_after_plan=True,
    )
    results, errors = run(worker, client)
    assert errors == []
    assert results[0]["status"] == "plan_ready"
    names = format_names(responder)
    assert "FILE_CONTENT_V1" not in names
    assert (("A2Q_QUALITY_GATE_V2" if mode == "GENERATE" else "B2Q_QUALITY_GATE_V2") in names) is maximum_quality
    client.create_batch.assert_not_called()
    state = json.loads(Path(worker.log.state_path).read_text(encoding="utf-8"))
    assert state["status"] == "plan_ready"
    assert state["preparation_snapshot"]["graph"]["contract"] == "IMPLEMENTATION_GRAPH_V3"


def test_modify_dry_run_stages_diff_and_never_changes_out(tmp_path):
    files = [
        {
            **default_files("MODIFY")[0],
            "path": "hello.txt",
            "action": "modify",
        }
    ]
    worker, client, _responder = scenario(
        tmp_path,
        "MODIFY",
        batch=False,
        maximum_quality=False,
        files=files,
        content_by_path={"hello.txt": "original\n"},
        dry_run=True,
    )
    out = Path(worker.cfg.out_dir)
    out.mkdir(exist_ok=True)
    target = out / "hello.txt"
    target.write_text("user-out\n", encoding="utf-8")

    results, errors = run(worker, client)
    assert errors == []
    assert results[0]["status"] == "dry_run"
    assert target.read_text(encoding="utf-8") == "user-out\n"
    state = json.loads(Path(worker.log.state_path).read_text(encoding="utf-8"))
    assert state["dry_run"] is True
    assert state["published_files"] == []
    staging_root = Path(worker.log.paths.run_dir) / state["staging_root"]
    assert (staging_root / "changes.diff").is_file()
    with pytest.raises(OrchestrationError, match="PUBLISH_DRY_RUN"):
        publish_staged_run(worker.log.paths.run_dir)


def test_explicit_unverified_take_publishes_only_if_expected_hash_still_matches(tmp_path):
    worker, client, _responder = scenario(tmp_path, "GENERATE")
    results, errors = run(worker, client)
    assert errors == [] and results[0]["status"] == "files_complete_unverified"
    target = Path(worker.cfg.out_dir) / "hello.txt"
    assert not target.exists()

    report = publish_staged_run(worker.log.paths.run_dir)
    assert report["status"] == "committed"
    assert target.read_text(encoding="utf-8") == "content:hello.txt\n"
    state = json.loads(Path(worker.log.state_path).read_text(encoding="utf-8"))
    assert state["status"] == "completed_unverified"
    assert state["unverified_publish_approved"] is True


def test_publish_conflict_preserves_user_change(tmp_path):
    worker, client, _responder = scenario(tmp_path, "GENERATE")
    results, errors = run(worker, client)
    assert errors == [] and results
    target = Path(worker.cfg.out_dir) / "hello.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("new user work\n", encoding="utf-8")

    with pytest.raises(Exception, match="PUBLISH_CONFLICT|expected"):
        publish_staged_run(worker.log.paths.run_dir)
    assert target.read_text(encoding="utf-8") == "new user work\n"


@pytest.mark.parametrize("mode", ["GENERATE", "MODIFY"])
def test_invalid_spine_dependency_blocks_before_a3_b3_or_batch(tmp_path, mode):
    worker, client, responder = scenario(tmp_path, mode, batch=True)

    original = client.create_response.side_effect

    def invalid(payload):
        value = original(payload)
        name = ((payload.get("text") or {}).get("format") or {}).get("name")
        if name in {"A2_SPINE_V1", "B2_SPINE_V1"}:
            decoded = json.loads(value["output_text"])
            decoded["result"]["data"]["files"][0]["dependencies"] = ["missing.py"]
            value["output_text"] = json.dumps(decoded)
        return value

    client.create_response.side_effect = invalid
    results, errors = run(worker, client)
    assert results == []
    assert errors
    assert "dependency" in errors[0].lower() or "závis" in errors[0].lower()
    assert "FILE_CONTENT_V1" not in format_names(responder)
    client.create_batch.assert_not_called()


@pytest.mark.parametrize("mode", ["GENERATE", "MODIFY"])
def test_live_verified_content_dependency_runs_provider_before_consumer(tmp_path, mode):
    action = "generate" if mode == "GENERATE" else "add"
    files = [
        {
            **default_files(mode)[0],
            "path": "provider.txt",
            "action": action,
        },
        {
            **default_files(mode)[0],
            "path": "consumer.txt",
            "action": action,
            "dependencies": ["provider.txt"],
            "content_dependencies": ["provider.txt"],
        },
    ]
    worker, client, responder = scenario(
        tmp_path,
        mode,
        batch=False,
        files=files,
    )
    results, errors = run(worker, client)
    assert errors == []
    assert results[0]["status"] == "files_complete_unverified"
    file_calls = [
        call
        for call in responder.calls
        if ((call.get("text") or {}).get("format") or {}).get("name")
        == "FILE_CONTENT_V1"
    ]
    paths = [
        json.loads(
            "".join(
                part["text"]
                for message in call["input"]
                for part in message["content"]
                if part.get("type") == "input_text"
            )
        )["file_context"]["working_context"]["target_file"]["path"]
        for call in file_calls
    ]
    assert paths == ["provider.txt", "consumer.txt"]
    consumer = json.loads(
        "".join(
            part["text"]
            for message in file_calls[1]["input"]
            for part in message["content"]
            if part.get("type") == "input_text"
        )
    )
    deps = consumer["file_context"]["working_context"]["verified_dependency_artifacts"]
    assert [row["path"] for row in deps] == ["provider.txt"]
    assert deps[0]["validation_status"] == "verified"


def test_batch_first_wave_excludes_verified_content_consumer(tmp_path):
    files = [
        {
            **default_files("GENERATE")[0],
            "path": "provider.txt",
        },
        {
            **default_files("GENERATE")[0],
            "path": "consumer.txt",
            "dependencies": ["provider.txt"],
            "content_dependencies": ["provider.txt"],
        },
    ]
    worker, client, _responder = scenario(
        tmp_path,
        "GENERATE",
        batch=True,
        files=files,
    )
    results, errors = run(worker, client)
    assert errors == []
    assert results[0]["status"] == "batch_pending"
    state = json.loads(Path(worker.log.state_path).read_text(encoding="utf-8"))
    manifest = state["generate_batch"]
    assert set(manifest["expected"].values()) == {"provider.txt"}
    assert manifest["deferred_paths"] == ["consumer.txt"]


def test_long_prompt_is_frozen_byte_exactly_without_paid_acknowledgement(tmp_path):
    worker, client, responder = scenario(tmp_path, "GENERATE", stop_after_plan=True)
    worker.cfg.prompt = "žluťoučký kůň\n" * 12000
    results, errors = run(worker, client)
    assert errors == [] and results[0]["status"] == "plan_ready"
    source = Path(worker.log.paths.misc_dir) / "source_pack_user_text.txt"
    assert source.read_bytes() == worker.cfg.prompt.encode("utf-8")
    state = json.loads(Path(worker.log.state_path).read_text(encoding="utf-8"))
    assert state["source_pack_hash"]
    # Preparation starts directly with A0R; there is no paid "acknowledgement".
    assert format_names(responder)[0] == "A0R_REQUIREMENTS_V2"


def test_file_content_wire_has_only_content_property(tmp_path):
    worker, client, responder = scenario(tmp_path, "GENERATE")
    results, errors = run(worker, client)
    assert errors == [] and results
    call = next(
        payload
        for payload in responder.calls
        if ((payload.get("text") or {}).get("format") or {}).get("name")
        == "FILE_CONTENT_V1"
    )
    schema = call["text"]["format"]["schema"]
    assert set(schema["properties"]) == {"content"}
    assert schema["required"] == ["content"]


def test_batch_import_stages_result_and_never_writes_out(tmp_path):
    worker, client, _responder = scenario(tmp_path, "GENERATE", batch=True)
    results, errors = run(worker, client)
    assert errors == [] and results[0]["status"] == "batch_pending"
    state = json.loads(Path(worker.log.state_path).read_text(encoding="utf-8"))
    manifest = state["generate_batch"]
    completed = {
        "id": "batch_work",
        "status": "completed",
        "input_file_id": state["batch_input_file_id"],
        "endpoint": "/v1/responses",
        "output_file_id": "file_results",
    }
    client.file_content.return_value = raw_jsonl(batch_output_rows(manifest))
    result = process_saved_batch(
        client,
        worker.log.paths.run_dir,
        "batch_work",
        worker.settings,
        batch=completed,
    )
    assert result["status"] == "files_complete_unverified"
    assert result["written"] == []
    assert not (Path(worker.cfg.out_dir) / "hello.txt").exists()
    staged = Path(worker.log.paths.run_dir) / result["staged_files"][0]["staged_path"]
    assert staged.read_text(encoding="utf-8") == "content:hello.txt\n"


def test_completed_hash_blocks_changed_rerun_before_network(tmp_path):
    worker, client, _responder = scenario(tmp_path, "GENERATE")
    out = Path(worker.cfg.out_dir)
    out.mkdir(exist_ok=True)
    target = out / "done.txt"
    target.write_text("changed", encoding="utf-8")
    worker.cfg.skip_paths = ["done.txt"]
    worker.cfg.completed_hashes = {
        "done.txt": hashlib.sha256(b"old").hexdigest()
    }
    results, errors = run(worker, client)
    assert results == []
    assert errors
    assert client.create_response.call_count == 0
