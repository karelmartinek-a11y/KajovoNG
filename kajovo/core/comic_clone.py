"""Atomická kopie komiksového grafu s typovanými vazbami a původem verzí."""
from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from .comic_store import JSON_FIELDS, decoded, now, uid
from .comic_types import ComicError, canonical


REFERENCE_KEYS = frozenset({
    "project_id", "bible_id", "entity_id", "asset_id", "raw_asset_id", "original_asset",
    "storage_asset_id", "panel_id", "prompt_id", "source_id", "source_document_id",
    "storyboard_id", "continuity_id", "base_version", "active_version", "active_revision",
    "derived_from", "revision_id", "entity_revision_id",
})
REFERENCE_LISTS = frozenset({"assets", "asset_ids", "entity_ids", "panel_ids"})
IDENTITY_OBJECTS = frozenset({"project", "entity", "entities", "bible", "panel", "version"})


def remap_value(value, mapping, *, key="", parent=""):
    if isinstance(value, str):
        if key in REFERENCE_KEYS or key in REFERENCE_LISTS or (
            key in {"id", "revision"} and parent in IDENTITY_OBJECTS
        ):
            return mapping.get(value, value)
        return value
    if isinstance(value, list):
        return [remap_value(item, mapping, key=key, parent=parent) for item in value]
    if isinstance(value, dict):
        return {name: remap_value(item, mapping, key=name, parent=key) for name, item in value.items()}
    return value


def duplicate_project(store, project_id, check_stop):
    copied = []
    committed = False
    with store.execution_lock(project_id):
        try:
            with store.transaction() as db:
                db.execute("PRAGMA defer_foreign_keys=ON")
                source_row = db.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
                if source_row is None:
                    raise ComicError("missing_project", "Zdrojový komiks neexistuje.")
                source = decoded(source_row)
                rows = {"projects": [source]}
                for table in ("assets", "bibles", "entities", "comic_documents", "panels"):
                    rows[table] = [decoded(row) for row in db.execute(
                        f"SELECT * FROM {table} WHERE project_id=?", (project_id,))]
                for table, owner, foreign in (
                    ("entity_revisions", "entities", "entity_id"),
                    ("prompts", "panels", "panel_id"), ("panel_versions", "panels", "panel_id"),
                ):
                    rows[table] = [decoded(row) for row in db.execute(
                        f"SELECT * FROM {table} WHERE {foreign} IN (SELECT id FROM {owner} WHERE project_id=?)",
                        (project_id,))]
                for table, query in (
                    ("style_refs", "project_id=?"),
                    ("entity_refs", "entity_id IN (SELECT id FROM entities WHERE project_id=?)"),
                    ("bindings", "prompt_id IN (SELECT id FROM prompts WHERE panel_id IN (SELECT id FROM panels WHERE project_id=?))"),
                ):
                    rows[table] = [dict(row) for row in db.execute(f"SELECT * FROM {table} WHERE {query}", (project_id,))]
                mapping = {row["id"]: uid() for values in rows.values() for row in values if "id" in row}
                target = mapping[project_id]
                stamp = now()
                for table, values in rows.items():
                    for old in values:
                        check_stop()
                        row = dict(old)
                        for key, value in old.items():
                            if key in JSON_FIELDS:
                                row[key] = remap_value(value, mapping)
                            elif isinstance(value, str) and (key == "id" or key in REFERENCE_KEYS):
                                row[key] = mapping.get(value, value)
                        if table == "projects":
                            row.update(name=source["name"][:192] + " – kopie", deleted=0, updated_at=stamp)
                        if table == "panel_versions":
                            row["item_id"] = None
                        if table in {"bibles", "comic_documents", "entity_revisions", "panel_versions"}:
                            row["provenance"] = {**row["provenance"], "copied_from": old["id"],
                                                 "copied_from_project": project_id}
                        if table == "assets":
                            src = (store.root / old["path"]).resolve()
                            if not src.is_relative_to(store.root) or hashlib.sha256(src.read_bytes()).hexdigest() != old["sha256"]:
                                raise ComicError("corrupt_asset", "Zdrojový artefakt kopie je poškozený.")
                            relative = Path("assets") / target / (row["id"] + src.suffix)
                            destination = store.root / relative
                            destination.parent.mkdir(parents=True, exist_ok=True)
                            copied.append(destination)
                            shutil.copyfile(src, destination)
                            if hashlib.sha256(destination.read_bytes()).hexdigest() != old["sha256"]:
                                raise ComicError("corrupt_asset", "Kopie artefaktu změnila hash.")
                            row["path"] = relative.as_posix()
                        for key in JSON_FIELDS & row.keys():
                            row[key] = canonical(row[key])
                        columns = ",".join(row)
                        db.execute(f"INSERT INTO {table}({columns}) VALUES({','.join('?' for _ in row)})", tuple(row.values()))
                store.event(db, target, "duplicate_project", {"source_project_id": project_id})
                for event in db.execute("SELECT data FROM events WHERE project_id=? AND operation='storyboard_materialized'", (project_id,)).fetchall():
                    store.event(db, target, "storyboard_materialized", remap_value(decoded(event)["data"], mapping))
            committed = True
            return target
        finally:
            if not committed:
                for path in copied:
                    path.unlink(missing_ok=True)
