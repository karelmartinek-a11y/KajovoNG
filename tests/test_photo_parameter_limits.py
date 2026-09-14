"""Neplatné parametry fotografií se odmítají před prvním přenosem."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from kajovo.core.photo_batch import prepare_and_submit, validate_image_edit_parameters


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
