"""Regrese nálezů A2-018 až A2-022 a A2-036 bez síťových volání."""
import copy
import sqlite3

import pytest
from PySide6.QtCore import QSize
from PySide6.QtWidgets import QApplication, QGraphicsSceneMouseEvent

from kajovo.comic_layout import prepare_storyboard_layout
from kajovo.core.comic_types import ComicError
from test_comic_domain import comic as comic_fixture, make_panel, png


@pytest.fixture
def comic(tmp_path):
    return comic_fixture.__wrapped__(tmp_path)


@pytest.mark.parametrize("kind,boundary", [("entity", "prepare"), ("entity", "mark"),
                                         ("batch", "prepare"), ("batch", "bind"), ("batch", "mark")])
def test_a2_018_local_failure_is_retryable(comic, tmp_path, monkeypatch, kind, boundary):
    service, client, project = comic
    if kind == "entity":
        entity = service.store.entity(project, "character", "Kája")
        source = tmp_path / "reference.png"
        source.write_bytes(png())
        service.import_references(project, [source], entity)
        operation = service.start_entity(entity)
    else:
        operation = service.start_panels(project, [make_panel(service, project)])
    from kajovo.core import comic_service
    from kajovo.core.orchestration.repository import OrchestrationRepository
    owner, attr = {
        "prepare": (service, "_prepare_image_effect"),
        "bind": (OrchestrationRepository, "bind_physical_request"),
        "mark": (comic_service, "mark_submission_started"),
    }[boundary]
    original = getattr(owner, attr)
    def fail(*args, **kwargs):
        if boundary == "mark" and args[1].stage != "COMIC_IMAGE":
            return original(*args, **kwargs)
        raise OSError("Lokální příprava selhala")
    with monkeypatch.context() as patch:
        patch.setattr(owner, attr, fail)
        with pytest.raises(OSError):
            service.run(operation)
    snapshot = service.store.get("operations", operation)["snapshot"]
    assert not snapshot.get("image_submitting")
    assert client.submits == client.image_calls == 0
    for batch in service.store.rows("batches", "operation_id=?", (operation,)):
        assert batch["status"] not in {"submitting", "submission_unknown"}
    service.run(operation)
    assert client.image_calls == 1 if kind == "entity" else client.submits == 1


@pytest.mark.parametrize("kind", ["entity", "batch"])
def test_a2_018_intent_write_failure_after_started_is_known_unsent(comic, tmp_path, monkeypatch, kind):
    from kajovo.core.orchestration.repository import repository_for_logger
    service, client, project = comic
    if kind == "entity":
        entity = service.store.entity(project, "character", "Kája")
        source = tmp_path / "reference.png"
        source.write_bytes(png())
        service.import_references(project, [source], entity)
        operation = service.start_entity(entity)
    else:
        operation = service.start_panels(project, [make_panel(service, project)])

    def unexpected_probe():
        raise AssertionError("Prokazatelně neodeslaná operace nesmí hledat vzdálenou dávku")

    monkeypatch.setattr(client, "list_batches", unexpected_probe)
    original_update = service.update_operation

    def fail_intent(identifier, status=None, snapshot=None, error=None):
        if snapshot and snapshot.get("image_submitting"):
            raise sqlite3.OperationalError("Zápis záměru před POST selhal")
        return original_update(identifier, status, snapshot, error)

    with monkeypatch.context() as patch:
        if kind == "entity":
            patch.setattr(service, "update_operation", fail_intent)
        else:
            with service.store.transaction() as db:
                db.execute("""CREATE TRIGGER fail_submit_intent BEFORE UPDATE OF status ON batches
                    WHEN NEW.status='submitting'
                    BEGIN SELECT RAISE(ABORT, 'Zápis záměru před POST selhal'); END""")
        try:
            with pytest.raises(sqlite3.Error, match="Zápis záměru"):
                service.run(operation)
        finally:
            if kind == "batch":
                with service.store.transaction() as db:
                    db.execute("DROP TRIGGER fail_submit_intent")

    record = service.store.get("operations", operation)
    assert record["status"] == "failed"
    assert not record["snapshot"].get("image_submitting")
    assert client.image_calls == client.submits == 0
    repo = repository_for_logger(service.logger(record))
    with repo.connect() as db:
        states = db.execute("""SELECT p.state FROM provider_operations p
            JOIN work_orders w ON w.work_order_hash=p.work_order_hash
            WHERE w.run_id=? AND w.route IN ('image_live','image_batch')""", (record["run_id"],)).fetchall()
    assert states == [("not_submitted",)]
    for batch in service.store.rows("batches", "operation_id=?", (operation,)):
        assert batch["status"] not in {"submitting", "submission_unknown"}
    service.run(operation)
    assert client.image_calls == 1 if kind == "entity" else client.submits == 1


@pytest.mark.parametrize("field", ["id", "status", "input_file_id", "endpoint"])
def test_a2_019_comic_requires_complete_identity(comic, field):
    service, client, project = comic
    operation = service.start_panels(project, [make_panel(service, project)])
    service.run(operation)
    batch = service.store.rows("batches", "operation_id=?", (operation,))[0]
    payload = copy.deepcopy(client.batches[batch["provider_id"]])
    payload.pop(field)
    with pytest.raises(ComicError):
        service.save_batch(batch["id"], payload)
    assert service.store.get("batches", batch["id"]) == batch


def test_a2_020_description_invalidates_bible_but_name_does_not(comic):
    service, _, project = comic
    row = service.store.get("projects", project)
    service.store.update_project(project, row["revision"], "Nový název", row["description"], row["style"])
    service._require_bible(project)
    row = service.store.get("projects", project)
    service.store.update_project(project, row["revision"], row["name"], "Jiný příběh", row["style"])
    with pytest.raises(ComicError, match="Popis"):
        service.start_story(project)


@pytest.mark.parametrize("balloon,kind", [("dialogová", "dialog"), ("myšlenková", "thought"), ("narativní", "caption")])
def test_a2_021_layout_receives_style_and_preserves_dialogue(comic, balloon, kind):
    app = QApplication.instance() or QApplication([])
    service, _, project = comic
    row = service.store.get("projects", project)
    service.store.update_project(project, row["revision"], row["name"], row["description"], {**row["style"], "balloon": balloon})
    board = {"panels": [{"id": "p", "position": 1, "dialogue": [{"speaker": "Kája", "text": "Ahoj!"}], "caption": "Ráno"}]}
    result, layout = service._layout_storyboard(board, project)
    assert result == board
    assert [layer["kind"] for layer in layout["overlays"]["p"]] == [kind, "caption"]
    assert layout == prepare_storyboard_layout(board, style={"balloon": balloon})[1]
    assert app is not None


@pytest.mark.parametrize("failure", ["sfx", "write"])
def test_a2_022_duplicate_is_atomic(comic, monkeypatch, failure):
    service, _, project = comic
    store = service.store
    panel = make_panel(service, project)
    if failure == "sfx":
        with store.transaction() as db:
            db.execute("UPDATE panels SET overlays=? WHERE id=?", ('[{"kind":"sfx","text":"BUM","x":0.1,"y":0.1,"w":0.2,"h":0.2,"tail_x":0.2,"tail_y":0.3,"font_size":0.02}]', panel))
    else:
        def fail(*args):
            raise sqlite3.OperationalError("Zápis události selhal")
        monkeypatch.setattr(store, "event", fail)
    before = {table: store.rows(table) for table in ("panels", "prompts")}
    with pytest.raises((ComicError, sqlite3.Error)):
        store.panel_action(panel, "duplicate")
    assert {table: store.rows(table) for table in before} == before


def test_a2_036_geometry_announced_before_layer_moves(monkeypatch):
    app = QApplication.instance() or QApplication([])
    from kajovo.studio.comic_editor import BalloonItem
    layer = {"kind": "dialog", "text": "Ahoj", "x": .1, "y": .1, "w": .2, "h": .2,
             "tail_x": .9, "tail_y": .9, "font_size": .02}
    item = BalloonItem(layer, QSize(1000, 1000))
    before = item.boundingRect()
    item.setPos(300, 300)
    observed = []
    original = item.prepareGeometryChange
    def announce():
        observed.append((layer["x"], layer["y"]))
        original()
    monkeypatch.setattr(item, "prepareGeometryChange", announce)
    item.mouseReleaseEvent(QGraphicsSceneMouseEvent())
    assert observed == [(.1, .1)]
    assert (layer["x"], layer["y"]) == (.3, .3)
    assert item.boundingRect() != before
    assert app is not None
