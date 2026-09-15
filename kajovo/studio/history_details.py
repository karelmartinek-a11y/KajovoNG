"""Typové a generické detaily jediného vybraného běhu, načítané až na vyžádání."""

from __future__ import annotations

import difflib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QDialog, QPlainTextEdit, QSplitter, QTabWidget, QTableWidget,
    QTableWidgetItem, QWidget,
)

from .components import action, actions, caption, panel, scroll, vertical
from .evidence import EvidenceView
from .history_artifacts import ArtifactBrowser, ArtifactGuard, TEXT_DIFF_LIMIT
from .history_models import RunView, format_duration


@dataclass(frozen=True)
class FileChange:
    path: str
    classification: str
    artifact: dict[str, Any] | None = None


def classify_modify_files(payload: dict[str, Any], state: dict[str, Any]) -> list[FileChange]:
    """Deterministicky klasifikuje pouze to, co dokládá manifest, write evidence a hash."""
    snapshot = state.get("preparation_snapshot") if isinstance(state.get("preparation_snapshot"), dict) else {}
    structure = snapshot.get("structure") if isinstance(snapshot.get("structure"), dict) else {}
    touched = {str(row.get("path")): row for row in structure.get("touched_files") or [] if isinstance(row, dict) and row.get("path")}
    preserved = {str(row.get("path")) for row in structure.get("preserved_files") or [] if isinstance(row, dict) and row.get("path")}
    skipped = set(state.get("completed_hashes") or {}) | set((state.get("ui_state") or {}).get("skip_paths") or [])
    failed = {str(row.get("path")) for row in state.get("missing_deliverables") or [] if isinstance(row, dict) and row.get("path")}
    artifacts = {}
    for record in payload.get("artifacts") or []:
        if record.get("role") != "modified_file":
            continue
        key = str(record.get("reconstruction_role") or (record.get("metadata") or {}).get("relative_path") or record.get("display_name") or "")
        if key:
            artifacts[key] = record
    rows: list[FileChange] = []
    for path, spec in touched.items():
        record = artifacts.get(path)
        if path in failed:
            kind = "chybové"
        elif path in skipped:
            kind = "přeskočené / reused"
        elif record:
            metadata = record.get("metadata") or {}
            kind = "nové" if spec.get("action") == "add" or metadata.get("before_sha256") in (None, "") else "změněné"
        elif state.get("dry_run"):
            kind = "návrh nového · dry-run" if spec.get("action") == "add" else "návrh změny · dry-run"
        else:
            kind = "chybové"
        rows.append(FileChange(path, kind, record))
    rows.extend(FileChange(path, "zachované") for path in sorted(preserved))
    for event in payload.get("events") or []:
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        if event.get("event_type") == "fs.change" and data.get("action") == "delete" and data.get("src"):
            rows.append(FileChange(str(data["src"]), "odstraněné"))
    return sorted(rows, key=lambda row: (row.classification, row.path.casefold()))


class TimelineEvidence(QWidget):
    def __init__(self, run: RunView, parent=None):
        super().__init__(parent)
        root = vertical(self, 0)
        table = QTableWidget(len(run.stages), 7)
        table.setHorizontalHeaderLabels(["#", "Fáze", "Název", "Stav", "Trvání", "Model", "Důkazy"])
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.setAccessibleName("Skutečné fáze běhu")
        for row, stage in enumerate(run.stages):
            values = [
                str(row + 1), stage.stage or "Není evidováno", stage.title, stage.status.label,
                format_duration(stage.duration) if stage.duration is not None else "Není evidováno",
                stage.model or "Není evidováno",
                f"{stage.response_count} odpovědí · {stage.artifact_count} artefaktů · {stage.error_count} chyb",
            ]
            for column, value in enumerate(values):
                table.setItem(row, column, QTableWidgetItem(value))
        table.resizeColumnsToContents()
        root.addWidget(table, 1)


class ModifyMap(QWidget):
    def __init__(self, context, bundle_root, payload, state, parent=None):
        super().__init__(parent)
        self.context = context
        self.bundle_root = Path(bundle_root)
        self.payload = payload
        self.changes = classify_modify_files(payload, state)
        root = vertical(self, 0)
        splitter = QSplitter(Qt.Horizontal)
        self.table = QTableWidget(len(self.changes), 2)
        self.table.setHorizontalHeaderLabels(["Soubor", "Klasifikace"])
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setAccessibleName("Mapa změn souborů")
        for row, change in enumerate(self.changes):
            self.table.setItem(row, 0, QTableWidgetItem(change.path))
            self.table.setItem(row, 1, QTableWidgetItem(change.classification))
        self.table.resizeColumnsToContents()
        splitter.addWidget(self.table)
        self.diff = QPlainTextEdit("Vyberte textový změněný soubor a zvolte Zobrazit diff.")
        self.diff.setReadOnly(True)
        self.diff.setAccessibleName("Side-by-side diff původní a nové verze")
        splitter.addWidget(self.diff)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([470, 780])
        root.addWidget(splitter, 1)
        root.addWidget(actions(action("history.modify.diff", "Zobrazit diff", self.load_diff)))

    def load_diff(self):
        row = self.table.currentRow()
        if not 0 <= row < len(self.changes):
            return
        change = self.changes[row]
        if not change.artifact:
            self.diff.setPlainText("Pro tuto položku není evidován nový místní textový artefakt.")
            return
        inputs = [record for record in self.payload.get("artifacts") or [] if record.get("role") == "in_project_file"
                  and str((record.get("metadata") or {}).get("relative_path") or record.get("reconstruction_role") or "") == change.path]
        if not inputs:
            self.diff.setPlainText("Původní verze není v Run Bundle evidována; falešný diff se nevytváří.")
            return
        before, after = inputs[-1], change.artifact

        def calculate(task):
            guard = ArtifactGuard(self.bundle_root)
            paths = [guard.resolve(before), guard.resolve(after)]
            if any(path.stat().st_size > TEXT_DIFF_LIMIT for path in paths):
                return "Soubor je větší než 5 MiB; zobrazuji pouze metadata a SHA-256.\n\n" + json.dumps(
                    {"původní": before.get("sha256"), "nový": after.get("sha256")}, ensure_ascii=False, indent=2)
            old = paths[0].read_text(encoding="utf-8")
            new = paths[1].read_text(encoding="utf-8")
            return "".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True), fromfile="Původní", tofile="Nový")) or "Obsah je totožný."

        self.context.operations.start("Výpočet diffu", calculate, self.diff.setPlainText, popup=False)


class CascadeStepsView(QWidget):
    """Chronologické kroky a lokální zvýraznění skutečných závislostí."""

    def __init__(self, run, state, payload, parent=None):
        super().__init__(parent)
        self.run, self.state, self.payload = run, state, payload
        definition = state.get("cascade_definition") if isinstance(state.get("cascade_definition"), dict) else {}
        definitions = {str(row.get("id") or ""): row for row in definition.get("steps") or [] if isinstance(row, dict)}
        root = vertical(self, 0)
        splitter = QSplitter(Qt.Horizontal)
        self.table = QTableWidget(len(run.stages), 8)
        self.table.setHorizontalHeaderLabels(["#", "Krok", "Model", "Stav", "Trvání", "Vstupy", "Výstupy", "Závislosti"])
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.dependencies = []
        for row, stage in enumerate(run.stages):
            spec = definitions.get(stage.stage, {})
            deps = sorted({str(item.get("source_step_id")) for item in spec.get("inputs") or []
                           if isinstance(item, dict) and item.get("source") == "output" and item.get("source_step_id")})
            self.dependencies.append(deps)
            values = [str(row + 1), stage.title, stage.model or "Není evidováno", stage.status.label,
                      format_duration(stage.duration) if stage.duration is not None else "Není evidováno",
                      str(len(spec.get("inputs") or [])), str(len(spec.get("outputs") or [])), ", ".join(deps) or "—"]
            for column, value in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(value))
        self.table.resizeColumnsToContents()
        self.inspector = QPlainTextEdit()
        self.inspector.setReadOnly(True)
        self.inspector.setAccessibleName("Inspektor vybraného kroku kaskády")
        self.table.currentCellChanged.connect(self.select_step)
        splitter.addWidget(self.table)
        splitter.addWidget(self.inspector)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([760, 480])
        root.addWidget(splitter, 1)
        if run.stages:
            self.table.setCurrentCell(0, 0)

    def select_step(self, row, _column, *_):
        if not 0 <= row < len(self.run.stages):
            return
        stage = self.run.stages[row]
        deps = set(self.dependencies[row])
        for index, candidate in enumerate(self.run.stages):
            color = QColor("#213F5A") if candidate.stage in deps else QColor("transparent")
            for column in range(self.table.columnCount()):
                item = self.table.item(index, column)
                if item:
                    item.setBackground(color)
        related = {}
        for key in ("requests", "responses", "validations", "artifacts", "events"):
            related[key] = [item for item in self.payload.get(key) or [] if item.get("step_id") == stage.step_id]
        self.inspector.setPlainText(json.dumps({"step": stage.technical, "dependencies": sorted(deps), **related},
                                               ensure_ascii=False, indent=2, default=str))


class RunDetailView(QWidget):
    def __init__(self, context, adapter, payload: dict[str, Any], state: dict[str, Any], run: RunView, parent=None):
        super().__init__(parent)
        self.context, self.adapter, self.payload, self.state, self.run = context, adapter, payload, state, run
        root = vertical(self)
        root.addWidget(caption(f"{run.mode} · {run.project}", "heading"))
        duration = "Není evidováno" if run.duration is None else format_duration(run.duration)
        root.addWidget(caption(
            f"{run.run_id} · {run.status.symbol} {run.status.label} · {run.transport} · {duration} · "
            f"{' / '.join(run.models) or 'model není evidován'}", "muted"))
        if run.legacy:
            root.addWidget(caption("Legacy běh je pouze ke čtení; chybějící kroky, checkpointy a provenance nejsou doplněny.", "error"))
        self.tabs = QTabWidget()
        self.tabs.setAccessibleName("Detail běhu")
        self.tabs.addTab(TimelineEvidence(run), "Časová osa")
        self._add_mode_overview()
        if run.mode == "MODIFY":
            self.tabs.addTab(ModifyMap(context, adapter.root, payload, state), "Mapa změn")
        if run.mode == "KASKADA":
            self.tabs.addTab(CascadeStepsView(run, state, payload), "Kroky a závislosti")
        artifacts = ArtifactBrowser(context=self.context)
        artifacts.set_artifacts(adapter.root, payload.get("artifacts") or [])
        self.tabs.addTab(artifacts, "Artefakty a náhled")
        for key, title in (("responses", "Odpovědi"), ("requests", "Requesty"), ("validations", "Validace"),
                           ("events", "Události"), ("lineage", "Lineage"), ("checkpoints", "Checkpointy")):
            view = EvidenceView(title)
            view.set_value(payload.get(key) or [])
            self.tabs.addTab(view, title)
        technical = EvidenceView("Technická evidence")
        technical.set_value({"run": payload.get("summary"), "state": state,
                             "integrity": payload.get("integrity") or {"status": "Není evidováno"}})
        self.tabs.addTab(technical, "Technické")
        root.addWidget(self.tabs, 1)

    def _add_mode_overview(self):
        mode = self.run.mode
        page = QWidget()
        layout = vertical(page)
        prompt = str((self.state.get("ui_state") or {}).get("prompt") or self.run.raw.get("input_summary") or "Není evidováno")
        box, body = panel("Původní zadání")
        body.addWidget(caption(prompt))
        layout.addWidget(box)
        if mode in {"GENERATE", "MODIFY"}:
            snapshot = self.state.get("preparation_snapshot") or {}
            box, body = panel("LIVE příprava a BATCH hranice" if self.run.transport == "BATCH" else "Přípravné fáze")
            evidenced_stage = next((stage.stage for stage in reversed(self.run.stages)
                                     if stage.stage not in {"A3", "B3", "BATCH"}), "")
            body.addWidget(caption(
                f"Kanonická/doložená fáze: {snapshot.get('canonical_stage') or evidenced_stage or 'Není evidováno'} · "
                f"BATCH ID: {', '.join(self.state.get('generate_batches') or ([self.state['batch_id']] if self.state.get('batch_id') else [])) or 'Není evidováno'}"
            ))
            if self.run.transport == "BATCH":
                body.addWidget(caption("Vzdálený stav a místní převzetí jsou oddělené. Remote-only výstupy nelze otevřít před importem.", "muted"))
            layout.addWidget(box)
            responses = [row for row in self.payload.get("responses") or [] if row.get("response_record_id")]
            input_tokens = sum(int(row.get("input_tokens") or 0) for row in responses)
            output_tokens = sum(int(row.get("output_tokens") or 0) for row in responses)
            reasoning_tokens = sum(int(row.get("reasoning_tokens") or 0) for row in responses)
            retries = sum(1 for row in self.payload.get("events") or []
                          if "attempt_failed" in str(row.get("event_type") or ""))
            cost = self.state.get("cost")
            box, body = panel("Request / response a spotřeba")
            body.addWidget(caption(
                f"Tokeny vstup/výstup/reasoning: {input_tokens if responses else 'Není evidováno'} / "
                f"{output_tokens if responses else 'Není evidováno'} / {reasoning_tokens if responses else 'Není evidováno'} · "
                f"retry: {retries} · cena: {cost if isinstance(cost, (int, float)) else 'Není evidováno'}"
            ))
            layout.addWidget(box)
        elif mode == "QA":
            responses = self.payload.get("responses") or []
            response = next((row for row in reversed(responses) if row.get("response_record_id")), {})
            answer = str(response.get("output_text") or "Není evidováno")
            box, body = panel("Odpověď")
            body.addWidget(caption(answer))
            layout.addWidget(box)
            box, body = panel("Technická metadata odpovědi")
            body.addWidget(caption(
                f"Response ID: {response.get('response_id') or 'Není evidováno'} · "
                f"status: {response.get('status') or 'Není evidováno'} · "
                f"tokeny vstup/výstup/reasoning: {response.get('input_tokens') if response.get('input_tokens') is not None else 'Není evidováno'} / "
                f"{response.get('output_tokens') if response.get('output_tokens') is not None else 'Není evidováno'} / "
                f"{response.get('reasoning_tokens') if response.get('reasoning_tokens') is not None else 'Není evidováno'} · "
                f"incomplete: {response.get('incomplete_reason') or '—'}"
            ))
            layout.addWidget(box)
        elif mode == "QFILE":
            passed = any(row.get("status") == "passed" and row.get("target_type") in {"file", "file_contract", "batch_import", "output"}
                         for row in self.payload.get("validations") or [])
            box, body = panel("Výsledný soubor")
            body.addWidget(caption("Souborový kontrakt platný" if passed else "Souborový kontrakt: Není evidováno"))
            body.addWidget(caption("Obsah ověřen" if self.state.get("human_verified") is True else "Obsah: Neověřeno", "muted"))
            layout.addWidget(box)
        elif mode == "KASKADA":
            box, body = panel("Kaskádová evidence")
            body.addWidget(caption(
                f"Kroků: {len(self.run.stages)} · selhaný krok: {self.state.get('failed_step_number') or 'Není evidováno'} · "
                f"závislosti jsou zvýrazněny pouze pro vybraný krok."
            ))
            layout.addWidget(box)
        elif mode == "COMIC":
            box, body = panel("Komiksová operace")
            body.addWidget(caption(f"Operation ID: {self.state.get('comic_operation_id') or 'Není evidováno'}"))
            layout.addWidget(box)
        else:
            box, body = panel("Generický detail")
            body.addWidget(caption("Tento typ běhu nemá speciální renderer; všechny kanonické záznamy zůstávají dostupné v záložkách."))
            layout.addWidget(box)
        layout.addStretch()
        self.tabs.addTab(scroll(page), "Přehled")


class RunDetailDialog(QDialog):
    def __init__(self, context, adapter, payload, state, run, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Run Studio · {run.run_id}")
        self.resize(1280, 820)
        self.setMinimumSize(640, 360)
        vertical(self, 0).addWidget(RunDetailView(context, adapter, payload, state, run))
