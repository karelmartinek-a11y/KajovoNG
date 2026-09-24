import copy
import json
from pathlib import Path
from unittest.mock import Mock

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
from delivery_fixtures import implementation_fixture


def specification():
    return implementation_fixture({
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
    })


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
    client.create_batch.return_value = {"id": "batch_work", "status": "validating", "input_file_id": "file_work", "endpoint": "/v1/responses"}
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
    from change_v2_fixtures import batch_output_rows
    if m.get("version") == 3:
        return batch_output_rows(m, dict.fromkeys(m["expected"].values(), "content\n"))
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


@pytest.mark.parametrize("code", ["context_length_exceeded", "max_output_tokens"])
def test_batch_keeps_provider_cause_and_file_identity(tmp_path, code):
    m = manifest()
    rows = outputs(m)
    body = rows[0]["response"]["body"]
    body["status"] = "incomplete" if code == "max_output_tokens" else "failed"
    body["incomplete_details" if code == "max_output_tokens" else "error"] = {
        "reason" if code == "max_output_tokens" else "code": code}
    result = import_results(m, [raw(rows)], str(tmp_path))
    cid = rows[0]["custom_id"]
    detail = result["error_details"][cid]
    assert detail["code"] == code and detail["cause_known"]
    assert detail["path"] == m["expected"][cid]
    assert not (tmp_path / detail["path"]).exists()
    assert result["status"] == "partial"


def test_live_preparation_then_one_request_per_file(tmp_path):
    from change_v2_fixtures import default_files, format_names, run, scenario

    files = [
        {**default_files("GENERATE")[0], "path": path}
        for path in ("maths.py", "main.py")
    ]
    files[1]["dependencies"] = ["maths.py"]
    worker, client, responder = scenario(tmp_path, "GENERATE", batch=True, files=files)
    results, errors = run(worker, client)
    assert not errors, errors
    assert results[0]["status"] == "batch_pending"
    assert format_names(responder) == [
        "A0R_REQUIREMENTS_V2", "A1_PLAN_V2", "A2_SPINE_V2",
        "A2_FILE_SPEC_V1", "A2_FILE_SPEC_V1",
    ]
    client.upload_file.assert_called_once()
    client.create_batch.assert_called_once()
    rows = [
        json.loads(line)
        for line in Path(client.upload_file.call_args.args[0])
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert len(rows) == 2
    assert all("previous_response_id" not in row["body"] for row in rows)
    assert all(row["body"]["store"] is False for row in rows)
    contexts = [json.loads(row["body"]["input"]) for row in rows]
    assert all("specification" not in context for context in contexts)
    assert all(row["body"]["text"]["format"]["name"] == "FILE_CONTENT_V1" for row in rows)
    assert {context["file_context"]["working_context"]["target_file"]["path"] for context in contexts} == {
        "maths.py", "main.py",
    }
    assert contexts[0]["file_context"]["file_context_hash"] != contexts[1]["file_context"]["file_context_hash"]
    assert all(not (tmp_path / "out" / path).exists() for path in ("maths.py", "main.py"))
    state = json.loads(Path(worker.log.state_path).read_text(encoding="utf-8"))
    assert state["status"] == "batch_pending"
    graph = state["generate_batch"]["snapshot"]["structure"]
    assert graph["contract"] == "IMPLEMENTATION_GRAPH_V3"
    assert {row["path"] for row in graph["spine"]["files"]} == {"maths.py", "main.py"}
    assert {row["path"] for row in graph["file_specs"]} == {"maths.py", "main.py"}
    assert state["generate_batch"]["requests"] == rows


@pytest.mark.parametrize(
    "fault", ["duplicate", "path", "incomplete", "missing", "chunk", "refusal", "error"]
)
def test_invalid_file_preserves_other_results(tmp_path, fault):
    m = manifest()
    rows = outputs(m)
    first = rows[0]
    if fault == "duplicate":
        rows.append(copy.deepcopy(first))
        with pytest.raises(ContractError, match="Duplicitní"):
            import_results(m, [raw(rows[::-1])], str(tmp_path))
        assert not list(tmp_path.iterdir())
        return
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
            payload["chunking"] = {"has_more": True}
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


def test_request_context_rejects_trailing_json():
    m = manifest()
    m["requests"][0]["body"]["input"] += " {}"
    with pytest.raises(ContractError):
        encode_requests(m)


def test_invalid_preparation_never_submits_batch(tmp_path):
    from change_v2_fixtures import format_names, run, scenario

    def invalid_spine(name, value, _data):
        if name == "A2_SPINE_V2":
            value["result"]["data"]["files"][0]["requires"] = ["unknown"]
        return value

    worker, client, responder = scenario(
        tmp_path, "GENERATE", batch=True, mutate=invalid_spine,
    )
    results, errors = run(worker, client)
    assert not results
    assert errors
    assert "hello.txt: neznámé interface binding." in errors[0]
    assert format_names(responder) == [
        "A0R_REQUIREMENTS_V2", "A1_PLAN_V2", "A2_SPINE_V2",
    ]
    client.upload_file.assert_not_called()
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


@pytest.mark.parametrize("legacy,retry_original,repair_allowed", [
    (True, True, False), (False, True, False), (False, True, True), (False, False, True),
])
def test_restart_import_responses_and_selective_retry(tmp_path, legacy, retry_original, repair_allowed):
    from change_v2_fixtures import batch_output_rows, run, scenario

    if legacy:
        worker = make_worker(tmp_path, "GENERATE")
        m = manifest()
        worker.log.update_state({
            "generate_batch": m, "batch_id": "batch_legacy",
            "out_dir": str(tmp_path / "out"),
        })
        client = Mock()
        client.retrieve_batch.return_value = {
            "id": "batch_legacy", "input_file_id": "file_legacy", "endpoint": "/v1/responses",
            "status": "completed", "output_file_id": "file_output",
        }
        client.file_content.return_value = raw(outputs(m))
        for _ in range(2):
            result = process_saved_batch(
                client, worker.log.paths.run_dir, "batch_legacy", worker.settings
            )
            assert result["status"] == "files_complete_unverified"
        before = Path(worker.log.state_path).read_bytes()
        with pytest.raises(ContractError, match="nový WorkOrder.*novou přípravu"):
            repeat_saved_batch(client, worker.log.paths.run_dir, "batch_legacy", ["maths.py"])
        assert Path(worker.log.state_path).read_bytes() == before
        client.upload_file.assert_not_called()
        client.create_batch.assert_not_called()
        client.create_response.assert_not_called()
        return

    worker, client, _ = scenario(
        tmp_path, "GENERATE", batch=True,
        files=[{"path": path, "action": "generate"} for path in ("maths.py", "main.py")],
    )
    worker.cfg.auto_repair = "within_approval" if repair_allowed else "off"
    results, errors = run(worker, client)
    assert results and not errors
    state = json.loads(Path(worker.log.state_path).read_text(encoding="utf-8"))
    m = state["generate_batch"]
    batch_id = state["batch_id"]
    client.reset_mock()
    client.retrieve_batch.return_value = {
        "id": batch_id, "input_file_id": "file_batch_input", "endpoint": "/v1/responses",
        "status": "completed",
        "output_file_id": "file_output",
    }
    rows = batch_output_rows(m)
    for row in rows:
        row["response"]["body"]["id"] = "resp_" + row["custom_id"]
    client.file_content.return_value = raw(rows)
    for _ in range(2):
        result = process_saved_batch(
            client, worker.log.paths.run_dir, batch_id, worker.settings
        )
        assert result["status"] == "files_complete_unverified"
    client.upload_file.return_value = {"id": "file_retry"}
    client.create_batch.return_value = {"id": "batch_retry", "input_file_id": "file_retry", "endpoint": "/v1/responses"}
    state_path = Path(worker.log.state_path)
    approved_state = state_path.read_bytes()
    for key, value in (
        ("scope_hash", "jiný rozsah"), ("approval_id", "jiné schválení"),
    ):
        denied = json.loads(approved_state)
        denied["execution_authorization"][key] = value
        state_path.write_text(json.dumps(denied), encoding="utf-8")
        with pytest.raises(ContractError, match="explicitní autorizaci"):
            repeat_saved_batch(client, worker.log.paths.run_dir, batch_id, ["maths.py"])
    client.upload_file.assert_not_called()
    client.create_batch.assert_not_called()
    state_path.write_bytes(approved_state)
    import sqlite3
    from unittest.mock import patch

    with patch(
        "kajovo.core.orchestration.repository.OrchestrationRepository.register_work_order",
        side_effect=sqlite3.IntegrityError("UNIQUE constraint failed"),
    ), pytest.raises(ContractError, match="souběžné operace"):
        repeat_saved_batch(client, worker.log.paths.run_dir, batch_id, ["maths.py"])
    client.upload_file.assert_not_called()
    client.create_batch.assert_not_called()
    assert state_path.read_bytes() == approved_state
    repeat_saved_batch(
        client,
        worker.log.paths.run_dir,
        batch_id,
        ["maths.py"],
        "Fix sum",
    )
    state = json.loads(Path(worker.log.state_path).read_text(encoding="utf-8"))
    retry = state["generate_batches"]["batch_retry"]
    assert state["generate_batch"] == m
    assert state["batch_id"] == batch_id
    assert len(retry["requests"]) == 1
    assert set(retry["expected"].values()) == {"maths.py"}
    order = next(iter(retry["work_orders"].values()))
    original_order = next(value for value in m["work_orders"].values() if value["target_path"] == "maths.py")
    assert order["task_id"] == original_order["task_id"]
    assert order["attempt_no"] == 2
    assert order["version"] == 3 and order["attempt_kind"] == "manual"
    assert order["attempt_id"] != original_order["attempt_id"]
    assert retry["snapshot_hash"] == m["snapshot_hash"]
    context = json.loads(retry["requests"][0]["body"]["input"])
    assert context["recovery_instruction"] == "Fix sum"
    assert context["repair_current_artifact"]["content"] == "content:maths.py\n"
    assert context["repair_current_artifact"]["sha256"] == state["generated_hashes"]["maths.py"]
    encode_requests(retry)
    client.create_response.assert_not_called()
    with pytest.raises(ContractError, match="předchozí pokus"):
        repeat_saved_batch(client, worker.log.paths.run_dir, batch_id, ["maths.py"])
    for current_id, current_manifest in (("batch_retry", retry), ("batch_retry3", None)):
        if current_manifest is None:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            current_manifest = state["generate_batches"][current_id]
        rows = batch_output_rows(current_manifest)
        for row in rows:
            row["response"]["body"]["id"] = "resp_" + row["custom_id"]
        client.file_content.return_value = raw(rows)
        client.retrieve_batch.return_value.update(id=current_id, input_file_id="file_retry")
        result = process_saved_batch(client, worker.log.paths.run_dir, current_id, worker.settings)
        assert result["status"] == "files_complete_unverified"
        if current_id == "batch_retry":
            client.create_batch.return_value = {"id": "batch_retry3", "input_file_id": "file_retry", "endpoint": "/v1/responses"}
            repeat_saved_batch(
                client, worker.log.paths.run_dir,
                batch_id if retry_original else current_id, ["maths.py"], "Fix sum again",
            )
    state = json.loads(state_path.read_text(encoding="utf-8"))
    third = state["generate_batches"]["batch_retry3"]
    third_order = next(iter(third["work_orders"].values()))
    assert third_order["attempt_no"] == 3
    assert third_order["task_id"] == original_order["task_id"]
    assert third_order["attempt_id"] not in {
        order["attempt_id"], original_order["attempt_id"],
    }
    assert third["snapshot"] == retry["snapshot"] == m["snapshot"]
    assert state["generate_batch"] == m
    assert state["batch_id"] == batch_id
    client.create_batch.return_value = {"id": "batch_retry4", "input_file_id": "file_retry", "endpoint": "/v1/responses"}
    repeat_saved_batch(client, worker.log.paths.run_dir,
                       batch_id if retry_original else "batch_retry3", ["maths.py"])
    state = json.loads(state_path.read_text(encoding="utf-8"))
    fourth = state["generate_batches"]["batch_retry4"]
    fourth_order = next(iter(fourth["work_orders"].values()))
    assert fourth_order["attempt_no"] == 4 and fourth_order["attempt_kind"] == "manual"
    assert state["generate_batch"] == m
    assert client.create_batch.call_count == client.upload_file.call_count == 3
    client.create_response.assert_not_called()
