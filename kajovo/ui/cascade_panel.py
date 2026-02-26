from __future__ import annotations

import copy
import json
import os
import re
import time
from typing import List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QPlainTextEdit,
    QRadioButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ..core.cascade_types import CascadeDefinition, CascadeStep
from .widgets import dialog_input_text, dialog_open_file, dialog_select_dir, msg_critical, msg_info, msg_warning


class CascadePanel(QWidget):
    def __init__(self, settings, model_provider, parent=None):
        super().__init__(parent)
        self.s = settings
        self._model_provider = model_provider
        self.cascade_dir = os.path.join(self.s.cache_dir, "cascades")
        os.makedirs(self.cascade_dir, exist_ok=True)

        self.definition = CascadeDefinition(name="nova_kaskada")
        self.current_step_index = -1
        self.current_file_path = ""

        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(6)

        top = QHBoxLayout()
        top.addWidget(QLabel("Název kaskády"))
        self.ed_name = QLineEdit("nova_kaskada")
        top.addWidget(self.ed_name, 1)
        self.cb_saved = QComboBox()
        self.cb_saved.setMinimumWidth(240)
        self.btn_refresh_saved = QPushButton("Refresh")
        self.btn_save = QPushButton("Uložit kaskádu")
        self.btn_save_as = QPushButton("Uložit jako...")
        self.btn_load = QPushButton("Načíst kaskádu")
        top.addWidget(QLabel("Uložené"))
        top.addWidget(self.cb_saved)
        top.addWidget(self.btn_refresh_saved)
        top.addWidget(self.btn_save)
        top.addWidget(self.btn_save_as)
        top.addWidget(self.btn_load)
        root.addLayout(top)

        out_row = QHBoxLayout()
        out_row.addWidget(QLabel("Default OUT directory (Kaskáda)"))
        self.ed_default_out_dir = QLineEdit()
        self.btn_default_out_browse = QPushButton("Browse")
        out_row.addWidget(self.ed_default_out_dir, 1)
        out_row.addWidget(self.btn_default_out_browse)
        root.addLayout(out_row)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)
        root.addWidget(split, 1)

        # LEFT: editor kroku
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(4, 4, 4, 4)
        lv.setSpacing(8)

        box_meta = QGroupBox("Editor kroku")
        fm = QFormLayout(box_meta)
        self.ed_step_title = QLineEdit()
        self.cb_step_model = QComboBox()
        self.sp_step_temp = QDoubleSpinBox()
        self.sp_step_temp.setRange(0.0, 2.0)
        self.sp_step_temp.setSingleStep(0.1)
        self.sp_step_temp.setValue(float(getattr(self.s, "default_temperature", 0.2)))
        self.chk_step_temp = QCheckBox("Použít temperature")
        self.chk_step_temp.setChecked(True)
        fm.addRow("Název", self.ed_step_title)
        fm.addRow("Model", self.cb_step_model)
        temp_row = QWidget()
        temp_row_l = QHBoxLayout(temp_row)
        temp_row_l.setContentsMargins(0, 0, 0, 0)
        temp_row_l.addWidget(self.chk_step_temp)
        temp_row_l.addWidget(self.sp_step_temp)
        fm.addRow("Temperature", temp_row)

        self.ed_prev_resp = QLineEdit()
        self.cb_prev_var = QComboBox()
        self.cb_prev_var.addItem("")
        prev_row = QWidget()
        prev_l = QHBoxLayout(prev_row)
        prev_l.setContentsMargins(0, 0, 0, 0)
        prev_l.addWidget(self.ed_prev_resp, 1)
        prev_l.addWidget(self.cb_prev_var)
        fm.addRow("previous_response_id", prev_row)
        self.lbl_prev_hint = QLabel("Pole je aktivní od kroku 2.")
        fm.addRow("", self.lbl_prev_hint)
        lv.addWidget(box_meta)

        box_input = QGroupBox("Inputs")
        iv = QVBoxLayout(box_input)
        self.txt_instructions = QPlainTextEdit()
        self.txt_instructions.setPlaceholderText("Instructions")
        self.txt_instructions.setMinimumHeight(96)
        self.txt_input_text = QPlainTextEdit()
        self.txt_input_text.setPlaceholderText("Input text (used only when Input Content JSON is empty)")
        self.txt_input_text.setMinimumHeight(82)
        self.txt_input_content = QPlainTextEdit()
        self.txt_input_content.setPlaceholderText("Input content parts JSON (Responses API list/object, has priority)")
        self.txt_input_content.setMinimumHeight(82)
        iv.addWidget(QLabel("Instructions"))
        iv.addWidget(self.txt_instructions)
        iv.addWidget(QLabel("Input Text (použije se jen když je Input Content JSON prázdné)"))
        iv.addWidget(self.txt_input_text)
        iv.addWidget(QLabel("Input Content JSON (přímá definice content parts, má prioritu)"))
        iv.addWidget(self.txt_input_content)
        lv.addWidget(box_input)

        box_files = QGroupBox("Files")
        fv = QVBoxLayout(box_files)
        self.lst_files = QListWidget()
        fv.addWidget(self.lst_files, 1)
        fr = QHBoxLayout()
        self.btn_add_file_ids = QPushButton("Add file_id(s)")
        self.btn_add_local_file = QPushButton("Add local file")
        self.btn_remove_file = QPushButton("Remove")
        fr.addWidget(self.btn_add_file_ids)
        fr.addWidget(self.btn_add_local_file)
        fr.addWidget(self.btn_remove_file)
        fv.addLayout(fr)
        lv.addWidget(box_files, 1)

        box_out = QGroupBox("Output / JSON schema")
        ov = QVBoxLayout(box_out)
        trow = QHBoxLayout()
        self.rb_out_text = QRadioButton("TEXT")
        self.rb_out_json = QRadioButton("JSON")
        self.rb_out_text.setChecked(True)
        trow.addWidget(self.rb_out_text)
        trow.addWidget(self.rb_out_json)
        trow.addStretch(1)
        ov.addLayout(trow)

        self.chk_schema_manifest = QCheckBox("Manifest souborů")
        self.chk_schema_prompts = QCheckBox("Seznam promptů")
        self.chk_schema_custom = QCheckBox("Uživatelské schema")
        self.ed_schema_custom_path = QLineEdit()
        self.ed_schema_custom_path.setReadOnly(True)
        self.btn_schema_custom = QPushButton("Vybrat schema JSON")
        ov.addWidget(self.chk_schema_manifest)
        ov.addWidget(self.chk_schema_prompts)
        ov.addWidget(self.chk_schema_custom)
        ov.addWidget(self.ed_schema_custom_path)
        ov.addWidget(self.btn_schema_custom)
        lv.addWidget(box_out)

        box_expected = QGroupBox("Expected output files (QFILE-like)")
        ev = QVBoxLayout(box_expected)
        self.chk_expected_out_files = QCheckBox("Enable expected output files")
        self.lst_expected_out_files = QListWidget()
        expected_btns = QHBoxLayout()
        self.btn_add_expected_out_file = QPushButton("Add")
        self.btn_remove_expected_out_file = QPushButton("Remove")
        expected_btns.addWidget(self.btn_add_expected_out_file)
        expected_btns.addWidget(self.btn_remove_expected_out_file)
        ev.addWidget(self.chk_expected_out_files)
        ev.addWidget(self.lst_expected_out_files, 1)
        ev.addLayout(expected_btns)
        lv.addWidget(box_expected)

        editor_buttons = QHBoxLayout()
        self.cb_insert_var = QComboBox()
        self.cb_insert_var.addItem("{{step.1.response_id}}")
        self.btn_insert_var = QPushButton("Vložit proměnnou…")
        self.btn_save_step = QPushButton("Uložit krok")
        editor_buttons.addWidget(self.cb_insert_var, 1)
        editor_buttons.addWidget(self.btn_insert_var)
        editor_buttons.addWidget(self.btn_save_step)
        lv.addLayout(editor_buttons)

        # RIGHT: seznam kroků
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(4, 4, 4, 4)
        rv.setSpacing(8)
        rv.addWidget(QLabel("Seznam kroků"))
        self.lst_steps = QListWidget()
        self.lst_steps.setDragDropMode(QListWidget.InternalMove)
        self.lst_steps.setDefaultDropAction(Qt.MoveAction)
        rv.addWidget(self.lst_steps, 1)
        actions = QHBoxLayout()
        self.btn_add_step = QPushButton("Přidat krok")
        self.btn_delete_step = QPushButton("Smazat krok")
        self.btn_duplicate_step = QPushButton("Duplikovat krok")
        self.btn_move_step_up = QPushButton("↑ Nahoru")
        self.btn_move_step_down = QPushButton("↓ Dolů")
        actions.addWidget(self.btn_add_step)
        actions.addWidget(self.btn_delete_step)
        actions.addWidget(self.btn_duplicate_step)
        actions.addWidget(self.btn_move_step_up)
        actions.addWidget(self.btn_move_step_down)
        rv.addLayout(actions)

        split.addWidget(left)
        split.addWidget(right)
        split.setStretchFactor(0, 2)
        split.setStretchFactor(1, 1)
        split.setSizes([980, 460])

        self.btn_add_step.clicked.connect(self.add_step)
        self.btn_delete_step.clicked.connect(self.delete_selected_step)
        self.btn_duplicate_step.clicked.connect(self.duplicate_selected_step)
        self.btn_move_step_up.clicked.connect(self.move_selected_step_up)
        self.btn_move_step_down.clicked.connect(self.move_selected_step_down)
        self.lst_steps.currentRowChanged.connect(self.on_step_selected)
        self.lst_steps.model().rowsMoved.connect(self._on_steps_reordered)

        self.btn_add_file_ids.clicked.connect(self._add_file_ids)
        self.btn_add_local_file.clicked.connect(self._add_local_file)
        self.btn_remove_file.clicked.connect(self._remove_selected_file)
        self.btn_add_expected_out_file.clicked.connect(self._add_expected_out_file)
        self.btn_remove_expected_out_file.clicked.connect(self._remove_expected_out_file)
        self.chk_expected_out_files.toggled.connect(self._update_expected_out_enabled)

        self.btn_save_step.clicked.connect(self.save_current_step)
        self.btn_insert_var.clicked.connect(self.insert_variable)
        self.cb_prev_var.currentTextChanged.connect(self._on_prev_var_selected)

        self.rb_out_text.toggled.connect(self._update_schema_enabled)
        self.rb_out_json.toggled.connect(self._update_schema_enabled)
        self.chk_schema_manifest.toggled.connect(self._schema_choice_guard)
        self.chk_schema_prompts.toggled.connect(self._schema_choice_guard)
        self.chk_schema_custom.toggled.connect(self._schema_choice_guard)
        self.chk_schema_manifest.toggled.connect(self._ensure_json_output_when_schema_checked)
        self.chk_schema_prompts.toggled.connect(self._ensure_json_output_when_schema_checked)
        self.chk_schema_custom.toggled.connect(self._ensure_json_output_when_schema_checked)
        self.btn_schema_custom.clicked.connect(self._choose_custom_schema)

        self.btn_default_out_browse.clicked.connect(self._browse_default_out_dir)

        self.btn_refresh_saved.clicked.connect(self.refresh_saved_list)
        self.btn_save.clicked.connect(self.save_current)
        self.btn_save_as.clicked.connect(self.save_as)
        self.btn_load.clicked.connect(self.load_selected)

        self.refresh_models()
        self.refresh_saved_list()
        self.add_step()

    def refresh_models(self):
        models = list(self._model_provider() or [])
        current = self.cb_step_model.currentText()
        self.cb_step_model.clear()
        self.cb_step_model.addItems(models)
        preferred = str(getattr(self.s, "default_model", "") or "").strip()
        if current:
            idx = self.cb_step_model.findText(current)
            if idx >= 0:
                self.cb_step_model.setCurrentIndex(idx)
                return
        if preferred:
            idx = self.cb_step_model.findText(preferred)
            if idx >= 0:
                self.cb_step_model.setCurrentIndex(idx)

    def _sanitize_name(self, raw: str) -> str:
        out = re.sub(r"[^A-Za-z0-9._-]+", "_", (raw or "").strip())
        return out.strip("._-") or "cascade"

    def _cascade_path(self, name: str) -> str:
        return os.path.join(self.cascade_dir, f"{self._sanitize_name(name)}.json")

    def _normalize_expected_rel_path(self, raw: str) -> str:
        rel = (raw or "").strip().replace("\\", "/")
        rel = rel.lstrip("/")
        parts = [p for p in rel.split("/") if p]
        if not parts:
            raise ValueError("Expected output path nesmí být prázdný.")
        if any(p == ".." for p in parts):
            raise ValueError(f"Expected output path nesmí obsahovat '..': {raw}")
        return "/".join(parts)

    def _collect_expected_out_files(self) -> List[str]:
        if not self.chk_expected_out_files.isChecked():
            return []
        out: List[str] = []
        seen = set()
        for i in range(self.lst_expected_out_files.count()):
            rel = self._normalize_expected_rel_path(self.lst_expected_out_files.item(i).text())
            if rel in seen:
                raise ValueError(f"Duplicita expected output path: {rel}")
            seen.add(rel)
            out.append(rel)
        return out

    def available_cascades(self) -> List[str]:
        if not os.path.isdir(self.cascade_dir):
            return []
        items = []
        for n in os.listdir(self.cascade_dir):
            if n.lower().endswith(".json"):
                items.append(n)
        items.sort()
        return items

    def refresh_saved_list(self):
        sel = self.cb_saved.currentText()
        self.cb_saved.clear()
        for n in self.available_cascades():
            self.cb_saved.addItem(n)
        idx = self.cb_saved.findText(sel)
        if idx >= 0:
            self.cb_saved.setCurrentIndex(idx)

    def get_selected_cascade_path(self) -> str:
        n = self.cb_saved.currentText().strip()
        if not n:
            return ""
        return os.path.join(self.cascade_dir, n)

    def get_selected_cascade_name(self) -> str:
        return self.cb_saved.currentText().strip()

    def get_definition(self) -> CascadeDefinition:
        self.definition.name = self.ed_name.text().strip() or self.definition.name
        self.definition.default_out_dir = self.ed_default_out_dir.text().strip()
        self.definition.updated_at = float(time.time())
        return self.definition

    def _step_summary(self, idx: int, step: CascadeStep) -> str:
        mode = "JSON" if step.output_type == "json" else "TEXT"
        schema = step.output_schema_kind or "-"
        return f"{idx}. {step.title or 'Krok'} | {step.model or '-'} | {mode}/{schema}"

    def _refresh_step_list(self):
        self.lst_steps.blockSignals(True)
        cur = self.current_step_index
        self.lst_steps.clear()
        for i, step in enumerate(self.definition.steps, start=1):
            it = QListWidgetItem(self._step_summary(i, step))
            it.setData(Qt.UserRole, i - 1)
            self.lst_steps.addItem(it)
        self.lst_steps.blockSignals(False)
        if self.definition.steps:
            if cur < 0 or cur >= len(self.definition.steps):
                cur = 0
            self.lst_steps.setCurrentRow(cur)
        self._refresh_variable_lists()

    def _refresh_variable_lists(self):
        self.cb_prev_var.blockSignals(True)
        self.cb_insert_var.blockSignals(True)
        self.cb_prev_var.clear()
        self.cb_prev_var.addItem("")
        self.cb_insert_var.clear()
        for i, step in enumerate(self.definition.steps, start=1):
            self.cb_prev_var.addItem(f"{{{{step.{i}.response_id}}}}")
            self.cb_insert_var.addItem(f"{{{{step.{i}.response_id}}}}")
            self.cb_insert_var.addItem(f"{{{{step.{i}.json}}}}")
            self.cb_insert_var.addItem(f"{{{{step.{i}.out_file_path:REL_PATH}}}}")
            self.cb_insert_var.addItem(f"{{{{step.{i}.out_file_id:REL_PATH}}}}")
            for rel in (step.expected_out_files or []):
                self.cb_insert_var.addItem(f"{{{{step.{i}.out_file_path:{rel}}}}}")
                self.cb_insert_var.addItem(f"{{{{step.{i}.out_file_id:{rel}}}}}")
        self.cb_prev_var.blockSignals(False)
        self.cb_insert_var.blockSignals(False)

    def add_step(self):
        models = list(self._model_provider() or [])
        preferred = str(getattr(self.s, "default_model", "") or "").strip()
        model = preferred if preferred in models else (models[0] if models else "")
        step = CascadeStep(title=f"Krok {len(self.definition.steps)+1}", model=model)
        self.definition.steps.append(step)
        self.current_step_index = len(self.definition.steps) - 1
        self._refresh_step_list()

    def delete_selected_step(self):
        i = self.lst_steps.currentRow()
        if i < 0 or i >= len(self.definition.steps):
            return
        del self.definition.steps[i]
        self.current_step_index = min(i, len(self.definition.steps)-1)
        if not self.definition.steps:
            self.add_step()
            return
        self._refresh_step_list()

    def duplicate_selected_step(self):
        i = self.lst_steps.currentRow()
        if i < 0 or i >= len(self.definition.steps):
            return
        cp = copy.deepcopy(self.definition.steps[i])
        cp.title = (cp.title or "Krok") + " (copy)"
        self.definition.steps.insert(i + 1, cp)
        self.current_step_index = i + 1
        self._refresh_step_list()

    def move_selected_step_up(self):
        i = self.lst_steps.currentRow()
        if i <= 0 or i >= len(self.definition.steps):
            return
        self.definition.steps[i - 1], self.definition.steps[i] = self.definition.steps[i], self.definition.steps[i - 1]
        self.current_step_index = i - 1
        self._refresh_step_list()

    def move_selected_step_down(self):
        i = self.lst_steps.currentRow()
        if i < 0 or i >= (len(self.definition.steps) - 1):
            return
        self.definition.steps[i + 1], self.definition.steps[i] = self.definition.steps[i], self.definition.steps[i + 1]
        self.current_step_index = i + 1
        self._refresh_step_list()

    def _on_steps_reordered(self):
        new_steps: List[CascadeStep] = []
        for row in range(self.lst_steps.count()):
            txt = self.lst_steps.item(row).text()
            old_idx = self.lst_steps.item(row).data(Qt.UserRole)
            try:
                old_idx = int(old_idx)
            except Exception:
                old_idx = -1
            if 0 <= old_idx < len(self.definition.steps):
                new_steps.append(self.definition.steps[old_idx])
        if len(new_steps) == len(self.definition.steps):
            self.definition.steps = new_steps
        self.current_step_index = self.lst_steps.currentRow()
        self._refresh_step_list()

    def on_step_selected(self, index: int):
        self.current_step_index = index
        if index < 0 or index >= len(self.definition.steps):
            return
        step = self.definition.steps[index]
        self.ed_step_title.setText(step.title)
        if step.model:
            idx = self.cb_step_model.findText(step.model)
            if idx >= 0:
                self.cb_step_model.setCurrentIndex(idx)
        self.chk_step_temp.setChecked(step.temperature is not None)
        if step.temperature is not None:
            self.sp_step_temp.setValue(float(step.temperature))
        self.txt_instructions.setPlainText(step.instructions)
        self.txt_input_text.setPlainText(step.input_text)
        self.txt_input_content.setPlainText(json.dumps(step.input_content_json, ensure_ascii=False) if step.input_content_json is not None else "")
        self.ed_prev_resp.setText(step.previous_response_id_expr or "")

        self.lst_files.clear()
        for fid in step.files_existing_ids:
            it = QListWidgetItem(f"FILE_ID: {fid}")
            it.setData(Qt.UserRole, {"kind": "id", "value": fid})
            self.lst_files.addItem(it)
        for lp in step.files_local_paths:
            it = QListWidgetItem(f"LOCAL: {lp}")
            it.setData(Qt.UserRole, {"kind": "local", "value": lp})
            self.lst_files.addItem(it)

        self.rb_out_text.setChecked(step.output_type != "json")
        self.rb_out_json.setChecked(step.output_type == "json")
        self.chk_schema_manifest.setChecked(step.output_schema_kind == "manifest")
        self.chk_schema_prompts.setChecked(step.output_schema_kind == "prompts")
        self.chk_schema_custom.setChecked(step.output_schema_kind == "custom")
        self.ed_schema_custom_path.setText("<inline schema>" if step.output_schema_kind == "custom" and step.output_schema_custom else "")
        self._update_schema_enabled()

        self.lst_expected_out_files.clear()
        for rel in (step.expected_out_files or []):
            self.lst_expected_out_files.addItem(rel)
        self.chk_expected_out_files.setChecked(bool(step.expected_out_files))
        self._update_expected_out_enabled()

        first = index == 0
        self.ed_prev_resp.setEnabled(not first)
        self.cb_prev_var.setEnabled(not first)

    def _selected_step(self) -> Optional[CascadeStep]:
        i = self.current_step_index
        if i < 0 or i >= len(self.definition.steps):
            return None
        return self.definition.steps[i]

    def _parse_input_content(self):
        raw = self.txt_input_content.toPlainText().strip()
        if not raw:
            return None
        try:
            parsed = json.loads(raw)
        except Exception as e:
            raise ValueError(f"Input Content není validní JSON: {e}")
        if not isinstance(parsed, (dict, list)):
            raise ValueError("Input Content musí být JSON object nebo list")
        return parsed

    def _pick_schema_kind(self) -> Optional[str]:
        kinds = []
        if self.chk_schema_manifest.isChecked():
            kinds.append("manifest")
        if self.chk_schema_prompts.isChecked():
            kinds.append("prompts")
        if self.chk_schema_custom.isChecked():
            kinds.append("custom")
        if len(kinds) > 1:
            raise ValueError("Vyber jen jeden zdroj schema.")
        return kinds[0] if kinds else None

    def _validate_custom_schema(self, schema_obj):
        if not isinstance(schema_obj, dict):
            raise ValueError("Custom schema musí být JSON objekt")
        if "type" not in schema_obj and "properties" not in schema_obj:
            raise ValueError("Custom schema musí mít alespoň klíč 'type' nebo 'properties'")

    def save_current_step(self) -> bool:
        step = self._selected_step()
        if step is None:
            return False
        try:
            input_content = self._parse_input_content()
            schema_kind = self._pick_schema_kind()
            output_type = "json" if self.rb_out_json.isChecked() else "text"
            expected_out_files = self._collect_expected_out_files()
            if output_type == "text" and schema_kind is not None:
                raise ValueError("Schema lze použít jen pro JSON output.")
        except ValueError as e:
            msg_warning(self, "Kaskáda", str(e))
            return False

        step.title = self.ed_step_title.text().strip()
        step.model = self.cb_step_model.currentText().strip()
        step.temperature = float(self.sp_step_temp.value()) if self.chk_step_temp.isChecked() else None
        step.instructions = self.txt_instructions.toPlainText()
        step.input_text = self.txt_input_text.toPlainText()
        step.input_content_json = input_content
        if step.input_content_json is not None and step.input_text.strip():
            msg_warning(self, "Kaskáda", "Input Text bude ignorován, protože Input Content JSON má prioritu.")
        step.previous_response_id_expr = self.ed_prev_resp.text().strip() or None
        step.output_type = output_type
        step.output_schema_kind = schema_kind
        step.expected_out_files = expected_out_files
        if schema_kind != "custom":
            step.output_schema_custom = None
        elif step.output_schema_custom is None:
            msg_warning(self, "Kaskáda", "Custom schema není načtené.")
            return False

        # files from list widget
        ids: List[str] = []
        lps: List[str] = []
        for i in range(self.lst_files.count()):
            data = self.lst_files.item(i).data(Qt.UserRole) or {}
            if data.get("kind") == "id":
                ids.append(str(data.get("value") or ""))
            elif data.get("kind") == "local":
                lps.append(str(data.get("value") or ""))
        step.files_existing_ids = [x for x in ids if x]
        step.files_local_paths = [x for x in lps if x]

        self.definition.updated_at = float(time.time())
        self._refresh_step_list()
        msg_info(self, "Kaskáda", "Krok uložen.")
        return True

    def _schema_choice_guard(self):
        if not self.rb_out_json.isChecked():
            return
        checked = [self.chk_schema_manifest, self.chk_schema_prompts, self.chk_schema_custom]
        active = [c for c in checked if c.isChecked()]
        if len(active) <= 1:
            return
        sender = self.sender()
        for c in checked:
            if c is not sender:
                c.blockSignals(True)
                c.setChecked(False)
                c.blockSignals(False)

    def _ensure_json_output_when_schema_checked(self, checked: bool):
        if checked and not self.rb_out_json.isChecked():
            self.rb_out_json.setChecked(True)

    def _update_schema_enabled(self):
        on = self.rb_out_json.isChecked()
        self.chk_schema_manifest.setEnabled(on)
        self.chk_schema_prompts.setEnabled(on)
        self.chk_schema_custom.setEnabled(on)
        self.btn_schema_custom.setEnabled(on and self.chk_schema_custom.isChecked())
        self.ed_schema_custom_path.setEnabled(on and self.chk_schema_custom.isChecked())

    def _choose_custom_schema(self):
        fp, _ = dialog_open_file(self, "Schema JSON", self.cascade_dir, "JSON (*.json);;All (*.*)")
        if not fp:
            return
        try:
            with open(fp, "r", encoding="utf-8") as f:
                obj = json.load(f)
            self._validate_custom_schema(obj)
        except Exception as e:
            msg_critical(self, "Schema", str(e))
            return
        step = self._selected_step()
        if step is not None:
            step.output_schema_custom = obj
        self.ed_schema_custom_path.setText(fp)
        self.chk_schema_custom.setChecked(True)
        self._schema_choice_guard()
        msg_info(self, "Schema", "Custom schema načteno.")

    def _add_file_ids(self):
        text, ok = dialog_input_text(self, "File IDs", "Vlož file_id (oddělení čárka/newline)")
        if not ok:
            return
        raw = text.strip()
        if not raw:
            return
        ids = []
        for part in re.split(r"[\n,;]+", raw):
            p = part.strip()
            if p:
                ids.append(p)
        for fid in ids:
            it = QListWidgetItem(f"FILE_ID: {fid}")
            it.setData(Qt.UserRole, {"kind": "id", "value": fid})
            self.lst_files.addItem(it)

    def _add_local_file(self):
        fp, _ = dialog_open_file(self, "Lokální soubor", "", "All (*.*)")
        if not fp:
            return
        it = QListWidgetItem(f"LOCAL: {fp}")
        it.setData(Qt.UserRole, {"kind": "local", "value": fp})
        self.lst_files.addItem(it)

    def _remove_selected_file(self):
        for it in self.lst_files.selectedItems():
            self.lst_files.takeItem(self.lst_files.row(it))

    def _add_expected_out_file(self):
        text, ok = dialog_input_text(self, "Expected output file", "Relativní cesta souboru vůči OUT")
        if not ok:
            return
        raw = text.strip()
        if not raw:
            return
        try:
            rel = self._normalize_expected_rel_path(raw)
        except ValueError as e:
            msg_warning(self, "Kaskáda", str(e))
            return
        for i in range(self.lst_expected_out_files.count()):
            if self.lst_expected_out_files.item(i).text() == rel:
                msg_warning(self, "Kaskáda", f"Expected output path už existuje: {rel}")
                return
        self.lst_expected_out_files.addItem(rel)
        self.chk_expected_out_files.setChecked(True)
        self._update_expected_out_enabled()

    def _remove_expected_out_file(self):
        for it in self.lst_expected_out_files.selectedItems():
            self.lst_expected_out_files.takeItem(self.lst_expected_out_files.row(it))
        if self.lst_expected_out_files.count() == 0:
            self.chk_expected_out_files.setChecked(False)
        self._update_expected_out_enabled()

    def _update_expected_out_enabled(self):
        enabled = self.chk_expected_out_files.isChecked()
        self.lst_expected_out_files.setEnabled(enabled)
        self.btn_add_expected_out_file.setEnabled(enabled)
        self.btn_remove_expected_out_file.setEnabled(enabled)

    def _browse_default_out_dir(self):
        start_dir = self.ed_default_out_dir.text().strip() or os.getcwd()
        path = dialog_select_dir(self, "Default OUT directory (Kaskáda)", start_dir)
        if path:
            self.ed_default_out_dir.setText(path)
            self.definition.default_out_dir = path.strip()

    def _on_prev_var_selected(self, text: str):
        if text:
            self.ed_prev_resp.setText(text)

    def insert_variable(self):
        token = self.cb_insert_var.currentText().strip()
        if not token:
            return
        for w in [self.ed_prev_resp, self.txt_instructions, self.txt_input_text, self.txt_input_content]:
            if w.hasFocus():
                if isinstance(w, QLineEdit):
                    w.insert(token)
                else:
                    w.insertPlainText(token)
                return
        self.txt_instructions.insertPlainText(token)

    def save_current(self):
        if not self.save_current_step():
            return
        name = self.ed_name.text().strip() or self.definition.name
        self.definition.name = name
        self.definition.default_out_dir = self.ed_default_out_dir.text().strip()
        if self.definition.created_at <= 0:
            self.definition.created_at = float(time.time())
        self.definition.updated_at = float(time.time())
        path = self._cascade_path(name)
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.definition.to_dict(), f, ensure_ascii=False, indent=2)
            self.current_file_path = path
            self.refresh_saved_list()
            base = os.path.basename(path)
            idx = self.cb_saved.findText(base)
            if idx >= 0:
                self.cb_saved.setCurrentIndex(idx)
            msg_info(self, "Kaskáda", f"Uloženo: {path}")
        except Exception as e:
            msg_critical(self, "Kaskáda", f"Uložení selhalo: {e}")

    def save_as(self):
        nm, ok = dialog_input_text(self, "Uložit jako", "Název kaskády", self.ed_name.text().strip() or "cascade")
        if not ok:
            return
        nm = nm.strip()
        if not nm:
            return
        self.ed_name.setText(nm)
        self.save_current()

    def load_selected(self):
        path = self.get_selected_cascade_path()
        if not path or not os.path.isfile(path):
            msg_warning(self, "Kaskáda", "Vyber uloženou kaskádu.")
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            definition = CascadeDefinition.from_dict(data)
        except Exception as e:
            msg_critical(self, "Kaskáda", f"Load selhal: {e}")
            return
        self.definition = definition
        self.current_file_path = path
        self.ed_name.setText(definition.name)
        self.ed_default_out_dir.setText(definition.default_out_dir or "")
        self.current_step_index = 0
        if not self.definition.steps:
            self.definition.steps.append(CascadeStep(title="Krok 1"))
        self._refresh_step_list()
        msg_info(self, "Kaskáda", f"Načteno: {path}")
