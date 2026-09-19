"""Regrese knihovny a pracovní pipeline bez sítě a bez provozních dat."""
import base64
import io
import json
from dataclasses import asdict
from pathlib import Path

import pytest
from PIL import Image

from kajovo.core.comic_service import ComicService
from kajovo.core.comic_store import ComicStore
from kajovo.core.comic_types import BIBLE_FIELDS, ComicError, PanelFormat, normalize_bible, validate_document, validate_style
from kajovo.core.config import AppSettings
from kajovo.core.image_runtime import image_capability, inspect_image, postprocess, validate_image_request


def png(size=(1024, 1024), color="navy"):
    stream = io.BytesIO()
    Image.new("RGB", size, color).save(stream, "PNG")
    return stream.getvalue()


class WorkingClient:
    """Řízený poskytovatel testu; všechny obrázky jsou lokální testovací data."""
    base_url = "https://test.invalid/v1"
    api_key = "test-key"

    def __init__(self):
        self._known_responses = set()
        self.files, self.batches, self.responses = {}, {}, []
        self.batch_rows = {}
        self.fail_ids = set()
        self.submits = 0
        self.image_calls = 0

    def _validate_resource_id(self, value):
        assert isinstance(value, str) and value

    def upload_file(self, path, purpose):
        identifier = "file_" + str(len(self.files))
        self.files[identifier] = Path(path).read_bytes()
        return {"id": identifier}

    def retrieve_file(self, identifier):
        return {"id": identifier}

    def create_response(self, body):
        self.responses.append(body)
        schema = body["text"]["format"]["schema"]
        value = {key: "Pravidla a zachování identity." for key in schema["properties"]}
        return {"id": "resp_" + str(len(self.responses)), "status": "completed",
                "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(value)}]}],
                "usage": {"input_tokens": 20, "output_tokens": 40}}

    def create_image(self, endpoint, body):
        validate_image_request(endpoint, body)
        self.image_calls += 1
        return {"data": [{"b64_json": base64.b64encode(png(tuple(map(int, body["size"].split("x"))))).decode()}],
                "usage": {"input_tokens": 20, "input_tokens_details": {"text_tokens": 10, "image_tokens": 10}, "output_tokens": 30}}

    def create_image_batch(self, file_id, rows):
        self.submits += 1
        identifier = "batch_" + str(self.submits)
        self.batch_rows[identifier] = rows
        self.batches[identifier] = {"id": identifier, "input_file_id": file_id, "endpoint": rows[0]["url"],
                                    "status": "in_progress", "request_counts": {"total": len(rows), "completed": 0, "failed": 0}}
        return self.batches[identifier]

    def retrieve_batch(self, identifier):
        return self.batches[identifier]

    def complete(self, identifier):
        rows = self.batch_rows[identifier]
        output = []
        for row in rows:
            if row["custom_id"] in self.fail_ids:
                output.append({"custom_id": row["custom_id"], "error": {"code": "moderation_blocked", "message": "Odmítnuto"}})
            else:
                output.append({"custom_id": row["custom_id"], "response": {"status_code": 200, "body": self.create_image(row["url"], row["body"])}})
        file_id = "output_" + identifier
        self.files[file_id] = "\n".join(json.dumps(r) for r in reversed(output)).encode()
        self.batches[identifier].update(status="completed", output_file_id=file_id)

    def file_content(self, identifier):
        return self.files[identifier]

    def list_batches(self):
        return list(self.batches.values())

    def cancel_batch(self, identifier):
        self.complete(identifier)
        self.batches[identifier]["status"] = "cancelled"
        return self.batches[identifier]


@pytest.fixture
def comic(tmp_path):
    settings = AppSettings(comic_library_dir=str(tmp_path / "comics"), log_dir=str(tmp_path / "log"))
    client = WorkingClient()
    service = ComicService(settings, client)
    project = service.store.project("Příběh")
    service.run(service.start_bible(project))
    return service, client, project


def make_panel(service, project, entities=()):
    panel = service.store.panel(project)
    nodes = [{"type": "text", "text": "U bazénu stojí "}]
    for entity in entities:
        row = service.store.get("entities", entity)
        nodes.append({"type": row["kind"] + "_ref", "entity_id": entity})
    service.store.save_panel(panel, 2, "Panel", {"version": 1, "nodes": nodes}, asdict(PanelFormat(1024, 1024)), [])
    return panel


def make_entity(service, project, tmp_path, kind="character", count=1):
    entity = service.store.entity(project, kind, "Karel" if kind == "character" else "Bazén")
    paths = []
    for index in range(count):
        path = tmp_path / f"source{index}.png"
        path.write_bytes(png())
        paths.append(path)
    service.import_references(project, paths, entity)
    service.run(service.start_entity(entity))
    return entity


def test_project_persistence_trash_and_revision(tmp_path):
    store = ComicStore(tmp_path / "library")
    project = store.project("Název")
    store.update_project(project, 1, "Nový", "Popis", {})
    assert ComicStore(store.root).get("projects", project)["name"] == "Nový"
    with pytest.raises(ComicError, match="mezitím"):
        store.update_project(project, 1, "Kolize", "", {})
    store.trash(project)
    assert store.get("projects", project)["deleted"] == 1
    store.trash(project, False)
    assert store.get("projects", project)["deleted"] == 0
    with store.connect() as db:
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []


def test_bible_explicit_options_always_win():
    style = validate_style({"sfx": False, "line": "silná", "color": "černobílá"})
    result = normalize_bible({k: "Zapni všechny efekty" for k in BIBLE_FIELDS}, style)
    assert "zakázány" in result["rules"]["sfx"]
    assert result["rules"]["linework_rules"] == "silná"
    assert result["explicit_options"] == style


def test_bible_real_request_contract_and_saved_revision(comic):
    service, client, project = comic
    bible = service.store.get("bibles", service.store.get("projects", project)["bible_id"])
    assert client.responses[0]["text"]["format"]["strict"] is True
    assert client.responses[0]["background"] is True
    assert bible["provenance"]["model"] == "gpt-6-astra"
    service.run(service.start_bible(project))
    assert len(service.store.rows("bibles")) == 2


@pytest.mark.parametrize("kind,count", [("character", 1), ("character", 3), ("environment", 1)])
def test_entity_generated_from_references(comic, tmp_path, kind, count):
    service, client, project = comic
    entity = make_entity(service, project, tmp_path, kind, count)
    revision = service.store.get("entity_revisions", service.store.get("entities", entity)["active_revision"])
    assert revision["descriptor"]
    assert inspect_image(service.store.asset_path(revision["asset_id"]).read_bytes())["width"] == 1536
    assert len(service.store.references(project, entity)) == count * 2
    assert client.image_calls == 1


@pytest.mark.parametrize("kinds", [[], ["character"], ["character", "character"], ["environment"], ["character", "environment"], ["character", "character", "environment"]])
def test_compiler_entity_combinations(comic, tmp_path, kinds):
    service, client, project = comic
    entities = [make_entity(service, project, tmp_path, kind) for kind in kinds]
    panel = make_panel(service, project, entities)
    snapshot = service.compile_panel(panel)
    assert len(snapshot["assets"]) == int(bool(kinds))
    assert len(snapshot["entities"]) == len(kinds)
    assert snapshot["endpoint"].endswith("edits" if kinds else "generations")
    # CHANGE COMIC, oddíl 7: shodné bajty sdílejí jeden obraz, ne jednu roli.
    for entity in snapshot["entities"]:
        assert entity["image_index"] == 1
        assert entity["slot_id"] == "IMAGE-001"


def test_archived_and_foreign_reference_rejected(comic, tmp_path):
    service, _, project = comic
    entity = make_entity(service, project, tmp_path)
    panel = make_panel(service, project, [entity])
    service.store.archive_entity(entity)
    with pytest.raises(ComicError, match="reference"):
        service.compile_panel(panel)
    other = service.store.project("Jiný")
    with pytest.raises(ComicError, match="jinému"):
        make_panel(service, other, [entity])


def test_ast_rejects_forged_nodes():
    with pytest.raises(ComicError):
        validate_document({"version": 1, "nodes": [{"type": "character_ref", "entity_id": "../../etc"}]})


@pytest.mark.parametrize("size", [(1024, 1024), (1600, 900), (900, 1600), (2480, 3508), (731, 987), (12, 5)])
def test_formats_preserve_exact_target_without_stretch(size):
    fmt = PanelFormat(*size)
    native = fmt.native_size(image_capability())
    w, h = map(int, native.split("x"))
    assert w % 16 == h % 16 == 0
    result, transform = postprocess(png((1024, 1024), "red"), fmt)
    with Image.open(io.BytesIO(result)) as image:
        assert image.size == size
        if size[0] != size[1] and min(size) > 100:
            assert image.getpixel((0, 0)) == (255, 255, 255, 255)
    assert transform["fit"] == "pad"


def test_batch_partial_retry_and_resume(comic):
    service, client, project = comic
    panels = [make_panel(service, project) for _ in range(3)]
    operation = service.start_panels(project, panels)
    assert service.run(operation)["status"] == "batch_pending"
    assert client.submits == 1
    batch = next(iter(client.batches))
    client.fail_ids.add(client.batch_rows[batch][1]["custom_id"])
    client.complete(batch)
    resumed = ComicService(service.settings, client)
    assert resumed.run(operation)["status"] == "partial"
    assert len(service.store.rows("panel_versions")) == 2
    service.run(operation)
    assert len(service.store.rows("panel_versions")) == 2
    retry = service.retry_failed(operation)
    service.run(retry)
    assert len(client.batch_rows["batch_2"]) == 1
    client.complete("batch_2")
    service.run(retry)
    assert len(service.store.rows("panel_versions")) == 3


def test_edit_creates_candidate_and_restore_preserves_history(comic):
    service, client, project = comic
    panel = make_panel(service, project)
    operation = service.start_panels(project, [panel])
    service.run(operation)
    client.complete("batch_1")
    service.run(operation)
    original = service.store.get("panels", panel)["active_version"]
    edit = service.start_panels(project, [panel], "Změň hodiny na modré")
    service.run(edit)
    assert client.batch_rows["batch_2"][0]["url"] == "/v1/images/edits"
    client.complete("batch_2")
    service.run(edit)
    assert service.store.get("panels", panel)["active_version"] == original
    versions = service.store.rows("panel_versions", "panel_id=?", (panel,))
    service.store.restore_version(panel, versions[-1]["id"])
    service.store.restore_version(panel, original)
    assert len(service.store.rows("panel_versions")) == 2


def test_unknown_submit_reconciles_without_second_post(comic, monkeypatch):
    service, client, project = comic
    operation = service.start_panels(project, [make_panel(service, project)])
    submit = client.create_image_batch
    def lost(*args):
        submit(*args)
        raise TimeoutError("Ztracená odpověď")
    monkeypatch.setattr(client, "create_image_batch", lost)
    with pytest.raises(SubmissionUnknown):
        service.run(operation)
    assert service.run(operation)["status"] == "batch_pending"
    assert client.submits == 1


def test_corrupt_input_never_uploads(comic, tmp_path):
    service, client, project = comic
    path = tmp_path / "script.png"
    path.write_text("<svg onload='alert(1)'/>")
    before = len(client.files)
    with pytest.raises(ComicError):
        service.import_references(project, [path])
    assert len(client.files) == before


def test_duplicate_remaps_entities_and_has_no_jobs(comic, tmp_path):
    service, _, project = comic
    entity = make_entity(service, project, tmp_path)
    make_panel(service, project, [entity])
    duplicate = service.duplicate_project(project)
    panel = service.store.rows("panels", "project_id=?", (duplicate,))[0]
    doc = service.store.get("prompts", panel["prompt_id"])["document"]
    assert doc["nodes"][1]["entity_id"] != entity
    assert not service.store.rows("operations", "project_id=?", (duplicate,))
    assert service.compile_panel(panel["id"])["entities"]


from kajovo.core.response_journal import SubmissionUnknown


def test_change_reference_slots_reach_real_batch_body_without_index_shift(comic, tmp_path, record_property):
    """COM-03: edit base, prvni entita a styl sdili bytes, druha entita je jina."""
    from kajovo.core.comic_store import canonical, now, uid
    from kajovo.core.image_runtime import normalized_image

    service, client, project = comic
    red, blue = png((64, 64), 'red'), png((64, 64), 'blue')
    style = service.store.asset(project, red, {'role': 'working'})
    service.store.add_reference(project, style)
    service.run(service.start_bible(project))
    entity_ids = sorted([make_entity(service, project, tmp_path), make_entity(service, project, tmp_path)])
    # Explicitne sestavene schvalene vstupni reference; zadny provider kandidat se neopravuje.
    for entity, data in zip(entity_ids, (red, blue), strict=True):
        previous = service.store.get('entity_revisions', service.store.get('entities', entity)['active_revision'])
        source = service.store.asset(project, data, {'role': 'generated_raw'})
        revision = uid()
        with service.store.transaction() as db:
            db.execute('INSERT INTO entity_revisions VALUES(?,?,?,?,?,?,?)', (
                revision, entity, previous['bible_id'], previous['descriptor'], source,
                canonical({'test_input': 'approved_reference'}), now(),
            ))
            db.execute('UPDATE entities SET active_revision=?,revision=revision+1 WHERE id=?', (revision, entity))
    panel = make_panel(service, project, list(reversed(entity_ids)))
    base_asset, version = service.store.asset(project, red, {'role': 'generated_raw'}), uid()
    with service.store.transaction() as db:
        db.execute('INSERT INTO panel_versions VALUES(?,?,?,?,?,?,?,?,?)', (
            version, panel, None, None, base_asset, base_asset, '[]', '{}', now(),
        ))
        db.execute('UPDATE panels SET active_version=? WHERE id=?', (version, panel))
    text_calls, image_calls, submit_calls = len(client.responses), client.image_calls, client.submits
    snap = service.compile_panel(panel, 'Preserve the scene.')
    assert [entry['id'] for entry in snap['entities']] == entity_ids
    assert [entry['image_index'] for entry in snap['entities']] == [1, 2]
    assert [entry['slot_id'] for entry in snap['entities']] == ['IMAGE-001', 'IMAGE-002']
    mapping = {role['role_id']: role['index'] for role in snap['reference_slots']['roles']}
    assert mapping['EDIT_BASE'] == mapping['ENTITY-' + entity_ids[0]] == mapping['STYLE-' + style] == 1
    assert mapping['ENTITY-' + entity_ids[1]] == 2
    assert len(snap['assets']) == 2
    assert service.store.asset_path(base_asset).read_bytes() == red
    assert service.store.asset_path(style).read_bytes() == red
    operation = service.start_panels(project, [panel], 'Preserve the scene.')
    service.run(operation)
    row = client.batch_rows['batch_1'][0]
    actual_inputs = [client.files[image['file_id']] for image in row['body']['images']]
    assert actual_inputs == [normalized_image(red), normalized_image(blue)]
    assert len(client.responses) == text_calls
    assert client.image_calls == image_calls
    assert client.submits == submit_calls + 1
    record_property('observed_transport_call_count', 1)
    record_property('transport_kind', 'offline controlled batch adapter')
    record_property('fixture_sha256', snap['reference_slots']['slots'][0]['normalized_sha256'])


def test_change_normalized_reference_keeps_original_metadata_bytes(comic):
    import hashlib
    from PIL.PngImagePlugin import PngInfo
    from kajovo.core.orchestration.image_slots import normalization_policy_hash

    service, client, project = comic
    stream = io.BytesIO()
    image = Image.new('RGBA', (16, 16), (10, 20, 30, 200))
    metadata = PngInfo()
    metadata.add_text('private-note', 'not transmitted')
    image.save(stream, 'PNG', pnginfo=metadata)
    original = stream.getvalue()
    identifier = service.store.asset(project, original, {'role': 'original'})
    before_calls = len(client.responses), client.image_calls, client.submits
    first = service._frozen_image_index(project, [identifier]).assets[0]
    second = service._frozen_image_index(project, [identifier]).assets[0]
    assert first == second
    assert first.storage_id != identifier
    assert first.transform_policy_hash == normalization_policy_hash()
    assert service.store.asset_path(identifier).read_bytes() == original
    normalized = service.store.asset_path(first.storage_id).read_bytes()
    assert hashlib.sha256(normalized).hexdigest() == first.normalized_sha256
    with Image.open(io.BytesIO(normalized)) as result:
        assert 'private-note' not in result.info
        assert result.getpixel((0, 0)) == (10, 20, 30, 200)
    assert (len(client.responses), client.image_calls, client.submits) == before_calls
