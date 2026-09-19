"""Příprava nesmí násobit historii ani odeslat kapacitně neověřený vstup."""
from copy import deepcopy
import base64
import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from kajovo.core.context_budget import preparation_measurement
from kajovo.core.context_compiler import content_hash
from kajovo.core.contracts import ContractError
from preparation_v2_helpers import decoded, preparation_scenario, prepare


def counted(payload, tokens=300000):
    return {"input_tokens": tokens, "request_hash": content_hash(payload)}


@pytest.mark.parametrize("mode", ["GENERATE", "MODIFY"])
@pytest.mark.parametrize("quality", [False, True])
def test_preparation_preserves_sources_once_without_inherited_history(tmp_path, mode, quality):
    source = "Přesné zadání"
    attachments = {
        "file_source": ("source.txt", b"Presny obsah prilohy"),
        "file_image": ("image.png", base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aE3sAAAAASUVORK5CYII=")),
    }
    worker, client, responder = preparation_scenario(
        tmp_path, mode, source=source, quality=quality, attachments=attachments)
    prepare(worker, client, previous_id="resp_old_chain")
    assert len(responder.calls) == 4 + int(quality)
    for payload in responder.calls:
        assert "previous_response_id" not in payload
        assert "conversation" not in payload
        assert payload["truncation"] == "disabled"
        context = decoded(payload)
        projection = context.get("source", context.get("change_source"))
        if projection is not None:
            segments = projection["segments"]
            assert "".join(row["text"] for row in segments if row["source_id"] == "SRC-USER-TEXT") == source
            assert len({(row["source_id"], row["segment_id"]) for row in segments}) == len(segments)
        assert json.dumps(context, ensure_ascii=False).count(source) <= 1
    first = decoded(responder.calls[0])
    assert first["source" if mode == "GENERATE" else "change_source"]["segments"]
    assert worker.source_context["segments"][0]["text"] == source
    assert len(worker.source_pack.sources) == 3
    assert len(worker.source_context["image_slots"]) == 1
    assert any(row["text"] == "Presny obsah prilohy" for row in worker.source_context["segments"])
    assert first["source" if mode == "GENERATE" else "change_source"]["image_slots"] == worker.source_context["image_slots"]
    assert client.file_content.call_count == 2
    client.count_input_tokens.assert_not_called()


@pytest.mark.parametrize("mode", ["GENERATE", "MODIFY"])
def test_resume_v2_plan_uses_saved_results_without_old_response_chain(tmp_path, mode):
    source = "obsah podkladu " * 10000
    worker, client, responder = preparation_scenario(tmp_path, mode, source=source)
    worker.cfg.model = worker.cfg.model_a1 = worker.cfg.model_a2 = "gpt-6-astra"
    prefix = "A" if mode == "GENERATE" else "B"

    def stop_after_plan():
        snapshot = worker.cfg.preparation_snapshot or {}
        if snapshot.get("canonical_stage") == prefix + "1":
            raise RuntimeError("Testovací zastavení po uloženém plánu")

    with patch.object(worker, "_check_stop", side_effect=stop_after_plan):
        with pytest.raises(RuntimeError, match="Testovací zastavení"):
            prepare(worker, client)
    checkpoint = deepcopy(worker.cfg.preparation_snapshot)
    assert checkpoint["canonical_stage"] == prefix + "1"
    assert len(responder.calls) == 2
    responder.calls.clear()
    client.count_input_tokens.reset_mock()
    prepare(worker, client, previous_id="resp_old_chain")
    assert [p["text"]["format"]["name"] for p in responder.calls] == [
        prefix + "2_SPINE_V1", prefix + "2_FILE_SPEC_V1"]
    assert all("previous_response_id" not in p for p in responder.calls)
    context = decoded(responder.calls[0])
    assert context["requirements" if mode == "GENERATE" else "change_requirements"] == checkpoint["requirements"]
    assert context["plan" if mode == "GENERATE" else "change_plan"] == checkpoint["plan"]
    assert source not in json.dumps(context, ensure_ascii=False)
    if mode == "GENERATE":
        assert "".join(row["text"] for row in decoded(responder.calls[1])["source"]["segments"]) == source
    report = json.loads((Path(worker.log.paths.run_dir) / "cost_context_report.json").read_text("utf-8"))
    assert report["requests"]
    assert all(row["status"] == "completed" and not row["blockers"] for row in report["requests"].values())
    assert checkpoint["spine"] is None
    assert worker.cfg.preparation_snapshot["canonical_stage"] == prefix + "2"


def test_structure_repair_replaces_candidate_without_growing_history(tmp_path):
    invalid = []

    def mutate(name, value, context):
        if name == "A2_SPINE_V1" and sum(
            p["text"]["format"]["name"] == name for p in responder.calls
        ) == 1:
            value["result"]["data"]["files"][0]["component_id"] = "COMP-UNKNOWN"
            invalid.append(deepcopy(value["result"]["data"]))
        return value

    worker, client, responder = preparation_scenario(tmp_path, "GENERATE", mutate=mutate)
    prepare(worker, client)
    assert len(responder.calls) == 5
    assert all("previous_response_id" not in p for p in responder.calls)
    repair = decoded(responder.calls[3])
    assert repair["input"] == decoded(responder.calls[2])
    assert "neznámá component" in repair["repair"]["error"]
    assert "repair" not in repair["input"]
    assert worker.cfg.preparation_snapshot["spine"]["files"][0]["component_id"] == "COMP-1"
    assert invalid[0] in repair["repair"].values(), "Oprava musí předat poslední vadný SPINE, bez starší historie."


def test_small_text_preparation_needs_no_remote_measurement():
    client = Mock()
    payload = {"model": "gpt-6-astra", "input": "krátký vstup"}
    report = preparation_measurement(payload, client)
    client.count_input_tokens.assert_not_called()
    assert not report["blockers"]
    assert report["output_budget"] == 128000


@pytest.mark.parametrize("fault", ["unavailable", "wrong_hash", "too_large", "invalid_count"])
def test_uncertain_or_oversized_input_blocks_before_generation(tmp_path, fault):
    worker, client, _ = preparation_scenario(tmp_path, "GENERATE", source="zdroj " * 20000)

    def count(payload):
        if fault == "unavailable":
            raise RuntimeError("síť nedostupná")
        value = counted(payload, 200000 if fault == "too_large" else 1000)
        if fault == "wrong_hash":
            value["request_hash"] = "cizí požadavek"
        if fault == "invalid_count":
            value["input_tokens"] = True
        return value

    client.count_input_tokens.side_effect = count
    with pytest.raises(ContractError) as exc:
        prepare(worker, client)
    assert exc.value.context_report["blockers"]
    client.count_input_tokens.assert_called_once()
    client.create_response.assert_not_called()
