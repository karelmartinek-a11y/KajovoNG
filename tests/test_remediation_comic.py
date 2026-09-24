"""Bezeztrátová sazba, atomické kopie a původ historických panelů."""
from dataclasses import asdict

import pytest
from PySide6.QtWidgets import QApplication

from kajovo.comic_layout import prepare_storyboard_layout, text_document
from kajovo.core.comic_store import now, uid
from kajovo.core.comic_types import PanelFormat, canonical
from test_comic_domain import comic as comic_fixture, make_panel


@pytest.fixture
def comic(tmp_path):
    return comic_fixture.__wrapped__(tmp_path)


@pytest.fixture
def layout_app():
    app = QApplication.instance() or QApplication([])
    yield app


def test_layout_keeps_all_graphemes_and_fits_actual_renderer(layout_app):
    text = ("Příliš žluťoučký kůň 👩‍👩‍👧‍👦 e\u0301! " * 400).rstrip()
    board = {"panels": [{"id": "p", "position": 1, "scene_id": "s", "visual": "Les",
                         "entity_ids": [], "dialogue": [{"speaker": "Kája", "text": text}], "caption": text}]}
    result, layout = prepare_storyboard_layout(board)
    assert len(result["panels"]) > 1
    assert "".join(line["text"] for panel in result["panels"] for line in panel["dialogue"]) == text
    assert "".join(panel["caption"] for panel in result["panels"]) == text
    assert prepare_storyboard_layout(board) == (result, layout)
    for layers in layout["overlays"].values():
        for layer in layers:
            doc, inner = text_document(layer, layout["format"]["width"], layout["format"]["height"])
            assert doc.size().height() <= inner.height()
            assert layer["y"] + layer["h"] <= .92


def test_panel_creation_is_complete_and_copy_handles_maximum_name(comic):
    from kajovo.core.comic_types import ComicError
    service, _, project = comic
    with pytest.raises(ComicError):
        service.store.panel(project, "x" * 201)
    assert service.store.rows("panels", "project_id=?", (project,)) == []
    panel = service.store.panel(project, "x" * 200)
    copied = service.store.panel_action(panel, "duplicate")
    value = service.store.get("panels", copied)
    assert len(value["name"]) == 200 and value["name"].endswith(" – kopie")
    assert service.store.get("prompts", value["prompt_id"])["panel_id"] == copied


def test_clone_is_atomic_and_preserves_typed_document_links(comic, monkeypatch):
    service, _, project = comic
    store = service.store
    panel = make_panel(service, project)
    bible = store.get("projects", project)["bible_id"]
    document_ids = [uid(), uid(), uid()]
    with store.transaction() as db:
        for index, kind in enumerate(("story", "script", "storyboard")):
            db.execute("INSERT INTO comic_documents VALUES(?,?,?,?,?,?,?,?)", (
                document_ids[index], project, kind, document_ids[index - 1] if index else None,
                canonical({"bible_id": bible, "source_document_id": document_ids[index - 1] if index else None,
                           "project": {"id": project, "description": project}}),
                canonical({"title": project}), canonical({"format": asdict(PanelFormat(731, 987))}), now(),
            ))
        db.execute("UPDATE panels SET storyboard_id=?,storyboard_position=1 WHERE id=?", (document_ids[-1], panel))
    target = service.duplicate_project(project)
    copies = store.rows("comic_documents", "project_id=?", (target,))
    mapped = {row["provenance"]["copied_from"]: row for row in copies}
    for index, identifier in enumerate(document_ids):
        row = mapped[identifier]
        assert row["input"]["bible_id"] == store.get("projects", target)["bible_id"]
        assert row["input"]["project"] == {"id": target, "description": project}
        assert row["result"]["title"] == project
        if index:
            assert row["source_id"] == mapped[document_ids[index - 1]]["id"]
    clone_panel = store.rows("panels", "project_id=?", (target,))[0]
    assert clone_panel["storyboard_id"] == mapped[document_ids[-1]]["id"]
    assert clone_panel["storyboard_position"] == 1
    before = store.rows("projects")
    def fail(*args):
        raise OSError("Přerušené kopírování")
    monkeypatch.setattr(service, "check_stop", fail)
    with pytest.raises(OSError):
        service.duplicate_project(project)
    assert store.rows("projects") == before


def test_clone_preserves_version_format_and_base_chain(comic):
    service, _, project = comic
    from test_comic_recovery import finish
    panel = make_panel(service, project)
    finish(service, service.client, project, [panel])
    original = service.store.get("panels", panel)
    service.store.save_panel(panel, original["revision"], original["name"],
                             service.store.get("prompts", original["prompt_id"])["document"],
                             asdict(PanelFormat(731, 987)), [])
    target = service.duplicate_project(project)
    clone = service.store.rows("panels", "project_id=?", (target,))[0]
    versions = service.store.rows("panel_versions", "panel_id=?", (clone["id"],))
    assert len(versions) == 2
    old = next(row for row in versions if row["provenance"]["copied_from"] == original["active_version"])
    service.store.restore_version(clone["id"], old["id"])
    assert service.store.get("panels", clone["id"])["format"] == original["format"]
