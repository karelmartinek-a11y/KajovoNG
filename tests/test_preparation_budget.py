"""Příprava nesmí násobit historii ani odeslat kapacitně neověřený vstup."""
from copy import deepcopy
import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from kajovo.core.context_budget import preparation_measurement
from kajovo.core.context_compiler import content_hash
from kajovo.core.contracts import ContractError
from kajovo.core.delivery_preparation import prepare_delivery
from kajovo.core.generate_batch import digest
from test_preparation_snapshot import snapshot
from test_workflows import make_worker, response
from delivery_fixtures import delivery_payloads


def counted(payload, tokens=300000):
    return {"input_tokens": tokens, "request_hash": content_hash(payload)}


def decoded(payload):
    return json.loads("".join(part["text"] for message in payload["input"]
                             for part in message["content"] if part["type"] == "input_text"))


@pytest.mark.parametrize("mode", ["GENERATE", "MODIFY"])
@pytest.mark.parametrize("quality", [False, True])
def test_preparation_preserves_sources_once_without_inherited_history(tmp_path, mode, quality):
    worker = make_worker(tmp_path, mode)
    worker.cfg.maximum_quality = quality
    values = list(delivery_payloads(mode))
    if quality:
        values.append(deepcopy(values[-1]))
    client = Mock()
    client.count_input_tokens.side_effect = lambda p: counted(p, 1000)
    calls = []

    def create(payload):
        calls.append(deepcopy(payload))
        return response(len(calls), values[len(calls) - 1])

    client.create_response.side_effect = create
    prepare_delivery(worker, client, mode, None, "Přesné zadání", ["file_source"], ["file_image"], None)
    assert len(calls) == len(values)
    for p in calls:
        assert "previous_response_id" not in p
        assert decoded(p)["source"] == "Přesné zadání"
        references = [part["file_id"] for msg in p["input"] for part in msg["content"] if "file_id" in part]
        assert references == ["file_source", "file_image"]
        assert p["truncation"] == "disabled"
    assert client.count_input_tokens.call_count == len(values)


@pytest.mark.parametrize("mode", ["GENERATE", "MODIFY"])
def test_resume_legacy_plan_uses_saved_results_without_old_response_chain(tmp_path, mode):
    worker = make_worker(tmp_path, mode)
    worker.cfg.model = "gpt-6-astra"
    worker.cfg.model_a1 = worker.cfg.model_a2 = "gpt-6-astra"
    checkpoint = snapshot(mode, stage="A1" if mode == "GENERATE" else "B1")
    checkpoint["prompt_hash"] = digest(worker.cfg.prompt)
    checkpoint["structure"] = None
    checkpoint.pop("snapshot_hash")
    checkpoint["snapshot_hash"] = digest(checkpoint)
    worker.cfg.preparation_snapshot = deepcopy(checkpoint)
    source = "obsah podkladu " * 100000
    client = Mock()
    client.count_input_tokens.side_effect = counted
    client.create_response.return_value = response(1, delivery_payloads(mode)[2])
    prepare_delivery(worker, client, mode, "resp_old_chain", source, [], [], None)
    client.create_response.assert_called_once()
    p = client.create_response.call_args.args[0]
    assert "previous_response_id" not in p
    context = decoded(p)
    assert context["source"] == source
    assert context["requirements"] == checkpoint["requirements"]
    assert context["plan"] == checkpoint["plan"]
    report = json.loads((Path(worker.log.paths.run_dir) / "cost_context_report.json").read_text("utf-8"))
    assert len(report["requests"]) == 1
    row = next(iter(report["requests"].values()))
    assert row["input_tokens_exact"] and row["input_tokens"] == 300000
    assert row["status"] == "completed" and not row["blockers"]
    assert checkpoint["structure"] is None


def test_structure_repair_replaces_candidate_without_growing_history(tmp_path):
    worker = make_worker(tmp_path, "GENERATE")
    values = list(delivery_payloads())
    client = Mock()
    calls = []
    client.count_input_tokens.side_effect = lambda p: counted(p, 1000)

    def create(payload):
        calls.append(deepcopy(payload))
        return response(len(calls), values[min(len(calls) - 1, 2)])

    client.create_response.side_effect = create
    from kajovo.core.delivery_preparation import validate_delivery_structure
    with patch("kajovo.core.delivery_preparation.validate_delivery_structure",
               side_effect=[ContractError("Oprav vazbu"), validate_delivery_structure(*values, "GENERATE")]):
        prepare_delivery(worker, client, "GENERATE", None, "zdroj", ["file_ref"], [], None)
    assert len(calls) == 4
    assert all("previous_response_id" not in p for p in calls)
    assert decoded(calls[-1])["structure"] == values[-1]
    assert decoded(calls[-1])["validation_errors"] == "Oprav vazbu"
    assert any(part.get("file_id") == "file_ref" for msg in calls[-1]["input"] for part in msg["content"])


def test_small_text_preparation_needs_no_remote_measurement():
    client = Mock()
    payload = {"model": "gpt-6-astra", "input": "krátký vstup"}
    report = preparation_measurement(payload, client)
    client.count_input_tokens.assert_not_called()
    assert not report["blockers"]
    assert report["output_budget"] == 128000


@pytest.mark.parametrize("fault", ["unavailable", "wrong_hash", "too_large", "invalid_count"])
def test_uncertain_or_oversized_input_blocks_before_generation(tmp_path, fault):
    worker = make_worker(tmp_path, "GENERATE")
    client = Mock()

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
        prepare_delivery(worker, client, "GENERATE", None, "zdroj", ["file_ref"], [], None)
    assert exc.value.context_report["blockers"]
    client.create_response.assert_not_called()
