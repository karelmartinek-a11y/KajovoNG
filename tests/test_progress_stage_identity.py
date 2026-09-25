"""Průběh vychází z identity skutečného kroku, nikoli z odhadu UI."""
import pytest

from change_v2_fixtures import run, scenario
from kajovo.core.progress import ProgressEvent


@pytest.mark.parametrize("mode,prefix", [("GENERATE", "A"), ("MODIFY", "B")])
@pytest.mark.parametrize("batch", [False, True])
def test_preparation_announces_real_steps_before_transport(tmp_path, mode, prefix, batch):
    worker, client, _ = scenario(tmp_path, mode, batch=batch)
    events = []
    worker.progress_event.connect(events.append)
    result, errors = run(worker, client)
    assert result and not errors
    stages = [prefix + name for name in ("0R", "1", "2_SPINE", "2_DETAIL")]
    for stage in stages:
        relevant = [event for event in events if event.stage == stage]
        assert relevant[0].state == "preparing"
        assert any(event.state == "waiting" and event.source == "api" for event in relevant)
        assert relevant[-1].state == "completed"
    assert not any(event.source == "api" and event.stage == "Lokální validace" for event in events)
