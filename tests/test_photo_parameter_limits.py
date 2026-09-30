"""Neplatné parametry fotografií se odmítají před prvním přenosem."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from kajovo.core.photo_batch import prepare_and_submit, validate_image_edit_parameters
from kajovo.core.photo_batch import PhotoBatchItem, image_edit_row
from kajovo.core.comic_types import ComicError
from kajovo.core.image_runtime import validate_image_request


@pytest.mark.parametrize("size", ["4096x2304", "3841x2160", "1024x1023", "1024x64", "16x16"])
def test_modern_image_dimensions_are_bounded(size):
    with pytest.raises(ValueError):
        validate_image_edit_parameters("gpt-image-2", "high", size, "png")


def test_documented_custom_dimensions_are_accepted():
    validate_image_edit_parameters("gpt-image-2", "high", "3840x2160", "webp")


def test_old_model_cannot_use_new_quality():
    with pytest.raises(ValueError):
        validate_image_edit_parameters("gpt-image-1.5", "max", "1024x1024", "png")


def test_invalid_job_does_not_upload(tmp_path):
    client = Mock()
    job = SimpleNamespace(image_model="gpt-image-2", quality="high", size="4096x2304", output_format="png")
    with pytest.raises(ValueError):
        prepare_and_submit(client, job, tmp_path)
    assert client.mock_calls == []


@pytest.mark.parametrize(
    "model",
    [
        "gpt-image-2.5-sunburst",
        "gpt-image-2.5-sunburst-2026-09-08",
        "gpt-image-2.5-flare",
        "gpt-image-2.5-flare-2026-09-08",
    ],
)
def test_gpt_image_25_photo_batch_omits_input_fidelity(model):
    item = PhotoBatchItem(
        item_id="photo_test",
        custom_id="photo-00001-test",
        source_path="source.png",
        source_name="source.png",
        source_sha256="0" * 64,
        uploaded_file_id="file_source",
    )
    row = image_edit_row(
        item,
        model=model,
        prompt="Edit the photograph.",
        quality="high",
        size="auto",
        output_format="png",
    )
    assert "input_fidelity" not in row["body"]
    with pytest.raises(ComicError, match="věrnost"):
        validate_image_request(
            "/v1/images/edits",
            {**row["body"], "input_fidelity": "high"},
        )
