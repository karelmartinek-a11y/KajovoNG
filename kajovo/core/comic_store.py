"""Transakční knihovna komiksů a neměnné souborové artefakty."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .comic_types import ComicError, PanelFormat, canonical, checked_text, validate_document, validate_overlays, validate_style


def uid():
    return uuid.uuid4().hex


def now():
    return datetime.now(timezone.utc).isoformat()


SCHEMA = """
CREATE TABLE migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
CREATE TABLE projects(id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL,
 style TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1, bible_id TEXT REFERENCES bibles(id),
 deleted INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE assets(id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
 path TEXT NOT NULL UNIQUE, sha256 TEXT NOT NULL, metadata TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE style_refs(project_id TEXT NOT NULL REFERENCES projects(id), asset_id TEXT NOT NULL REFERENCES assets(id),
 position INTEGER NOT NULL, PRIMARY KEY(project_id,asset_id));
CREATE TABLE bibles(id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
 input TEXT NOT NULL, result TEXT NOT NULL, provenance TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE entities(id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
 kind TEXT NOT NULL CHECK(kind IN ('character','environment')), name TEXT NOT NULL, description TEXT NOT NULL,
 revision INTEGER NOT NULL DEFAULT 1, active_revision TEXT REFERENCES entity_revisions(id), archived INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE entity_refs(entity_id TEXT NOT NULL REFERENCES entities(id), asset_id TEXT NOT NULL REFERENCES assets(id),
 role TEXT NOT NULL, position INTEGER NOT NULL, PRIMARY KEY(entity_id,asset_id));
CREATE TABLE entity_revisions(id TEXT PRIMARY KEY, entity_id TEXT NOT NULL REFERENCES entities(id),
 bible_id TEXT NOT NULL REFERENCES bibles(id), descriptor TEXT NOT NULL, asset_id TEXT NOT NULL REFERENCES assets(id),
 provenance TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE panels(id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), name TEXT NOT NULL,
 position INTEGER NOT NULL, revision INTEGER NOT NULL DEFAULT 1, prompt_id TEXT REFERENCES prompts(id),
 format TEXT NOT NULL, overlays TEXT NOT NULL, active_version TEXT REFERENCES panel_versions(id), deleted INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE prompts(id TEXT PRIMARY KEY, panel_id TEXT NOT NULL REFERENCES panels(id),
 document TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE bindings(prompt_id TEXT NOT NULL REFERENCES prompts(id), entity_id TEXT NOT NULL REFERENCES entities(id),
 PRIMARY KEY(prompt_id,entity_id));
CREATE TABLE operations(id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
 kind TEXT NOT NULL, target_id TEXT, status TEXT NOT NULL, snapshot TEXT NOT NULL, error TEXT NOT NULL DEFAULT '{}',
 run_id TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE batches(id TEXT PRIMARY KEY, operation_id TEXT NOT NULL REFERENCES operations(id), endpoint TEXT NOT NULL,
 status TEXT NOT NULL, input_file_id TEXT, provider_id TEXT, payload TEXT NOT NULL DEFAULT '{}',
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE batch_items(id TEXT PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES batches(id),
 panel_id TEXT NOT NULL REFERENCES panels(id), custom_id TEXT NOT NULL UNIQUE, snapshot TEXT NOT NULL,
 status TEXT NOT NULL, error TEXT NOT NULL DEFAULT '{}', result TEXT NOT NULL DEFAULT '{}');
CREATE TABLE panel_versions(id TEXT PRIMARY KEY, panel_id TEXT NOT NULL REFERENCES panels(id),
 item_id TEXT UNIQUE REFERENCES batch_items(id), base_version TEXT REFERENCES panel_versions(id),
 asset_id TEXT NOT NULL REFERENCES assets(id), raw_asset_id TEXT NOT NULL REFERENCES assets(id),
 overlays TEXT NOT NULL, provenance TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE uploads(asset_id TEXT NOT NULL REFERENCES assets(id), account TEXT NOT NULL,
 file_id TEXT NOT NULL, PRIMARY KEY(asset_id,account));
CREATE TABLE events(id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT REFERENCES projects(id),
 operation TEXT NOT NULL, data TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE INDEX panels_order ON panels(project_id,deleted,position);
CREATE INDEX operations_pending ON operations(status);
CREATE INDEX entities_project ON entities(project_id,kind,archived);
CREATE INDEX batch_items_panel ON batch_items(panel_id,status);
CREATE INDEX batch_items_batch ON batch_items(batch_id,status);
CREATE INDEX batches_operation ON batches(operation_id);
CREATE TRIGGER prompt_owner BEFORE UPDATE OF prompt_id ON panels WHEN NEW.prompt_id IS NOT NULL
BEGIN SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM prompts WHERE id=NEW.prompt_id AND panel_id=NEW.id)
THEN RAISE(ABORT,'Neplatny dokument panelu') END; END;
CREATE TRIGGER bible_owner BEFORE UPDATE OF bible_id ON projects WHEN NEW.bible_id IS NOT NULL
BEGIN SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM bibles WHERE id=NEW.bible_id AND project_id=NEW.id)
THEN RAISE(ABORT,'Neplatna bible projektu') END; END;
CREATE TRIGGER entity_revision_owner BEFORE UPDATE OF active_revision ON entities WHEN NEW.active_revision IS NOT NULL
BEGIN SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM entity_revisions WHERE id=NEW.active_revision AND entity_id=NEW.id)
THEN RAISE(ABORT,'Neplatna revize entity') END; END;
CREATE TRIGGER panel_version_owner BEFORE UPDATE OF active_version ON panels WHEN NEW.active_version IS NOT NULL
BEGIN SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM panel_versions WHERE id=NEW.active_version AND panel_id=NEW.id)
THEN RAISE(ABORT,'Neplatna verze panelu') END; END;
"""

JSON_FIELDS = {"style", "metadata", "input", "result", "provenance", "document", "format", "overlays", "snapshot", "error", "payload", "data"}
TABLES = {"projects", "assets", "bibles", "entities", "entity_revisions", "panels", "prompts", "operations", "batches", "batch_items", "panel_versions", "events"}


def decoded(row):
    value = dict(row)
    for key in JSON_FIELDS & value.keys():
        value[key] = json.loads(value[key])
    return value


class ComicStore:
    def __init__(self, root):
        self.root = Path(root).expanduser().resolve()
        self.path = self.root / "comics.sqlite"

    def initialize(self):
        self.root.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise ComicError("unsupported_database", "Knihovna vyžaduje jinou verzi aplikace.")
            if version == 0:
                existing = db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
                if existing:
                    raise ComicError("unsupported_database", "Existující neznámou databázi nelze přepsat.")
                db.executescript("BEGIN IMMEDIATE;" + SCHEMA + "PRAGMA user_version=1; INSERT INTO migrations VALUES(1,datetime('now')); COMMIT;")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
        finally:
            db.close()

    @contextmanager
    def transaction(self):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def rows(self, table, where="1", params=(), order="created_at"):
        if table not in TABLES:
            raise ValueError(table)
        if not self.path.exists():
            return []
        if table == "batch_items" and order == "created_at":
            order = "custom_id"
        with self.connect() as db:
            return [decoded(r) for r in db.execute(f"SELECT * FROM {table} WHERE {where} ORDER BY {order}", params)]

    def get(self, table, identifier):
        rows = self.rows(table, "id=?", (identifier,))
        if not rows:
            raise ComicError("missing_record", "Požadovaný záznam knihovny neexistuje.")
        return rows[0]

    def event(self, db, project, operation, data):
        db.execute("INSERT INTO events(project_id,operation,data,created_at) VALUES(?,?,?,?)", (project, operation, canonical(data), now()))

    def project(self, name, description="", style=None):
        self.initialize()
        name = checked_text(name, "Název komiksu", 200, True)
        description = checked_text(description, "Popis komiksu", 4000)
        identifier, stamp = uid(), now()
        with self.transaction() as db:
            db.execute("INSERT INTO projects(id,name,description,style,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                       (identifier, name, description, canonical(validate_style(style or {})), stamp, stamp))
            self.event(db, identifier, "create_project", {})
        return identifier

    def update_project(self, identifier, revision, name, description, style):
        checked_text(name, "Název", 200, True)
        checked_text(description, "Popis", 4000)
        with self.transaction() as db:
            cur = db.execute("UPDATE projects SET name=?,description=?,style=?,revision=revision+1,updated_at=? WHERE id=? AND revision=?",
                             (name, description, canonical(validate_style(style)), now(), identifier, revision))
            if cur.rowcount != 1:
                raise ComicError("revision_conflict", "Projekt mezitím změnila jiná operace. Obnovte zobrazení.")
            self.event(db, identifier, "update_project", {"revision": revision + 1})

    def trash(self, project, deleted=True):
        if self.rows("operations", "project_id=? AND status NOT IN ('completed','partial','failed','cancelled')", (project,)):
            raise ComicError("operation_active", "Projekt má nedokončenou operaci. Nejprve ji dokončete nebo zrušte.")
        with self.transaction() as db:
            db.execute("UPDATE projects SET deleted=?,revision=revision+1,updated_at=? WHERE id=?", (int(deleted), now(), project))
            self.event(db, project, "trash_project" if deleted else "restore_project", {})

    def asset(self, project, data, metadata):
        digest, identifier = hashlib.sha256(data).hexdigest(), uid()
        suffix = ".png" if data.startswith(b"\x89PNG") else ".jpg" if data.startswith(b"\xff\xd8") else ".webp" if data.startswith(b"RIFF") else ".jsonl" if metadata.get("role") == "batch_input" else ".bin"
        relative = f"assets/{project}/{identifier}{suffix}"
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        with self.transaction() as db:
            db.execute("INSERT INTO assets VALUES(?,?,?,?,?,?)", (identifier, project, relative, digest, canonical(metadata), now()))
        return identifier

    def asset_path(self, identifier):
        record = self.get("assets", identifier)
        path = (self.root / record["path"]).resolve()
        if not path.is_relative_to(self.root) or not path.is_file():
            raise ComicError("missing_asset", "Obrazový soubor knihovny chybí.")
        if hashlib.sha256(path.read_bytes()).hexdigest() != record["sha256"]:
            raise ComicError("corrupt_asset", "Obrazový soubor má změněný kontrolní otisk.")
        return path

    def references(self, project, entity=None):
        if not self.path.exists():
            return []
        with self.connect() as db:
            if entity:
                rows = db.execute("SELECT asset_id,role FROM entity_refs WHERE entity_id=? ORDER BY position", (entity,)).fetchall()
            else:
                rows = db.execute("SELECT asset_id,'style' AS role FROM style_refs WHERE project_id=? ORDER BY position", (project,)).fetchall()
            return [dict(r) for r in rows]

    def add_reference(self, project, asset, entity=None, role="working"):
        if self.get("assets", asset)["project_id"] != project:
            raise ComicError("broken_reference", "Reference patří jinému komiksu.")
        with self.transaction() as db:
            if entity:
                if self.get("entities", entity)["project_id"] != project:
                    raise ComicError("broken_reference", "Entita patří jinému komiksu.")
                db.execute("INSERT INTO entity_refs VALUES(?,?,?,(SELECT COUNT(*) FROM entity_refs WHERE entity_id=?))", (entity, asset, role, entity))
                db.execute("UPDATE entities SET active_revision=NULL,revision=revision+1 WHERE id=?", (entity,))
            else:
                db.execute("INSERT INTO style_refs VALUES(?,?,(SELECT COUNT(*) FROM style_refs WHERE project_id=?))", (project, asset, project))
                db.execute("UPDATE projects SET revision=revision+1 WHERE id=?", (project,))
            self.event(db, project, "add_reference", {"asset_id": asset, "entity_id": entity})

    def entity(self, project, kind, name, description=""):
        if kind not in ("character", "environment"):
            raise ComicError("invalid_input", "Neznámý typ entity.")
        checked_text(name, "Název entity", 200, True)
        checked_text(description, "Popis entity", 4000)
        identifier, stamp = uid(), now()
        with self.transaction() as db:
            db.execute("INSERT INTO entities(id,project_id,kind,name,description,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                       (identifier, project, kind, name, description, stamp, stamp))
            self.event(db, project, "create_entity", {"entity_id": identifier})
        return identifier

    def archive_entity(self, entity, archived=True):
        record = self.get("entities", entity)
        with self.transaction() as db:
            db.execute("UPDATE entities SET archived=?,revision=revision+1 WHERE id=?", (int(archived), entity))
            self.event(db, record["project_id"], "archive_entity", {"entity_id": entity, "archived": archived})

    def update_entity(self, entity, revision, name, description):
        record = self.get("entities", entity)
        checked_text(name, "Název entity", 200, True)
        checked_text(description, "Popis entity", 4000)
        with self.transaction() as db:
            cur = db.execute("UPDATE entities SET name=?,description=?,active_revision=NULL,revision=revision+1,updated_at=? WHERE id=? AND revision=?",
                             (name, description, now(), entity, revision))
            if cur.rowcount != 1:
                raise ComicError("revision_conflict", "Entitu mezitím změnila jiná operace.")
            self.event(db, record["project_id"], "update_entity", {"entity_id": entity})

    def remove_references(self, project, entity=None):
        with self.transaction() as db:
            if entity:
                if self.get("entities", entity)["project_id"] != project:
                    raise ComicError("broken_reference", "Entita patří jinému projektu.")
                db.execute("DELETE FROM entity_refs WHERE entity_id=?", (entity,))
                db.execute("UPDATE entities SET active_revision=NULL,revision=revision+1 WHERE id=?", (entity,))
            else:
                db.execute("DELETE FROM style_refs WHERE project_id=?", (project,))
                db.execute("UPDATE projects SET revision=revision+1 WHERE id=?", (project,))
            self.event(db, project, "remove_references", {"entity_id": entity})

    def panel(self, project, name="Panel"):
        identifier, stamp = uid(), now()
        with self.transaction() as db:
            db.execute("INSERT INTO panels(id,project_id,name,position,format,overlays,created_at,updated_at) VALUES(?,?,?,(SELECT COUNT(*) FROM panels WHERE project_id=?),?,'[]',?,?)",
                       (identifier, project, name, project, canonical({"width": 2048, "height": 2048, "dpi": 300, "fit": "pad", "experimental": False}), stamp, stamp))
        self.save_panel(identifier, 1, name, {"version": 1, "nodes": []}, self.get("panels", identifier)["format"], [])
        return identifier

    def save_panel(self, identifier, revision, name, document, fmt, overlays):
        panel = self.get("panels", identifier)
        checked_text(name, "Název panelu", 200, True)
        validate_document(document)
        PanelFormat(**fmt)
        project = self.get("projects", panel["project_id"])
        validate_overlays(overlays, project["style"]["sfx"])
        bindings = {n["entity_id"] for n in document["nodes"] if n["type"] != "text"}
        for node in document["nodes"]:
            if node["type"] != "text":
                entity = self.get("entities", node["entity_id"])
                if entity["project_id"] != panel["project_id"] or node["type"] != entity["kind"] + "_ref":
                    raise ComicError("broken_reference", "Odkaz patří jinému projektu nebo typu entity.")
        prompt_id = uid()
        final_asset = None
        transform = None
        if panel["active_version"] and panel["format"] != fmt:
            from .image_runtime import postprocess
            base = self.get("panel_versions", panel["active_version"])
            rendered, transform = postprocess(self.asset_path(base["raw_asset_id"]).read_bytes(), PanelFormat(**fmt))
            final_asset = self.asset(panel["project_id"], rendered, {"role": "panel", "transform": transform})
        with self.transaction() as db:
            db.execute("INSERT INTO prompts VALUES(?,?,?,?)", (prompt_id, identifier, canonical(document), now()))
            db.executemany("INSERT INTO bindings VALUES(?,?)", [(prompt_id, entity) for entity in bindings])
            cur = db.execute("UPDATE panels SET name=?,prompt_id=?,format=?,overlays=?,revision=revision+1,updated_at=? WHERE id=? AND revision=?",
                             (name, prompt_id, canonical(fmt), canonical(overlays), now(), identifier, revision))
            if cur.rowcount != 1:
                raise ComicError("revision_conflict", "Panel mezitím změnila jiná operace. Obnovte zobrazení.")
            if panel["active_version"] and (panel["overlays"] != overlays or final_asset):
                base = db.execute("SELECT * FROM panel_versions WHERE id=?", (panel["active_version"],)).fetchone()
                version = uid()
                db.execute("INSERT INTO panel_versions VALUES(?,?,?,?,?,?,?,?,?)", (version, identifier, None, base["id"], final_asset or base["asset_id"], base["raw_asset_id"], canonical(overlays), canonical({"kind": "canvas_edit" if final_asset else "text_edit", "base_version": base["id"], "format": fmt, "transform": transform}), now()))
                db.execute("UPDATE panels SET active_version=? WHERE id=?", (version, identifier))
            self.event(db, panel["project_id"], "save_panel", {"panel_id": identifier, "prompt_id": prompt_id})

    def panel_action(self, identifier, action):
        panel = self.get("panels", identifier)
        if action == "duplicate":
            new = self.panel(panel["project_id"], panel["name"] + " – kopie")
            self.save_panel(new, 2, panel["name"] + " – kopie", self.get("prompts", panel["prompt_id"])["document"], panel["format"], panel["overlays"])
            return new
        with self.transaction() as db:
            if action == "delete":
                if db.execute("SELECT 1 FROM batch_items WHERE panel_id=? AND status IN ('prepared','submitted','received')", (identifier,)).fetchone():
                    raise ComicError("operation_active", "Panel čeká na dokončení operace.")
                db.execute("UPDATE panels SET deleted=1 WHERE id=?", (identifier,))
            elif action in ("up", "down"):
                records = list(db.execute("SELECT id FROM panels WHERE project_id=? AND deleted=0 ORDER BY position,created_at", (panel["project_id"],)))
                ids = [r[0] for r in records]
                a = ids.index(identifier)
                b = max(0, min(len(ids) - 1, a + (-1 if action == "up" else 1)))
                ids[a], ids[b] = ids[b], ids[a]
                db.executemany("UPDATE panels SET position=? WHERE id=?", list(enumerate(ids)))
            else:
                raise ComicError("invalid_input", "Neznámá akce panelu.")
            self.event(db, panel["project_id"], action + "_panel", {"panel_id": identifier})

    def restore_version(self, panel_id, version_id):
        version = self.get("panel_versions", version_id)
        panel = self.get("panels", panel_id)
        if version["panel_id"] != panel_id:
            raise ComicError("invalid_input", "Verze patří jinému panelu.")
        self.asset_path(version["asset_id"])
        fmt = version["provenance"].get("format") or version["provenance"].get("snapshot", {}).get("format") or panel["format"]
        with self.transaction() as db:
            db.execute("UPDATE panels SET active_version=?,overlays=?,format=?,revision=revision+1 WHERE id=?", (version_id, canonical(version["overlays"]), canonical(fmt), panel_id))
            self.event(db, panel["project_id"], "restore_version", {"panel_id": panel_id, "version_id": version_id})

    @contextmanager
    def execution_lock(self, identifier):
        """OS zámek se po pádu procesu uvolní bez odhadu podle stáří souboru."""
        import re
        if not re.fullmatch(r"[a-f0-9]{32}", identifier):
            raise ComicError("invalid_input", "Neplatný identifikátor zámku.")
        directory = self.root / "locks"
        directory.mkdir(exist_ok=True)
        with (directory / identifier).open("a+b") as handle:
            handle.seek(0)
            if os.fstat(handle.fileno()).st_size == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise ComicError("operation_active", "Operaci právě zpracovává jiná instance.") from exc
            try:
                yield
            finally:
                handle.seek(0)
                if os.name == "nt":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
