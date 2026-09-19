"""Typové a generické detaily jediného vybraného běhu, načítané až na vyžádání."""

from __future__ import annotations

import difflib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QTextCursor, QTextFormat
from PySide6.QtWidgets import (
    QDialog, QPlainTextEdit, QSplitter, QTabWidget, QTableWidget,
    QTableWidgetItem, QTextEdit, QWidget,
)

from .components import action, actions, caption, scroll, vertical
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
    skipped = set(state.get("_verified_skip_paths") or [])
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
            kind = "přeskočené · hash ověřen"
        elif record:
            metadata = record.get("metadata") or {}
            kind = "nové" if spec.get("action") == "add" else "změněné" if spec.get("action") == "modify" or metadata.get("before_sha256") else "klasifikace nezapsána"
        elif state.get("dry_run"):
            kind = "návrh nového · dry-run" if spec.get("action") == "add" else "návrh změny · dry-run"
        else:
            kind = "výsledek nezapsán"
        rows.append(FileChange(path, kind, record))
    rows.extend(FileChange(path, "zachované") for path in sorted(preserved))
    for event in payload.get("events") or []:
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        if event.get("event_type") == "fs.change" and data.get("action") == "delete" and data.get("src"):
            rows.append(FileChange(str(data["src"]), "odstraněné"))
    return sorted(rows, key=lambda row: (row.classification, row.path.casefold()))




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
        versions = QSplitter(Qt.Horizontal)
        self.before = QPlainTextEdit()
        self.before.setReadOnly(True)
        self.before.setAccessibleName("Původní verze souboru")
        self.diff.setAccessibleName("Nová verze souboru")
        for title, editor in (("Původní verze", self.before), ("Nová verze", self.diff)):
            box = QWidget()
            layout = vertical(box, 0)
            layout.addWidget(caption(title, "section"))
            layout.addWidget(editor, 1)
            versions.addWidget(box)
        self.before.verticalScrollBar().valueChanged.connect(self.diff.verticalScrollBar().setValue)
        self.diff.verticalScrollBar().valueChanged.connect(self.before.verticalScrollBar().setValue)
        splitter.addWidget(versions)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([470, 780])
        root.addWidget(splitter, 1)
        counts = {}
        for change in self.changes:
            counts[change.classification] = counts.get(change.classification, 0) + 1
        root.addWidget(caption(" · ".join(f"{count} {kind}" for kind, count in counts.items()), "muted"))
        failure = state.get("failure_detail") or {}
        if failure:
            root.addWidget(caption(f"{failure.get('stage') or failure.get('operation', '')}: {failure.get('message', '')}", "error"))
            root.addWidget(caption(str(failure.get("next_step") or "")))
        elif state.get("error"):
            root.addWidget(caption(str(state["error"]), "error"))
        self.generation = 0
        self.table.currentCellChanged.connect(lambda *_: self.load_diff())
        if self.changes:
            self.table.setCurrentCell(0, 0)

    def load_diff(self):
        self.generation += 1
        generation = self.generation
        self.before.clear()
        self.before.setExtraSelections([])
        self.diff.setExtraSelections([])
        row = self.table.currentRow()
        if not 0 <= row < len(self.changes):
            return
        change = self.changes[row]
        if not change.artifact:
            self.diff.setPlainText("Pro tuto položku není evidován nový místní textový artefakt.")
            return
        inputs = [record for record in self.payload.get("artifacts") or [] if record.get("role") == "in_project_file"
                  and str((record.get("metadata") or {}).get("relative_path") or record.get("reconstruction_role") or "") == change.path]
        is_new = change.classification == "nové"
        if not inputs and not is_new:
            self.diff.setPlainText("Původní verze není v Run Bundle evidována; falešný diff se nevytváří.")
            return
        before, after = inputs[-1] if inputs else None, change.artifact

        def calculate(task):
            guard = ArtifactGuard(self.bundle_root)
            paths = [guard.resolve(after)] + ([guard.resolve(before)] if before else [])
            if any(path.stat().st_size > TEXT_DIFF_LIMIT for path in paths):
                return "Soubor je větší než 5 MiB; zobrazuji pouze metadata a SHA-256.\n\n" + json.dumps(
                    {"původní": (before or {}).get("sha256"), "nový": after.get("sha256")}, ensure_ascii=False, indent=2)
            try:
                old = paths[1].read_text(encoding="utf-8") if before else ""
                new = paths[0].read_text(encoding="utf-8")
            except UnicodeError:
                return "Binární soubor · textové porovnání není dostupné.\n" + json.dumps({"původní": before, "nový": after}, ensure_ascii=False, indent=2)
            if "\x00" in old or "\x00" in new:
                return "Binární obsah · SHA-256 původní: " + str((before or {}).get("sha256")) + "\nNový: " + str(after.get("sha256"))
            left, right = old.splitlines(), new.splitlines()
            if len(left) + len(right) > 30000:
                return "Porovnání přesahuje 30 000 řádků. Použijte export obou souborů."
            return old, new, difflib.SequenceMatcher(None, left, right).get_opcodes()

        def safe_calculate(task):
            try:
                return calculate(task)
            except (ValueError, OSError) as error:
                return str(error)

        def show(value):
            if generation != self.generation:
                return
            if isinstance(value, str):
                self.diff.setPlainText(value)
                return
            old, new, opcodes = value
            self.before.setPlainText(old)
            self.diff.setPlainText(new)
            for editor, side, color in ((self.before, 1, "#56323D"), (self.diff, 3, "#214A42")):
                selections = []
                for opcode in opcodes:
                    if opcode[0] == "equal":
                        continue
                    for line in range(opcode[side], opcode[side + 1]):
                        selection = QTextEdit.ExtraSelection()
                        selection.cursor = QTextCursor(editor.document().findBlockByNumber(line))
                        selection.format.setBackground(QColor(color))
                        selection.format.setProperty(QTextFormat.FullWidthSelection, True)
                        selections.append(selection)
                editor.setExtraSelections(selections)

        self.context.operations.start_read("Porovnání verzí souboru", safe_calculate, show, popup=False)


class CascadeStepsView(QWidget):
    """Chronologické kroky a lokální zvýraznění skutečných závislostí."""

    def __init__(self, run, state, payload, parent=None):
        super().__init__(parent)
        from .history_cascade import CascadeTimeline
        from .history_overview import PhaseInspector
        self.run, self.state, self.payload = run, state, payload
        definition = state.get("cascade_definition") if isinstance(state.get("cascade_definition"), dict) else {}
        definitions = {str(row.get("id") or ""): row for row in definition.get("steps") or [] if isinstance(row, dict)}
        self.dependencies = []
        for stage in run.stages:
            spec = definitions.get(stage.stage, {})
            deps = sorted({str(item.get("source_step_id")) for item in spec.get("inputs") or []
                           if isinstance(item, dict) and item.get("source") == "output" and item.get("source_step_id")})
            self.dependencies.append(deps)
        root = vertical(self, 0)
        splitter = QSplitter(Qt.Horizontal)
        self.table = CascadeTimeline(run.stages)
        self.inspector = PhaseInspector(run, payload)
        self.dependency_label = caption("", "muted")
        self.inspector.body.insertWidget(2, self.dependency_label)
        self.table.step_selected.connect(self.select_step)
        splitter.addWidget(self.table)
        splitter.addWidget(self.inspector)
        splitter.setSizes([680, 340])
        root.addWidget(splitter, 1)
        if run.stages:
            failed = next((i for i, stage in enumerate(run.stages) if stage.status.key == "failed"), 0)
            self.table.setCurrentCell(failed, 0)

    def select_step(self, row, *_):
        if not 0 <= row < len(self.run.stages):
            return
        deps = self.dependencies[row]
        self.table.highlight_dependencies(deps)
        self.inspector.select(self.run.stages[row])
        titles = {stage.stage: stage.title for stage in self.run.stages}
        self.dependency_label.setText("Navazuje na: " + (", ".join(titles.get(value, value) for value in deps) or "bez uložených závislostí"))


class RunDetailView(QWidget):
    def __init__(self, context, adapter, payload: dict[str, Any], state: dict[str, Any], run: RunView, parent=None):
        super().__init__(parent)
        from .history_overview import PhaseInspector, TextCard, human_answer
        from .history_models import RunTableModel
        from .history_timeline import RunTrackView

        self.context, self.adapter, self.payload, self.state, self.run = context, adapter, payload, state, run
        self.phase_text = None
        root = vertical(self)
        root.addWidget(caption(f"{run.mode} · {run.project}", "heading"))
        duration = format_duration(run.duration) if run.duration is not None else "Celkový čas nebyl uložen"
        root.addWidget(caption(f"{run.run_id}  ·  {run.status.symbol} {run.status.label}  ·  {run.transport}  ·  {duration}", "muted"))
        failure = state.get("failure_detail") or {}
        if failure:
            root.addWidget(caption(f"{failure.get('stage') or failure.get('operation', '')}: {failure.get('message', '')}", "error"))
            root.addWidget(caption(str(failure.get("next_step") or "")))
        elif state.get("error"):
            root.addWidget(caption(str(state["error"]), "error"))
        if state.get("batch_imports"):
            failures = [detail for result in state["batch_imports"].values()
                        for detail in result.get("error_details", {}).values()]
            if failures:
                root.addWidget(caption(f"Dávkové chyby: {len(failures)}. Příčiny a dotčené soubory jsou v záložce Chyby.", "error"))
        if run.legacy:
            root.addWidget(caption("Starší záznam · pouze pro čtení. Podrobný průběh nebyl uložen.", "muted"))
        if state.get("dry_run") or run.status.key == "dry_run":
            root.addWidget(caption("Dry-run · návrh změn. Do projektové složky nebyly zapsány soubory.", "error"))
        self.timeline = RunTrackView()
        self.timeline_model = RunTableModel(self)
        self.timeline_model.set_runs([run])
        self.timeline.setModel(self.timeline_model)
        self.timeline.setColumnHidden(0, True)
        self.timeline.setColumnWidth(1, 200)
        self.timeline.setFixedHeight(168)
        self.timeline.horizontalHeader().setStretchLastSection(True)
        self.timeline.setAccessibleName("Časová osa vybraného běhu")
        root.addWidget(self.timeline)
        self.phase_label = caption("Vyberte fázi na časové ose.", "muted")
        root.addWidget(self.phase_label)

        self.tabs = QTabWidget()
        self.tabs.setAccessibleName("Výsledky a soubory běhu")
        overview = QWidget()
        from PySide6.QtWidgets import QSizePolicy
        overview.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        overview.setMinimumHeight(400 if run.mode in {"QFILE", "KASKADA"} else 340)
        body = vertical(overview, 0)
        columns = QSplitter(Qt.Horizontal)
        prompt = str((state.get("ui_state") or {}).get("prompt") or run.raw.get("input_summary") or "")
        left = QWidget()
        left_body = vertical(left, 0)
        left_body.addWidget(TextCard("Původní zadání", prompt, adapter.root), 2)
        if run.mode == "QFILE" and human_answer(payload):
            left_body.addWidget(TextCard("Doprovodný text", human_answer(payload), adapter.root), 1)
        inputs = [row for row in payload.get("artifacts") or []
                  if row.get("role") in {"user_input", "attached_file", "in_project_file", "input"}]
        if inputs:
            input_browser = ArtifactBrowser(context=context)
            input_browser.set_artifacts(adapter.root, inputs)
            input_browser.table.setMaximumHeight(110)
            left_body.addWidget(input_browser, 1)
        columns.addWidget(left)
        self.inspector = PhaseInspector(run, payload)
        if run.mode in {"QA", "QFILE"}:
            if run.mode == "QA":
                columns.addWidget(TextCard("Odpověď", human_answer(payload), adapter.root))
            else:
                output = ArtifactBrowser(context=context)
                records = [row for row in payload.get("artifacts") or []
                           if row.get("role") in {"generated_file", "modified_file", "batch_output", "output", "log_export"}]
                output.set_artifacts(adapter.root, records)
                output.table.setMaximumHeight(110)
                if len(records) == 1:
                    output.table.hide()
                    output.layout().insertWidget(0, caption(str(records[0].get("display_name") or "Výsledný soubor"), "section"))
                columns.addWidget(output)
                passed = any(row.get("status") == "passed" and row.get("target_type") in
                             {"file", "file_contract", "output", "batch_import"} for row in payload.get("validations") or [])
                body.addWidget(caption(("✓ Souborový kontrakt platný" if passed else "Souborový kontrakt: neověřeno")
                                       + "  ·  " + ("Obsah ověřen" if state.get("human_verified") is True else "Obsah nebyl člověkem ověřen"), "muted"))
            columns.setSizes([480, 760])
        elif run.mode == "KASKADA":
            cascade = CascadeStepsView(run, state, payload)
            columns.addWidget(cascade)
            columns.setSizes([350, 850])
        else:
            phases = QWidget()
            phases_body = vertical(phases, 0)
            phases_body.addWidget(caption("Výstupy přípravných fází" if run.mode in {"GENERATE", "MODIFY"}
                                         else "Výstup fáze", "section"))
            phase_text = TextCard("Výstup vybrané fáze", "", adapter.root)
            self.phase_text = phase_text
            phases_body.addWidget(phase_text, 1)
            columns.addWidget(phases)
            columns.addWidget(self.inspector)
            columns.setSizes([380, 400, 390])
        body.addWidget(columns, 1)
        if run.transport == "BATCH":
            rows = []
            imports = state.get("batch_imports") or {}
            for identifier, remote in (state.get("batch_records") or {}).items():
                local = imports.get(identifier) or {}
                counts = remote.get("request_counts") or {}
                from .history_state import present_state
                rows.append(f"{identifier} · Vzdáleně: {present_state(remote.get('status')).label}"
                            f" · Převzetí: {present_state(local.get('import_status')).label if local else 'Soubory nepřevzaty'}"
                            + (f" · Hotovo {counts.get('completed', 0)} / {counts.get('total', 0)}" if counts else ""))
            body.insertWidget(0, caption("\n".join(rows) or "Dávka je evidována; vzdálený stav nebyl uložen.", "muted"))
        self.tabs.addTab(scroll(overview), "Přehled")
        if run.mode == "MODIFY":
            self.tabs.addTab(ModifyMap(context, adapter.root, payload, state), "Mapa změn a porovnání")
            self.tabs.setCurrentIndex(1)
        artifacts = ArtifactBrowser(context=context)
        artifacts.set_artifacts(adapter.root, payload.get("artifacts") or [])
        self.tabs.addTab(artifacts, "Soubory")
        failed_validations = [v for v in (payload.get("validations") or []) if v.get("status") == "failed"]
        batch_errors = {key: value.get("error_details") or value.get("file_errors") or value.get("errors")
                        for key, value in (state.get("batch_imports") or {}).items()}
        recovery_events = [event for event in (payload.get("events") or [])
                           if event.get("type") in {"response.poll_error", "validation.recovered", "response.poll_recovered"}]
        if state.get("failure_detail") or state.get("error") or failed_validations or any(batch_errors.values()) or recovery_events:
            errors_view = EvidenceView("Chyby, opravy a jejich důkazy")
            errors_view.set_value({"poslední_chyba": state.get("failure_detail") or state.get("error"),
                                   "validace": payload.get("validations") or [], "dávky": batch_errors,
                                   "zotavení": recovery_events})
            self.tabs.addTab(errors_view, "Chyby")
        root.addWidget(self.tabs, 1)
        if run.mode in {"QA", "QFILE"}:
            from .history_data import unique_responses
            responses = unique_responses(payload.get("responses") or [])
            tokens = []
            for key, label in (("input_tokens", "Vstup"), ("output_tokens", "Výstup"), ("reasoning_tokens", "Uvažování")):
                values = [row[key] for row in responses if isinstance(row.get(key), int)]
                if values:
                    tokens.append(f"{label}: {sum(values)} tokenů")
            ids = [row.get("response_id") for row in responses if row.get("response_id")]
            if tokens or ids:
                root.addWidget(caption(" · ".join(tokens) + ("\nOdpověď: " + ", ".join(ids) if ids else ""), "muted"))
        self.selected_stage = run.stages[-1] if run.stages else None
        self.timeline.stage_selected.connect(self.select_stage)
        if self.selected_stage:
            self.timeline.select_stage(run.run_id, self.selected_stage.step_id)
            self.select_stage(run, self.selected_stage)
        root.addWidget(actions(action("history.detail.evidence", "Technická evidence", self.open_evidence)))
        if run.mode in {"GENERATE", "MODIFY", "QFILE"}:
            artifacts.layout().insertWidget(0, actions(action("history.detail.current_outputs", "Ověřit současné soubory OUT", self.check_outputs)))
            self.output_status = QPlainTextEdit("Historie dokládá tehdejší zápis. Aktuální soubory zatím nebyly zkontrolovány.")
            self.output_status.setReadOnly(True)
            self.output_status.setMaximumHeight(110)
            artifacts.layout().addWidget(self.output_status)
        if run.mode in {"QA", "QFILE"}:
            self.inspector.setParent(self)
            self.inspector.hide()

    def check_outputs(self):
        from kajovo.core.runlog import inspect_current_outputs
        root = self.adapter.root
        self.output_status.setPlainText("Kontroluji existenci a otisky souborů…")

        def show(rows):
            labels = {"matching": "odpovídá zápisu", "changed": "změněn", "missing": "chybí", "unknown": "nelze ověřit"}
            self.output_status.setPlainText("\n".join(
                f"{r['path']}: {labels[r['current_status']]}" for r in rows
            ) or "Chybí evidence zapsaných souborů; nelze potvrdit dodávku.")

        self.context.operations.start_read("Kontrola současných výstupů", lambda task: inspect_current_outputs(root), show, popup=False)

    def select_stage(self, _run, stage):
        self.selected_stage = stage
        self.inspector.select(stage)
        if self.phase_text:
            from .history_overview import human_answer
            text = human_answer(self.payload, stage.step_id)
            self.phase_text.text = text
            self.phase_text.editor.setPlainText(text[:1024 * 1024] or (
                "Výstup je ve vzdálené dávce. Nejprve převezměte soubory."
                if stage.status.key == "ready_to_import" else "Text této fáze nebyl uložen."))
            self.phase_text.copy_button.setEnabled(bool(text))
            self.phase_text.save_button.setEnabled(bool(text))
        self.phase_label.setText(f"Vybraná fáze: {stage.title} · {stage.stage} · {stage.status.label}"
                                + (f" · {stage.model}" if stage.model else ""))

    def open_evidence(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Technická evidence běhu")
        dialog.resize(1000, 700)
        tabs = QTabWidget()
        for key, title in (("steps", "Záznamy kroků"), ("responses", "Odpovědi"),
                           ("requests", "Požadavky"), ("validations", "Validace"),
                           ("events", "Události"), ("lineage", "Návaznosti"),
                           ("checkpoints", "Body obnovy")):
            view = EvidenceView(title)
            view.set_value(self.payload.get(key) or [])
            tabs.addTab(view, title)
        view = EvidenceView("Stav běhu")
        view.set_value({"run": self.payload.get("summary"), "state": self.state})
        tabs.addTab(view, "Stav běhu")
        vertical(dialog).addWidget(tabs)
        dialog.exec()


class RunDetailDialog(QDialog):
    def __init__(self, context, adapter, payload, state, run, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Run Studio · {run.run_id}")
        self.resize(1360, 960)
        self.setMinimumSize(640, 360)
        from .history_data import checked_checkpoints
        from .history_launcher import HistoryBranchLauncher
        from .history_policy import ActionAvailabilityPolicy, apply_decision

        self.context, self.adapter, self.state = context, adapter, state
        self.page = parent if hasattr(parent, "clone_source") else getattr(parent, "history", None)
        self.launcher = HistoryBranchLauncher(context, self.page.refresh if self.page else None)
        self.checkpoints = payload.get("checkpoints") or []
        root = vertical(self, 0)
        self.view = RunDetailView(context, adapter, payload, state, run)
        root.addWidget(self.view, 1)
        if self.page:
            lineage = payload.get("lineage") or []
            children = self.page.reverse_lineage.get(run.run_id, [])
            if lineage or children:
                branches = QWidget()
                branch_body = vertical(branches)
                branch_body.addWidget(caption("Návaznosti běhu", "section"))
                for record in lineage + children:
                    related = record.get("source_run_id") if record in lineage else record.get("target_run_id")
                    if related and related != run.run_id:
                        relation = {"repair": "Opravná větev", "rerun": "Opakované spuštění", "continue": "Pokračování", "clone": "Nové zadání"}.get(record.get("relation_type"), "Navazující běh")
                        branch_body.addWidget(caption(f"{relation} · {record.get('source_checkpoint_id') or 'Bod větvení nebyl uložen'}", "muted"))
                        branch_body.addWidget(action("history.related.open", str(related),
                            lambda _checked=False, identifier=related: self.page.open_related(identifier)))
                branch_body.addStretch()
                self.view.tabs.addTab(branches, "Větve")
        self.branch_buttons = {
            "rerun": action("history.detail.rerun", "Znovu spustit", lambda: self.branch("rerun")),
            "repair": action("history.detail.repair", "Opravit", lambda: self.branch("repair"), "primary"),
            "continue": action("history.detail.continue", "Pokračovat", lambda: self.branch("continue")),
            "edit_branch": action("history.detail.edit", "Upravit zadání nové větve", lambda: self.branch("rerun", True)),
            "clone": action("history.detail.clone", "Klonovat jako nové zadání", self.clone),
            "complete_batch": action("history.detail.batch", "Převzít soubory", self.complete_batch, "primary"),
            "publish_staged": action(
                "history.detail.publish",
                "Převzít neověřené",
                self.publish_staged,
                "primary",
            ),
        }
        root.addWidget(actions(*self.branch_buttons.values()))

        def update(checkpoints):
            self.checkpoints = checkpoints
            decisions = ActionAvailabilityPolicy().evaluate(payload.get("summary") or {}, state, checkpoints,
                                                            legacy=adapter.legacy)
            for name, button in self.branch_buttons.items():
                apply_decision(button, decisions[name])
                if name in {"clone", "complete_batch", "publish_staged"} and not self.page:
                    button.setEnabled(False)
                    button.setToolTip("Tato akce vyžaduje otevření detailu z Historie aplikace.")

        update([])
        if adapter.bundle and payload.get("checkpoints"):
            context.operations.start_read("Ověření bodů obnovy",
                lambda task: checked_checkpoints(adapter, payload.get("checkpoints") or [], payload.get("artifacts") or []),
                update, popup=False)

    def branch(self, relation, edit_input=False):
        from .history_composer import BranchComposer
        stage = self.view.selected_stage
        composer = BranchComposer(self.launcher, self.adapter, self.checkpoints, relation,
                                  stage.stage if stage else "", self, edit_input=edit_input)
        if composer.exec() == QDialog.Accepted and composer.preview:
            try:
                self.launcher.launch_async(self.adapter, composer.preview, composer.repair_instruction(),
                                           receive=lambda record: self.page.focus_new_branch(record.identifier) if self.page else None)
                self.accept()
            except (ValueError, OSError, KeyError) as error:
                from PySide6.QtWidgets import QMessageBox
                QMessageBox.warning(self, "Větev nebyla spuštěna", str(error))

    def clone(self):
        if self.page:
            self.page.clone_source(self.adapter, self.state)
            self.accept()

    def complete_batch(self):
        if self.page:
            self.page.complete_batch_source(self.adapter, self.state)

    def publish_staged(self):
        if self.page:
            self.page.adapter = self.adapter
            self.page._state = self.state
            self.page.publish_staged()
