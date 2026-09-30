"""Obraz kaskády musí před publikací projít dekódováním a kontrolou formátu."""

import base64
import io
import json
from unittest.mock import patch

import pytest
import requests
from PIL import Image

from kajovo.core.cascade_contract import output_machine_key
from kajovo.core.cascade_pipeline import CascadeRunConfig, CascadeRunExecutor
from kajovo.core.cascade_types import CascadeDefinition, CascadeOutput, CascadeStep
from kajovo.core.comic_types import IMAGE_MODEL
from kajovo.core.config import AppSettings
from kajovo.core.run_bundle import LegacyRunAdapter
from test_cascade_http_closure import MODEL, client_for


def image_bytes(fmt):
    stream = io.BytesIO()
    Image.new("RGB", (32, 32), "red").save(stream, fmt)
    return stream.getvalue()


class ImageHttp:
    def __init__(self, output, raw):
        self.output, self.raw = output, raw
        self.calls = []

    def request(self, method, url, **kwargs):
        path = url.split("/v1", 1)[1]
        body = kwargs.get("json")
        self.calls.append({"method": method, "path": path, "body": body})
        if path == "/models":
            value = {"data": [{"id": MODEL}, {"id": IMAGE_MODEL}], "has_more": False}
        elif path == "/responses/input_tokens":
            value = {"input_tokens": 128}
        elif path == "/responses":
            assert method == "POST" and body["model"] == MODEL
            data = {output_machine_key(self.output): {"contract": "CASCADE_BINARY_TASK_V1",
                    "path": self.output.file_name, "instructions": "Vytvoř červený obraz"}}
            value = {"id": "resp_image_plan", "status": "completed", "model": MODEL,
                     "output_text": json.dumps(data), "usage": {"input_tokens": 128, "output_tokens": 32}}
        elif path == "/images/generations":
            assert method == "POST" and body["model"] == IMAGE_MODEL
            assert body["n"] == 1 and body["output_format"] == "png"
            assert json.loads(body["prompt"])["task"]["path"] == "výsledek.png"
            value = {"created": 1, "data": [{"b64_json": base64.b64encode(self.raw).decode()}]}
        elif method == "POST" and path == "/files":
            assert kwargs["data"] == {"purpose": "user_data"}
            assert kwargs["files"]["file"][1].read() == self.raw
            value = {"id": "file_image", "filename": self.output.file_name, "bytes": len(self.raw)}
        elif path == "/files/file_image":
            value = {"id": "file_image", "filename": self.output.file_name, "bytes": len(self.raw)}
        else:
            raise AssertionError(f"Nečekaná hranice {method} {path}")
        response = requests.Response()
        response.status_code = 200
        response.headers["Content-Type"] = "application/json"
        response._content = json.dumps(value).encode()
        return response


@pytest.mark.parametrize("raw,valid", [(image_bytes("PNG"), True), (image_bytes("JPEG"), False), (b"not an image", False)])
def test_cascade_image_http_validates_exact_format_before_publish(tmp_path, raw, valid):
    output = CascadeOutput(kind="file", file_type="png", file_name="výsledek.png")
    step = CascadeStep(title="Obraz", model=MODEL, input_text="Vyrob obraz", deterministic=True, outputs=[output])
    value = CascadeDefinition("Image HTTP", steps=[step])
    transport = ImageHttp(output, raw)
    worker = CascadeRunExecutor(CascadeRunConfig("Obraz", value, "", str(tmp_path / "OUT")),
                                AppSettings(log_dir=str(tmp_path / "LOG")), "synthetic")
    results, errors = [], []
    worker.finished_ok.connect(results.append)
    worker.finished_err.connect(errors.append)
    with patch("kajovo.core.cascade_pipeline.OpenAIClient", lambda *a, **k: client_for(transport)):
        worker.execute()
    assert bool(results) is valid, (results, errors)
    assert bool(errors) is not valid
    assert sum(row["path"] == "/images/generations" for row in transport.calls) == 1
    target = tmp_path / "OUT" / "výsledek.png"
    if valid:
        assert target.read_bytes() == raw
        assert LegacyRunAdapter(worker.logger.paths.run_dir).bundle.verify_integrity()["valid"]
    else:
        assert not target.exists()
        state = json.loads((tmp_path / "LOG" / worker.logger.run_id / "run_state.json").read_text())
        assert state["status"] == "failed"
