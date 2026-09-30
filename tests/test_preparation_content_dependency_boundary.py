"""Cyklus výrobních podkladů se opravuje před přijetím SPINE, nikoli po DETAIL."""

from copy import deepcopy

import pytest

from kajovo.core.contracts import ContractError
from kajovo.core.orchestration.contracts import canonical_sha256
from kajovo.core.orchestration.waves import build_execution_dag
from preparation_v2_helpers import decoded, preparation_scenario, prepare


def _cycle(spine, *, content=True):
    rows = spine["files"]
    for index, row in enumerate(rows):
        other = rows[1 - index]["path"]
        row["dependencies"] = [other]
        row["content_dependencies"] = [other] if content else []
        row["dependency_content_mode"] = "verified_content" if content else "contract"
        row["dependency_content_reason"] = "Potřebuji obsah druhého souboru." if content else ""


def _scenario(tmp_path, mode, *, mutate=None, quality=False):
    worker, client, responder = preparation_scenario(tmp_path, mode, mutate=mutate, quality=quality)
    worker.cfg.auto_repair = "within_approval"
    second = deepcopy(responder.files[0])
    second["path"] = "second.txt"
    responder.files.append(second)
    return worker, client, responder


@pytest.mark.parametrize("mode", ["GENERATE", "MODIFY"])
def test_content_cycle_is_repaired_in_spine_before_any_detail(tmp_path, mode):
    rejected = []

    def mutate(name, value, context):
        if name.endswith("SPINE_V2") and not rejected:
            _cycle(value["result"]["data"])
            rejected.append(deepcopy(value["result"]["data"]))
        return value

    worker, client, responder = _scenario(tmp_path, mode, mutate=mutate)
    prepare(worker, client)
    prefix = "A" if mode == "GENERATE" else "B"
    names = [call["text"]["format"]["name"] for call in responder.calls]
    assert names == [
        prefix + "0R_REQUIREMENTS_V2", prefix + "1_PLAN_V2",
        prefix + "2_SPINE_V2", prefix + "2_SPINE_V2",
        prefix + "2_FILE_SPEC_V1", prefix + "2_FILE_SPEC_V1",
    ]
    repair = decoded(responder.calls[3])
    assert repair["input"] == decoded(responder.calls[2])
    assert repair["repair"]["candidate"] == rejected[0]
    assert "CONTENT_DEPENDENCY_CYCLE" in repair["repair"]["error"]
    assert worker.cfg.preparation_snapshot["canonical_stage"] == prefix + "2"
    assert len(build_execution_dag({"spine": worker.cfg.preparation_snapshot["spine"]}).waves) == 1


@pytest.mark.parametrize("mode", ["GENERATE", "MODIFY"])
@pytest.mark.parametrize("policy,attempts,code", [
    ("off", 1, "CONTENT_DEPENDENCY_CYCLE"),
    ("within_approval", 2, "NO_PROGRESS"),
])
def test_unrepaired_content_cycle_never_accepts_spine_or_calls_detail(tmp_path, mode, policy, attempts, code):
    def mutate(name, value, context):
        if name.endswith("SPINE_V2"):
            _cycle(value["result"]["data"])
        return value

    worker, client, responder = _scenario(tmp_path, mode, mutate=mutate)
    worker.cfg.auto_repair = policy
    with pytest.raises(ContractError) as caught:
        prepare(worker, client)
    assert caught.value.code == code
    if policy == "off":
        assert caught.value.issues[0].stage == ("A2_SPINE" if mode == "GENERATE" else "B2_SPINE")
    names = [call["text"]["format"]["name"] for call in responder.calls]
    assert len([name for name in names if name.endswith("SPINE_V2")]) == attempts
    assert not any("FILE_SPEC" in name or "FILE_CONTENT" in name for name in names)
    assert worker.cfg.preparation_snapshot["canonical_stage"] == ("A1" if mode == "GENERATE" else "B1")
    assert not worker.cfg.preparation_snapshot.get("spine")
    assert worker.log.bundle.steps()[-1]["status"] == "failed"


@pytest.mark.parametrize("mode", ["GENERATE", "MODIFY"])
def test_contract_only_cycle_still_allows_same_production_wave(tmp_path, mode):
    def mutate(name, value, context):
        if name.endswith("SPINE_V2"):
            _cycle(value["result"]["data"], content=False)
        return value

    worker, client, responder = _scenario(tmp_path, mode, mutate=mutate)
    prepare(worker, client)
    dag = build_execution_dag({"spine": worker.cfg.preparation_snapshot["spine"]})
    assert len(dag.waves) == 1
    assert len(dag.waves[0]) == 2
    assert len([call for call in responder.calls if call["text"]["format"]["name"].endswith("SPINE_V2")]) == 1


@pytest.mark.parametrize("mode", ["GENERATE", "MODIFY"])
def test_quality_gate_cycle_is_repaired_in_quality_gate(tmp_path, mode):
    rejected = []

    def mutate(name, value, context):
        if "QUALITY_GATE" in name and not rejected:
            _cycle(value["result"]["data"]["corrected_spine"])
            rejected.append(True)
        return value

    worker, client, responder = _scenario(tmp_path, mode, mutate=mutate, quality=True)
    prepare(worker, client)
    names = [call["text"]["format"]["name"] for call in responder.calls]
    assert sum("QUALITY_GATE" in name for name in names) == 2
    assert sum("SPINE_V2" in name for name in names) == 1
    assert "CONTENT_DEPENDENCY_CYCLE" in decoded(responder.calls[-1])["repair"]["error"]


@pytest.mark.parametrize("mode", ["GENERATE", "MODIFY"])
def test_restored_invalid_spine_blocks_before_provider_without_rewriting_evidence(tmp_path, mode):
    worker, client, responder = _scenario(tmp_path, mode)
    prepare(worker, client)
    snapshot = deepcopy(worker.cfg.preparation_snapshot)
    _cycle(snapshot["spine"])
    snapshot["graph"] = None
    snapshot["file_specs"] = []
    snapshot["canonical_stage"] = "A2_SPINE" if mode == "GENERATE" else "B2_SPINE"
    snapshot.pop("snapshot_hash", None)
    snapshot["snapshot_hash"] = canonical_sha256(snapshot)
    worker.cfg.preparation_snapshot = deepcopy(snapshot)
    client.create_response.reset_mock()
    with pytest.raises(ContractError, match="CONTENT_DEPENDENCY_CYCLE"):
        prepare(worker, client)
    client.create_response.assert_not_called()
    assert worker.cfg.preparation_snapshot == snapshot
