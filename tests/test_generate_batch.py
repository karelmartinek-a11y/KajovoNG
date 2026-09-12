import copy
import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from kajovo.core.contracts import ContractError
from kajovo.core.generate_batch import (
    build_manifest,
    encode_requests,
    import_results,
    process_saved_batch,
    repeat_saved_batch,
)
from test_workflows import make_worker, response


def specification():
    return {
        "contract": "A2_STRUCTURE",
        "version": 2,
        "rules": ["Python 3.12"],
        "packages": [],
        "interfaces": [
            {"id": "add", "definition": "maths.py exports add(a: int, b: int) -> int"}
        ],
        "files": [
            {
                "path": "maths.py",
                "purpose": "součet",
                "language": "Python",
                "kind": "text",
                "dependencies": [],
                "provides": ["add"],
                "requires": [],
                "behavior": "Vrátí a+b.",
            },
            {
                "path": "main.py",
                "purpose": "vstup",
                "language": "Python",
                "kind": "text",
                "dependencies": ["maths.py"],
                "provides": [],
                "requires": ["add"],
                "behavior": "Vypíše add(2,3).",
            },
        ],
    }


def manifest():
    return build_manifest(
        "run_test", "test", {"contract": "A1_PLAN"}, specification(), "gpt-4.1-nano", 0
    )


def test_unknown_work_submission_blocks_resume(tmp_path):
    from kajovo.core.runlog import RunLogger

    worker = make_worker(tmp_path, "GENERATE")
    client = Mock()
    client.upload_file.return_value = {"id": "file_work"}
    client.create_batch.side_effect = TimeoutError("Neznámý výsledek")
    with pytest.raises(TimeoutError):
        worker._submit_generate_batch(client, manifest())
    with pytest.raises(ValueError):
        RunLogger(worker.settings.log_dir, worker.log.run_id, resume=True)


def test_work_submission_uses_locally_validated_rows_without_second_submit(tmp_path):
    worker = make_worker(tmp_path, "GENERATE")
    client = Mock()
    client.upload_file.return_value = {"id": "file_work"}
    client.create_batch.return_value = {"id": "batch_work", "status": "validating"}
    result = worker._submit_generate_batch(client, manifest())
    assert result["batch_id"] == "batch_work"
    client.create_batch.assert_called_once()
    kwargs = client.create_batch.call_args.kwargs
    assert kwargs["input_file_id"] == "file_work"
    assert kwargs["endpoint"] == "/v1/responses"
    assert kwargs["_prevalidated_rows"] == manifest()["requests"]
    state = json.loads(Path(worker.log.state_path).read_text(encoding="utf-8"))
    assert state["submission_unknown"] is False


def outputs(m):
    return [
        {
            "custom_id": cid,
            "response": {
                "status_code": 200,
                "body": {
                    **response(
                        i,
                        {
                            "contract": "A3_FILE",
                            "path": path,
                            "content": "content\n",
                            "chunking": {
                                "chunk_index": 0,
                                "chunk_count": 1,
                                "has_more": False,
                                "next_chunk_index": None,
                            },
                        },
                    ),
                    "status": "completed",
                },
            },
        }
        for i, (cid, path) in enumerate(m["expected"].items())
    ]


def raw(rows):
    return "\n".join(json.dumps(row) for row in rows).encode()


def test_live_preparation_then_one_request_per_file(tmp_path):
    worker = make_worker(tmp_path, "GENERATE")
    worker.cfg.send_as_c = True
    client = Mock()
    client.create_response.side_effect = [
        response(1, {"contract": "A1_PLAN"}),
        response(2, specification()),
    ]
    client.upload_file.return_value = {"id": "file_input"}
    client.create_batch.return_value = {"id": "batch_test"}
    results, errors = [], []
    worker.finished_ok.connect(results.append)
    worker.finished_err.connect(errors.append)
    with patch("kajovo.core.pipeline.OpenAIClient", return_value=client):
        worker.run()
    assert not errors
    assert client.create_response.call_count == 2
    rows = [
        json.loads(line)
        for line in Path(client.upload_file.call_args.args[0])
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert len(rows) == 2
    assert all("previous_response_id" not in row["body"] for row in rows)
    assert all(row["body"]["store"] is False for row in rows)
    snapshots = [json.loads(row["body"]["input"])["specification"] for row in rows]
    assert snapshots[0] == snapshots[1]
    assert not (tmp_path / "out" / "maths.py").exists()
    state = json.loads(Path(worker.log.state_path).read_text(encoding="utf-8"))
    assert state["status"] == "batch_pending"
    assert state["generate_batch"]["snapshot"]["structure"] == specification()


@pytest.mark.parametrize(
    "fault", ["duplicate", "path", "incomplete", "missing", "chunk", "refusal", "error"]
)
def test_invalid_file_preserves_other_results(tmp_path, fault):
    m = manifest()
    rows = outputs(m)
    first = rows[0]
    if fault == "duplicate":
        rows.append(copy.deepcopy(first))
    elif fault == "missing":
        rows.pop(0)
    elif fault == "incomplete":
        first["response"]["body"]["status"] = "incomplete"
    elif fault == "error":
        first["error"] = {"code": "batch_expired"}
    elif fault == "refusal":
        first["response"]["body"]["output"] = [{"content": [{"type": "refusal"}]}]
    else:
        payload = json.loads(first["response"]["body"]["output_text"])
        if fault == "path":
            payload["path"] = "../escape.py"
        else:
            payload["chunking"]["has_more"] = True
        first["response"]["body"]["output_text"] = json.dumps(payload)
    result = import_results(m, [raw(rows[::-1])], str(tmp_path))
    assert result["status"] == "partial"
    assert not (tmp_path / "maths.py").exists()
    assert (tmp_path / "main.py").exists()


def test_unknown_id_prevents_all_writes(tmp_path):
    m = manifest()
    rows = outputs(m)
    rows[-1]["custom_id"] = "foreign"
    with pytest.raises(ContractError):
        import_results(m, [raw(rows)], str(tmp_path))
    assert not list(tmp_path.iterdir())


def test_reimport_protects_user_changes(tmp_path):
    m = manifest()
    data = raw(outputs(m))
    result = import_results(m, [data], str(tmp_path))
    assert result["status"] == "files_complete_unverified"
    assert not import_results(m, [data], str(tmp_path), result["hashes"])["errors"]
    (tmp_path / "maths.py").write_text("user change", encoding="utf-8")
    result = import_results(m, [data], str(tmp_path), result["hashes"])
    assert result["errors"]
    assert (tmp_path / "maths.py").read_text() == "user change"


def test_snapshot_tampering_rejected():
    m = manifest()
    m["snapshot"]["prompt"] = "different"
    with pytest.raises(ContractError):
        encode_requests(m)


def test_unknown_dependency_rejected():
    spec = specification()
    spec["files"][0]["dependencies"] = ["missing.py"]
    with pytest.raises(ContractError):
        build_manifest("r", "p", {}, spec, "gpt-4.1-nano", 0)


@pytest.mark.parametrize("fault", ["case", "provider", "binary", "package", "version"])
def test_invalid_specifications(fault):
    spec = specification()
    if fault == "case":
        spec["files"][1]["path"] = "MATHS.PY"
    elif fault == "provider":
        spec["files"][0]["provides"] = []
    elif fault == "binary":
        spec["files"][1]["path"] = "image.png"
    elif fault == "package":
        spec["packages"] = [{"name": "dependency", "version": ""}]
    else:
        spec["version"] = 1
    with pytest.raises(ContractError):
        build_manifest("r", "p", {}, spec, "gpt-4.1-nano", 0)


def test_request_context_and_map_must_match():
    m = manifest()
    m["requests"][0]["body"]["input"] = m["requests"][1]["body"]["input"]
    with pytest.raises(ContractError):
        encode_requests(m)


def test_invalid_preparation_never_submits_batch(tmp_path):
    worker = make_worker(tmp_path, "GENERATE")
    worker.cfg.send_as_c = True
    bad = specification()
    bad["files"][1]["requires"] = ["unknown"]
    client = Mock()
    client.create_response.side_effect = [response(0, {"contract": "A1_PLAN"})] + [
        response(i + 1, bad) for i in range(3)
    ]
    errors = []
    worker.finished_err.connect(errors.append)
    with patch("kajovo.core.pipeline.OpenAIClient", return_value=client):
        worker.run()
    assert errors
    assert client.create_response.call_count == 4
    client.create_batch.assert_not_called()


def test_reasoning_batch_has_no_sampling():
    m = build_manifest("r", "p", {}, specification(), "gpt-5-mini", 0.7)
    assert all("temperature" not in r["body"] for r in m["requests"])


def test_preparation_capabilities_follow_override_models(tmp_path):
    from kajovo.core.request_rules import validate_run_options

    worker = make_worker(tmp_path, "GENERATE")
    worker.cfg.send_as_c = True
    worker.cfg.model_a1 = worker.cfg.model_a2 = "gpt-4.1-mini"
    worker.cfg.attached_vector_store_ids = ["vs_test"]
    worker.cfg.model_caps["supports_file_search"] = False
    worker.cfg.caps_by_model = {
        "gpt-4.1-mini": {
            "ok_basic": True,
            "supports_file_search": True,
            "supports_previous_response_id": True,
        }
    }
    worker.cfg.available_models = ["gpt-4o-mini", "gpt-4.1-mini"]
    validate_run_options(worker.cfg)
    assert worker._preparation_cap("supports_file_search")
    worker.cfg.caps_by_model["gpt-4.1-mini"]["supports_file_search"] = False
    with pytest.raises(ValueError):
        validate_run_options(worker.cfg)


def test_output_change_after_repair_submission_is_preserved(tmp_path):
    m = manifest()
    first = import_results(m, [raw(outputs(m))], str(tmp_path))
    path = tmp_path / "maths.py"
    path.write_text("new user edit", encoding="utf-8")
    result = import_results(
        m, [raw(outputs(m))], str(tmp_path), first["hashes"], first["hashes"]
    )
    assert result["errors"]
    assert path.read_text() == "new user edit"


def test_restart_import_responses_and_selective_retry(tmp_path):
    worker = make_worker(tmp_path, "GENERATE")
    m = manifest()
    worker.log.update_state(
        {
            "generate_batch": m,
            "batch_id": "batch_test",
            "out_dir": str(tmp_path / "out"),
        }
    )
    client = Mock()
    client.retrieve_batch.return_value = {
        "status": "completed",
        "output_file_id": "file_output",
    }
    client.file_content.return_value = raw(outputs(m))
    for _ in range(2):
        result = process_saved_batch(
            client, worker.log.paths.run_dir, "batch_test", worker.settings
        )
        assert result["status"] == "files_complete_unverified"
    client.upload_file.return_value = {"id": "file_retry"}
    client.create_batch.return_value = {"id": "batch_retry"}
    repeat_saved_batch(
        client,
        worker.log.paths.run_dir,
        "batch_test",
        ["maths.py"],
        "Fix sum",
    )
    state = json.loads(Path(worker.log.state_path).read_text(encoding="utf-8"))
    retry = state["generate_batches"]["batch_retry"]
    assert len(retry["requests"]) == 1
    assert retry["snapshot_hash"] == m["snapshot_hash"]
    assert "current_content" in retry["requests"][0]["body"]["input"]
    client.create_response.assert_not_called()
