from __future__ import annotations

import pytest

from kajovo.core.runs.cancellation import CancellationToken, RunCancelled


def test_cancellation_token_starts_active_and_cancels_idempotently():
    token = CancellationToken()
    assert token.is_cancelled() is False
    token.raise_if_cancelled()
    token.cancel()
    token.cancel()
    assert token.is_cancelled() is True
    with pytest.raises(RunCancelled):
        token.raise_if_cancelled()
