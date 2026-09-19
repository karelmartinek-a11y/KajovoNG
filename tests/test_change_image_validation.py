"""CHANGE PHO-02 a spolecne obrazove limity; vzdy skutecne dekodovatelne fixture."""
from __future__ import annotations

import base64
import io
import json
from unittest.mock import Mock

import pytest
from PIL import Image

from kajovo.core.comic_types import ComicError
from kajovo.core.image_runtime import inspect_image, normalized_image
from kajovo.core.orchestration.errors import OrchestrationError
from kajovo.core.photo_batch import download_results, inspect_photo_bytes, new_job


def pixels(fmt='PNG', color=(10, 20, 30)):
    output = io.BytesIO()
    Image.new('RGB', (12, 8), color).save(output, fmt)
    return output.getvalue()


def test_normalization_keeps_palette_transparency_and_removes_exif():
    palette = Image.new('P', (2, 1))
    palette.putpalette([255, 0, 0, 0, 0, 255] + [0] * 762)
    palette.putdata([0, 1])
    output = io.BytesIO()
    palette.save(output, 'PNG', transparency=0)
    result = normalized_image(output.getvalue())
    with Image.open(io.BytesIO(result)) as image:
        image.load()
        assert image.mode == 'RGBA'
        assert image.getpixel((0, 0))[3] == 0
        assert image.getpixel((1, 0)) == (0, 0, 255, 255)
        assert not image.info


def test_exif_orientation_is_applied_once():
    image = Image.new('RGB', (2, 3))
    image.putpixel((0, 0), (255, 0, 0))
    exif = image.getexif()
    exif[274] = 6
    stream = io.BytesIO()
    image.save(stream, 'PNG', exif=exif)
    raw = stream.getvalue()
    normalized = normalized_image(raw)
    assert normalized_image(normalized) == normalized
    with Image.open(io.BytesIO(normalized)) as result:
        assert result.size == (3, 2)
        assert result.getpixel((2, 0)) == (255, 0, 0)
        assert not result.getexif()
    with Image.open(io.BytesIO(raw)) as unchanged:
        assert unchanged.getexif()[274] == 6


def test_photo_and_comic_apply_same_fifty_million_byte_limit():
    # Bajt navic se odmitne pred dekodovanim; neni vytvaren falesny uspesny obraz.
    too_large = b'\0' * 50_000_001
    with pytest.raises(ComicError):
        inspect_image(too_large)
    with pytest.raises(OrchestrationError, match='IMAGE_INVALID'):
        inspect_photo_bytes(too_large)


@pytest.mark.parametrize('bad', [b'not-an-image', b'\x89PNG\r\n\x1a\n', b''])
def test_neither_workflow_accepts_corrupt_bytes(bad):
    with pytest.raises(ComicError):
        inspect_image(bad)
    with pytest.raises(OrchestrationError, match='IMAGE_INVALID'):
        inspect_photo_bytes(bad)


def test_photo_format_must_match_real_bytes():
    with pytest.raises(OrchestrationError, match='IMAGE_INVALID'):
        inspect_photo_bytes(pixels('JPEG'), 'png')
    assert inspect_photo_bytes(pixels(), 'png')['format'] == 'PNG'


@pytest.mark.parametrize('body', [
    {'data': []},
    {'data': 'wrong-type'},
    {'data': [{'b64_json': base64.b64encode(pixels()).decode()}] * 2},
])
def test_photo_result_requires_exactly_one_image_without_output_write(tmp_path, body, record_property):
    source = tmp_path / 'source.png'
    source.write_bytes(pixels())
    job = new_job(
        source_paths=[str(source)], human_prompt='Preserve', professional_prompt='',
        final_prompt='Preserve', prompt_source='manual', template_id='', prompt_model='',
        prompt_response_id='', image_model='gpt-image-2.5-sunburst', quality='high',
        size='auto', output_format='png', output_dir=str(tmp_path / 'out'),
    )
    job.batch_id = 'batch_offline'
    client = Mock()
    client.retrieve_batch.return_value = {
        'id': job.batch_id, 'status': 'completed', 'output_file_id': 'file_result',
    }
    client.file_content.return_value = (json.dumps({
        'custom_id': job.items[0].custom_id, 'response': {'status_code': 200, 'body': body},
    }) + '\n').encode()
    result = download_results(client, job, tmp_path / 'log')
    assert result.items[0].status == 'failed'
    assert list((tmp_path / 'out').iterdir()) == []
    client.create_response.assert_not_called()
    client.create_image.assert_not_called()
    client.create_image_batch.assert_not_called()
    record_property('observed_transport_call_count', 0)
