"""Bezpečné čtení, náhled a export skutečných ArtifactRecord."""

from __future__ import annotations

import hashlib
import json
import difflib
import mimetypes
from pathlib import Path
import shutil
import os
import tempfile
import zipfile

from PySide6.QtCore import QUrl, Qt, Slot
from PySide6.QtGui import QDesktopServices, QGuiApplication, QPixmap
from PySide6.QtWidgets import (
    QFileDialog, QLabel, QPlainTextEdit, QStackedWidget, QTableWidget, QTableWidgetItem, QWidget,
)

from kajovo.core.safe_config import redact_evidence
from kajovo.core.utils import safe_join_under_root

from .components import action, actions, caption, vertical


TEXT_PREVIEW_LIMIT = 1024 * 1024
TEXT_DIFF_LIMIT = 5 * 1024 * 1024


def _redacted_export_bytes(path: Path) -> tuple[bytes, bool]:
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return raw, False

    suffix = path.suffix.lower()
    if suffix == ".json":
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            redacted = redact_evidence(text)
        else:
            redacted = json.dumps(
                redact_evidence(value),
                ensure_ascii=False,
                indent=2,
            ) + "\n"
    elif suffix == ".jsonl":
        lines = []
        for line in text.splitlines():
            if not line.strip():
                lines.append("")
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                lines.append(str(redact_evidence(line)))
            else:
                lines.append(
                    json.dumps(
                        redact_evidence(value),
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                )
        redacted = "\n".join(lines)
        if text.endswith("\n"):
            redacted += "\n"
    else:
        redacted = str(redact_evidence(text))
    encoded = redacted.encode("utf-8")
    return encoded, encoded != raw


def export_run_bundle(adapter, destination):
    """Create a derived redacted ZIP; the immutable source bundle is never rewritten."""
    root, target = adapter.root.resolve(), Path(destination).resolve()
    if target == root or root in target.parents:
        raise ValueError("Export nesmí přepsat zdrojový Run Bundle.")
    if adapter.bundle and adapter.bundle.verify_integrity().get("status") == "changed":
        raise ValueError("Run Bundle neprošel kontrolou integrity.")
    guard = ArtifactGuard(root)
    for record in adapter.artifacts():
        if record.get("available_local") is not False and record.get("sha256"):
            guard.resolve(record)

    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=".run-export-",
        suffix=".zip",
        dir=target.parent,
    )
    os.close(descriptor)
    changed: list[str] = []
    exported: list[dict[str, object]] = []
    archive_root = root.name + "_REDACTED"
    try:
        with zipfile.ZipFile(
            temporary,
            "w",
            compression=zipfile.ZIP_DEFLATED,
        ) as archive:
            for path in sorted(root.rglob("*")):
                if not path.is_file():
                    continue
                resolved = path.resolve()
                if root not in resolved.parents:
                    raise ValueError(
                        "Souborový odkaz vede mimo zdrojový Run Bundle."
                    )
                relative = path.relative_to(root).as_posix()
                payload, was_changed = _redacted_export_bytes(path)
                if was_changed:
                    changed.append(relative)
                exported.append(
                    {
                        "path": relative,
                        "sha256": hashlib.sha256(payload).hexdigest(),
                        "bytes": len(payload),
                        "redacted": was_changed,
                    }
                )
                archive.writestr(
                    f"{archive_root}/{relative}",
                    payload,
                )
            manifest = {
                "version": 1,
                "kind": "derived_redacted_run_export",
                "source_run_id": root.name,
                "source_bundle_unchanged": True,
                "canonical_source_integrity_checked": bool(adapter.bundle),
                "redacted_paths": changed,
                "files": exported,
                "notice": (
                    "Tento ZIP je odvozený redigovaný export. Jeho hashe "
                    "popisují exportované bytes, nikoli kanonický původní RunBundle."
                ),
            }
            archive.writestr(
                f"{archive_root}/DERIVED_REDACTED_EXPORT.json",
                json.dumps(
                    manifest,
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
            )
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return str(target)


class ArtifactGuard:
    def __init__(self, bundle_root: str | Path):
        self.root = Path(bundle_root).resolve()

    def resolve(self, record: dict) -> Path:
        if record.get("available_local") is False:
            raise ValueError("Artefakt je pouze vzdálený; místní bytes zatím nejsou k dispozici.")
        relative = record.get("path_in_bundle")
        if not isinstance(relative, str) or not relative:
            raise ValueError("Artefakt nemá evidovanou místní cestu.")
        target = Path(safe_join_under_root(str(self.root), relative))
        if not target.is_file():
            raise ValueError("Evidovaný místní artefakt neexistuje.")
        expected = record.get("sha256")
        if not isinstance(expected, str) or len(expected) != 64:
            raise ValueError("Artefakt nemá ověřitelný SHA-256.")
        digest = hashlib.sha256()
        with target.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        actual = digest.hexdigest()
        if actual != expected:
            raise ValueError("Obsah artefaktu neodpovídá evidovanému SHA-256.")
        return target

    def export(self, record: dict, destination: str | Path) -> Path:
        source = self.resolve(record)
        target = Path(destination).resolve()
        if target == self.root or self.root in target.parents:
            raise ValueError("Export nesmí přepsat zdrojový Run Bundle.")
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".artifact-export-", dir=target.parent)
        os.close(descriptor)
        try:
            shutil.copyfile(source, temporary)
            with open(temporary, "r+b") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
                os.fsync(stream.fileno())
            if digest != record["sha256"]:
                raise ValueError("Obsah exportu neodpovídá evidovanému SHA-256.")
            if target.exists():
                shutil.copymode(target, temporary)
            os.replace(temporary, target)
        finally:
            Path(temporary).unlink(missing_ok=True)
        return target


class ArtifactBrowser(QWidget):
    def __init__(self, parent=None, *, context=None):
        super().__init__(parent)
        self.context = context
        self.preview_generation = 0
        self.bundle_root: Path | None = None
        self.artifacts: list[dict] = []
        self.guard: ArtifactGuard | None = None
        self._checks = {}
        self._text_ready = False
        root = vertical(self, 0)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Soubor", "Role", "MIME", "Velikost", "Integrita"])
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.ExtendedSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setAccessibleName("Artefakty vybraného běhu")
        self.table.currentCellChanged.connect(lambda *_: self.preview())
        self.table.itemSelectionChanged.connect(self._availability)
        root.addWidget(self.table, 1)
        self.preview_stack = QStackedWidget()
        self.text_preview = QPlainTextEdit()
        self.text_preview.setReadOnly(True)
        self.text_preview.setAccessibleName("Textový náhled artefaktu")
        self.image_preview = QLabel("Vyberte obrázek")
        self.image_preview.setAlignment(Qt.AlignCenter)
        self.image_preview.setScaledContents(False)
        self.meta_preview = QPlainTextEdit()
        self.meta_preview.setReadOnly(True)
        self.meta_preview.setAccessibleName("Metadata artefaktu")
        for widget in (self.text_preview, self.image_preview, self.meta_preview):
            self.preview_stack.addWidget(widget)
        self.pdf_document = None
        try:
            from PySide6.QtPdf import QPdfDocument
            from PySide6.QtPdfWidgets import QPdfView
            self.pdf_document = QPdfDocument(self)
            self.pdf_preview = QPdfView()
            self.pdf_preview.setDocument(self.pdf_document)
            self.pdf_preview.setZoomMode(QPdfView.ZoomMode.FitToWidth)
            self.preview_stack.addWidget(self.pdf_preview)
        except ImportError:
            self.pdf_preview = None
        self.pdf_supported = self.pdf_document is not None and self.pdf_preview is not None
        self.preview_stack.setMinimumHeight(120)
        root.addWidget(self.preview_stack, 1)
        self.pdf_previous = action("history.pdf.previous", "Předchozí stránka", lambda: self.pdf_page(-1))
        self.pdf_next = action("history.pdf.next", "Další stránka", lambda: self.pdf_page(1))
        self.pdf_controls = actions(
            self.pdf_previous, self.pdf_next,
            action("history.pdf.fit", "Celá stránka", self.pdf_fit),
            action("history.pdf.zoom.in", "+", lambda: self.pdf_zoom(1.25)),
            action("history.pdf.zoom.out", "−", lambda: self.pdf_zoom(0.8)))
        self.pdf_controls.hide()
        root.addWidget(self.pdf_controls)
        if self.pdf_supported:
            self.pdf_document.pageCountChanged.connect(self._pdf_availability)
            self.pdf_preview.pageNavigator().currentPageChanged.connect(self._pdf_availability)
        self.notice = caption("Vyberte skutečný místní artefakt.", "muted")
        root.addWidget(self.notice)
        self.buttons = {
            "open": action("history.artifact.open", "Otevřít", self.open),
            "save": action("history.artifact.save", "Uložit jako", self.save),
            "export": action("history.artifact.export", "Exportovat", self.save),
            "copy": action("history.artifact.copy", "Kopírovat text", self.copy_text),
            "txt": action("history.artifact.txt", "Uložit TXT", self.save_text),
            "metadata": action("history.artifact.metadata", "Metadata", self.metadata),
            "compare": action("history.artifact.compare", "Porovnat vybrané", self.compare),
        }
        root.addWidget(actions(*self.buttons.values()))

    def set_artifacts(self, root: str | Path, records: list[dict]):
        self._checks.clear()
        self.bundle_root = Path(root)
        self.guard = ArtifactGuard(root)
        self.artifacts = list(records)
        self.table.setRowCount(len(records))
        for row, record in enumerate(records):
            mime = str(record.get("mime_type") or mimetypes.guess_type(str(record.get("display_name") or ""))[0] or "application/octet-stream")
            values = [
                str(record.get("display_name") or "Není evidováno"),
                {"generated_file": "Výsledek", "modified_file": "Upravený soubor", "batch_output": "Výstup dávky",
                 "in_project_file": "Vstupní soubor", "attached_file": "Příloha", "user_input": "Zadání",
                 "manifest": "Podklady", "intermediate_file": "Průběžný výstup"}.get(record.get("role"), str(record.get("role") or "Není evidováno")),
                mime,
                str(record.get("size_bytes") if record.get("size_bytes") is not None else "Není evidováno"),
                "SHA-256" if record.get("sha256") else "Neověřeno",
            ]
            for column, value in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(value))
        self.table.resizeColumnsToContents()
        if records:
            self.table.setCurrentCell(0, 0)
        else:
            self.meta_preview.setPlainText("Žádné artefakty nejsou evidovány.")
            self.preview_stack.setCurrentWidget(self.meta_preview)
        self._availability()

    def selected(self) -> dict | None:
        row = self.table.currentRow()
        return self.artifacts[row] if 0 <= row < len(self.artifacts) else None

    def _path(self) -> Path:
        record = self.selected()
        if not record or not self.guard:
            raise ValueError("Nejprve vyberte artefakt.")
        return self.guard.resolve(record)

    def _availability(self):
        record = self.selected()
        local = bool(record and self._checks.get(record.get("artifact_id") or record.get("path_in_bundle")))
        for key in ("open", "save", "export"):
            self.buttons[key].setEnabled(local)
            self.buttons[key].setToolTip("Akce vyžaduje existující místní bytes a platný SHA-256." if not local else "")
        textual = local and self._text_ready
        for key in ("copy", "txt"):
            self.buttons[key].setVisible(textual)
        self.buttons["compare"].setVisible(len(self.artifacts) > 1)
        self.buttons["copy"].setEnabled(textual)
        self.buttons["txt"].setEnabled(textual)
        self.buttons["metadata"].setEnabled(bool(record))
        rows = self.table.selectionModel().selectedRows()
        self.buttons["compare"].setEnabled(len(rows) == 2 and all(
            self._checks.get(self.artifacts[index.row()].get("artifact_id") or self.artifacts[index.row()].get("path_in_bundle"))
            for index in rows))

    def preview(self):
        self._text_ready = False
        self._availability()
        record = self.selected()
        if not record:
            return
        self.preview_generation += 1
        generation = self.preview_generation
        self.notice.setText("Ověřuji integritu a připravuji místní náhled…")
        if self.context:
            self.context.operations.start_read(
                "Příprava náhledu artefaktu",
                lambda task: self._prepare_preview(record),
                lambda value: self._show_preview(value) if generation == self.preview_generation else None,
                popup=False,
                identifier=f"artifact.preview:{id(self)}:{generation}",
            )
            return
        self._show_preview(self._prepare_preview(record))

    def _prepare_preview(self, record):
        try:
            if not self.guard:
                raise ValueError("Run Bundle není načten.")
            path = self.guard.resolve(record)
            mime = str(record.get("mime_type") or mimetypes.guess_type(path.name)[0] or "application/octet-stream")
            if mime.startswith("text/") or path.suffix.lower() in {".md", ".json", ".jsonl", ".csv", ".py", ".ts", ".tsx", ".js", ".yaml", ".yml"}:
                with path.open("rb") as stream:
                    data = stream.read(TEXT_PREVIEW_LIMIT + 1)
                return {"kind": "text", "value": data[:TEXT_PREVIEW_LIMIT].decode("utf-8", errors="replace"),
                        "clipped": len(data) > TEXT_PREVIEW_LIMIT}
            if mime.startswith("image/"):
                return {"kind": "image", "path": str(path)}
            if mime == "application/pdf" and self.pdf_supported:
                return {"kind": "pdf", "path": str(path)}
            return {"kind": "metadata", "value": self._metadata_text(record) + "\n\nBinární obsah se nezobrazuje jako text."}
        except (ValueError, OSError) as error:
            return {"kind": "error", "value": self._metadata_text(record) + "\n\n" + str(error), "error": str(error)}

    def _show_preview(self, prepared):
        kind = prepared["kind"]
        self.pdf_controls.setVisible(kind == "pdf")
        record = self.selected() or {}
        self._checks[record.get("artifact_id") or record.get("path_in_bundle")] = kind != "error"
        self._text_ready = kind == "text"
        self._availability()
        if kind == "text":
            self.text_preview.setPlainText(prepared["value"] + (
                "\n\n[Náhled je omezen na 1 MiB; kanonický soubor zůstal úplný.]" if prepared["clipped"] else ""
            ))
            self.preview_stack.setCurrentWidget(self.text_preview)
        elif kind == "image":
            pixmap = QPixmap(prepared["path"])
            if pixmap.isNull():
                self.meta_preview.setPlainText("Obrázek nelze dekódovat.")
                self.preview_stack.setCurrentWidget(self.meta_preview)
                self.notice.setText("Obrázek nelze dekódovat.")
                return
            self.image_preview.setPixmap(pixmap.scaled(900, 520, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            self.preview_stack.setCurrentWidget(self.image_preview)
        elif kind == "pdf":
            self.pdf_document.load(prepared["path"])
            self.preview_stack.setCurrentWidget(self.pdf_preview)
            self._pdf_availability()
        else:
            self.meta_preview.setPlainText(prepared["value"])
            self.preview_stack.setCurrentWidget(self.meta_preview)
        if kind == "error":
            self.notice.setText(prepared["error"])
        else:
            self.notice.setText("Náhled je read-only; kanonický artefakt se nemění.")

    def pdf_page(self, delta):
        if self.pdf_supported and self.pdf_document.pageCount():
            navigator = self.pdf_preview.pageNavigator()
            page = max(0, min(self.pdf_document.pageCount() - 1, navigator.currentPage() + delta))
            from PySide6.QtCore import QPointF
            navigator.jump(page, QPointF(), self.pdf_preview.zoomFactor())
            self._pdf_availability()

    @Slot()
    def _pdf_availability(self):
        from shiboken6 import isValid
        if self.pdf_supported and isValid(self.pdf_document) and isValid(self.pdf_preview):
            page = self.pdf_preview.pageNavigator().currentPage()
            self.pdf_previous.setEnabled(page > 0)
            self.pdf_next.setEnabled(page + 1 < self.pdf_document.pageCount())

    def pdf_fit(self):
        if self.pdf_supported:
            self.pdf_preview.setZoomMode(self.pdf_preview.ZoomMode.FitInView)

    def pdf_zoom(self, factor):
        if self.pdf_supported:
            value = self.pdf_preview.zoomFactor()
            self.pdf_preview.setZoomMode(self.pdf_preview.ZoomMode.Custom)
            self.pdf_preview.setZoomFactor(max(0.05, min(8.0, value * factor)))

    @staticmethod
    def _metadata_text(record):
        return "\n".join(f"{key}: {value}" for key, value in record.items() if key != "metadata") + "\nmetadata: " + str(record.get("metadata") or {})

    def metadata(self):
        record = self.selected()
        if record:
            self.meta_preview.setPlainText(self._metadata_text(record))
            self.preview_stack.setCurrentWidget(self.meta_preview)

    def open(self):
        record, guard = self.selected(), self.guard
        if self.context and record and guard:
            self.context.operations.start_read("Ověření souboru před otevřením", lambda task: str(guard.resolve(record)),
                lambda path: QDesktopServices.openUrl(QUrl.fromLocalFile(path)), popup=False)
            return
        try:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._path())))
        except (ValueError, OSError) as error:
            self.notice.setText(str(error))

    def save(self):
        record = self.selected()
        if not record or not self.guard:
            return
        destination, _ = QFileDialog.getSaveFileName(self, "Exportovat ověřený artefakt", str(record.get("display_name") or "artefakt"))
        if destination:
            if self.context:
                guard = self.guard
                self.context.operations.start_read("Uložení ověřené kopie souboru",
                    lambda task: guard.export(record, destination),
                    lambda _value: self.notice.setText("Ověřená kopie artefaktu byla uložena."), popup=False)
                return
            try:
                self.guard.export(record, destination)
                self.notice.setText("Ověřená kopie artefaktu byla uložena.")
            except (ValueError, OSError) as error:
                self.notice.setText(str(error))

    def copy_text(self):
        QGuiApplication.clipboard().setText(self.text_preview.toPlainText())

    def save_text(self):
        destination, _ = QFileDialog.getSaveFileName(self, "Uložit textový náhled", "nahled.txt", "Text (*.txt)")
        if destination:
            try:
                target = Path(destination).resolve()
                source = self.bundle_root.resolve()
                if target == source or source in target.parents:
                    raise ValueError("Uložení nesmí přepsat zdrojový Run Bundle.")
                target.write_text(self.text_preview.toPlainText(), encoding="utf-8")
            except (OSError, ValueError) as error:
                self.notice.setText(str(error))

    def compare(self):
        rows = sorted({index.row() for index in self.table.selectionModel().selectedRows()})
        if len(rows) != 2 or not self.guard:
            self.notice.setText("Pro porovnání vyberte právě dva artefakty.")
            return
        records, guard = [self.artifacts[row] for row in rows], self.guard
        self.preview_generation += 1
        generation = self.preview_generation
        self.notice.setText("Porovnávám ověřené místní soubory…")

        def calculate(task):
            try:
                paths = [guard.resolve(record) for record in records]
                if any(path.stat().st_size > TEXT_DIFF_LIMIT for path in paths):
                    raise ValueError("Soubor přesahuje limit 5 MiB pro textové porovnání.")
                old = paths[0].read_text(encoding="utf-8")
                new = paths[1].read_text(encoding="utf-8")
                if "\x00" in old or "\x00" in new:
                    raise ValueError("Binární obsah nemá textový diff.")
                left, right = old.splitlines(True), new.splitlines(True)
                if len(left) + len(right) > 30000:
                    raise ValueError("Porovnání přesahuje 30 000 řádků.")
                value = "".join(difflib.unified_diff(left, right,
                    fromfile=str(records[0].get("display_name") or "Původní"),
                    tofile=str(records[1].get("display_name") or "Nový"))) or "Obsah je totožný."
                return "text", value
            except (ValueError, OSError, UnicodeError) as error:
                return "metadata", self._metadata_text(records[0]) + "\n\n" + self._metadata_text(records[1]) + "\n\n" + str(error)

        def receive(result):
            if generation != self.preview_generation:
                return
            kind, value = result
            editor = self.text_preview if kind == "text" else self.meta_preview
            editor.setPlainText(value)
            self.preview_stack.setCurrentWidget(editor)
            self.pdf_controls.hide()
            self._text_ready = kind == "text"
            self._availability()
            self.notice.setText("Porovnání je připravené; oba zdrojové soubory zůstaly beze změny.")

        if self.context:
            self.context.operations.start_read("Porovnání souborů", calculate, receive)
        else:
            receive(calculate(None))
