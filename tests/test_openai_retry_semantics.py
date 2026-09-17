from __future__ import annotations

from unittest.mock import Mock, patch

import pytest
import requests

from kajovo.core.config import RetryPolicy
from kajovo.core.openai_transport import OpenAIError, SubmissionOutcomeUnknown
from kajovo.core.retry import with_retry


def test_net_013_unknown_side_effect_is_not_retried_by_generic_wrapper(tmp_path):
    from kajovo.core.openai_client import OpenAIClient

    source = tmp_path / "payload.txt"
    source.write_text("data", encoding="utf-8")
    client = OpenAIClient("test")
    client._sdk = None
    client.session.request = Mock(side_effect=requests.Timeout("lost"))

    with patch("kajovo.core.retry.time.sleep"), pytest.raises(SubmissionOutcomeUnknown):
        with_retry(
            lambda: client.upload_file(str(source)),
            RetryPolicy(max_attempts=4),
        )

    assert client.session.request.call_count == 1


def test_upload_http_429_is_single_request_without_workflow_retry(tmp_path):
    from kajovo.core.openai_client import OpenAIClient

    source = tmp_path / "payload.txt"
    source.write_text("data", encoding="utf-8")
    client = OpenAIClient("test")
    client._sdk = None
    response = Mock(status_code=429, headers={"content-type": "application/json"}, text="rate")
    response.json.return_value = {"error": {"code": "rate_limit"}}
    client.session.request = Mock(return_value=response)

    with pytest.raises(OpenAIError) as caught:
        client.upload_file(str(source))

    assert caught.value.status_code == 429
    assert client.session.request.call_count == 1
