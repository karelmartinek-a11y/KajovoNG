"""Textová příprava médií prochází reálnou evidencí transportu bez sítě."""
import json

import pytest
import requests

from kajovo.core.comic_service import ComicService
from kajovo.core.comic_types import BIBLE_FIELDS
from kajovo.core.config import AppSettings
from kajovo.core.openai_client import OpenAIClient
from kajovo.core.photo_prompt import professionalize_prompt
from kajovo.core.run_bundle import LegacyRunAdapter


@pytest.mark.parametrize("workflow", ["photo", "comic"])
def test_media_preparation_links_transport_to_existing_step(tmp_path, monkeypatch, workflow):
    client = OpenAIClient("test-only", base_url="https://test.invalid/v1")
    calls = []

    def send(method, url, **kwargs):
        request = requests.Request(method, url, json=kwargs.get("json")).prepare()
        calls.append(request)
        if request.url.endswith("/models"):
            data = {"data": [{"id": "gpt-5.4"}]}
        elif request.url.endswith("/responses/input_tokens"):
            data = {"input_tokens": 100}
        else:
            assert request.method == "POST" and request.url.endswith("/responses")
            payload = json.loads(request.body)
            value = ({"professional_prompt": "Preserve the room and adjust exposure.",
                      "edit_actions": ["Adjust exposure."], "preserve_invariants": [],
                      "acceptance_criteria": ["Exposure is adjusted."]}
                     if workflow == "photo" else {key: "Konkrétní pravidlo." for key in BIBLE_FIELDS})
            assert payload["text"]["format"]["strict"] is True
            data = {"id": "resp_media", "status": "completed", "output_text": json.dumps(value)}
        response = requests.Response()
        response.status_code = 200
        response.headers["Content-Type"] = "application/json"
        response._content = json.dumps(data).encode()
        response.request = request
        return response

    monkeypatch.setattr(client.session, "request", send)
    log_dir = tmp_path / "log"
    if workflow == "photo":
        result = professionalize_prompt(client, "gpt-5.4", "Uprav expozici.", log_dir)
        assert result.response_id == "resp_media"
    else:
        service = ComicService(AppSettings(comic_library_dir=str(tmp_path / "comics"), log_dir=str(log_dir)), client)
        project = service.store.project("Příběh")
        assert service.run(service.start_bible(project))["status"] == "completed"
    assert sum(request.url.endswith("/responses") for request in calls) == 1
    root = next(log_dir.glob("RUN_*"))
    adapter = LegacyRunAdapter(root)
    records = [row for row in adapter.requests() if row["request_role"] == "transport"]
    steps = {row["step_id"] for row in adapter.steps()}
    assert records and all(row["step_id"] in steps for row in records if row["endpoint"] == "/v1/responses")
