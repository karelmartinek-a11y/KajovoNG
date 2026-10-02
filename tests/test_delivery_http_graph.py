"""Výrobní graf přes produkční HTTP transport a nové procesy; žádná živá síť."""

import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

import pytest
import requests
from native_provider_fixtures import native_fixture

from change_v2_fixtures import V2Responder, _file_input_json, response, scenario
from kajovo.core.openai_client import OpenAIClient

ROOT = Path(__file__).resolve().parents[1]


def graph_files(mode):
    rows = []
    for index, path in enumerate(("seed.txt", "middle.txt", "final.txt")):
        predecessor = [rows[-1]["path"]] if index else []
        rows.append({"path": path, "action": "generate" if mode == "GENERATE" else "modify" if index == 0 else "add",
                     "dependencies": predecessor, "content_dependencies": predecessor})
    if mode == "MODIFY":
        rows.append({"path": "preserved.txt", "action": "preserve"})
    return rows


def expected_content(mode, path):
    seed = f"{mode}: původ obsahu\n"
    return {"seed.txt": seed, "middle.txt": f"middle[{seed.strip()}]\n",
            "final.txt": f"final[middle[{seed.strip()}]]\n"}[path]


class RecordingHttp:
    """HTTP obálky, multipart a JSONL; žádné předvyplněné doménové dokončení."""

    def __init__(self, root, mode):
        self.root, self.mode = root, mode
        self.path = root / "http-provider.json"
        self.state = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {"files": {}, "batches": {}, "responses": 0}
        self.responder = V2Responder(mode, files=graph_files(mode))
        self.calls = []

    def save(self):
        self.path.write_text(json.dumps(self.state), encoding="utf-8")

    def answer(self, payload):
        name = payload["text"]["format"]["name"]
        if name == "FILE_CONTENT_V1":
            data = json.loads(payload["input"]) if isinstance(payload["input"], str) else _file_input_json(payload)
            context = data["file_context"]["working_context"]
            target = context["target_file"]["path"]
            dependencies = context["verified_dependency_artifacts"]
            assert [row["path"] for row in dependencies] == context["target_file"]["content_dependencies"]
            for row in dependencies:
                content = expected_content(self.mode, row["path"])
                assert row["content"] == content
                assert row["output_hash"] == hashlib.sha256(content.encode()).hexdigest()
            value = response(self.state["responses"], {"content": expected_content(self.mode, target)}, payload["model"])
        else:
            value = self.responder(payload)
        self.state["responses"] += 1
        value["id"] = f"resp_graph_{self.state['responses']}"
        return value

    def request(self, method, url, **kwargs):
        assert url.startswith("https://offline.invalid/v1/")
        path = url.split("/v1", 1)[1]
        record = {"method": method, "path": path, "body": copy.deepcopy(kwargs.get("json"))}
        self.calls.append(record)
        raw = None
        if (method, path) == ("GET", "/models"):
            value = {"data": [{"id": "gpt-4o-mini"}], "has_more": False}
        elif (method, path) == ("POST", "/responses/input_tokens"):
            value = {"input_tokens": 1000}
        elif (method, path) == ("POST", "/responses"):
            value = self.answer(kwargs["json"])
        elif (method, path) == ("POST", "/files"):
            assert kwargs["data"] == {"purpose": "batch"}
            assert "Content-Type" not in kwargs["headers"]
            raw_input = kwargs["files"]["file"][1].read()
            rows = [json.loads(line) for line in raw_input.splitlines()]
            assert len(rows) == 1
            assert all(row["method"] == "POST" and row["url"] == "/v1/responses" for row in rows)
            assert all(row["body"]["text"]["format"]["name"] == "FILE_CONTENT_V1" for row in rows)
            assert all("previous_response_id" not in row["body"] and "background" not in row["body"] for row in rows)
            identifier = f"file_input_{len(self.state['files'])}"
            self.state["files"][identifier] = base64.b64encode(raw_input).decode()
            record["uploaded_jsonl"] = rows
            value = {"id": identifier}
        elif (method, path) == ("POST", "/batches"):
            body = kwargs["json"]
            assert set(body) == {"input_file_id", "endpoint", "completion_window"}
            assert body["endpoint"] == "/v1/responses" and body["completion_window"] == "24h"
            assert body["input_file_id"] in self.state["files"]
            identifier = f"batch_graph_{len(self.state['batches'])}"
            value = {"id": identifier, **body, "status": "validating"}
            self.state["batches"][identifier] = value
        elif method == "GET" and path.startswith("/batches/"):
            identifier = path.split("/")[-1]
            value = copy.deepcopy(self.state["batches"][identifier])
            rows = [json.loads(line) for line in base64.b64decode(self.state["files"][value["input_file_id"]]).splitlines()]
            output = [{"custom_id": row["custom_id"], "error": None,
                       "response": {"status_code": 200, "body": self.answer(row["body"])}} for row in rows]
            output_id = "file_output_" + identifier
            if output_id not in self.state["files"]:
                self.state["files"][output_id] = base64.b64encode(("\n".join(json.dumps(row) for row in output) + "\n").encode()).decode()
            value.update(status="completed", output_file_id=output_id, error_file_id=None,
                         request_counts={"total": len(rows), "completed": len(rows), "failed": 0})
        elif method == "GET" and path.endswith("/content"):
            raw = base64.b64decode(self.state["files"][path.split("/")[2]])
            value = None
        elif method == "DELETE" and path.startswith("/files/"):
            value = {"id": path.split("/")[-1], "deleted": True}
        else:
            raise AssertionError(f"Neočekávané HTTP volání: {method} {path}")
        self.save()
        with (self.root / "http-calls.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        response = requests.Response()
        response.status_code = 200
        response.headers["Content-Type"] = "application/octet-stream" if raw is not None else "application/json"
        response._content = raw if raw is not None else json.dumps(native_fixture(method, path, value, kwargs.get("json") or kwargs.get("data"))).encode()
        return response


def delivery_process(root, mode, batch, phase):
    from kajovo.core.config import AppSettings
    from kajovo.core.generate_batch import process_saved_batch
    from kajovo.core.orchestration.publish import publish_staged_run
    from kajovo.core.run_bundle import LegacyRunAdapter

    transport = RecordingHttp(root, mode)
    client = OpenAIClient("offline-test", base_url="https://offline.invalid/v1")
    client._sdk = None
    client.session = transport
    client._transport.session = transport
    info_path = root / "delivery-info.json"
    if phase == "prepare":
        worker, _client, _responder = scenario(root, mode, batch=batch, maximum_quality=True, files=graph_files(mode))
        results, errors = [], []
        worker.finished_ok.connect(results.append)
        worker.finished_err.connect(errors.append)
        with patch("kajovo.core.runs.executor.OpenAIClient", return_value=client):
            worker.run()
        assert not errors, errors
        assert results[0]["status"] == ("batch_pending" if batch else "files_complete_unverified")
        info = {"run": str(worker.log.paths.run_dir), "out": worker.cfg.out_dir,
                "in": worker.cfg.in_dir, "batch_id": getattr(worker, "batch_id", None)}
        info_path.write_text(json.dumps(info), encoding="utf-8")
        assert not list(Path(info["out"]).glob("*.txt"))
    else:
        info = json.loads(info_path.read_text(encoding="utf-8"))
        run_dir = Path(info["run"])
        if phase == "wave":
            state = json.loads((run_dir / "run_state.json").read_text(encoding="utf-8"))
            batch_id = state.get("pending_followup_batch_id") or info.get("batch_id") or state["batch_id"]
            # Aktuální neimportovaná wave se vybírá z kanonické evidence.
            pending = [identifier for identifier in state.get("generate_batches", {})
                       if state.get("batch_imports", {}).get(identifier, {}).get("import_status") != "files_complete_unverified"]
            batch_id = pending[-1] if pending else batch_id
            result = process_saved_batch(client, run_dir, batch_id, AppSettings())
            if result.get("next_batch_id"):
                info["batch_id"] = result["next_batch_id"]
                info_path.write_text(json.dumps(info), encoding="utf-8")
            assert result["status"] in {"batch_pending", "files_complete_unverified"}
            assert not any(row["path"] == "/responses" and row["method"] == "POST" for row in transport.calls)
        else:
            report = publish_staged_run(run_dir)
            assert report["status"] == "committed"
            for path in ("seed.txt", "middle.txt", "final.txt"):
                assert (Path(info["out"]) / path).read_bytes() == expected_content(mode, path).encode()
            if mode == "MODIFY":
                assert (Path(info["in"]) / "preserved.txt").read_bytes() == b"original\n"
                assert not (Path(info["out"]) / "preserved.txt").exists()
                assert (Path(info["in"]) / "seed.txt").read_bytes() == b"original\n"
            assert LegacyRunAdapter(run_dir).bundle.verify_integrity()["valid"]
            assert not transport.calls


@pytest.mark.parametrize("mode", ["GENERATE", "MODIFY"])
@pytest.mark.parametrize("batch", [False, True])
def test_graph_http_three_content_waves_restart_and_publish(tmp_path, mode, batch):
    phases = ["prepare", *(["wave"] * 3 if batch else []), "publish"]
    environment = dict(os.environ, QT_QPA_PLATFORM="offscreen", KAJOVO_LIVE_ACCEPTANCE="0")
    environment["PYTHONPATH"] = os.pathsep.join([str(ROOT), str(ROOT / "tests")])
    for phase in phases:
        code = "from pathlib import Path; import sys; from test_delivery_http_graph import delivery_process; " + f"delivery_process(Path(sys.argv[1]), {mode!r}, {batch!r}, {phase!r})"
        result = subprocess.run([sys.executable, "-c", code, str(tmp_path)], cwd=tmp_path,
                                env=environment, capture_output=True, text=True, timeout=90)
        assert result.returncode == 0, result.stdout + result.stderr
    calls = [json.loads(line) for line in (tmp_path / "http-calls.jsonl").read_text(encoding="utf-8").splitlines()]
    submits = [row for row in calls if row["method"] == "POST" and row["path"] == "/batches"]
    assert len(submits) == (3 if batch else 0)
    requests_ = [row["body"] for row in calls if row["method"] == "POST" and row["path"] == "/responses"]
    names = [row["text"]["format"]["name"] for row in requests_]
    assert names.count("A2Q_QUALITY_GATE_V3" if mode == "GENERATE" else "B2Q_QUALITY_GATE_V3") == 1
    assert names.count("FILE_CONTENT_V1") == (0 if batch else 3)
    assert all(row["text"]["format"]["strict"] is True for row in requests_)
