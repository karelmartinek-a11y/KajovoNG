"""Přesný význam přípravných podkladů v LIVE a zmrazeném BATCH payloadu."""
import copy
import hashlib
import json

import pytest

from change_v2_fixtures import requirements_data, plan_data, spine_data, file_spec
from kajovo.core.context_compiler import ContextCompiler
from kajovo.core.contracts import ContractError
from kajovo.core.generate_batch import build_manifest, encode_requests
from kajovo.core.orchestration.contracts import canonical_sha256
from kajovo.core.orchestration.preparation import _source_subset, modify_obligations


def prepared(mode="GENERATE"):
    text = "Zdroj se zachovaným CRLF.\r\n"
    segments = [{"source_id": "S1", "segment_id": "S1-1", "text": text,
                 "start_byte": 0, "end_byte": len(text.encode()), "sha256": hashlib.sha256(text.encode()).hexdigest()}]
    req = requirements_data({"segments": segments})
    plan = plan_data()
    files = [{"path": "consumer.txt", "action": "generate" if mode == "GENERATE" else "modify"}]
    if mode == "MODIFY":
        files[0].update(dependencies=["preserved.txt"], content_dependencies=["preserved.txt"])
        files.append({"path": "preserved.txt", "action": "preserve"})
    spine = spine_data(mode, files)
    graph = {"contract": "IMPLEMENTATION_GRAPH_V3", "mode": mode,
             "source_snapshot_hash": "a" * 64, "requirements_hash": canonical_sha256(req), "plan_hash": canonical_sha256(plan),
             "spine": spine, "file_specs": [{"path": row["path"], "spec": file_spec(row, req["requirements"][0]["source_refs"])} for row in spine["files"]],
             "verification_profile_ids": []}
    return {"structure": graph, "requirements": req, "plan": plan, "source_segments": segments}


def test_batch_and_live_use_identical_source_segments_and_modify_obligations():
    snapshot = prepared("MODIFY")
    originals = {"consumer.txt": "old\r\n", "preserved.txt": "unchanged\r\n"}
    wrapper = {"change_requirements": snapshot["requirements"], "preserve": [{"id": "P1", "statement": "Zachovat ABI", "requirement_ids": []}],
               "migration_requirements": ["Migrovat existující data."]}
    manifest = build_manifest("RUN_TEST", "Prompt", snapshot["plan"], snapshot["structure"], "gpt-5.6-luna", None,
                              requirements=snapshot["requirements"], mode="MODIFY", originals=originals,
                              source_segments=snapshot["source_segments"], requirements_wrapper=wrapper,
                              completed_targets={"preserved.txt"}, expected_target_hashes={"consumer.txt": None})
    request = manifest["requests"][0]
    batch_context = json.loads(request["body"]["input"])["file_context"]
    live_context = ContextCompiler(manifest["snapshot"]).compile("consumer.txt", originals=originals)
    assert batch_context == live_context
    assert snapshot["source_segments"][0]["text"] in json.dumps(batch_context, ensure_ascii=False).replace("\\r", "\r").replace("\\n", "\n")
    assert batch_context["working_context"]["modify_obligations"] == modify_obligations(wrapper, ["REQ-1"])
    encode_requests(manifest)


def test_missing_or_changed_source_segment_is_not_silently_omitted():
    for fault in ("missing", "changed"):
        snapshot = prepared()
        if fault == "missing":
            snapshot["source_segments"] = []
        else:
            snapshot["source_segments"][0]["text"] = "jiný text"
        with pytest.raises(ContractError, match="segment"):
            ContextCompiler(snapshot).compile("consumer.txt")


def test_empty_requirements_subset_does_not_resend_all_attachments():
    from types import SimpleNamespace
    source = {"segments": prepared()["source_segments"], "attachments": [{"source_id": "S1"}], "image_slots": [{"source_id": "S2"}]}
    worker = SimpleNamespace(source_context=source)
    assert _source_subset(worker, []) == {"segments": [], "attachments": [], "image_slots": []}
    assert _source_subset(worker, None) == source


def test_archived_batch_import_does_not_recompile_under_new_compiler(monkeypatch):
    snapshot = prepared()
    manifest = build_manifest("RUN_TEST", "Prompt", snapshot["plan"], snapshot["structure"], "gpt-5.6-luna", None,
                              requirements=snapshot["requirements"], source_segments=snapshot["source_segments"],
                              expected_target_hashes={"consumer.txt": None})
    def incompatible(*args, **kwargs):
        raise AssertionError("Nový compiler není historický importer")
    monkeypatch.setattr(ContextCompiler, "compile", incompatible)
    monkeypatch.setattr("kajovo.core.generate_batch.validate_response_payload", incompatible)
    monkeypatch.setattr("kajovo.core.generate_batch.measure_request", incompatible)
    encode_requests(manifest, archived=True)
    changed = copy.deepcopy(manifest)
    changed["requests"][0]["body"]["instructions"] += " změna"
    with pytest.raises(ContractError):
        encode_requests(changed, archived=True)
