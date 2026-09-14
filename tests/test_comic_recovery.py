"""Výpadky, neměnnost verzí a ochrana hranic komiksové knihovny."""
import base64
from dataclasses import asdict
import json
import sqlite3

import pytest

from kajovo.core.comic_service import ComicService
from kajovo.core.comic_types import ComicError, PanelFormat
from test_comic_domain import comic as comic_fixture, make_entity, make_panel


@pytest.fixture(name="comic")
def recovery_comic(tmp_path):
    return comic_fixture.__wrapped__(tmp_path)


def test_sunburst_never_sends_unsupported_fidelity(comic):
    service, _, _ = comic
    body = service.image_body("Scéna", "1024x1024", ["file_reference"])
    assert "input_fidelity" not in body
    from kajovo.core.image_runtime import validate_image_request
    with pytest.raises(ComicError, match="věrnost"):
        validate_image_request("/v1/images/edits", {**body, "input_fidelity": "high"})


def test_real_upload_validator_accepts_image_jsonl_and_rejects_mixing(comic):
    service, _, _ = comic
    from kajovo.core.openai_client import OpenAIClient
    client = OpenAIClient("test-only")
    body = service.image_body("Scéna", "1024x1024", ["file_reference"])
    row = {"custom_id": "panel_1", "method": "POST", "url": "/v1/images/edits", "body": body}
    assert client.validate_batch_data(json.dumps(row).encode()) == [row]
    invalid = {**row, "custom_id": "panel_2", "url": "/v1/responses"}
    with pytest.raises(ValueError):
        client.validate_batch_data((json.dumps(row) + "\n" + json.dumps(invalid)).encode())


def finish(service, client, project, panels):
    operation = service.start_panels(project, panels)
    service.run(operation)
    for batch in service.store.rows("batches", "operation_id=?", (operation,)):
        client.complete(batch["provider_id"])
    service.run(operation)
    return operation


def test_canvas_edit_and_restore_keep_original(comic):
    service, client, project = comic
    panel = make_panel(service, project)
    finish(service, client, project, [panel])
    old = service.store.get("panels", panel)
    old_version = service.store.get("panel_versions", old["active_version"])
    original = service.store.asset_path(old_version["asset_id"]).read_bytes()
    service.store.save_panel(panel, old["revision"], old["name"], service.store.get("prompts", old["prompt_id"])["document"], asdict(PanelFormat(731, 987)), [])
    changed = service.store.get("panels", panel)
    assert changed["active_version"] != old["active_version"]
    assert service.store.asset_path(old_version["asset_id"]).read_bytes() == original
    from PIL import Image
    with Image.open(service.store.asset_path(service.store.get("panel_versions", changed["active_version"])["asset_id"])) as image:
        assert image.size == (731, 987)
    service.store.restore_version(panel, old["active_version"])
    assert service.store.get("panels", panel)["format"] == old["format"]


def test_storage_failure_resumes_from_downloaded_archive(comic, monkeypatch):
    service, client, project = comic
    panel = make_panel(service, project)
    operation = service.start_panels(project, [panel])
    service.run(operation)
    client.complete(service.store.rows("batches")[0]["provider_id"])
    original = service.store.asset
    def fail_final(project, data, metadata):
        if metadata["role"] == "panel":
            raise OSError("Testovací plný disk")
        return original(project, data, metadata)
    monkeypatch.setattr(service.store, "asset", fail_final)
    with pytest.raises(OSError):
        service.run(operation)
    monkeypatch.setattr(client, "file_content", lambda _: pytest.fail("Archiv se nesmí znovu stahovat"))
    recovered = ComicService(service.settings, client)
    assert recovered.run(operation)["status"] == "completed"
    assert client.submits == 1
    assert len(recovered.store.rows("panel_versions")) == 1


def test_corrupt_image_does_not_discard_successful_panel(comic):
    service, client, project = comic
    panels = [make_panel(service, project) for _ in range(2)]
    operation = service.start_panels(project, panels)
    service.run(operation)
    batch = service.store.rows("batches")[0]
    client.complete(batch["provider_id"])
    file_id = client.batches[batch["provider_id"]]["output_file_id"]
    rows = [json.loads(line) for line in client.files[file_id].splitlines()]
    rows[0]["response"]["body"]["data"][0]["b64_json"] = base64.b64encode(b"invalid raster").decode()
    client.files[file_id] = "\n".join(json.dumps(row) for row in rows).encode()
    assert service.run(operation)["status"] == "partial"
    assert len(service.store.rows("panel_versions")) == 1
    assert len(service.store.rows("batch_items", "status='failed'")) == 1


def test_prompt_foreign_key_and_operation_lock(comic):
    service, _, project = comic
    first, second = make_panel(service, project), make_panel(service, project)
    prompt = service.store.get("panels", second)["prompt_id"]
    with pytest.raises(sqlite3.IntegrityError), service.store.transaction() as db:
        db.execute("UPDATE panels SET prompt_id=? WHERE id=?", (prompt, first))
    with service.store.execution_lock(project), pytest.raises(ComicError, match="jiná instance"):
        with service.store.execution_lock(project):
            pytest.fail("Druhý vlastník získal zámek")


def test_reference_change_invalidates_canonical_entity(comic, tmp_path):
    service, _, project = comic
    entity = make_entity(service, project, tmp_path)
    original = service.store.get("entities", entity)
    assert original["active_revision"]
    service.store.update_entity(entity, original["revision"], "Petr", "Nový oděv")
    assert service.store.get("entities", entity)["active_revision"] is None
    assert len(service.store.rows("entity_revisions")) == 1


def test_cancel_before_submission_never_calls_provider(comic):
    service, client, project = comic
    operation = service.start_panels(project, [make_panel(service, project)])
    assert service.cancel(operation)["status"] == "cancelled"
    assert client.submits == 0


def test_import_only_never_submits_prepared_batches(comic):
    service, client, project = comic
    operation = service.start_panels(project, [make_panel(service, project)])
    assert service.run(operation, allow_submit=False)["status"] == "batch_pending"
    assert client.submits == 0


def test_asset_path_cannot_escape_library(comic, tmp_path):
    service, _, project = comic
    asset = service.store.asset(project, b"local", {"role": "test"})
    outside = tmp_path / "outside"
    outside.write_bytes(b"local")
    with service.store.transaction() as db:
        db.execute("UPDATE assets SET path=? WHERE id=?", (str(outside), asset))
    with pytest.raises(ComicError, match="chybí"):
        service.store.asset_path(asset)


def test_panel_order_delete_and_pending_guard(comic):
    service, _, project = comic
    first, second = make_panel(service, project), make_panel(service, project)
    service.store.panel_action(second, "up")
    assert service.store.rows("panels", order="position")[0]["id"] == second
    service.store.panel_action(second, "down")
    assert service.store.rows("panels", order="position")[0]["id"] == first
    service.start_panels(project, [first])
    with pytest.raises(ComicError, match="čeká"):
        service.store.panel_action(first, "delete")
    service.store.panel_action(second, "delete")
    assert service.store.get("panels", second)["deleted"]


def test_reference_removal_preserves_historical_assets(comic, tmp_path):
    service, _, project = comic
    entity = make_entity(service, project, tmp_path)
    references = service.store.references(project, entity)
    asset = references[0]["asset_id"]
    service.store.add_reference(project, asset)
    service.store.remove_references(project)
    assert service.store.references(project) == []
    service.store.remove_references(project, entity)
    assert service.store.references(project, entity) == []
    assert service.store.get("entities", entity)["active_revision"] is None
    assert service.store.asset_path(asset).is_file()
    assert len(service.store.rows("entity_revisions")) == 1
