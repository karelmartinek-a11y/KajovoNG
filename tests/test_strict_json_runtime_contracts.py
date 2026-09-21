import sqlite3

import pytest

from kajovo.core.contracts import ContractError, parse_json_strict
from kajovo.core.generate_batch import encode_requests
from kajovo.core.openai_client import OpenAIClient, image_batch_submit_payload
from kajovo.core.orchestration.errors import OrchestrationError
from kajovo.core.photo_batch import _jsonl
from kajovo.core.recoverable_artifacts import save_artifact
from kajovo.core.response_journal import ResponseJournal
from kajovo.core.run_bundle import _json_bytes
from kajovo.core.runlog import RunLogger
from kajovo.core.runs.recovery import _validate_runtime_manifest
from kajovo.core.comic_store import ComicStore
from kajovo.core.comic_types import canonical as comic_canonical
from test_generate_batch import manifest


@pytest.mark.parametrize(
    "text",
    [
        'prefix {"x": 1}',
        '{"x": 1} suffix',
        '{"x": 1, "x": 2}',
        '{"x": NaN}',
        '{"x": Infinity}',
    ],
)
def test_core_strict_json_rejects_noncanonical_input(text):
    with pytest.raises(ContractError):
        parse_json_strict(text)


def test_batch_jsonl_rejects_duplicate_keys_before_policy_or_network():
    client = OpenAIClient("test-key")
    payload = (
        '{"custom_id":"a","custom_id":"b","method":"POST",'
        '"url":"/v1/responses","body":{}}\n'
    ).encode("utf-8")
    with pytest.raises((ContractError, OrchestrationError)):
        client.validate_batch_data(payload)


def test_batch_file_context_rejects_trailing_json():
    value = manifest()
    value["requests"][0]["body"]["input"] += "\nTRAILING"
    with pytest.raises(ContractError):
        encode_requests(value)


def test_image_batch_submit_payload_contains_only_documented_batch_fields():
    body = image_batch_submit_payload("file_123", "/v1/images/edits")
    assert body == {
        "input_file_id": "file_123",
        "endpoint": "/v1/images/edits",
        "completion_window": "24h",
        "output_expires_after": {
            "anchor": "created_at",
            "seconds": 2592000,
        },
    }


def test_response_journal_rejects_duplicate_keys(tmp_path):
    logger = RunLogger(str(tmp_path), "RUN_STRICT_JOURNAL")
    ResponseJournal(logger).save()
    path = logger.find_json("manifests", "response_journal")
    assert path
    with open(path, "w", encoding="utf-8") as stream:
        stream.write('{"version":1,"version":1,"entries":{}}')
    with pytest.raises((ContractError, OrchestrationError)):
        ResponseJournal(logger)


def test_content_addressed_artifact_rejects_non_json_python_type(tmp_path):
    with pytest.raises(OrchestrationError):
        save_artifact(tmp_path, "bad", {"value": object()})


def test_run_bundle_serializer_rejects_non_json_python_type():
    with pytest.raises(OrchestrationError):
        _json_bytes({"value": object()})


def test_response_runtime_rejects_unknown_attribute():
    runtime = {
        "attributes": {
            "_diag_text": "",
            "_in_dir_info": None,
            "_vector_store_ids": [],
            "_diag_vector_store_ids": [],
            "_fs_tools": None,
            "_diag_zip_path": "",
            "_input_kind_cache": {},
            "_file_name_cache": {},
            "_unexpected_runtime_field": "must-not-restore",
        },
        "diag_file_ids": [],
        "preparation_snapshot": None,
        "response_id": "",
        "resume_files": [],
        "resume_prev_id": None,
    }
    with pytest.raises(ContractError, match="množinu atributů"):
        _validate_runtime_manifest(runtime)


def test_photo_jsonl_rejects_duplicate_keys():
    with pytest.raises(ValueError, match="nekanonický JSON"):
        _jsonl(b'{"custom_id":"a","custom_id":"b"}\n', "photo-output")


def test_comic_sqlite_physically_rejects_invalid_json(tmp_path):
    store = ComicStore(tmp_path / "comic")
    store.initialize()
    with store.connect() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 4
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                """
                INSERT INTO projects(
                    id,name,description,style,created_at,updated_at
                ) VALUES(?,?,?,?,?,?)
                """,
                ("p1", "P", "", "{invalid", "now", "now"),
            )


def test_comic_sqlite_rejects_invalid_json_on_update(tmp_path):
    store = ComicStore(tmp_path / "comic")
    project = store.project("P")
    with store.connect() as db:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "UPDATE projects SET style=? WHERE id=?",
                ("{invalid", project),
            )



def test_comic_canonical_maps_only_tuple_sequences_to_json_arrays():
    assert comic_canonical({"slots": ({"index": 1},)}) == (
        '{"slots":[{"index":1}]}'
    )
    with pytest.raises(OrchestrationError):
        comic_canonical({"bad": object()})
