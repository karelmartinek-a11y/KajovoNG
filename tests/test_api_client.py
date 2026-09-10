import io
import unittest
from unittest.mock import Mock, patch

import pytest
import requests

from kajovo.core.openai_client import OpenAIClient, OpenAIError


def test_paginated_files_are_complete():
    client = OpenAIClient("test")
    client._sdk = None
    with patch.object(client, "_req", side_effect=[
        {"data": [{"id": "a"}], "has_more": True, "last_id": "a"},
        {"data": [{"id": "b"}], "has_more": False},
    ]) as request:
        assert [item["id"] for item in client.list_files()] == ["a", "b"]
        assert "after=a" in request.call_args.args[1]


def test_sdk_mutation_failure_does_not_repeat_via_rest():
    client = OpenAIClient("test")
    client._sdk = Mock()
    client._sdk.responses.create.side_effect = RuntimeError("lost response")
    client.validate_access = Mock()
    with patch.object(client, "_req") as request:
        with pytest.raises(OpenAIError):
            client._send_response({"model": "gpt-4.1", "input": "test"})
        request.assert_not_called()


@pytest.mark.parametrize("identifier", ["../files/file-other", "file-a?limit=1", "file-a#fragment", "", "file-a/b"])
def test_resource_id_cannot_change_endpoint(identifier):
    client = OpenAIClient("test")
    client._sdk = Mock()
    with pytest.raises(ValueError):
        client.delete_file(identifier)
    client._sdk.files.delete.assert_not_called()


def test_multipart_header_and_retry_rewind():
    client = OpenAIClient("test")
    assert "Content-Type" not in client.session.headers
    stream = io.BytesIO(b"complete")
    bodies = []

    def request(*args, **kwargs):
        bodies.append(kwargs["files"]["file"][1].read())
        if len(bodies) == 1:
            raise requests.Timeout()
        return Mock(status_code=200, headers={"content-type": "application/json"}, json=lambda: {})

    with patch.object(client.session, "request", side_effect=request), patch("kajovo.core.openai_client.time.sleep"):
        client._req("POST", "/files", files={"file": ("x.txt", stream)})
    assert bodies == [b"complete", b"complete"]


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
        with patch("kajovo.core.openai_client.time.sleep", return_value=None):
            out = client._req("GET", "/models")
        self.assertEqual(out, {"data": []})
