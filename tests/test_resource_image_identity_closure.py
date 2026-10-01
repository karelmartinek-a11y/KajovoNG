"""Obnova obrazu ověřuje vlastníka odpovědi před zápisem stagingu."""
import base64
import hashlib
import io
from pathlib import Path
from unittest.mock import Mock

import pytest
from PIL import Image

from kajovo.core.contracts import ContractError
from kajovo.core.orchestration import resource_delivery
from test_audit2_preparation import image_delivery
from test_workflows import make_worker


@pytest.mark.parametrize("corruption", [
    "foreign_target", "missing_target", "target_type", "missing_provider",
    "provider_type", "foreign_response_id", "envelope_type", "valid",
])
def test_saved_image_owner_is_checked_before_resource_staging(tmp_path, corruption):
    worker = make_worker(tmp_path, "GENERATE")
    worker._delivery_expected_target_hashes = {"asset.png": None}
    graph = {"spine": {"files": [{"path": "asset.png", "kind": "binary_required"}],
                       "resource_deliveries": [image_delivery()]}}
    output = io.BytesIO()
    Image.new("RGB", (8, 8), "green").save(output, format="PNG")
    binary = output.getvalue()
    payload = {"target_path": "asset.png", "provider_id": "image_saved",
               "response": {"id": "image_saved", "data": [
                   {"b64_json": base64.b64encode(binary).decode("ascii")} ]}}
    if corruption == "foreign_target":
        payload["target_path"] = "other.png"
    elif corruption == "missing_target":
        del payload["target_path"]
    elif corruption == "target_type":
        payload["target_path"] = ["asset.png"]
    elif corruption == "missing_provider":
        del payload["provider_id"]
    elif corruption == "provider_type":
        payload["provider_id"] = 7
    elif corruption == "foreign_response_id":
        payload["response"]["id"] = "image_foreign"
    elif corruption == "envelope_type":
        payload = []
    evidence = Path(worker.log.save_json(
        "responses", resource_delivery._resource_image_response_name("asset.png"), payload,
    ))
    before = evidence.read_bytes()
    client = Mock()
    if corruption != "valid":
        with pytest.raises(ContractError, match="identit|cíl|obálk|poškozen"):
            resource_delivery.dispatch_resource_target(worker, client, graph, "asset.png")
        assert not list(Path(worker.log.paths.run_dir).glob("staging/**/*.png"))
        assert not getattr(worker, "_resource_staged_files", {})
    else:
        result = resource_delivery.dispatch_resource_target(worker, client, graph, "asset.png")
        assert result["status"] == "completed_unverified"
        staged = Path(worker.log.paths.run_dir, result["staged"]["staged_path"])
        assert staged.read_bytes() == binary
        assert result["staged"]["sha256"] == hashlib.sha256(binary).hexdigest()
    client.create_image.assert_not_called()
    assert evidence.read_bytes() == before
