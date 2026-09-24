import io
import unittest
from unittest.mock import Mock, patch

import pytest
import requests

from kajovo.core.openai_client import OpenAIClient, OpenAIError, image_batch_submit_payload
from kajovo.core.openai_transport import SubmissionOutcomeUnknown
from kajovo.core.photo_batch import image_edit_model_ids


def test_paginated_files_are_complete():
    client = OpenAIClient("test")
    client._sdk = None
    with patch.object(client, "_req", side_effect=[
        {"data": [{"id": "a"}], "has_more": True, "last_id": "a"},
        {"data": [{"id": "b"}], "has_more": False},
    ]) as request:
        assert [item["id"] for item in client.list_files()] == ["a", "b"]
        assert "after=a" in request.call_args.args[1]


def test_response_submit_uses_transport_even_when_sdk_exists():
    client = OpenAIClient("test")
    client._sdk = Mock()
    result = {"id": "resp_1", "status": "completed", "output": []}
    with patch.object(client, "_req", return_value=result) as request:
        assert client._send_response({"model": "gpt-4.1", "input": "test"}) == result
    client._sdk.responses.create.assert_not_called()
    request.assert_called_once()


@pytest.mark.parametrize("identifier", ["../files/file-other", "file-a?limit=1", "file-a#fragment", "", "file-a/b"])
def test_resource_id_cannot_change_endpoint(identifier):
    client = OpenAIClient("test")
    client._sdk = Mock()
    with pytest.raises(ValueError):
        client.delete_file(identifier)
    client._sdk.files.delete.assert_not_called()


def test_container_file_download_is_read_only_and_identifiers_are_validated():
    client = OpenAIClient("test")
    client._req = Mock(return_value=b"exact binary data")
    assert client.container_file_content("cntr_1", "cfile_1") == b"exact binary data"
    client._req.assert_called_once_with("GET", "/containers/cntr_1/files/cfile_1/content")
    client._req.reset_mock()
    with pytest.raises(ValueError):
        client.container_file_content("cntr_1/../other", "cfile_1")
    client._req.assert_not_called()


def test_multipart_upload_timeout_is_not_retried_or_rewound():
    client = OpenAIClient("test")
    assert "Content-Type" not in client.session.headers
    stream = io.BytesIO(b"complete")
    bodies = []

    def request(*args, **kwargs):
        bodies.append(kwargs["files"]["file"][1].read())
        raise requests.Timeout("lost")

    with patch.object(client.session, "request", side_effect=request):
        with pytest.raises(SubmissionOutcomeUnknown):
            client._req("POST", "/files", files={"file": ("x.txt", stream)})
    assert bodies == [b"complete"]


class OpenAIClientErrorMappingTests(unittest.TestCase):
    def test_raises_openai_error_for_non_retryable_http(self):
        client = OpenAIClient("k")
        client._sdk = None
        resp = Mock(status_code=400, headers={"content-type": "application/json"}, text="bad request")
        client.session.request = Mock(return_value=resp)
        with self.assertRaises(OpenAIError):
            client._req("GET", "/models")

    def test_retries_on_timeout_then_succeeds(self):
        client = OpenAIClient("k")
        client._sdk = None
        ok = Mock(status_code=200, headers={"content-type": "application/json"})
        ok.json.return_value = {"data": []}
        client.session.request = Mock(side_effect=[requests.Timeout("t"), ok])
        client._transport.sleeper = lambda _seconds: None
        out = client._req("GET", "/models")
        self.assertEqual(out, {"data": []})


@pytest.mark.parametrize("raw", [
    b'{"custom_id":"a","custom_id":"b","method":"POST","url":"/v1/responses","body":{}}',
    b'{"custom_id":"a","method":"POST","url":"/v1/responses","body":{"x":NaN}}',
    b'{"custom_id":"a","method":"POST","url":"/v1/responses","body":{}} {}',
])
def test_batch_jsonl_rejects_ambiguous_json_before_upload(raw):
    client = OpenAIClient("test")
    with pytest.raises(ValueError):
        client.validate_batch_data(raw)


def test_image_batch_submit_payload_keeps_documented_output_expiration():
    payload = image_batch_submit_payload("file_batch", "/v1/images/edits")
    assert payload == {
        "input_file_id": "file_batch",
        "endpoint": "/v1/images/edits",
        "completion_window": "24h",
        "output_expires_after": {
            "anchor": "created_at",
            "seconds": 2592000,
        },
    }


def test_create_image_batch_performs_one_canonical_post():
    model = image_edit_model_ids()[0]
    row = {
        "custom_id": "photo_1",
        "method": "POST",
        "url": "/v1/images/edits",
        "body": {
            "model": model,
            "images": [{"file_id": "file_photo"}],
            "prompt": "Preserve reality.",
            "n": 1,
            "size": "auto",
            "quality": "high",
            "output_format": "png",
            "background": "auto",
        },
    }
    client = OpenAIClient("test")
    result = {"id": "batch_photo", "status": "validating"}
    with patch.object(client, "_req", return_value=result) as request:
        assert client.create_image_batch("file_batch", [row]) == result
    request.assert_called_once_with(
        "POST",
        "/batches",
        json_body={
            "input_file_id": "file_batch",
            "endpoint": "/v1/images/edits",
            "completion_window": "24h",
            "output_expires_after": {
                "anchor": "created_at",
                "seconds": 2592000,
            },
        },
        max_attempts=1,
    )
