from unittest.mock import Mock

import pytest
from PIL import Image

from kajovo.core.compat import MAX_INPUT_FILE_BYTES, is_compatible_path, validate_input_file_sizes
from kajovo.core.openai_client import OpenAIClient
from kajovo.core.orchestration.errors import OrchestrationError
from kajovo.core.orchestration.source_pack import freeze_run_sources, source_context
from test_workflows import make_worker


@pytest.mark.parametrize("name,accepted", [("code.go", True), ("slides.pptx", True),
                                         ("code.CPP", True), ("data.csv", False),
                                         ("config.yaml", False), ("archive.zip", False)])
def test_file_search_formats(name, accepted):
    assert is_compatible_path(name) is accepted


@pytest.mark.parametrize("metadata", [[{}], [{"bytes": -1}], [{"bytes": True}],
                                     [{"bytes": MAX_INPUT_FILE_BYTES}],
                                     [{"bytes": MAX_INPUT_FILE_BYTES - 1}, {"bytes": 2}]])
def test_input_size_rejects_invalid_or_excessive_total(metadata):
    with pytest.raises(ValueError):
        validate_input_file_sizes(metadata)


def test_input_size_boundary():
    validate_input_file_sizes([{"bytes": MAX_INPUT_FILE_BYTES - 1}, {"bytes": 1}])


def test_unsupported_file_is_not_indexed():
    client = OpenAIClient("test")
    client.retrieve_file = Mock(return_value={"filename": "data.csv"})
    client._req = Mock()
    with pytest.raises(ValueError):
        client.add_file_to_vector_store("vs_test", "file-test")
    client._req.assert_not_called()



def test_source_pack_freezes_remote_files_and_vector_store_members(tmp_path):
    worker = make_worker(tmp_path, "QA")
    worker.cfg.input_file_ids = ["file_direct"]
    worker.cfg.attached_file_ids = ["file_direct"]
    worker.cfg.attached_vector_store_ids = ["vs_one"]

    client = Mock()
    client.list_vector_store_files.return_value = [{"id": "file_vector"}]
    metadata = {
        "file_direct": {"id": "file_direct", "filename": "direct.txt", "bytes": 6},
        "file_vector": {"id": "file_vector", "filename": "vector.txt", "bytes": 6},
    }
    payloads = {
        "file_direct": b"direct",
        "file_vector": b"vector",
    }
    client.retrieve_file.side_effect = lambda fid: metadata[fid]
    client.file_content.side_effect = lambda fid: payloads[fid]

    pack = freeze_run_sources(
        worker.cfg, worker.settings, worker.log, client=client
    )
    remote = [source for source in pack.sources if source.kind == "file"]
    assert len(remote) == 2
    assert {source.byte_length for source in remote} == {6}
    assert client.file_content.call_count == 2

    evidence = source_context(worker.log, pack)
    texts = {row["text"] for row in evidence["segments"]}
    assert "direct" in texts
    assert "vector" in texts

    artifacts = [
        row
        for row in worker.log.bundle.artifacts()
        if row["role"] == "remote_input"
    ]
    assert {
        row["metadata"]["provider_file_id"] for row in artifacts
    } == {"file_direct", "file_vector"}


def test_source_pack_blocks_when_remote_bytes_cannot_be_frozen(tmp_path):
    worker = make_worker(tmp_path, "QA")
    worker.cfg.input_file_ids = ["file_missing"]
    client = Mock()
    client.retrieve_file.return_value = {
        "id": "file_missing",
        "filename": "missing.txt",
        "bytes": 1,
    }
    client.file_content.side_effect = RuntimeError("expired")

    with pytest.raises(OrchestrationError, match="zmrazit"):
        freeze_run_sources(
            worker.cfg, worker.settings, worker.log, client=client
        )


def test_source_pack_approves_validated_project_image_as_asset(tmp_path):
    worker = make_worker(tmp_path, "MODIFY")
    project = tmp_path / "project"
    project.mkdir()
    image_path = project / "logo.png"
    Image.new("RGB", (8, 8), (255, 255, 255)).save(image_path, format="PNG")
    worker.cfg.in_dir = str(project)

    pack = freeze_run_sources(
        worker.cfg, worker.settings, worker.log, client=Mock()
    )
    image_sources = [source for source in pack.sources if source.kind == "image"]
    assert len(image_sources) == 1
    assert image_sources[0].media_type == "image/png"

    evidence = source_context(worker.log, pack)
    assert evidence["image_slots"][0]["slot_id"] == image_sources[0].id
    provider = next(
        row for row in evidence["_provider_inputs"]
        if row["source_id"] == image_sources[0].id
    )
    assert provider["filename"] == "logo.png"

    artifact = next(
        row for row in worker.log.bundle.artifacts()
        if row["role"] == "in_project_file"
        and (row.get("metadata") or {}).get("relative_path") == "logo.png"
    )
    assert artifact["metadata"]["policy_decision"] == "approved_asset"
