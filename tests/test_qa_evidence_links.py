"""Odkazy QA musí odpovídat dostupným podkladům, nikoli vymyšleným ID."""
from unittest.mock import Mock

import pytest

from change_v2_fixtures import run
from test_workflows import make_worker, response


@pytest.mark.parametrize("identifier,valid", [("SRC-USER-TEXT", True), ("missing-evidence", False), (None, False)])
def test_qa_evidence_resolves_to_supplied_sources(tmp_path, identifier, valid):
    worker = make_worker(tmp_path, "QA")
    events = []
    worker.progress_event.connect(events.append)
    client = Mock()
    client.create_response.return_value = response(0, {"result": {"status": "ready", "data": {
        "answer": "Odpověď.", "claims": [{"id": "C1", "text": "Tvrzení.",
        "evidence_ids": [identifier] if identifier else [], "certainty": "supported"}], "limitations": [],
    }}})
    results, errors = run(worker, client)
    assert bool(results) is valid
    assert bool(errors) is not valid
    stages = [(event.stage, event.state) for event in events]
    assert ("QA_INPUT", "completed") in stages
    assert ("QA_RESPONSE", "waiting") in stages
    assert ("QA_RESPONSE", "completed") in stages
    assert ("QA_VALIDATION", "active") in stages
    assert (("QA_VALIDATION", "completed") in stages) is valid
    if not valid:
        assert ("neznámé podklady" if identifier else "nemá žádné podklady") in errors[0]
