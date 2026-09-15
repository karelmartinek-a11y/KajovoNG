"""Bezpečné čtení, náhled a export skutečných ArtifactRecord."""

from __future__ import annotations

import hashlib
import difflib
import mimetypes
from pathlib import Path
import shutil

from PySide6.QtCore import QUrl, Qt
from PySide6.QtGui import QDesktopServices, QGuiApplication, QPixmap
from PySide6.QtWidgets import (
    QFileDialog, QLabel, QPlainTextEdit, QStackedWidget, QTableWidget, QTableWidgetItem, QWidget,
)

from kajovo.core.utils import safe_join_under_root

from .components import action, actions, caption, vertical


TEXT_PREVIEW_LIMIT = 1024 * 1024
TEXT_DIFF_LIMIT = 5 * 1024 * 1024


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
        shutil.copyfile(source, target)
        return target


class ArtifactBrowser(QWidget):
    def __init__(self, parent=None, *, context=None):
        super().__init__(parent)
        self.context = context
        self.preview_generation = 0
        self.bundle_root: Path | None = None
        self.artifacts: list[dict] = []
        self.guard: ArtifactGuard | None = None
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
            self.preview_stack.addWidget(self.pdf_preview)
        except ImportError:
            self.pdf_preview = None
        self.pdf_supported = self.pdf_document is not None and self.pdf_preview is not None
        self.preview_stack.setMinimumHeight(180)
        root.addWidget(self.preview_stack, 1)
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
        self.bundle_root = Path(root)
        self.guard = ArtifactGuard(root)
        self.artifacts = list(records)
        self.table.setRowCount(len(records))
        for row, record in enumerate(records):
            mime = str(record.get("mime_type") or mimetypes.guess_type(str(record.get("display_name") or ""))[0] or "application/octet-stream")
            values = [
                str(record.get("display_name") or "Není evidováno"),
                str(record.get("role") or "Není evidováno"),
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
        local = bool(record and record.get("available_local") is not False and record.get("path_in_bundle") and record.get("sha256"))
        for key in ("open", "save", "export"):
            self.buttons[key].setEnabled(local)
            self.buttons[key].setToolTip("Akce vyžaduje existující místní bytes a platný SHA-256." if not local else "")
        textual = local and str((record or {}).get("mime_type") or "").startswith("text/")
        self.buttons["copy"].setEnabled(textual)
        self.buttons["txt"].setEnabled(textual)
        self.buttons["metadata"].setEnabled(bool(record))
        self.buttons["compare"].setEnabled(len(self.table.selectionModel().selectedRows()) == 2)

    def preview(self):
        self._availability()
        record = self.selected()
        if not record:
            return
        self.preview_generation += 1
        generation = self.preview_generation
        self.notice.setText("Ověřuji integritu a připravuji místní náhled…")
        if self.context:
            self.context.operations.start(
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
        else:
            self.meta_preview.setPlainText(prepared["value"])
            self.preview_stack.setCurrentWidget(self.meta_preview)
        if kind == "error":
            self.notice.setText(prepared["error"])
        else:
            self.notice.setText("Náhled je read-only; kanonický artefakt se nemění.")

    @staticmethod
    def _metadata_text(record):
        return "\n".join(f"{key}: {value}" for key, value in record.items() if key != "metadata") + "\nmetadata: " + str(record.get("metadata") or {})

    def metadata(self):
        record = self.selected()
        if record:
            self.meta_preview.setPlainText(self._metadata_text(record))
            self.preview_stack.setCurrentWidget(self.meta_preview)

    def open(self):
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
                Path(destination).write_text(self.text_preview.toPlainText(), encoding="utf-8")
            except OSError as error:
                self.notice.setText(str(error))

    def compare(self):
        rows = sorted({index.row() for index in self.table.selectionModel().selectedRows()})
        if len(rows) != 2 or not self.guard:
            self.notice.setText("Pro porovnání vyberte právě dva artefakty.")
            return
        records = [self.artifacts[row] for row in rows]
        try:
            paths = [self.guard.resolve(record) for record in records]
            if any(path.stat().st_size > TEXT_DIFF_LIMIT for path in paths):
                value = (
                    "Velký nebo binární obsah se neporovnává synchronně.\n\n"
                    f"{records[0].get('display_name')}: {records[0].get('sha256')}\n"
                    f"{records[1].get('display_name')}: {records[1].get('sha256')}"
                )
            else:
                old = paths[0].read_text(encoding="utf-8")
                new = paths[1].read_text(encoding="utf-8")
                value = "".join(
                    difflib.unified_diff(
                        old.splitlines(True),
                        new.splitlines(True),
                        fromfile=str(records[0].get("display_name") or "Původní"),
                        tofile=str(records[1].get("display_name") or "Nový"),
                    )
                ) or "Obsah je totožný."
            self.text_preview.setPlainText(value)
            self.preview_stack.setCurrentWidget(self.text_preview)
        except (ValueError, OSError, UnicodeError) as error:
            self.meta_preview.setPlainText(
                self._metadata_text(records[0]) + "\n\n---\n\n" + self._metadata_text(records[1])
                + "\n\nTextový diff není dostupný: " + str(error)
            )
            self.preview_stack.setCurrentWidget(self.meta_preview)
