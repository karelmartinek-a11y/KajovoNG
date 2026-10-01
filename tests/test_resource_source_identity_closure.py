"""Stejný název dvou schválených podkladů neurčuje konkrétní resource."""
import hashlib
from pathlib import Path
from unittest.mock import Mock

import pytest

from kajovo.core.contracts import ContractError
from kajovo.core.orchestration import resource_delivery
from test_workflows import make_worker


def sources(tmp_path, collision=False):
    worker = make_worker(tmp_path, "GENERATE")
    worker._delivery_expected_target_hashes = {"asset.bin": None}
    worker.source_context = {"attachments": []}
    for index, data in enumerate((b"prvni skutecny obsah", b"druhy skutecny obsah")):
        filename = "SRC-1" if collision and index == 0 else "logo.bin"
        source = tmp_path / str(index) / filename
        source.parent.mkdir()
        source.write_bytes(data)
        metadata = {"source_id": f"SRC-{index}", "filename": filename,
                    "sha256": hashlib.sha256(data).hexdigest()}
        worker.source_context["attachments"].append(metadata)
        worker.log.bundle.archive_artifact(source, role="source", metadata=metadata)
    return worker


def graph(identifier):
    return {"mode": "GENERATE", "spine": {
        "files": [{"path": "asset.bin", "kind": "binary_required", "action": "generate"}],
        "resource_deliveries": [{"path": "asset.bin", "producer": "existing_asset",
                                 "source_or_task_id": identifier}],
    }}


@pytest.mark.parametrize("boundary", ["plan", "dispatch"])
def test_ambiguous_resource_filename_rejected_before_staging(tmp_path, boundary):
    worker = sources(tmp_path)
    client = Mock()
    with pytest.raises(ContractError, match="víceznačný"):
        if boundary == "plan":
            resource_delivery.validate_resource_plan(worker, graph("logo.bin"))
        else:
            resource_delivery.dispatch_resource_target(worker, client, graph("logo.bin"), "asset.bin")
    assert not list(Path(worker.log.paths.run_dir).rglob("asset.bin"))
    assert not client.mock_calls


@pytest.mark.parametrize("collision", [False, True])
def test_explicit_resource_source_id_preserves_exact_bytes(tmp_path, collision):
    worker = sources(tmp_path, collision)
    resource_delivery.validate_resource_plan(worker, graph("SRC-1"))
    data, name = resource_delivery._bundle_source_bytes(worker, "SRC-1")
    assert data == b"druhy skutecny obsah"
    assert name == "logo.bin"
    client = Mock()
    result = resource_delivery.dispatch_resource_target(worker, client, graph("SRC-1"), "asset.bin")
    staged = Path(worker.log.paths.run_dir) / result["staged"]["staged_path"]
    assert staged.read_bytes() == data
    assert result["staged"]["sha256"] == hashlib.sha256(data).hexdigest()
    assert not client.mock_calls
