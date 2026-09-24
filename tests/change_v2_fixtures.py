"""Deterministic CHANGE/V2 workflow fixtures; no network or paid API calls."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from unittest.mock import Mock

from kajovo.core.context_compiler import content_hash
from test_workflows import make_worker


def _input_text(payload):
    text = []
    value = payload.get("input")
    if isinstance(value, str):
        return value
    for message in value or []:
        if not isinstance(message, dict):
            continue
        for part in message.get("content") or []:
            if isinstance(part, dict) and part.get("type") == "input_text":
                text.append(str(part.get("text") or ""))
    return "".join(text)


def _input_json(payload):
    return json.loads(_input_text(payload))


def _file_input_json(payload):
    text = _input_text(payload)
    marker = '{"file_context":'
    offset = text.find(marker)
    if offset < 0:
        raise AssertionError("FILE_CONTENT_V1 fixture nemá file_context.")
    value, _end = json.JSONDecoder().raw_decode(text[offset:])
    return value


def _source_refs(source):
    return [
        {
            "source_id": row["source_id"],
            "segment_id": row["segment_id"],
            "start_byte": row["start_byte"],
            "end_byte": row["end_byte"],
            "sha256": row["sha256"],
        }
        for row in source.get("segments", [])
    ]


def requirements_data(source):
    refs = _source_refs(source)
    return {
        "product_intent": "Implementovat přesně schválený testovací cíl.",
        "requirements": [
            {
                "id": "REQ-1",
                "kind": "explicit",
                "statement": "Dodat požadovaný textový soubor.",
                "source_refs": refs,
                "derived_from": [],
                "necessity": "Explicitní uživatelský požadavek.",
                "priority": "mandatory",
                "acceptance_ids": ["AC-1"],
            }
        ],
        "invariants": [],
        "flows": [],
        "lifecycles": [],
        "acceptance": [
            {
                "id": "AC-1",
                "requirement_ids": ["REQ-1"],
                "method": "static",
                "assertion": "Výsledný staged soubor obsahuje přesný očekávaný text.",
                "mandatory": True,
            }
        ],
        "assumptions": [],
        "out_of_scope": [],
    }


def plan_data():
    return {
        "project": {
            "name": "Fixture",
            "language": "text",
            "runtime": "none",
            "target_os": [],
        },
        "components": [
            {
                "id": "COMP-1",
                "responsibility": "Vlastní testovací výstup.",
                "requirement_ids": ["REQ-1"],
                "flow_ids": [],
                "depends_on": [],
            }
        ],
        "decisions": [],
        "packages": [],
        "integration_rules": [],
        "verification_intents": [
            {
                "id": "VERIFY-1",
                "profile": "static",
                "criterion_ids": ["AC-1"],
                "expected_result": "Staged text odpovídá kontraktu.",
                "requires_network": False,
                "requires_credentials": False,
            }
        ],
    }


def default_files(mode):
    return [
        {
            "path": "hello.txt",
            "action": "generate" if mode == "GENERATE" else "add",
            "kind": "text",
            "language": "text",
            "purpose": "Testovací výstup.",
            "requirement_ids": ["REQ-1"],
            "provides": [],
            "requires": [],
            "dependencies": [],
            "content_dependencies": [],
        }
    ]


def spine_data(mode, files):
    rows = []
    for item in files:
        content_dependencies = list(item.get("content_dependencies") or [])
        rows.append(
            {
                "path": item["path"],
                "component_id": "COMP-1",
                "action": item["action"],
                "kind": item.get("kind", "text"),
                "language": item.get("language", "text"),
                "purpose": item.get("purpose", "Testovací výstup."),
                "requirement_ids": list(item.get("requirement_ids") or ["REQ-1"]),
                "provides": list(item.get("provides") or []),
                "requires": list(item.get("requires") or []),
                "dependencies": list(item.get("dependencies") or []),
                "content_dependencies": content_dependencies,
                "dependency_content_mode": (
                    "verified_content" if content_dependencies else "contract"
                ),
                "dependency_content_reason": (
                    "Consumer potřebuje přesný ověřený obsah provideru."
                    if content_dependencies
                    else ""
                ),
            }
        )
    return {
        "files": rows,
        "interfaces": [],
        "obligation_owners": [],
        "resource_deliveries": [],
    }


def file_spec(target, source_refs):
    return {
        "behavior": f"Dodat úplný obsah {target['path']}.",
        "facets": [],
        "required_facets": [],
        "interface_bindings": [],
        "acceptance_ids": ["AC-1"],
        "test_scenarios": [
            {
                "id": "TEST-1",
                "given": "Validovaný implementační kontrakt.",
                "when": "Soubor je vyroben.",
                "then": "Obsah splní AC-1.",
                "criterion_ids": ["AC-1"],
            }
        ],
        "source_refs": copy.deepcopy(source_refs),
        "assumptions": [],
        "unresolved_questions": [],
        "expected_visible_tokens": 128,
        "allow_empty": False,
        "preserved_behavior": (
            ["Zachovat dosavadní chování mimo změnu."]
            if target["action"] == "modify"
            else []
        ),
    }


def response(index, value, model="gpt-4o-mini", *, status="completed"):
    return {
        "id": f"resp_v2_{index}",
        "object": "response",
        "model": model,
        "status": status,
        "output_text": json.dumps(value, ensure_ascii=False),
        "usage": {
            "input_tokens": 100,
            "output_tokens": 50,
            "output_tokens_details": {"reasoning_tokens": 0},
        },
    }


class V2Responder:
    def __init__(self, mode, files=None, content_by_path=None, mutate=None):
        self.mode = mode
        self.files = copy.deepcopy(files or default_files(mode))
        self.content_by_path = dict(content_by_path or {})
        self.mutate = mutate
        self.calls = []
        self.index = 0

    def _ready(self, data):
        return {"result": {"status": "ready", "data": data}}

    def __call__(self, payload):
        self.calls.append(copy.deepcopy(payload))
        name = ((payload.get("text") or {}).get("format") or {}).get("name")
        data = (
            _file_input_json(payload)
            if name == "FILE_CONTENT_V1"
            else _input_json(payload)
        )
        if name == "A0R_REQUIREMENTS_V2":
            value = self._ready(requirements_data(data["source"]))
        elif name == "B0R_REQUIREMENTS_V2":
            value = self._ready(
                {
                    "change_requirements": requirements_data(
                        data["change_source"]
                    ),
                    "preserve": [],
                    "migration_requirements": [],
                }
            )
        elif name == "A1_PLAN_V2":
            value = self._ready(plan_data())
        elif name == "B1_PLAN_V2":
            existing = {row["path"] for row in data["project_inventory"]}
            add = [
                row["path"]
                for row in self.files
                if row["action"] == "add"
            ]
            modify = [
                row["path"]
                for row in self.files
                if row["action"] == "modify"
            ]
            preserve = [
                row["path"]
                for row in self.files
                if row["action"] == "preserve"
            ]
            # Keep the fixture truthful to the immutable project inventory.
            assert not (set(add) & existing)
            assert set(modify) <= existing
            assert set(preserve) <= existing
            value = self._ready(
                {
                    "plan": plan_data(),
                    "files_to_add": add,
                    "files_to_modify": modify,
                    "preserved_files": preserve,
                    "baseline_findings": [],
                }
            )
        elif name in {"A2_SPINE_V2", "B2_SPINE_V2"}:
            value = self._ready(spine_data(self.mode, self.files))
        elif name in {"A2_FILE_SPEC_V1", "B2_FILE_SPEC_V1"}:
            target = data["target"]
            refs = [
                ref
                for requirement in data["requirements"]
                for ref in requirement["source_refs"]
            ]
            value = self._ready(file_spec(target, refs))
        elif name in {"A2Q_QUALITY_GATE_V3", "B2Q_QUALITY_GATE_V3"}:
            graph = data["implementation_graph"]
            value = self._ready(
                {
                    "corrected_spine": graph["spine"],
                    "corrected_file_specs": graph["file_specs"],
                    "findings": [],
                }
            )
        elif name == "FILE_CONTENT_V1":
            target = data["file_context"]["working_context"]["target_file"]
            path = target["path"]
            value = {
                "content": self.content_by_path.get(
                    path, f"content:{path}\n"
                )
            }
        else:
            raise AssertionError(f"Unexpected structured-output contract: {name!r}")
        if self.mutate:
            value = self.mutate(name, value, data)
        result = response(self.index, value, payload.get("model") or "gpt-4o-mini")
        self.index += 1
        return result


def make_client(mode, files=None, content_by_path=None, mutate=None):
    responder = V2Responder(
        mode,
        files=files,
        content_by_path=content_by_path,
        mutate=mutate,
    )
    client = Mock()
    client.count_input_tokens.side_effect = lambda payload: {
        "input_tokens": 1000,
        "request_hash": content_hash(payload),
    }
    client.upload_file.return_value = {"id": "file_batch_input"}
    client.retrieve_file.return_value = {
        "id": "file_test",
        "filename": "input.txt",
        "bytes": 100,
    }
    client.create_batch.return_value = {
        "id": "batch_work",
        "status": "validating",
        "input_file_id": "file_batch_input",
        "endpoint": "/v1/responses",
    }
    client.create_response.side_effect = responder
    return client, responder


def scenario(
    tmp_path,
    mode,
    batch=False,
    maximum_quality=False,
    *,
    files=None,
    content_by_path=None,
    dry_run=False,
    stop_after_plan=False,
    mutate=None,
):
    worker = make_worker(tmp_path, mode)
    worker.cfg.send_as_c = batch
    worker.cfg.maximum_quality = maximum_quality
    worker.cfg.dry_run = dry_run
    worker.cfg.stop_after_plan = stop_after_plan
    worker.cfg.verification_profile_ids = []
    if mode == "MODIFY":
        in_dir = tmp_path / "in"
        in_dir.mkdir(exist_ok=True)
        worker.cfg.in_dir = str(in_dir)
        for item in files or default_files(mode):
            if item["action"] in {"modify", "preserve"}:
                path = in_dir / item["path"]
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    content_by_path.get(item["path"], "original\n")
                    if content_by_path
                    else "original\n",
                    encoding="utf-8",
                )
    client, responder = make_client(
        mode,
        files=files,
        content_by_path=content_by_path,
        mutate=mutate,
    )
    return worker, client, responder


def run(worker, client):
    results = []
    errors = []
    worker.finished_ok.connect(results.append)
    worker.finished_err.connect(errors.append)
    from unittest.mock import patch

    with patch("kajovo.core.runs.executor.OpenAIClient", return_value=client):
        worker.run()
    return results, errors


def format_names(responder):
    return [
        ((payload.get("text") or {}).get("format") or {}).get("name")
        for payload in responder.calls
    ]


def staged_path(worker, relative):
    state = json.loads(Path(worker.log.state_path).read_text(encoding="utf-8"))
    row = next(item for item in state.get("staged_files", []) if item["path"] == relative)
    return Path(worker.log.paths.run_dir) / row["staged_path"]


def batch_output_rows(manifest, content_by_path=None):
    content_by_path = content_by_path or {}
    rows = []
    for index, (custom_id, path) in enumerate(manifest["expected"].items()):
        rows.append(
            {
                "custom_id": custom_id,
                "response": {
                    "status_code": 200,
                    "body": response(
                        index,
                        {
                            "content": content_by_path.get(
                                path, f"content:{path}\n"
                            )
                        },
                    ),
                },
            }
        )
    return rows


def raw_jsonl(rows):
    return (
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n"
    ).encode("utf-8")
