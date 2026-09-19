"""Zmrazené offline podklady a odpovědi pro izolované testy přípravy V2."""

from copy import deepcopy
import json

from change_v2_fixtures import V2Responder, scenario
from kajovo.core.delivery_preparation import prepare_delivery
from kajovo.core.orchestration.source_pack import freeze_run_sources, source_context


def decoded(payload):
    return json.loads("".join(
        part["text"] for message in payload["input"]
        for part in message["content"] if part["type"] == "input_text"
    ))


class PreparationResponder(V2Responder):
    """Rozbalí opravný vstup pro společnou fixture, eviduje přesný request."""

    def __call__(self, payload):
        normalized = deepcopy(payload)
        context = decoded(payload)
        if "repair" in context:
            normalized["input"] = [{"role": "user", "content": [{
                "type": "input_text",
                "text": json.dumps(context["input"], ensure_ascii=False),
            }]}]
        try:
            return super().__call__(normalized)
        finally:
            self.calls[-1] = deepcopy(payload)


def preparation_scenario(tmp_path, mode, *, source="Přesné zadání", quality=False,
                         mutate=None, attachments=None):
    worker, client, _ = scenario(tmp_path, mode, maximum_quality=quality)
    worker.cfg.prompt = source
    if attachments:
        worker.cfg.attached_file_ids = list(attachments)
        client.retrieve_file.side_effect = lambda identifier: {
            "id": identifier, "filename": attachments[identifier][0],
            "bytes": len(attachments[identifier][1]),
        }
        client.file_content.side_effect = lambda identifier: attachments[identifier][1]
    worker.source_pack = freeze_run_sources(worker.cfg, worker.settings, worker.log, client=client)
    worker.source_context = source_context(worker.log, worker.source_pack)
    responder = PreparationResponder(mode, mutate=mutate)
    client.create_response.side_effect = responder
    return worker, client, responder


def prepare(worker, client, previous_id=None):
    return prepare_delivery(worker, client, worker.cfg.mode, previous_id,
                            worker.cfg.prompt, [], [], None)
