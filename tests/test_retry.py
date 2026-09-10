from unittest.mock import Mock, patch

import pytest

from kajovo.core.config import RetryPolicy
from kajovo.core.openai_client import OpenAIError
from kajovo.core.retry import CircuitBreaker, with_retry


def test_open_breaker_does_not_consume_request_attempt():
    breaker = CircuitBreaker(1, 10)
    breaker.on_failure()
    with patch("kajovo.core.retry.time.sleep"):
        assert with_retry(lambda: "ok", RetryPolicy(max_attempts=1), breaker) == "ok"


def test_http_400_containing_500_in_body_is_not_retried():
    request = Mock(side_effect=OpenAIError("Invalid limit 500", status_code=400))
    with pytest.raises(OpenAIError):
        with_retry(request, RetryPolicy())
    assert request.call_count == 1
