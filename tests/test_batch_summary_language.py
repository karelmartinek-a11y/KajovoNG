"""Souhrn vzdálené dávky zachovává počty a české tvary chyby."""

import pytest

from kajovo.studio.batches import BatchesPage


@pytest.mark.parametrize("failed,label", [(0, "0 chyb"), (1, "1 chyba"), (2, "2 chyby"),
                                         (4, "4 chyby"), (5, "5 chyb"), (11, "11 chyb")])
def test_batch_summary_preserves_counts_and_declines_errors(failed, label):
    record = {"id": "batch_example", "photo": None, "state": {},
              "remote": {"request_counts": {"completed": 8, "failed": failed, "total": 20}}}
    summary = BatchesPage._summary(None, record)
    assert summary == f"{8 + failed} z 20 úloh · {label}"
