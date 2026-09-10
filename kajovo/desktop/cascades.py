"""Editor kaskád se samostatným rozepsaným krokem a atomickým uložením."""

import copy
import json
import re
import time
from pathlib import Path
from PySide6.QtWidgets import QWidget, QListWidget, QTabWidget, QCheckBox
from ..core.cascade_types import CascadeDefinition, CascadeStep
from ..core.request_rules import validate_response_payload, uses_reasoning_defaults
from ..core.utils import atomic_write_text, safe_join_under_root
from .design import column, row, button, text, combo, number, editor, form, scroll, label
from .dialogs import msg_warning, msg_info, dialog_input_text, dialog_open_file, dialog_select_dir


class CascadePanel(QWidget):
    def __init__(self, settings, models, parent=None):
        super().__init__(parent)
        self.s = settings
        self.models = models
        self.cascade_dir = str(Path(settings.log_dir).parent / "cascades")
        Path(self.cascade_dir).mkdir(parents=True, exist_ok=True)
        self.definition = CascadeDefinition("Nová kaskáda")
        self.current_step_index = -1
        layout = column(self)
        self.saved = combo()
        self.saved.setPlaceholderText("Žádná uložená kaskáda")
        self.ed_name = text("Nová kaskáda")
        layout.addWidget(
            row(
                self.saved,
                button("Načíst", self.load_selected),
                button("Obnovit", self.refresh_saved_list),
            )
        )
        layout.addWidget(
            row(
                self.ed_name,
                button("Uložit", self.save_current, "Primary"),
                button("Uložit jako", self.save_as),
            )
        )
        self.ed_default_out_dir = text(placeholder="Výchozí OUT kaskády")
        layout.addWidget(
            row(self.ed_default_out_dir, button("Vybrat OUT", self._browse_default_out_dir))
        )
        self.lst_steps = QListWidget()
        self.lst_steps.setMaximumHeight(90)
        self.lst_steps.currentRowChanged.connect(self.on_step_selected)
        layout.addWidget(self.lst_steps)
        layout.addWidget(
            row(
                button("Přidat krok", self.add_step),
                button("Duplikovat", self.duplicate_selected_step),
                button("Nahoru", self.move_selected_step_up),
                button("Dolů", self.move_selected_step_down),
                button("Odstranit", self.delete_selected_step),
            )
        )
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        base = QWidget()
        bl = column(base, 16)
        fields = form(bl)
        self.ed_step_title = text()
        self.cb_step_model = combo(models())
        self.chk_step_temp = QCheckBox("Poslat teplotu")
        self.sp_step_temp = number(0.2, maximum=2, decimal=True)
        self.ed_prev_resp = text(placeholder="resp_… nebo proměnná předchozího kroku")
        for name, field in (
            ("Název kroku", self.ed_step_title),
            ("Model", self.cb_step_model),
            ("Teplota", row(self.chk_step_temp, self.sp_step_temp)),
            ("Návaznost", self.ed_prev_resp),
        ):
            fields.addRow(name, field)
        self.cb_prev_var = combo()
        bl.addWidget(row(self.cb_prev_var, button("Použít jako návaznost", self._use_previous)))
        self.txt_instructions = editor()
        self.txt_input_text = editor()
        bl.addWidget(label("Instrukce"))
        bl.addWidget(self.txt_instructions)
        bl.addWidget(label("Zadání kroku"))
        bl.addWidget(self.txt_input_text)
        self.tabs.addTab(scroll(base), "Zadání kroku")
        data = QWidget()
        dl = column(data, 16)
        dl.addWidget(
            label(
                "Strukturovaný vstup (JSON objekt/seznam). Má přednost před textovým zadáním.",
                "Hint",
            )
        )
        self.txt_input_content = editor()
        dl.addWidget(self.txt_input_content)
        self.file_ids = editor(height=80)
        self.local_paths = editor(height=80)
        dl.addWidget(label("file_id, jeden na řádek"))
        dl.addWidget(self.file_ids)
        dl.addWidget(label("Lokální soubory, jedna cesta na řádek"))
        dl.addWidget(self.local_paths)
        dl.addWidget(button("Přidat lokální soubor", self._add_local_file))
        self.variable = combo()
        dl.addWidget(row(self.variable, button("Vložit proměnnou do zadání", self.insert_variable)))
        self.tabs.addTab(scroll(data), "Vstupy a proměnné")
        output = QWidget()
        ol = column(output, 16)
        self.output_type = combo(["text", "json"])
        self.schema_kind = combo(["Bez schématu", "manifest", "prompts", "custom"])
        self.schema_kind.currentIndexChanged.connect(self._schema_changed)
        self.custom_schema = editor("{}")
        ol.addWidget(row(label("Typ výstupu"), self.output_type, label("Schéma"), self.schema_kind))
        ol.addWidget(label("Vlastní JSON schéma"))
        ol.addWidget(self.custom_schema)
        ol.addWidget(button("Načíst JSON schéma", self._choose_custom_schema))
        self.expected_files = editor(height=100)
        self.output_type.currentTextChanged.connect(
            lambda value: self.expected_files.setEnabled(value == "json")
        )
        self.expected_files.setEnabled(False)
        ol.addWidget(label("Očekávané výstupní soubory: relativní cesta na řádku"))
        ol.addWidget(self.expected_files)
        self.tabs.addTab(scroll(output), "Výstup a soubory")
        layout.addWidget(button("Uložit změny kroku", self.save_current_step, "Primary"))
        self.cb_step_model.currentTextChanged.connect(self._update_temperature_enabled)
        self.refresh_saved_list()
        self._update_temperature_enabled()
        self.add_step()

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
        from ..core.model_registry import model_spec

        try:
            payload = {
                "model": self.cb_step_model.currentText(),
                "temperature": self.sp_step_temp.value(),
            }
            model_spec(payload["model"])
            validate_response_payload(payload)
            allowed = not uses_reasoning_defaults(payload["model"])
        except ValueError:
            allowed = False
        self.chk_step_temp.setEnabled(allowed)
        self.sp_step_temp.setEnabled(allowed)
        if not allowed:
            self.chk_step_temp.setChecked(False)

    def available_cascades(self):
        return sorted(path.name for path in Path(self.cascade_dir).glob("*.json"))

    def refresh_saved_list(self):
        current = self.saved.currentText()
        self.saved.clear()
        self.saved.addItems(self.available_cascades())
        self.saved.setCurrentText(current)

    def get_selected_cascade_path(self):
        return (
            str(Path(self.cascade_dir) / self.saved.currentText())
            if self.saved.currentText()
            else ""
        )

    def get_selected_cascade_name(self):
        return self.saved.currentText()

    def get_definition(self):
        return copy.deepcopy(self.definition)

    def _refresh_step_list(self):
        selected = self.current_step_index
        self.lst_steps.blockSignals(True)
        self.lst_steps.clear()
        self.lst_steps.addItems(
            [
                f"{index + 1}. {step.title or 'Krok'} · {step.model or 'vyberte model'} · {step.output_type}"
                for index, step in enumerate(self.definition.steps)
            ]
        )
        self.lst_steps.setCurrentRow(selected)
        self.lst_steps.blockSignals(False)

    def add_step(self):
        self.definition.steps.append(
            CascadeStep(
                title=f"Krok {len(self.definition.steps) + 1}", model=next(iter(self.models()), "")
            )
        )
        self.current_step_index = len(self.definition.steps) - 1
        self._refresh_step_list()
        self.on_step_selected(self.current_step_index)

    def delete_selected_step(self):
        if self.current_step_index < 0:
            return
        del self.definition.steps[self.current_step_index]
        self.current_step_index = min(self.current_step_index, len(self.definition.steps) - 1)
        self._refresh_step_list()
        self.on_step_selected(self.current_step_index)

    def duplicate_selected_step(self):
        if self.current_step_index >= 0:
            self.definition.steps.insert(
                self.current_step_index + 1,
                copy.deepcopy(self.definition.steps[self.current_step_index]),
            )
            self.current_step_index += 1
            self._refresh_step_list()
            self.on_step_selected(self.current_step_index)

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
            self.on_step_selected(target)

    def move_selected_step_up(self):
        self._move(-1)

    def move_selected_step_down(self):
        self._move(1)

    def on_step_selected(self, index):
        self.current_step_index = index
        self.tabs.setEnabled(index >= 0)
        if not 0 <= index < len(self.definition.steps):
            return
        step = self.definition.steps[index]
        self.ed_step_title.setText(step.title)
        self._select_model(step.model)
        self.chk_step_temp.setChecked(step.temperature is not None)
        self.sp_step_temp.setValue(step.temperature or 0)
        self.txt_instructions.setPlainText(step.instructions)
        self.txt_input_text.setPlainText(step.input_text)
        self.txt_input_content.setPlainText(
            json.dumps(step.input_content_json, ensure_ascii=False, indent=2)
            if step.input_content_json is not None
            else ""
        )
        self.ed_prev_resp.setText(step.previous_response_id_expr or "")
        self.file_ids.setPlainText("\n".join(step.files_existing_ids))
        self.local_paths.setPlainText("\n".join(step.files_local_paths))
        self.output_type.setCurrentText(step.output_type)
        self.schema_kind.setCurrentText(step.output_schema_kind or "Bez schématu")
        self.custom_schema.setPlainText(
            json.dumps(step.output_schema_custom or {}, ensure_ascii=False, indent=2)
        )
        self.expected_files.setPlainText("\n".join(step.expected_out_files))
        self._update_temperature_enabled()
        self.variable.clear()
        self.cb_prev_var.clear()
        for previous in range(index):
            response = "{{step." + str(previous + 1) + ".response_id}}"
            self.cb_prev_var.addItem(response)
            self.variable.addItem(response)
            if self.definition.steps[previous].output_type == "json":
                self.variable.addItem("{{step." + str(previous + 1) + ".json}}")
            for path in self.definition.steps[previous].expected_out_files:
                self.variable.addItems(
                    [
                        "{{step." + str(previous + 1) + "." + kind + ":" + path + "}}"
                        for kind in ("out_file_id", "out_file_path")
                    ]
                )
        self.cb_prev_var.setEnabled(index > 0)

    def save_current_step(self):
        index = self.current_step_index
        if not 0 <= index < len(self.definition.steps):
            return False
        try:
            raw = self.txt_input_content.toPlainText().strip()
            schema = self.schema_kind.currentText()
            kind = None if schema == "Bez schématu" else schema
            expected = [
                path.strip().replace("\\", "/")
                for path in self.expected_files.toPlainText().splitlines()
                if path.strip()
            ]
            for path in expected:
                safe_join_under_root("/validation", path)
            step = CascadeStep.from_dict(
                dict(
                    title=self.ed_step_title.text(),
                    model=self.cb_step_model.currentText(),
                    temperature=self.sp_step_temp.value()
                    if self.chk_step_temp.isChecked()
                    else None,
                    instructions=self.txt_instructions.toPlainText(),
                    input_text=self.txt_input_text.toPlainText(),
                    input_content_json=json.loads(raw) if raw else None,
                    previous_response_id_expr=self.ed_prev_resp.text().strip() or None,
                    files_existing_ids=[
                        line.strip()
                        for line in self.file_ids.toPlainText().splitlines()
                        if line.strip()
                    ],
                    files_local_paths=[
                        line.strip()
                        for line in self.local_paths.toPlainText().splitlines()
                        if line.strip()
                    ],
                    output_type=self.output_type.currentText(),
                    output_schema_kind=kind,
                    output_schema_custom=json.loads(self.custom_schema.toPlainText())
                    if kind == "custom"
                    else None,
                    expected_out_files=expected,
                )
            )
            if kind and step.output_type != "json":
                raise ValueError("Schéma vyžaduje JSON výstup.")
            if expected and step.output_type != "json":
                raise ValueError("Očekávané výstupní soubory vyžadují JSON výstup.")
            from ..core.cascade_pipeline import PLACEHOLDER_RE

            for reference in PLACEHOLDER_RE.finditer(
                json.dumps(step.to_dict(), ensure_ascii=False)
            ):
                target = int(reference.group(1))
                if not 1 <= target <= index:
                    raise ValueError("Proměnná smí odkazovat pouze na předchozí krok.")
                if (
                    reference.group(2) == "json"
                    and self.definition.steps[target - 1].output_type != "json"
                ):
                    raise ValueError("Odkaz na JSON vyžaduje JSON výstup předchozího kroku.")
            if kind == "custom":
                if not step.output_schema_custom or not (
                    {"type", "properties"} & step.output_schema_custom.keys()
                ):
                    raise ValueError("Vlastní schéma vyžaduje type nebo properties.")
                from jsonschema import Draft202012Validator

                Draft202012Validator.check_schema(step.output_schema_custom)
            payload = {"model": step.model}
            if step.temperature is not None:
                payload["temperature"] = step.temperature
            if step.input_content_json is not None:
                parts = (
                    step.input_content_json
                    if isinstance(step.input_content_json, list)
                    else [step.input_content_json]
                )
                payload["input"] = [{"role": "user", "content": parts}]
            validate_response_payload(payload)
        except Exception as exc:
            msg_warning(self, "Krok není uložen", str(exc))
            return False
        self.definition.steps[index] = step
        self.definition.updated_at = time.time()
        self._refresh_step_list()
        if step.input_content_json is not None and step.input_text.strip():
            msg_info(
                self,
                "Priorita vstupů",
                "JSON vstup má přednost. Textové zadání tohoto kroku se neodešle.",
            )
        return True

    def _schema_changed(self):
        custom = self.schema_kind.currentText() == "custom"
        self.custom_schema.setEnabled(custom)
        if self.schema_kind.currentIndex() > 0:
            self.output_type.setCurrentText("json")

    def _choose_custom_schema(self):
        path, _ = dialog_open_file(self, "Načíst vlastní schéma", filters="JSON (*.json)")
        if path:
            try:
                self.custom_schema.setPlainText(Path(path).read_text(encoding="utf-8"))
                self.schema_kind.setCurrentText("custom")
            except OSError as exc:
                msg_warning(self, "Schéma", str(exc))

    def _add_local_file(self):
        path, _ = dialog_open_file(self, "Přidat soubor ke kroku")
        if path:
            self.local_paths.appendPlainText(path)

    def _browse_default_out_dir(self):
        path = dialog_select_dir(self, "Výchozí OUT")
        if path:
            self.ed_default_out_dir.setText(path)

    def _use_previous(self):
        self.ed_prev_resp.setText(self.cb_prev_var.currentText())

    def insert_variable(self):
        self.txt_input_text.insertPlainText(self.variable.currentText())

    def save_current(self):
        if self.current_step_index >= 0 and not self.save_current_step():
            return
        name = self.ed_name.text().strip()
        if not name or not self.definition.steps:
            msg_warning(self, "Uložení kaskády", "Vyplňte název a přidejte alespoň jeden krok.")
            return
        filename = re.sub(r"[^\w .-]", "_", name).strip(" .") + ".json"
        self.definition.name = name
        self.definition.default_out_dir = self.ed_default_out_dir.text().strip()
        try:
            atomic_write_text(
                safe_join_under_root(self.cascade_dir, filename),
                json.dumps(self.definition.to_dict(), ensure_ascii=False, indent=2),
            )
        except Exception as exc:
            msg_warning(self, "Uložení kaskády", str(exc))
            return
        self.refresh_saved_list()
        self.saved.setCurrentText(filename)
        msg_info(self, "Kaskáda uložena", filename)

    def save_as(self):
        name, ok = dialog_input_text(
            self, "Uložit kaskádu jako", "Nový název:", self.ed_name.text()
        )
        if ok and name.strip():
            self.ed_name.setText(name.strip())
            self.save_current()

    def load_selected(self):
        try:
            definition = CascadeDefinition.from_dict(
                json.loads(Path(self.get_selected_cascade_path()).read_text(encoding="utf-8"))
            )
        except Exception as exc:
            msg_warning(self, "Načtení kaskády", str(exc))
            return
        self.definition = definition
        self.ed_name.setText(definition.name)
        self.ed_default_out_dir.setText(definition.default_out_dir)
        self.current_step_index = 0 if definition.steps else -1
        self._refresh_step_list()
        self.on_step_selected(self.current_step_index)
