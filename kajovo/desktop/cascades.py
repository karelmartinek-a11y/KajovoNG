"""Deterministický editor kaskád: kroky vlevo, detail a validace vpravo."""

from __future__ import annotations

import copy
import json
import re
import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QHeaderView,
    QListWidget,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)

from ..core.cascade_contract import (
    CascadeValidationError,
    validate_cascade_definition,
)
from ..core.cascade_types import (
    CASCADE_FILE_TYPES,
    CascadeDecisionOption,
    CascadeDefinition,
    CascadeInput,
    CascadeOutput,
    CascadeOutputRef,
    CascadeStep,
)
from ..core.request_rules import uses_reasoning_defaults, validate_response_payload
from ..core.utils import atomic_write_text, safe_join_under_root
from .design import button, column, combo, editor, form, label, number, row, scroll, text
from .dialogs import (
    dialog_input_text,
    dialog_open_file,
    dialog_select_dir,
    msg_info,
    msg_warning,
)


INPUT_SOURCE_LABELS = {
    "text": "Nový text",
    "local_file": "Lokální soubor",
    "file_id": "OpenAI file_id",
    "output": "Výstup předchozího kroku",
}
INPUT_SOURCE_BY_LABEL = {value: key for key, value in INPUT_SOURCE_LABELS.items()}

OUTPUT_KIND_LABELS = {
    "text": "Text",
    "json": "JSON",
    "file": "Soubor",
    "decision": "Rozhodnutí",
}
OUTPUT_KIND_BY_LABEL = {value: key for key, value in OUTPUT_KIND_LABELS.items()}

FILE_MODE_LABELS = {
    "create": "Vytvořit nový soubor",
    "modify": "Upravit existující vstupní soubor",
}
FILE_MODE_BY_LABEL = {value: key for key, value in FILE_MODE_LABELS.items()}


class CascadePanel(QWidget):
    def __init__(self, settings, models, parent=None):
        super().__init__(parent)
        self.s = settings
        self.models = models
        self.cascade_dir = str(Path(settings.log_dir).parent / "cascades")
        Path(self.cascade_dir).mkdir(parents=True, exist_ok=True)
        Path(self.cascade_dir, ".runtime").mkdir(parents=True, exist_ok=True)

        self.definition = CascadeDefinition("Nová kaskáda")
        self.current_step_index = -1
        self._selected_input_index = -1
        self._selected_output_index = -1
        self._selected_decision_index = -1
        self._draft_inputs: list[CascadeInput] = []
        self._draft_outputs: list[CascadeOutput] = []
        self.runtime_state: dict = {}
        self._last_runtime_marker = ""

        root = column(self, 12)

        self.saved = combo()
        self.saved.setPlaceholderText("Žádná uložená kaskáda")
        self.ed_name = text("Nová kaskáda")
        root.addWidget(
            row(
                self.saved,
                button("Načíst", self.load_selected),
                button("Obnovit", self.refresh_saved_list),
            )
        )
        root.addWidget(
            row(
                self.ed_name,
                button("Uložit", self.save_current, "Primary"),
                button("Uložit jako", self.save_as),
            )
        )
        self.ed_default_out_dir = text(placeholder="Výchozí OUT kaskády")
        root.addWidget(
            row(self.ed_default_out_dir, button("Vybrat OUT", self._browse_default_out_dir))
        )

        self.splitter = QSplitter(Qt.Horizontal)
        root.addWidget(self.splitter, 1)

        left = QWidget()
        ll = column(left, 0)
        ll.addWidget(label("Kroky kaskády", "Heading"))
        self.tbl_steps = QTableWidget(0, 5)
        self.tbl_steps.setHorizontalHeaderLabels(["#", "Krok", "Kontext", "Výstupy", "Stav"])
        self.tbl_steps.setEditTriggers(QTableWidget.NoEditTriggers)
        self.tbl_steps.setSelectionBehavior(QTableWidget.SelectRows)
        self.tbl_steps.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tbl_steps.verticalHeader().hide()
        self.tbl_steps.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.tbl_steps.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.tbl_steps.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.tbl_steps.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.tbl_steps.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.tbl_steps.currentCellChanged.connect(
            lambda current_row, _current_col, _old_row, _old_col: self.on_step_selected(
                current_row
            )
        )
        ll.addWidget(self.tbl_steps, 1)
        ll.addWidget(
            row(
                button("Přidat krok", self.add_step, "Primary"),
                button("Duplikovat", self.duplicate_selected_step),
            )
        )
        ll.addWidget(
            row(
                button("Nahoru", self.move_selected_step_up),
                button("Dolů", self.move_selected_step_down),
                button("Odstranit", self.delete_selected_step, "Danger"),
            )
        )
        self.splitter.addWidget(left)

        right = QWidget()
        rl = column(right, 0)
        self.tabs = QTabWidget()
        rl.addWidget(self.tabs, 1)
        self.btn_save_step = button("Uložit změny kroku", self.save_current_step, "Primary")
        rl.addWidget(self.btn_save_step)
        self.splitter.addWidget(right)
        self.splitter.setStretchFactor(0, 2)
        self.splitter.setStretchFactor(1, 5)

        self._build_step_tab()
        self._build_inputs_tab()
        self._build_outputs_tab()
        self._build_final_tab()
        self._build_status_tab()

        self.refresh_saved_list()
        self.add_step()

        self.runtime_timer = QTimer(self)
        self.runtime_timer.timeout.connect(self._poll_runtime)
        self.runtime_timer.start(1000)

    def _build_step_tab(self):
        widget = QWidget()
        layout = column(widget, 16)
        fields = form(layout)
        self.ed_step_title = text()
        self.cb_step_model = combo(self.models())
        self.chk_step_temp = QCheckBox("Poslat teplotu")
        self.sp_step_temp = number(0.2, maximum=2, decimal=True)
        self.cb_context = combo()
        self.btn_new_context = button("Nový kontext", self._new_context)
        fields.addRow("Název kroku", self.ed_step_title)
        fields.addRow("Model", self.cb_step_model)
        fields.addRow("Teplota", row(self.chk_step_temp, self.sp_step_temp))
        fields.addRow("Kontext", row(self.cb_context, self.btn_new_context))
        layout.addWidget(
            label(
                "Stejný kontext automaticky navazuje přes previous_response_id na poslední odpověď "
                "tohoto kontextu. Nový kontext začíná čistě.",
                "Hint",
            )
        )
        self.txt_input_text = editor(height=220)
        layout.addWidget(label("Zadání kroku"))
        layout.addWidget(self.txt_input_text, 1)
        layout.addWidget(
            label(
                "Každý krok se při chybě nebo neplatném výstupu automaticky pokusí dokončit nejvýše třikrát.",
                "Hint",
            )
        )
        self.cb_step_model.currentTextChanged.connect(self._update_temperature_enabled)
        self.chk_step_temp.toggled.connect(self._update_temperature_enabled)
        self.tabs.addTab(scroll(widget), "Krok")

    def _build_inputs_tab(self):
        widget = QWidget()
        layout = column(widget, 16)
        layout.addWidget(
            label(
                "Vstup může být nový údaj nebo konkrétní deterministický výstup některého předchozího kroku.",
                "Hint",
            )
        )
        self.tbl_inputs = QTableWidget(0, 3)
        self.tbl_inputs.setHorizontalHeaderLabels(["Název", "Zdroj", "Hodnota"])
        self.tbl_inputs.setEditTriggers(QTableWidget.NoEditTriggers)
        self.tbl_inputs.setSelectionBehavior(QTableWidget.SelectRows)
        self.tbl_inputs.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tbl_inputs.verticalHeader().hide()
        self.tbl_inputs.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.tbl_inputs.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.tbl_inputs.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.tbl_inputs.currentCellChanged.connect(
            lambda row_index, _c, _or, _oc: self._select_input(row_index)
        )
        layout.addWidget(self.tbl_inputs, 1)

        fields = form(layout)
        self.ed_input_name = text()
        self.cb_input_source = combo(INPUT_SOURCE_LABELS.values())
        self.ed_input_value = editor(height=90)
        self.cb_input_output = combo()
        fields.addRow("Název vstupu", self.ed_input_name)
        fields.addRow("Zdroj", self.cb_input_source)
        fields.addRow("Nová hodnota / cesta / file_id", self.ed_input_value)
        fields.addRow("Výstup předchozího kroku", self.cb_input_output)
        layout.addWidget(
            row(
                button("Vybrat lokální soubor", self._browse_input_file),
                button("Přidat vstup", self._add_input, "Primary"),
                button("Upravit vybraný", self._update_input),
                button("Odstranit", self._delete_input, "Danger"),
            )
        )
        self.cb_input_source.currentTextChanged.connect(self._refresh_input_controls)
        self.tabs.addTab(scroll(widget), "Vstupy")

    def _build_outputs_tab(self):
        widget = QWidget()
        layout = column(widget, 16)
        layout.addWidget(
            label(
                "Výstupy jsou předem pojmenované a typ se vybírá výhradně z nabídky. "
                "Podle typu program vytvoří a vynutí odpovídající výstupní kontrakt.",
                "Hint",
            )
        )
        self.tbl_outputs = QTableWidget(0, 4)
        self.tbl_outputs.setHorizontalHeaderLabels(["Název", "Typ", "Soubor / režim", "Rozhodnutí"])
        self.tbl_outputs.setEditTriggers(QTableWidget.NoEditTriggers)
        self.tbl_outputs.setSelectionBehavior(QTableWidget.SelectRows)
        self.tbl_outputs.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tbl_outputs.verticalHeader().hide()
        self.tbl_outputs.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.tbl_outputs.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.tbl_outputs.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.tbl_outputs.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.tbl_outputs.currentCellChanged.connect(
            lambda row_index, _c, _or, _oc: self._select_output(row_index)
        )
        layout.addWidget(self.tbl_outputs, 1)

        fields = form(layout)
        self.ed_output_name = text("Výstup")
        self.cb_output_kind = combo(OUTPUT_KIND_LABELS.values())
        self.cb_file_type = combo(CASCADE_FILE_TYPES)
        self.ed_file_name = text(placeholder="např. vysledek.xlsx")
        self.cb_file_mode = combo(FILE_MODE_LABELS.values())
        self.cb_modify_input = combo()
        fields.addRow("Název výstupu", self.ed_output_name)
        fields.addRow("Typ výstupu", self.cb_output_kind)
        fields.addRow("Typ souboru", self.cb_file_type)
        fields.addRow("Název souboru", self.ed_file_name)
        fields.addRow("Režim souboru", self.cb_file_mode)
        fields.addRow("Upravovaný vstupní soubor", self.cb_modify_input)
        layout.addWidget(
            row(
                button("Přidat výstup", self._add_output, "Primary"),
                button("Upravit vybraný", self._update_output),
                button("Odstranit", self._delete_output, "Danger"),
            )
        )

        layout.addWidget(label("Možné odpovědi rozhodnutí a cílové budoucí kroky"))
        self.tbl_decisions = QTableWidget(0, 2)
        self.tbl_decisions.setHorizontalHeaderLabels(["Odpověď", "Přejít na krok"])
        self.tbl_decisions.setEditTriggers(QTableWidget.NoEditTriggers)
        self.tbl_decisions.setSelectionBehavior(QTableWidget.SelectRows)
        self.tbl_decisions.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tbl_decisions.verticalHeader().hide()
        self.tbl_decisions.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tbl_decisions.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.tbl_decisions.currentCellChanged.connect(
            lambda row_index, _c, _or, _oc: self._select_decision(row_index)
        )
        layout.addWidget(self.tbl_decisions)

        self.ed_decision_value = text(placeholder="např. Ano / Ne / Průměrný")
        self.sp_decision_target = number(1, minimum=1, maximum=999)
        layout.addWidget(
            row(
                self.ed_decision_value,
                label("→ krok"),
                self.sp_decision_target,
                button("Přidat odpověď", self._add_decision),
                button("Upravit", self._update_decision),
                button("Odstranit", self._delete_decision, "Danger"),
            )
        )

        self.cb_output_kind.currentTextChanged.connect(self._refresh_output_controls)
        self.cb_file_mode.currentTextChanged.connect(self._refresh_output_controls)
        self.cb_file_type.currentTextChanged.connect(self._normalize_file_extension)
        self.tabs.addTab(scroll(widget), "Výstupy")

    def _build_final_tab(self):
        widget = QWidget()
        layout = column(widget, 16)
        layout.addWidget(
            label(
                "Vyberte, které konkrétní deterministické výstupy mají být výsledkem celé kaskády.",
                "Hint",
            )
        )
        self.cb_final_available = combo()
        self.lst_final = QListWidget()
        layout.addWidget(row(self.cb_final_available, button("Přidat", self._add_final_output)))
        layout.addWidget(self.lst_final, 1)
        layout.addWidget(button("Odebrat vybraný", self._remove_final_output, "Danger"))
        self.tabs.addTab(widget, "Finální výstup")

    def _build_status_tab(self):
        widget = QWidget()
        layout = column(widget, 16)
        self.lbl_runtime = label("Kaskáda zatím nemá zaznamenanou chybu.", "Hint")
        self.txt_runtime_technical = editor(readonly=True, height=180)
        layout.addWidget(self.lbl_runtime)
        layout.addWidget(label("Technický detail"))
        layout.addWidget(self.txt_runtime_technical)
        layout.addWidget(
            row(
                button("Příští běh od tohoto kroku", self._run_from_selected, "Primary"),
                button("Příští běh od začátku", self._run_from_start),
            )
        )
        layout.addWidget(
            label(
                "Při spuštění od prostředního kroku se použijí jen mezivýstupy z posledního běhu, "
                "jejichž předchozí kroky se od té doby nezměnily. Všechny následující staré výstupy se zneplatní.",
                "Hint",
            )
        )
        self.tabs.addTab(widget, "Stav a opakování")

    def refresh_models(self):
        selected = self.cb_step_model.currentText()
        self.cb_step_model.clear()
        self.cb_step_model.addItems(self.models())
        self._select_model(selected)

    def _select_model(self, model):
        if model and self.cb_step_model.findText(model) < 0:
            self.cb_step_model.addItem(model)
            item = self.cb_step_model.model().item(self.cb_step_model.count() - 1)
            item.setEnabled(False)
            item.setToolTip("Uložený model není dostupný. Zvolte jiný.")
        self.cb_step_model.setCurrentText(model)

    def _update_temperature_enabled(self):
        try:
            payload = {
                "model": self.cb_step_model.currentText(),
                "temperature": self.sp_step_temp.value(),
            }
            validate_response_payload(payload)
            allowed = not uses_reasoning_defaults(payload["model"])
        except ValueError:
            allowed = False
        self.chk_step_temp.setEnabled(allowed)
        self.sp_step_temp.setEnabled(allowed and self.chk_step_temp.isChecked())
        if not allowed:
            self.chk_step_temp.setChecked(False)

    def available_cascades(self):
        return sorted(path.name for path in Path(self.cascade_dir).glob("*.json"))

    def refresh_saved_list(self):
        current = self.saved.currentText()
        self.saved.blockSignals(True)
        self.saved.clear()
        self.saved.addItems(self.available_cascades())
        self.saved.setCurrentText(current)
        self.saved.blockSignals(False)

    def get_selected_cascade_path(self):
        return (
            str(Path(self.cascade_dir) / self.saved.currentText())
            if self.saved.currentText()
            else ""
        )

    def get_selected_cascade_name(self):
        return self.saved.currentText()

    def get_definition(self):
        if 0 <= self.current_step_index < len(self.definition.steps):
            self.save_current_step(show_message=False)
        return copy.deepcopy(self.definition)

    def _runtime_path(self):
        name = self.definition.name or self.ed_name.text().strip() or "cascade"
        safe = re.sub(r"[^\w .-]", "_", name).strip(" .") or "cascade"
        return Path(self.cascade_dir) / ".runtime" / f"{safe}.runtime.json"

    def _poll_runtime(self):
        path = self._runtime_path()
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(state, dict):
                return
        except (OSError, ValueError, TypeError):
            return
        marker = f"{state.get('run_id')}|{state.get('status')}|{state.get('failed_step_id')}"
        changed = marker != self._last_runtime_marker
        self._last_runtime_marker = marker
        self.runtime_state = state
        self._refresh_runtime_view()
        self._refresh_step_list()
        if changed and state.get("status") == "failed":
            window = self.window()
            if hasattr(window, "select_page"):
                try:
                    window.select_page("cascade")
                except Exception:
                    pass
            failed = str(state.get("failed_step_id") or "")
            index = self.definition.step_index(failed) if failed else -1
            if index >= 0:
                self.tbl_steps.setCurrentCell(index, 0)
                self.tabs.setCurrentIndex(4)

    def _refresh_runtime_view(self):
        state = self.runtime_state or {}
        status = str(state.get("status") or "")
        if status == "failed":
            number_value = state.get("failed_step_number") or "?"
            human = str(state.get("human_error") or "Kaskáda se zastavila chybou.")
            self.lbl_runtime.setText(f"Krok {number_value}: {human}")
        elif status == "completed":
            self.lbl_runtime.setText("Poslední běh kaskády byl dokončen.")
        elif status == "running":
            self.lbl_runtime.setText("Kaskáda právě běží.")
        elif status == "cancelled":
            self.lbl_runtime.setText("Poslední běh byl zastaven.")
        else:
            self.lbl_runtime.setText("Kaskáda zatím nemá zaznamenanou chybu.")
        self.txt_runtime_technical.setPlainText(str(state.get("technical_error") or ""))

    def _step_status(self, step: CascadeStep, index: int) -> str:
        if self.runtime_state.get("failed_step_id") == step.id:
            return "CHYBA"
        if self.definition.run_from_step_id == step.id:
            return "START"
        if not step.title.strip() or not step.model.strip() or not step.outputs:
            return "DOPLNIT"
        for output in step.outputs:
            if output.kind == "decision":
                if len(output.decision_options) < 2:
                    return "DOPLNIT"
                if any(
                    option.target_step_number > len(self.definition.steps)
                    and not option.target_step_id
                    for option in output.decision_options
                ):
                    return "ČEKÁ"
        return "OK"

    def _refresh_step_list(self):
        selected = self.current_step_index
        self.tbl_steps.blockSignals(True)
        self.tbl_steps.setRowCount(len(self.definition.steps))
        for index, step in enumerate(self.definition.steps):
            outputs = ", ".join(output.name for output in step.outputs) or "bez výstupu"
            values = [
                str(index + 1),
                step.title or "Krok",
                step.context_id or "—",
                outputs,
                self._step_status(step, index),
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                if col in (0, 4):
                    item.setTextAlignment(Qt.AlignCenter)
                self.tbl_steps.setItem(index, col, item)
        if 0 <= selected < self.tbl_steps.rowCount():
            self.tbl_steps.setCurrentCell(selected, 0)
        self.tbl_steps.blockSignals(False)
        self._refresh_final_controls()

    def _refresh_contexts(self, selected=""):
        contexts = []
        for index, step in enumerate(self.definition.steps):
            if index >= self.current_step_index and self.current_step_index >= 0:
                break
            if step.context_id and step.context_id not in contexts:
                contexts.append(step.context_id)
        if selected and selected not in contexts:
            contexts.append(selected)
        if not contexts:
            contexts = ["Kontext 1"]
        self.cb_context.blockSignals(True)
        self.cb_context.clear()
        self.cb_context.addItems(contexts)
        self.cb_context.setCurrentText(selected or contexts[0])
        self.cb_context.blockSignals(False)

    def _new_context(self):
        existing = {
            step.context_id for step in self.definition.steps if step.context_id
        }
        default = f"Kontext {len(existing) + 1}"
        value, ok = dialog_input_text(self, "Nový kontext", "Název kontextu:", default)
        value = value.strip()
        if ok and value:
            if self.cb_context.findText(value) < 0:
                self.cb_context.addItem(value)
            self.cb_context.setCurrentText(value)

    def _previous_outputs(self):
        rows = []
        for step_index, step in enumerate(self.definition.steps[: max(0, self.current_step_index)]):
            for output in step.outputs:
                rows.append(
                    (
                        f"{step_index + 1}. {step.title or 'Krok'} · {output.name} ({OUTPUT_KIND_LABELS.get(output.kind, output.kind)})",
                        step.id,
                        output.id,
                        output.kind,
                    )
                )
        return rows

    def _refresh_input_controls(self):
        source = INPUT_SOURCE_BY_LABEL.get(self.cb_input_source.currentText(), "text")
        uses_previous = source == "output"
        self.cb_input_output.setEnabled(uses_previous)
        self.ed_input_value.setEnabled(not uses_previous)

    def _refresh_input_output_combo(self):
        current = self.cb_input_output.currentData()
        self.cb_input_output.clear()
        for visible, step_id, output_id, kind in self._previous_outputs():
            self.cb_input_output.addItem(visible, (step_id, output_id, kind))
        if current is not None:
            for index in range(self.cb_input_output.count()):
                if self.cb_input_output.itemData(index) == current:
                    self.cb_input_output.setCurrentIndex(index)
                    break

    def _input_display_value(self, item: CascadeInput) -> str:
        if item.source == "output":
            for visible, step_id, output_id, _kind in self._previous_outputs():
                if (step_id, output_id) == (item.source_step_id, item.source_output_id):
                    return visible
            return "Chybějící výstup"
        value = item.value.replace("\n", " ")
        return value[:140] + ("…" if len(value) > 140 else "")

    def _refresh_inputs(self):
        self.tbl_inputs.blockSignals(True)
        self.tbl_inputs.setRowCount(len(self._draft_inputs))
        for row_index, item in enumerate(self._draft_inputs):
            values = [
                item.name,
                INPUT_SOURCE_LABELS.get(item.source, item.source),
                self._input_display_value(item),
            ]
            for col, value in enumerate(values):
                self.tbl_inputs.setItem(row_index, col, QTableWidgetItem(value))
        self.tbl_inputs.blockSignals(False)
        self._refresh_input_output_combo()
        self._refresh_modify_input_combo()
        self._selected_input_index = -1

    def _clear_input_form(self):
        self.ed_input_name.clear()
        self.cb_input_source.setCurrentText(INPUT_SOURCE_LABELS["text"])
        self.ed_input_value.clear()
        self._selected_input_index = -1
        self._refresh_input_controls()

    def _select_input(self, index):
        if not 0 <= index < len(self._draft_inputs):
            return
        self._selected_input_index = index
        item = self._draft_inputs[index]
        self.ed_input_name.setText(item.name)
        self.cb_input_source.setCurrentText(INPUT_SOURCE_LABELS.get(item.source, item.source))
        self.ed_input_value.setPlainText(item.value)
        if item.source == "output":
            for combo_index in range(self.cb_input_output.count()):
                data = self.cb_input_output.itemData(combo_index)
                if data and tuple(data[:2]) == (item.source_step_id, item.source_output_id):
                    self.cb_input_output.setCurrentIndex(combo_index)
                    break
        self._refresh_input_controls()

    def _input_from_form(self, keep_id=""):
        name = self.ed_input_name.text().strip()
        source = INPUT_SOURCE_BY_LABEL.get(self.cb_input_source.currentText(), "text")
        item = CascadeInput(
            id=keep_id or CascadeInput().id,
            name=name,
            source=source,
            value=self.ed_input_value.toPlainText().strip(),
        )
        if source == "output":
            data = self.cb_input_output.currentData()
            if not data:
                raise ValueError("Vyberte konkrétní výstup předchozího kroku.")
            item.source_step_id, item.source_output_id = data[0], data[1]
            item.value = ""
        if not item.name:
            raise ValueError("Vstup musí mít název.")
        if source != "output" and not item.value:
            raise ValueError("Vyplňte hodnotu vstupu.")
        return item

    def _add_input(self):
        try:
            self._draft_inputs.append(self._input_from_form())
        except Exception as exc:
            msg_warning(self, "Vstup nelze přidat", str(exc))
            return
        self._refresh_inputs()
        self._clear_input_form()

    def _update_input(self):
        index = self._selected_input_index
        if not 0 <= index < len(self._draft_inputs):
            return
        try:
            old_id = self._draft_inputs[index].id
            self._draft_inputs[index] = self._input_from_form(old_id)
        except Exception as exc:
            msg_warning(self, "Vstup nelze upravit", str(exc))
            return
        self._refresh_inputs()
        self._clear_input_form()

    def _delete_input(self):
        index = self._selected_input_index
        if not 0 <= index < len(self._draft_inputs):
            return
        removed = self._draft_inputs.pop(index)
        for output in self._draft_outputs:
            if output.modify_input_id == removed.id:
                output.modify_input_id = ""
        self._refresh_inputs()
        self._refresh_outputs()
        self._clear_input_form()

    def _browse_input_file(self):
        path, _ = dialog_open_file(self, "Vybrat vstupní soubor")
        if path:
            self.cb_input_source.setCurrentText(INPUT_SOURCE_LABELS["local_file"])
            self.ed_input_value.setPlainText(path)

    def _refresh_modify_input_combo(self):
        current = self.cb_modify_input.currentData()
        self.cb_modify_input.clear()
        previous_map = {
            (step_id, output_id): kind
            for _visible, step_id, output_id, kind in self._previous_outputs()
        }
        for item in self._draft_inputs:
            is_file = item.source in ("local_file", "file_id")
            if item.source == "output":
                is_file = (
                    previous_map.get((item.source_step_id, item.source_output_id)) == "file"
                )
            if is_file:
                self.cb_modify_input.addItem(item.name, item.id)
        if current:
            for index in range(self.cb_modify_input.count()):
                if self.cb_modify_input.itemData(index) == current:
                    self.cb_modify_input.setCurrentIndex(index)
                    break

    def _normalize_file_extension(self):
        file_type = self.cb_file_type.currentText().strip()
        name = self.ed_file_name.text().strip()
        if not file_type or not name:
            return
        path = Path(name)
        if not path.suffix:
            self.ed_file_name.setText(name + "." + file_type)

    def _refresh_output_controls(self):
        kind = OUTPUT_KIND_BY_LABEL.get(self.cb_output_kind.currentText(), "text")
        is_file = kind == "file"
        is_decision = kind == "decision"
        for widget in (
            self.cb_file_type,
            self.ed_file_name,
            self.cb_file_mode,
        ):
            widget.setEnabled(is_file)
        self.cb_modify_input.setEnabled(
            is_file and FILE_MODE_BY_LABEL.get(self.cb_file_mode.currentText()) == "modify"
        )
        for widget in (
            self.tbl_decisions,
            self.ed_decision_value,
            self.sp_decision_target,
        ):
            widget.setEnabled(is_decision)

    def _decision_summary(self, output: CascadeOutput) -> str:
        if output.kind != "decision":
            return ""
        return "; ".join(
            f"{item.value}→{item.target_step_number or '?'}"
            for item in output.decision_options
        )

    def _refresh_outputs(self):
        selected = self._selected_output_index
        self.tbl_outputs.blockSignals(True)
        self.tbl_outputs.setRowCount(len(self._draft_outputs))
        for row_index, output in enumerate(self._draft_outputs):
            file_info = ""
            if output.kind == "file":
                file_info = (
                    f"{output.file_name} · {output.file_type.upper()} · "
                    f"{FILE_MODE_LABELS.get(output.file_mode, output.file_mode)}"
                )
            values = [
                output.name,
                OUTPUT_KIND_LABELS.get(output.kind, output.kind),
                file_info,
                self._decision_summary(output),
            ]
            for col, value in enumerate(values):
                self.tbl_outputs.setItem(row_index, col, QTableWidgetItem(value))
        self.tbl_outputs.blockSignals(False)
        if 0 <= selected < len(self._draft_outputs):
            self._selected_output_index = selected
            self.tbl_outputs.setCurrentCell(selected, 0)
        else:
            self._selected_output_index = -1
            self._selected_decision_index = -1
            self._refresh_decisions()
        self._refresh_final_controls()

    def _clear_output_form(self):
        self.ed_output_name.setText("Výstup")
        self.cb_output_kind.setCurrentText(OUTPUT_KIND_LABELS["text"])
        self.ed_file_name.clear()
        self.cb_file_mode.setCurrentText(FILE_MODE_LABELS["create"])
        self._selected_output_index = -1
        self._selected_decision_index = -1
        self._refresh_decisions()
        self._refresh_output_controls()

    def _select_output(self, index):
        if not 0 <= index < len(self._draft_outputs):
            return
        self._selected_output_index = index
        output = self._draft_outputs[index]
        self.ed_output_name.setText(output.name)
        self.cb_output_kind.setCurrentText(OUTPUT_KIND_LABELS.get(output.kind, output.kind))
        if output.file_type:
            self.cb_file_type.setCurrentText(output.file_type)
        self.ed_file_name.setText(output.file_name)
        self.cb_file_mode.setCurrentText(FILE_MODE_LABELS.get(output.file_mode, output.file_mode))
        self._refresh_modify_input_combo()
        for combo_index in range(self.cb_modify_input.count()):
            if self.cb_modify_input.itemData(combo_index) == output.modify_input_id:
                self.cb_modify_input.setCurrentIndex(combo_index)
                break
        self._refresh_decisions()
        self._refresh_output_controls()

    def _output_from_form(self, keep_id="", decision_options=None):
        kind = OUTPUT_KIND_BY_LABEL.get(self.cb_output_kind.currentText(), "text")
        output = CascadeOutput(
            id=keep_id or CascadeOutput().id,
            name=self.ed_output_name.text().strip(),
            kind=kind,
        )
        if not output.name:
            raise ValueError("Výstup musí mít název.")
        if kind == "file":
            output.file_type = self.cb_file_type.currentText().strip().lower()
            output.file_name = self.ed_file_name.text().strip().replace("\\", "/")
            output.file_mode = FILE_MODE_BY_LABEL.get(
                self.cb_file_mode.currentText(), "create"
            )
            if output.file_mode == "modify":
                output.modify_input_id = str(self.cb_modify_input.currentData() or "")
        if kind == "decision":
            output.decision_options = copy.deepcopy(decision_options or [])
        return output

    def _add_output(self):
        try:
            output = self._output_from_form()
            if output.kind == "decision":
                output.decision_options = []
            self._draft_outputs.append(output)
        except Exception as exc:
            msg_warning(self, "Výstup nelze přidat", str(exc))
            return
        self._refresh_outputs()
        self._clear_output_form()

    def _update_output(self):
        index = self._selected_output_index
        if not 0 <= index < len(self._draft_outputs):
            return
        try:
            old = self._draft_outputs[index]
            self._draft_outputs[index] = self._output_from_form(
                old.id,
                old.decision_options,
            )
        except Exception as exc:
            msg_warning(self, "Výstup nelze upravit", str(exc))
            return
        self._refresh_outputs()
        self.tbl_outputs.setCurrentCell(index, 0)

    def _delete_output(self):
        index = self._selected_output_index
        if not 0 <= index < len(self._draft_outputs):
            return
        removed = self._draft_outputs.pop(index)
        self.definition.final_outputs = [
            ref
            for ref in self.definition.final_outputs
            if not (
                0 <= self.current_step_index < len(self.definition.steps)
                and ref.step_id == self.definition.steps[self.current_step_index].id
                and ref.output_id == removed.id
            )
        ]
        self._refresh_outputs()
        self._clear_output_form()

    def _refresh_decisions(self):
        options = []
        if 0 <= self._selected_output_index < len(self._draft_outputs):
            output = self._draft_outputs[self._selected_output_index]
            if output.kind == "decision":
                options = output.decision_options
        self.tbl_decisions.blockSignals(True)
        self.tbl_decisions.setRowCount(len(options))
        for index, option in enumerate(options):
            self.tbl_decisions.setItem(index, 0, QTableWidgetItem(option.value))
            self.tbl_decisions.setItem(
                index,
                1,
                QTableWidgetItem(str(option.target_step_number or "?")),
            )
        self.tbl_decisions.blockSignals(False)
        self._selected_decision_index = -1

    def _select_decision(self, index):
        if not 0 <= self._selected_output_index < len(self._draft_outputs):
            return
        output = self._draft_outputs[self._selected_output_index]
        if not 0 <= index < len(output.decision_options):
            return
        self._selected_decision_index = index
        option = output.decision_options[index]
        self.ed_decision_value.setText(option.value)
        self.sp_decision_target.setValue(max(1, option.target_step_number or 1))

    def _decision_from_form(self, old=None):
        value = self.ed_decision_value.text().strip()
        target = self.sp_decision_target.value()
        if not value:
            raise ValueError("Zadejte jednu z možných odpovědí.")
        if target <= self.current_step_index + 1:
            raise ValueError("Rozhodnutí musí směřovat do některého z budoucích kroků.")
        target_step_id = ""
        if old and old.target_step_number == target:
            target_step_id = old.target_step_id
        return CascadeDecisionOption(
            value=value,
            target_step_id=target_step_id,
            target_step_number=target,
        )

    def _add_decision(self):
        if not 0 <= self._selected_output_index < len(self._draft_outputs):
            msg_warning(self, "Rozhodnutí", "Nejdříve vyberte rozhodovací výstup.")
            return
        output = self._draft_outputs[self._selected_output_index]
        if output.kind != "decision":
            return
        try:
            option = self._decision_from_form()
            if any(item.value == option.value for item in output.decision_options):
                raise ValueError("Stejná odpověď už v rozhodnutí existuje.")
            output.decision_options.append(option)
        except Exception as exc:
            msg_warning(self, "Rozhodnutí", str(exc))
            return
        self._refresh_decisions()
        self._refresh_outputs()
        self.tbl_outputs.setCurrentCell(self._selected_output_index, 0)

    def _update_decision(self):
        if not 0 <= self._selected_output_index < len(self._draft_outputs):
            return
        output = self._draft_outputs[self._selected_output_index]
        index = self._selected_decision_index
        if not 0 <= index < len(output.decision_options):
            return
        try:
            option = self._decision_from_form(output.decision_options[index])
            if any(
                item.value == option.value and item_index != index
                for item_index, item in enumerate(output.decision_options)
            ):
                raise ValueError("Stejná odpověď už v rozhodnutí existuje.")
            output.decision_options[index] = option
        except Exception as exc:
            msg_warning(self, "Rozhodnutí", str(exc))
            return
        self._refresh_decisions()
        self._refresh_outputs()
        self.tbl_outputs.setCurrentCell(self._selected_output_index, 0)

    def _delete_decision(self):
        if not 0 <= self._selected_output_index < len(self._draft_outputs):
            return
        output = self._draft_outputs[self._selected_output_index]
        index = self._selected_decision_index
        if 0 <= index < len(output.decision_options):
            del output.decision_options[index]
        self._refresh_decisions()
        self._refresh_outputs()
        self.tbl_outputs.setCurrentCell(self._selected_output_index, 0)

    def _refresh_final_controls(self):
        selected_data = self.cb_final_available.currentData()
        self.cb_final_available.clear()
        for step_index, step in enumerate(self.definition.steps):
            outputs = (
                self._draft_outputs
                if step_index == self.current_step_index
                else step.outputs
            )
            for output in outputs:
                self.cb_final_available.addItem(
                    f"{step_index + 1}. {step.title or 'Krok'} · {output.name}",
                    (step.id, output.id),
                )
        if selected_data:
            for index in range(self.cb_final_available.count()):
                if self.cb_final_available.itemData(index) == selected_data:
                    self.cb_final_available.setCurrentIndex(index)
                    break
        self.lst_final.clear()
        for ref in self.definition.final_outputs:
            visible = None
            for index in range(self.cb_final_available.count()):
                if self.cb_final_available.itemData(index) == (ref.step_id, ref.output_id):
                    visible = self.cb_final_available.itemText(index)
                    break
            if visible:
                self.lst_final.addItem(visible)
                self.lst_final.item(self.lst_final.count() - 1).setData(
                    Qt.UserRole, (ref.step_id, ref.output_id)
                )

    def _add_final_output(self):
        data = self.cb_final_available.currentData()
        if not data:
            return
        key = tuple(data)
        if any(
            (ref.step_id, ref.output_id) == key for ref in self.definition.final_outputs
        ):
            return
        self.definition.final_outputs.append(
            CascadeOutputRef(step_id=key[0], output_id=key[1])
        )
        self._refresh_final_controls()

    def _remove_final_output(self):
        item = self.lst_final.currentItem()
        if not item:
            return
        data = item.data(Qt.UserRole)
        if data:
            self.definition.final_outputs = [
                ref
                for ref in self.definition.final_outputs
                if (ref.step_id, ref.output_id) != tuple(data)
            ]
        self._refresh_final_controls()

    def add_step(self):
        current_context = (
            self.definition.steps[-1].context_id if self.definition.steps else "Kontext 1"
        )
        self.definition.steps.append(
            CascadeStep(
                title=f"Krok {len(self.definition.steps) + 1}",
                model=next(iter(self.models()), ""),
                context_id=current_context,
                deterministic=True,
                outputs=[CascadeOutput(name="Výstup", kind="text")],
            )
        )
        self.current_step_index = len(self.definition.steps) - 1
        self._refresh_step_list()
        self.tbl_steps.setCurrentCell(self.current_step_index, 0)
        self.on_step_selected(self.current_step_index)

    def delete_selected_step(self):
        index = self.current_step_index
        if not 0 <= index < len(self.definition.steps):
            return
        removed = self.definition.steps[index]
        del self.definition.steps[index]
        self.definition.final_outputs = [
            ref for ref in self.definition.final_outputs if ref.step_id != removed.id
        ]
        for step in self.definition.steps:
            step.inputs = [
                item for item in step.inputs if item.source_step_id != removed.id
            ]
            for output in step.outputs:
                if output.kind == "decision":
                    for option in output.decision_options:
                        if option.target_step_id == removed.id:
                            option.target_step_id = ""
        self.current_step_index = min(index, len(self.definition.steps) - 1)
        if not self.definition.steps:
            self.add_step()
            return
        self._refresh_step_list()
        self.tbl_steps.setCurrentCell(self.current_step_index, 0)

    def duplicate_selected_step(self):
        index = self.current_step_index
        if not 0 <= index < len(self.definition.steps):
            return
        source = copy.deepcopy(self.definition.steps[index])
        duplicate = CascadeStep(
            title=source.title + " – kopie",
            model=source.model,
            temperature=source.temperature,
            instructions=source.instructions,
            input_text=source.input_text,
            context_id=source.context_id,
            deterministic=True,
            inputs=copy.deepcopy(source.inputs),
            outputs=copy.deepcopy(source.outputs),
        )
        input_id_map = {}
        for item in duplicate.inputs:
            old_id = item.id
            item.id = CascadeInput().id
            input_id_map[old_id] = item.id
        for output in duplicate.outputs:
            output.id = CascadeOutput().id
            if output.modify_input_id in input_id_map:
                output.modify_input_id = input_id_map[output.modify_input_id]
        self.definition.steps.insert(index + 1, duplicate)
        self.current_step_index = index + 1
        self._refresh_step_list()
        self.tbl_steps.setCurrentCell(self.current_step_index, 0)

    def _move(self, offset):
        index = self.current_step_index
        target = index + offset
        if index >= 0 and 0 <= target < len(self.definition.steps):
            self.definition.steps[index], self.definition.steps[target] = (
                self.definition.steps[target],
                self.definition.steps[index],
            )
            self.current_step_index = target
            self._refresh_step_list()
            self.tbl_steps.setCurrentCell(target, 0)

    def move_selected_step_up(self):
        self._move(-1)

    def move_selected_step_down(self):
        self._move(1)

    def on_step_selected(self, index):
        if not 0 <= index < len(self.definition.steps):
            self.tabs.setEnabled(False)
            self.current_step_index = -1
            return
        self.current_step_index = index
        self.tabs.setEnabled(True)
        step = self.definition.steps[index]
        step.ensure_inputs()
        step.ensure_outputs()
        self.ed_step_title.setText(step.title)
        self._select_model(step.model)
        self.chk_step_temp.setChecked(step.temperature is not None)
        self.sp_step_temp.setValue(step.temperature or 0)
        self.txt_input_text.setPlainText(step.input_text)
        self._refresh_contexts(step.context_id)
        self._draft_inputs = copy.deepcopy(step.inputs)
        self._draft_outputs = copy.deepcopy(step.outputs)
        self._refresh_inputs()
        self._refresh_outputs()
        self._refresh_final_controls()
        self._refresh_runtime_view()
        self._update_temperature_enabled()
        self._clear_input_form()
        self._clear_output_form()

    def save_current_step(self, checked=False, show_message=True):
        index = self.current_step_index
        if not 0 <= index < len(self.definition.steps):
            return False
        try:
            step = copy.deepcopy(self.definition.steps[index])
            step.title = self.ed_step_title.text().strip()
            step.model = self.cb_step_model.currentText().strip()
            step.temperature = (
                self.sp_step_temp.value() if self.chk_step_temp.isChecked() else None
            )
            step.input_text = self.txt_input_text.toPlainText()
            step.context_id = self.cb_context.currentText().strip()
            step.deterministic = True
            step.inputs = copy.deepcopy(self._draft_inputs)
            step.outputs = copy.deepcopy(self._draft_outputs)
            step.input_content_json = None
            step.files_existing_ids = []
            step.files_local_paths = []
            step.previous_response_id_expr = None
            step.output_schema_kind = None
            step.output_schema_custom = None
            step.expected_out_files = []
            step.output_type = "json"
            if not step.outputs:
                raise ValueError("Krok musí mít alespoň jeden definovaný výstup.")

            candidate = copy.deepcopy(self.definition)
            candidate.steps[index] = step
            candidate.steps = candidate.steps[: index + 1]
            candidate.final_outputs = []
            candidate.run_from_step_id = ""
            warnings = validate_cascade_definition(candidate, strict=False)
            self.definition.steps[index] = step
            self.definition.updated_at = time.time()
            self.definition.version = max(2, self.definition.version)
            self._refresh_step_list()
            if show_message and warnings:
                msg_info(
                    self,
                    "Krok uložen jako rozpracovaný",
                    "Krok je uložen, ale některé budoucí vazby ještě čekají na vytvoření cílových kroků.",
                    details="\n".join(warnings),
                )
            return True
        except Exception as exc:
            if show_message:
                msg_warning(self, "Krok není uložen", str(exc))
            return False

    def _browse_default_out_dir(self):
        path = dialog_select_dir(self, "Výchozí OUT")
        if path:
            self.ed_default_out_dir.setText(path)

    def save_current(self):
        if self.current_step_index >= 0 and not self.save_current_step(show_message=False):
            msg_warning(self, "Uložení kaskády", "Nejdříve opravte aktuální krok.")
            return
        name = self.ed_name.text().strip()
        if not name or not self.definition.steps:
            msg_warning(
                self,
                "Uložení kaskády",
                "Vyplňte název a přidejte alespoň jeden krok.",
            )
            return
        self.definition.name = name
        self.definition.default_out_dir = self.ed_default_out_dir.text().strip()
        self.definition.updated_at = time.time()
        self.definition.version = max(2, self.definition.version)

        try:
            warnings = validate_cascade_definition(self.definition, strict=False)
            filename = re.sub(r"[^\w .-]", "_", name).strip(" .") + ".json"
            atomic_write_text(
                safe_join_under_root(self.cascade_dir, filename),
                json.dumps(self.definition.to_dict(), ensure_ascii=False, indent=2),
            )
        except Exception as exc:
            msg_warning(self, "Uložení kaskády", str(exc))
            return

        self.refresh_saved_list()
        self.saved.setCurrentText(filename)
        if warnings:
            msg_info(
                self,
                "Kaskáda uložena jako rozpracovaná",
                "Definice je uložená, ale před spuštěním musí být doplněny všechny budoucí vazby.",
                details="\n".join(warnings),
            )
        else:
            try:
                validate_cascade_definition(copy.deepcopy(self.definition), strict=True)
                msg_info(self, "Kaskáda uložena", filename)
            except CascadeValidationError as exc:
                msg_info(
                    self,
                    "Kaskáda uložena",
                    "Soubor je uložen, ale kaskáda zatím není připravena ke spuštění.",
                    details=str(exc),
                )

    def save_as(self):
        name, ok = dialog_input_text(
            self,
            "Uložit kaskádu jako",
            "Nový název:",
            self.ed_name.text(),
        )
        if ok and name.strip():
            self.ed_name.setText(name.strip())
            self.save_current()

    def load_selected(self):
        try:
            path = self.get_selected_cascade_path()
            if not path:
                raise ValueError("Nejdříve vyberte uloženou kaskádu.")
            definition = CascadeDefinition.from_dict(
                json.loads(Path(path).read_text(encoding="utf-8"))
            )
        except Exception as exc:
            msg_warning(self, "Načtení kaskády", str(exc))
            return
        self.definition = definition
        self.ed_name.setText(definition.name)
        self.ed_default_out_dir.setText(definition.default_out_dir)
        self.current_step_index = 0 if definition.steps else -1
        self.runtime_state = {}
        self._last_runtime_marker = ""
        self._poll_runtime()
        self._refresh_step_list()
        if definition.steps:
            self.tbl_steps.setCurrentCell(self.current_step_index, 0)
            self.on_step_selected(self.current_step_index)

    def _persist_for_next_run(self):
        path = self.get_selected_cascade_path()
        if not path:
            raise ValueError("Kaskádu nejdříve uložte, aby ji bylo možné spustit.")
        self.definition.name = self.ed_name.text().strip() or self.definition.name
        self.definition.default_out_dir = self.ed_default_out_dir.text().strip()
        self.definition.updated_at = time.time()
        self.definition.version = max(2, self.definition.version)
        validate_cascade_definition(copy.deepcopy(self.definition), strict=True)
        atomic_write_text(
            path,
            json.dumps(self.definition.to_dict(), ensure_ascii=False, indent=2),
        )

    def _run_from_selected(self):
        if not 0 <= self.current_step_index < len(self.definition.steps):
            return
        if not self.save_current_step(show_message=False):
            msg_warning(self, "Opakování", "Nejdříve opravte a uložte aktuální krok.")
            return
        self.definition.run_from_step_id = self.definition.steps[self.current_step_index].id
        self.definition.updated_at = time.time()
        try:
            self._persist_for_next_run()
        except Exception as exc:
            msg_warning(self, "Opakování nelze připravit", str(exc))
            return
        self._refresh_step_list()
        msg_info(
            self,
            "Počáteční krok nastaven",
            f"Příští běh začne krokem {self.current_step_index + 1}. "
            "Předchozí mezivýstupy se použijí jen tehdy, pokud jejich definice zůstaly beze změny.",
        )

    def _run_from_start(self):
        if 0 <= self.current_step_index < len(self.definition.steps):
            self.save_current_step(show_message=False)
        self.definition.run_from_step_id = ""
        self.definition.updated_at = time.time()
        try:
            self._persist_for_next_run()
        except Exception as exc:
            msg_warning(self, "Opakování nelze připravit", str(exc))
            return
        self._refresh_step_list()
        msg_info(self, "Počáteční krok nastaven", "Příští běh začne od prvního kroku.")

    def _add_local_file(self):
        self._browse_input_file()

    def _schema_changed(self):
        self._refresh_output_controls()

    def _choose_custom_schema(self):
        msg_info(
            self,
            "Výstupní kontrakt",
            "V deterministické kaskádě se JSON Schema vytváří automaticky podle zvolených výstupů.",
        )

    def _use_previous(self):
        if self.cb_input_output.count():
            self.cb_input_source.setCurrentText(INPUT_SOURCE_LABELS["output"])

    def insert_variable(self):
        self._use_previous()
