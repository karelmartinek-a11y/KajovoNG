from __future__ import annotations

import base64
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from kajovo.core.photo_batch import (
    ImageEditBatchAdapter,
    PhotoBatchItem,
    download_results,
    image_edit_model_ids,
    image_edit_row,
    new_job,
    validate_image_edit_rows,
)
from kajovo.core.photo_prompt import professionalize_payload, professionalize_prompt
from kajovo.core.photo_templates import PhotoTemplateStore


def test_builtin_templates_are_visible_and_immutable(tmp_path):
    store = PhotoTemplateStore(tmp_path / "templates.json")
    builtins = store.list()
    assert any(item.template_id == "builtin-hotel-booking" for item in builtins)
    with pytest.raises(ValueError):
        store.delete("builtin-hotel-booking")
    copy = store.duplicate("builtin-hotel-booking")
    assert copy.builtin is False
    assert copy.prompt == store.get("builtin-hotel-booking").prompt


def test_custom_template_round_trip(tmp_path):
    store = PhotoTemplateStore(tmp_path / "templates.json")
    item = store.create("Můj pokoj", "Keep the room realistic.", "Popis", "Pokoj")
    assert store.get(item.template_id).name == "Můj pokoj"
    store.update(item.template_id, name="Nový název", prompt="Keep geometry.", description="D", category="C")
    assert store.get(item.template_id).prompt == "Keep geometry."
    store.delete(item.template_id)
    with pytest.raises(KeyError):
        store.get(item.template_id)


def test_professionalize_payload_is_real_responses_work_not_preflight():
    payload = professionalize_payload("gpt-5.6-luna", "zesvětli pokoj a srovnej svislice")
    assert payload["model"] == "gpt-5.6-luna"
    assert payload["store"] is False
    assert "instructions" in payload
    assert "professional_prompt" in payload["text"]["format"]["schema"]["properties"]
    assert "USER_PROMPT" in payload["input"]


def test_professionalize_replaces_only_after_valid_response():
    client = Mock()
    client.create_response.return_value = {
        "id": "resp_photo",
        "status": "completed",
        "output_text": json.dumps({"professional_prompt": "Correct verticals and preserve the real room."}),
    }
    result = professionalize_prompt(client, "gpt-5.6-luna", "srovnej stěny")
    assert result.original_prompt == "srovnej stěny"
    assert result.professional_prompt.startswith("Correct verticals")
    client.create_response.assert_called_once()


def _image_model():
    models = image_edit_model_ids()
    assert models, "Pevná matice musí obsahovat alespoň jeden Image Edit BATCH model."
    return models[0]


def test_image_edit_batch_row_uses_image_endpoint():
    item = PhotoBatchItem(
        item_id="one",
        custom_id="photo-1",
        source_path="a.jpg",
        source_name="a.jpg",
        source_sha256="abc",
        uploaded_file_id="file_photo123",
    )
    row = image_edit_row(
        item,
        model=_image_model(),
        prompt="Improve exposure while preserving reality.",
        quality="high",
        size="auto",
        output_format="png",
    )
    assert row["url"] == "/v1/images/edits"
    assert row["body"]["images"] == [{"file_id": "file_photo123"}]
    assert validate_image_edit_rows([row]) == [row]


def test_image_batch_adapter_performs_one_working_post_only():
    client = Mock()
    client._validate_resource_id.return_value = None
    client._req.return_value = {"id": "batch_photo", "status": "validating"}
    item = PhotoBatchItem("i", "c", "a.jpg", "a.jpg", "hash", "file_photo")
    row = image_edit_row(item, model=_image_model(), prompt="Keep it real.", quality="high", size="auto", output_format="png")
    result = ImageEditBatchAdapter(client).submit("file_batch", [row])
    assert result["id"] == "batch_photo"
    client._req.assert_called_once_with(
        "POST",
        "/batches",
        json_body={"input_file_id": "file_batch", "endpoint": "/v1/images/edits", "completion_window": "24h"},
        max_attempts=1,
    )


def test_download_results_preserves_original_and_maps_custom_id(tmp_path):
    source = tmp_path / "room.jpg"
    source.write_bytes(b"original-jpeg")
    output_dir = tmp_path / "out"
    log_dir = tmp_path / "log"
    job = new_job(
        source_paths=[str(source)],
        human_prompt="upravit",
        professional_prompt="",
        final_prompt="Improve it realistically.",
        prompt_source="manual",
        template_id="",
        prompt_model="",
        prompt_response_id="",
        image_model=_image_model(),
        quality="high",
        size="auto",
        output_format="png",
        output_dir=str(output_dir),
    )
    item = job.items[0]
    job.batch_id = "batch_test"
    job.output_file_id = "file_output"
    generated = b"fake-png-bytes"
    line = {
        "custom_id": item.custom_id,
        "response": {"status_code": 200, "body": {"data": [{"b64_json": base64.b64encode(generated).decode()}]}},
        "error": None,
    }
    client = Mock()
    client.retrieve_batch.return_value = {
        "id": job.batch_id,
        "status": "completed",
        "output_file_id": "file_output",
        "request_counts": {"total": 1, "completed": 1, "failed": 0},
    }
    client.file_content.return_value = (json.dumps(line) + "\n").encode()
    result = download_results(client, job, log_dir)
    assert source.read_bytes() == b"original-jpeg"
    assert result.items[0].status == "downloaded"
    target = Path(result.items[0].output_path)
    assert target.name == "room_edited.png"
    assert target.read_bytes() == generated


def test_invalid_jsonl_is_not_silently_ignored(tmp_path):
    source = tmp_path / "room.jpg"
    source.write_bytes(b"source")
    job = new_job(
        source_paths=[str(source)], human_prompt="x", professional_prompt="", final_prompt="x",
        prompt_source="manual", template_id="", prompt_model="", prompt_response_id="",
        image_model=_image_model(), quality="high", size="auto", output_format="png",
        output_dir=str(tmp_path / "out"),
    )
    job.batch_id = "batch_bad"
    job.output_file_id = "file_bad"
    client = Mock()
    client.retrieve_batch.return_value = {"status": "completed", "output_file_id": "file_bad", "request_counts": {"total": 1}}
    client.file_content.return_value = b"not-json\n"
    with pytest.raises(ValueError, match="neplatný JSON"):
        download_results(client, job, tmp_path / "log")
