from __future__ import annotations

import base64
import io
import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from PIL import Image

from kajovo.core.model_registry import model_spec
from kajovo.core.photo_batch import (
    ImageEditBatchAdapter,
    PhotoBatchItem,
    download_results,
    image_edit_model_ids,
    image_edit_row,
    new_job,
    prepare_and_submit,
    refresh_job,
    validate_image_edit_rows,
)
from kajovo.core.photo_prompt import professionalize_payload, professionalize_prompt
from kajovo.core.photo_templates import PhotoTemplateStore


def _image_bytes(fmt="PNG"):
    stream = io.BytesIO()
    Image.new("RGB", (8, 6), (120, 130, 140)).save(stream, format=fmt)
    return stream.getvalue()


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
    assert payload["text"]["format"]["name"] == "PHOTO_PLAN_V1"
    assert set(payload["text"]["format"]["schema"]["properties"]) == {
        "professional_prompt",
        "edit_actions",
        "preserve_invariants",
        "acceptance_criteria",
    }
    assert "USER_PROMPT" in payload["input"]


def test_professionalize_replaces_only_after_valid_response(tmp_path):
    client = Mock()
    from kajovo.core.context_compiler import content_hash

    client.count_input_tokens.side_effect = lambda payload: {
        "input_tokens": 100,
        "request_hash": content_hash(payload),
    }
    client.create_response.return_value = {
        "id": "resp_photo",
        "status": "completed",
        "model": "gpt-5.6-luna",
        "output_text": json.dumps(
            {
                "professional_prompt": "Correct verticals and preserve the real room.",
                "edit_actions": ["Correct verticals."],
                "preserve_invariants": ["Preserve the real room."],
                "acceptance_criteria": ["Verticals are corrected without unrelated changes."],
            }
        ),
        "usage": {"input_tokens": 100, "output_tokens": 30},
    }
    result = professionalize_prompt(
        client,
        "gpt-5.6-luna",
        "srovnej stěny",
        tmp_path / "LOG",
    )
    assert result.original_prompt == "srovnej stěny"
    assert result.professional_prompt.startswith("Correct verticals")
    assert result.photo_plan["version"] == 1
    assert result.photo_plan["acceptance_criteria"]
    client.create_response.assert_called_once()
    assert list((tmp_path / "LOG").glob("RUN_PHOTO_PROMPT_*/bundle.json"))


def _image_model():
    models = image_edit_model_ids()
    assert models, "Pevná matice musí obsahovat alespoň jeden Image Edit BATCH model."
    return models[0]


def test_image_model_selector_excludes_general_responses_models():
    models = image_edit_model_ids()
    assert "gpt-5.6-luna" not in models
    assert "gpt-5.6-sol" not in models
    assert models
    assert models[0] == "gpt-image-2.5-sunburst"
    for model in models:
        spec = model_spec(model)
        assert bool((spec.get("image_capabilities") or {}).get("batch", spec["batch"])) is True
        assert "inpainting" in spec["features"]
        assert any(
            isinstance(endpoint, list)
            and len(endpoint) >= 2
            and endpoint[1] == "v1/images/edits"
            for endpoint in spec["endpoints"]
        )


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


def test_image_batch_adapter_delegates_to_canonical_client_transport():
    client = Mock()
    client._validate_resource_id.return_value = None
    client.create_image_batch.return_value = {"id": "batch_photo", "status": "validating"}
    item = PhotoBatchItem("i", "c", "a.jpg", "a.jpg", "hash", "file_photo")
    row = image_edit_row(item, model=_image_model(), prompt="Keep it real.", quality="high", size="auto", output_format="png")
    result = ImageEditBatchAdapter(client).submit("file_batch", [row])
    assert result["id"] == "batch_photo"
    client.create_image_batch.assert_called_once_with("file_batch", [row])
    client._req.assert_not_called()


def test_download_results_preserves_original_and_maps_custom_id(tmp_path):
    source = tmp_path / "room.jpg"
    original = _image_bytes("JPEG")
    source.write_bytes(original)
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
    generated = _image_bytes("PNG")
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
    assert source.read_bytes() == original
    assert result.items[0].status == "downloaded"
    target = Path(result.items[0].output_path)
    assert target.name == "room_edited.png"
    assert target.read_bytes() == generated
    assert result.items[0].output_width == 8
    assert result.items[0].output_height == 6
    assert result.items[0].output_format_detected == "PNG"


def test_corrupt_image_payload_is_not_marked_downloaded(tmp_path):
    source = tmp_path / "room.jpg"
    source.write_bytes(_image_bytes("JPEG"))
    job = new_job(
        source_paths=[str(source)], human_prompt="x", professional_prompt="", final_prompt="x",
        prompt_source="manual", template_id="", prompt_model="", prompt_response_id="",
        image_model=_image_model(), quality="high", size="auto", output_format="png",
        output_dir=str(tmp_path / "out"),
    )
    job.batch_id = "batch_bad_image"
    job.output_file_id = "file_bad_image"
    item = job.items[0]
    line = {
        "custom_id": item.custom_id,
        "response": {"status_code": 200, "body": {
            "data": [{"b64_json": base64.b64encode(b"not-an-image").decode()}]
        }},
    }
    client = Mock()
    client.retrieve_batch.return_value = {
        "status": "completed", "output_file_id": "file_bad_image",
        "request_counts": {"total": 1, "completed": 1, "failed": 0},
    }
    client.file_content.return_value = (json.dumps(line) + "\n").encode()
    result = download_results(client, job, tmp_path / "log")
    assert result.status == "failed"
    assert result.items[0].status == "failed"
    assert not list((tmp_path / "out").glob("*_edited.png"))


def test_unknown_photo_batch_submit_recovers_exact_remote_match(tmp_path):
    source = tmp_path / "room.jpg"
    source.write_bytes(_image_bytes("JPEG"))
    job = new_job(
        source_paths=[str(source)], human_prompt="x", professional_prompt="", final_prompt="x",
        prompt_source="manual", template_id="", prompt_model="", prompt_response_id="",
        image_model=_image_model(), quality="high", size="auto", output_format="png",
        output_dir=str(tmp_path / "out"),
    )
    job.status = "submission_unknown"
    job.input_file_id = "file_input"
    client = Mock()
    client.list_batches.return_value = [{
        "id": "batch_recovered",
        "input_file_id": "file_input",
        "endpoint": "/v1/images/edits",
        "status": "validating",
        "request_counts": {"total": 1, "completed": 0, "failed": 0},
    }]
    client.retrieve_batch.return_value = {
        "id": "batch_recovered",
        "status": "in_progress",
        "request_counts": {"total": 1, "completed": 0, "failed": 0},
    }

    recovered = refresh_job(client, job, tmp_path / "log")

    assert recovered.batch_id == "batch_recovered"
    assert recovered.status == "in_progress"
    client.list_batches.assert_called_once_with()
    client.retrieve_batch.assert_called_once_with("batch_recovered")
    assert (tmp_path / "log" / "PHOTO" / job.job_id / "photo_job.json").is_file()


def test_invalid_jsonl_is_not_silently_ignored(tmp_path):
    source = tmp_path / "room.jpg"
    source.write_bytes(_image_bytes("JPEG"))
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


def test_photo_studio_page_constructs_without_api(qtbot, tmp_path):
    from kajovo.core.config import AppSettings
    from kajovo.studio.context import StudioContext
    from kajovo.studio.operations import Operations
    from kajovo.studio.photos import PhotosPage

    context = StudioContext(
        AppSettings(log_dir=str(tmp_path / "LOG"), cache_dir=str(tmp_path / "cache")),
        Operations(None),
        api_key="",
    )
    context.models = [_image_model()]
    page = PhotosPage(context)
    qtbot.addWidget(page)
    context.models_changed.emit()
    assert page.prompt.isEnabled()
    assert page.template.count() >= 6
    assert page.image_model.count() >= 1
    assert page.start_button.isEnabled()


def test_photo_studio_is_real_main_navigation_page(qtbot, tmp_path):
    from kajovo.core.config import AppSettings
    from kajovo.studio.application import create_window
    from kajovo.studio.photos import PhotosPage

    window = create_window(
        AppSettings(log_dir=str(tmp_path / "LOG"), cache_dir=str(tmp_path / "cache")),
        api_key="",
    )
    qtbot.addWidget(window)
    assert isinstance(window.pages["photos"], PhotosPage)
    window.select_page("photos")
    assert window.stack.widget(window.stack.currentIndex()).widget() is window.pages["photos"]



def test_photo_batch_submit_creates_work_order_and_provider_operation(tmp_path):
    source = tmp_path / "room.jpg"
    source.write_bytes(_image_bytes("JPEG"))
    log_dir = tmp_path / "LOG"
    job = new_job(
        source_paths=[str(source)],
        human_prompt="Preserve reality.",
        professional_prompt="",
        final_prompt="Preserve reality.",
        prompt_source="manual",
        template_id="",
        prompt_model="",
        prompt_response_id="",
        image_model=_image_model(),
        quality="high",
        size="auto",
        output_format="png",
        output_dir=str(tmp_path / "out"),
    )
    client = Mock()
    uploads = iter([{"id": "file_source"}, {"id": "file_batch"}])
    client.upload_file.side_effect = lambda *args, **kwargs: next(uploads)
    client._validate_resource_id.return_value = None
    client.create_image_batch.return_value = {
        "id": "batch_photo",
        "status": "validating",
        "input_file_id": "file_batch",
        "endpoint": "/v1/images/edits",
        "request_counts": {"total": 1, "completed": 0, "failed": 0},
    }

    prepare_and_submit(client, job, log_dir)

    root = log_dir / "PHOTO" / job.job_id
    assert (root / "work_order_v2.json").is_file()
    from kajovo.core.orchestration.repository import OrchestrationRepository

    repo = OrchestrationRepository(log_dir / "orchestration.sqlite3")
    with repo.connect() as db:
        work = db.execute(
            "SELECT route,task_id FROM work_orders WHERE run_id=?",
            (job.job_id,),
        ).fetchall()
        operations = db.execute(
            "SELECT state,provider_id FROM provider_operations"
        ).fetchall()
    assert work == [("image_batch", "PHOTO_BATCH_SUBMIT")]
    assert operations == [("submitted", "batch_photo")]
    client.create_image_batch.assert_called_once()
    client._req.assert_not_called()


def test_photo_batch_uncertain_submit_is_not_reposted(tmp_path):
    source = tmp_path / "room.jpg"
    source.write_bytes(_image_bytes("JPEG"))
    log_dir = tmp_path / "LOG"
    job = new_job(
        source_paths=[str(source)],
        human_prompt="Preserve reality.",
        professional_prompt="",
        final_prompt="Preserve reality.",
        prompt_source="manual",
        template_id="",
        prompt_model="",
        prompt_response_id="",
        image_model=_image_model(),
        quality="high",
        size="auto",
        output_format="png",
        output_dir=str(tmp_path / "out"),
    )
    client = Mock()
    uploads = iter([{"id": "file_source"}, {"id": "file_batch"}])
    client.upload_file.side_effect = lambda *args, **kwargs: next(uploads)
    client._validate_resource_id.return_value = None
    client.create_image_batch.side_effect = TimeoutError("lost response")

    with pytest.raises(TimeoutError):
        prepare_and_submit(client, job, log_dir)
    assert job.status == "submission_unknown"
    assert client.create_image_batch.call_count == 1
    client._req.assert_not_called()

    # Recovery checks the exact Files input + endpoint and never POSTs again.
    client.create_image_batch.side_effect = None
    client.list_batches.return_value = [
        {
            "id": "batch_recovered",
            "input_file_id": job.input_file_id,
            "endpoint": "/v1/images/edits",
            "status": "in_progress",
            "request_counts": {"total": 1, "completed": 0, "failed": 0},
        }
    ]
    client.retrieve_batch.return_value = client.list_batches.return_value[0]
    refresh_job(client, job, log_dir)
    assert job.batch_id == "batch_recovered"
    assert client.create_image_batch.call_count == 1
    client._req.assert_not_called()
