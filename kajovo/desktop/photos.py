"""Photo Studio: profesionální úpravy fotografií výhradně přes Image Edit BATCH."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QSize, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.openai_client import OpenAIClient
from ..core.photo_batch import (
    SUPPORTED_IMAGE_EXTENSIONS,
    download_results,
    image_edit_model_ids,
    load_jobs,
    new_job,
    prepare_and_submit,
    refresh_job,
    response_prompt_models,
    save_job,
)
from ..core.photo_prompt import professionalize_prompt
from ..core.photo_templates import PhotoPromptTemplate, PhotoTemplateStore
from .design import button, card, combo, editor, form, label, row, table, text
from .jobs import Jobs


class TemplateDialog(QDialog):
    def __init__(self, parent=None, template: PhotoPromptTemplate | None = None, prompt: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Šablona promptu")
        self.resize(720, 560)
        layout = QVBoxLayout(self)
        fields = QFormLayout()
        layout.addLayout(fields)
        self.name = QLineEdit(template.name if template else "")
        self.category = QLineEdit(template.category if template else "Vlastní")
        self.description = QLineEdit(template.description if template else "")
        self.prompt = QPlainTextEdit(template.prompt if template else prompt)
        self.prompt.setMinimumHeight(300)
        fields.addRow("Název", self.name)
        fields.addRow("Kategorie", self.category)
        fields.addRow("Popis", self.description)
        fields.addRow("Prompt", self.prompt)
        actions = QHBoxLayout()
        actions.addStretch()
        cancel = QPushButton("Zrušit")
        save = QPushButton("Uložit")
        save.setObjectName("Primary")
        cancel.clicked.connect(self.reject)
        save.clicked.connect(self.accept)
        actions.addWidget(cancel)
        actions.addWidget(save)
        layout.addLayout(actions)

    def values(self):
        return (
            self.name.text().strip(),
            self.prompt.toPlainText().strip(),
            self.description.text().strip(),
            self.category.text().strip(),
        )


class PhotoStudioPanel(QWidget):
    logline = Signal(str)

    def __init__(self, settings, api_key_provider, model_provider, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.api_key_provider = api_key_provider
        self.model_provider = model_provider
        self.jobs = Jobs(self)
        self.template_store = PhotoTemplateStore(Path(settings.cache_dir) / "photo_templates.json")
        self._last_prompt_before = ""
        self._last_human_prompt = ""
        self._last_professional_prompt = ""
        self._prompt_response_id = ""
        self._prompt_model_used = ""
        self._active_template_id = ""
        self._job_records = []
        self._build_ui()
        self.refresh_templates()
        self.refresh_models()
        self.refresh_jobs()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)

        intro, _intro_layout = card(
            "Photo Studio",
            "Hromadná profesionální editace existujících fotografií. Samotné fotografie se upravují výhradně přes BATCH /v1/images/edits; LIVE Responses se používá jen pro volitelné vylepšení textového promptu.",
        )
        root.addWidget(intro)

        toolbar = row(
            button("Přidat fotografie", self.add_photos, "Primary"),
            button("Přidat složku", self.add_folder),
            button("Vybrat vše", self.select_all_photos),
            button("Zrušit výběr", self.clear_photo_selection),
            button("Odebrat vybrané", self.remove_selected_photos),
            button("Odebrat vše", self.clear_photos),
        )
        root.addWidget(toolbar)

        splitter = QSplitter(Qt.Horizontal)
        self.photo_list = QListWidget()
        self.photo_list.setViewMode(QListView.IconMode)
        self.photo_list.setResizeMode(QListView.Adjust)
        self.photo_list.setMovement(QListView.Static)
        self.photo_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.photo_list.setIconSize(QSize(128, 96))
        self.photo_list.setGridSize(QSize(170, 142))
        self.photo_list.setMinimumWidth(360)
        self.photo_list.currentItemChanged.connect(self._update_input_preview)
        self.photo_list.itemSelectionChanged.connect(self._update_photo_count)
        splitter.addWidget(self.photo_list)

        preview_box = QWidget()
        preview_layout = QVBoxLayout(preview_box)
        preview_layout.setContentsMargins(8, 0, 8, 0)
        preview_layout.addWidget(label("Náhled", "", False))
        self.input_preview = QLabel("Vyberte fotografii")
        self.input_preview.setAlignment(Qt.AlignCenter)
        self.input_preview.setMinimumSize(420, 300)
        self.input_preview.setStyleSheet("background:#e9eef4; border:1px solid #c7d2df; border-radius:7px;")
        preview_layout.addWidget(self.input_preview, 1)
        self.photo_count = label("0 fotografií · 0 vybráno", "Hint")
        preview_layout.addWidget(self.photo_count)
        splitter.addWidget(preview_box)

        templates_box = QWidget()
        templates_layout = QVBoxLayout(templates_box)
        templates_layout.setContentsMargins(8, 0, 0, 0)
        templates_layout.addWidget(label("Šablony", "", False))
        self.template_list = QListWidget()
        self.template_list.setMinimumWidth(300)
        self.template_list.itemDoubleClicked.connect(lambda *_: self.use_template())
        templates_layout.addWidget(self.template_list, 1)
        templates_layout.addWidget(
            row(
                button("Použít", self.use_template, "Primary"),
                button("Uložit jako", self.save_template),
            )
        )
        templates_layout.addWidget(
            row(
                button("Upravit", self.edit_template),
                button("Duplikovat", self.duplicate_template),
                button("Smazat", self.delete_template, "Danger"),
            )
        )
        splitter.addWidget(templates_box)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)
        splitter.setStretchFactor(2, 2)
        root.addWidget(splitter, 2)

        prompt_card, pl = card("Zadání úpravy")
        self.prompt_editor = editor(height=190)
        self.prompt_editor.setPlaceholderText(
            "Popište vlastními slovy, jak chcete fotografie upravit. Např.: Zesvětli pokoj, srovnej svislice, rozsviť lampy a odstraň kabel u televize, ale nic nového nepřidávej."
        )
        pl.addWidget(self.prompt_editor)
        self.prompt_model = combo()
        self.btn_professionalize = button("Vylepšit prompt", self.professionalize, "Primary")
        self.btn_professionalize.setToolTip(
            "Převede lidské zadání přes pracovní Responses API na přesný profesionální prompt. Při chybě původní text zůstane beze změny."
        )
        self.btn_undo_prompt = button("Vrátit původní text", self.undo_prompt)
        self.btn_undo_prompt.setEnabled(False)
        prompt_actions = row(
            label("Model pro prompt", "Hint"),
            self.prompt_model,
            self.btn_professionalize,
            self.btn_undo_prompt,
        )
        pl.addWidget(prompt_actions)
        self.prompt_meta = label("", "Hint")
        pl.addWidget(self.prompt_meta)
        root.addWidget(prompt_card)

        config_card, cl = card("Parametry BATCH úpravy")
        cfg = form(cl)
        self.image_model = combo()
        self.image_model.currentTextChanged.connect(self._on_image_model_changed)
        self.quality = combo(["auto", "low", "medium", "high"])
        self.quality.setCurrentText("high")
        self.size = combo(["auto", "1024x1024", "1536x1024", "1024x1536", "2048x1152", "1152x2048", "2048x2048", "4096x2304", "2304x4096"])
        self.output_format = combo(["png", "jpeg", "webp"])
        self.output_format.setCurrentText("png")
        self.output_dir = text(placeholder="Adresář pro upravené fotografie")
        cfg.addRow("Image model", self.image_model)
        cfg.addRow("Kvalita", self.quality)
        cfg.addRow("Velikost", self.size)
        cfg.addRow("Formát", self.output_format)
        cfg.addRow("Výstup", row(self.output_dir, button("Vybrat", self.choose_output_dir)))
        self.btn_submit = button("Odeslat vybrané fotografie do BATCH", self.submit_batch, "Primary")
        cl.addWidget(self.btn_submit)
        root.addWidget(config_card)

        jobs_card, jl = card("Photo BATCH joby")
        self.job_list = QListWidget()
        self.job_list.currentItemChanged.connect(self._job_selected)
        jl.addWidget(self.job_list)
        jl.addWidget(
            row(
                button("Obnovit stav", self.refresh_selected_job),
                button("Stáhnout výsledky", self.download_selected_job, "Primary"),
                button("Zrušit BATCH", self.cancel_selected_job, "Danger"),
                button("Otevřít výstupní složku", self.open_selected_output),
            )
        )
        self.result_table = table(["Fotografie", "Stav", "Výsledek", "Chyba"])
        self.result_table.itemSelectionChanged.connect(self._update_result_preview)
        jl.addWidget(self.result_table)
        result_split = QSplitter(Qt.Horizontal)
        self.result_source_preview = QLabel("Originál")
        self.result_output_preview = QLabel("Výsledek")
        for widget in (self.result_source_preview, self.result_output_preview):
            widget.setAlignment(Qt.AlignCenter)
            widget.setMinimumHeight(240)
            widget.setStyleSheet("background:#e9eef4; border:1px solid #c7d2df; border-radius:7px;")
            result_split.addWidget(widget)
        jl.addWidget(result_split)
        root.addWidget(jobs_card, 2)

    def refresh_models(self):
        available = list(self.model_provider() or [])
        prompt_selected = self.prompt_model.currentText()
        image_selected = self.image_model.currentText()
        prompts = response_prompt_models(available if available else None)
        images = image_edit_model_ids(available if available else None)
        self.prompt_model.clear()
        self.prompt_model.addItems(prompts)
        if prompt_selected in prompts:
            self.prompt_model.setCurrentText(prompt_selected)
        elif "gpt-5.6-luna" in prompts:
            self.prompt_model.setCurrentText("gpt-5.6-luna")
        self.image_model.clear()
        self.image_model.addItems(images)
        if image_selected in images:
            self.image_model.setCurrentText(image_selected)
        elif "gpt-image-2" in images:
            self.image_model.setCurrentText("gpt-image-2")
        self.btn_submit.setEnabled(bool(images))
        self.btn_professionalize.setEnabled(bool(prompts))
        if not images:
            self.image_model.setToolTip("Katalog účtu ani pevná matice nenabízí model s Image Edit BATCH.")
        self._on_image_model_changed()

    def _on_image_model_changed(self, *_):
        model = self.image_model.currentText()
        selected = self.quality.currentText() or "high"
        values = ["auto", "low", "medium", "high"]
        if "2.5" in model:
            values += ["xhigh", "max"]
        self.quality.clear()
        self.quality.addItems(values)
        self.quality.setCurrentText(selected if selected in values else "high")

    def _thumbnail(self, path: str) -> QIcon:
        pixmap = QPixmap(path)
        if pixmap.isNull():
            return QIcon()
        return QIcon(pixmap.scaled(128, 96, Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def _add_paths(self, paths):
        existing = {self.photo_list.item(i).data(Qt.UserRole) for i in range(self.photo_list.count())}
        added = 0
        for raw in paths:
            path = str(Path(raw).expanduser().resolve())
            if path in existing or Path(path).suffix.lower() not in SUPPORTED_IMAGE_EXTENSIONS:
                continue
            if not Path(path).is_file():
                continue
            item = QListWidgetItem(self._thumbnail(path), Path(path).name)
            item.setData(Qt.UserRole, path)
            item.setToolTip(path)
            self.photo_list.addItem(item)
            item.setSelected(True)
            existing.add(path)
            added += 1
        if self.photo_list.count() and not self.photo_list.currentItem():
            self.photo_list.setCurrentRow(0)
        self._update_photo_count()
        if added:
            self.logline.emit(f"Photo Studio: přidáno {added} fotografií.")

    def add_photos(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Přidat fotografie",
            "",
            "Fotografie (*.png *.jpg *.jpeg *.webp)",
        )
        if paths:
            self._add_paths(paths)

    def add_folder(self):
        directory = QFileDialog.getExistingDirectory(self, "Přidat složku fotografií")
        if not directory:
            return
        root = Path(directory)
        paths = [str(path) for path in sorted(root.iterdir()) if path.is_file() and path.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS]
        self._add_paths(paths)

    def select_all_photos(self):
        self.photo_list.selectAll()
        self._update_photo_count()

    def clear_photo_selection(self):
        self.photo_list.clearSelection()
        self._update_photo_count()

    def remove_selected_photos(self):
        for item in list(self.photo_list.selectedItems()):
            self.photo_list.takeItem(self.photo_list.row(item))
        self._update_photo_count()

    def clear_photos(self):
        self.photo_list.clear()
        self.input_preview.setPixmap(QPixmap())
        self.input_preview.setText("Vyberte fotografii")
        self._update_photo_count()

    def _selected_paths(self):
        return [item.data(Qt.UserRole) for item in self.photo_list.selectedItems()]

    def _update_photo_count(self):
        self.photo_count.setText(f"{self.photo_list.count()} fotografií · {len(self.photo_list.selectedItems())} vybráno")

    def _show_image(self, target: QLabel, path: str, placeholder: str):
        pixmap = QPixmap(path) if path else QPixmap()
        if pixmap.isNull():
            target.setPixmap(QPixmap())
            target.setText(placeholder)
            return
        target.setText("")
        target.setPixmap(pixmap.scaled(560, 380, Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def _update_input_preview(self, current, _previous=None):
        self._update_photo_count()
        self._show_image(self.input_preview, current.data(Qt.UserRole) if current else "", "Vyberte fotografii")

    def refresh_templates(self):
        selected_id = self._selected_template_id()
        self.template_list.clear()
        try:
            templates = self.template_store.list()
        except ValueError as exc:
            QMessageBox.warning(self, "Šablony", str(exc))
            return
        for template in templates:
            prefix = "★ " if template.builtin else ""
            item = QListWidgetItem(f"{prefix}{template.name}\n{template.description}")
            item.setData(Qt.UserRole, template.template_id)
            item.setToolTip(template.prompt)
            self.template_list.addItem(item)
            if template.template_id == selected_id:
                self.template_list.setCurrentItem(item)

    def _selected_template_id(self):
        item = self.template_list.currentItem() if hasattr(self, "template_list") else None
        return str(item.data(Qt.UserRole)) if item else ""

    def use_template(self):
        template_id = self._selected_template_id()
        if not template_id:
            return
        template = self.template_store.get(template_id)
        if self.prompt_editor.toPlainText().strip():
            answer = QMessageBox.question(
                self,
                "Použít šablonu",
                "Prompt již obsahuje text. Chcete jej nahradit vybranou šablonou?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return
        self.prompt_editor.setPlainText(template.prompt)
        self._active_template_id = template.template_id
        self.prompt_meta.setText(f"Použita šablona: {template.name}")

    def save_template(self):
        dialog = TemplateDialog(self, prompt=self.prompt_editor.toPlainText())
        if dialog.exec() != QDialog.Accepted:
            return
        try:
            self.template_store.create(*dialog.values())
            self.refresh_templates()
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, "Šablony", str(exc))

    def edit_template(self):
        template_id = self._selected_template_id()
        if not template_id:
            return
        template = self.template_store.get(template_id)
        if template.builtin:
            QMessageBox.information(self, "Šablony", "Vestavěnou šablonu nelze přepsat. Použijte Duplikovat a upravte kopii.")
            return
        dialog = TemplateDialog(self, template=template)
        if dialog.exec() != QDialog.Accepted:
            return
        try:
            name, prompt, description, category = dialog.values()
            self.template_store.update(template_id, name=name, prompt=prompt, description=description, category=category)
            self.refresh_templates()
        except (ValueError, OSError, KeyError) as exc:
            QMessageBox.warning(self, "Šablony", str(exc))

    def duplicate_template(self):
        template_id = self._selected_template_id()
        if not template_id:
            return
        try:
            duplicated = self.template_store.duplicate(template_id)
            self.refresh_templates()
            for index in range(self.template_list.count()):
                item = self.template_list.item(index)
                if item.data(Qt.UserRole) == duplicated.template_id:
                    self.template_list.setCurrentItem(item)
                    break
        except (ValueError, OSError, KeyError) as exc:
            QMessageBox.warning(self, "Šablony", str(exc))

    def delete_template(self):
        template_id = self._selected_template_id()
        if not template_id:
            return
        try:
            template = self.template_store.get(template_id)
            if template.builtin:
                raise ValueError("Vestavěnou šablonu nelze smazat.")
            if QMessageBox.question(self, "Smazat šablonu", f"Opravdu smazat šablonu „{template.name}“?") != QMessageBox.Yes:
                return
            self.template_store.delete(template_id)
            self.refresh_templates()
        except (ValueError, OSError, KeyError) as exc:
            QMessageBox.warning(self, "Šablony", str(exc))

    def professionalize(self):
        original = self.prompt_editor.toPlainText().strip()
        model = self.prompt_model.currentText().strip()
        if not original:
            QMessageBox.warning(self, "Vylepšit prompt", "Nejprve napište zadání úpravy fotografie.")
            return
        if not model:
            QMessageBox.warning(self, "Vylepšit prompt", "Není dostupný vhodný Responses model.")
            return
        key = self.api_key_provider()
        if not key:
            QMessageBox.warning(self, "Vylepšit prompt", "Nejprve nastavte OpenAI API klíč.")
            return
        self.btn_professionalize.setEnabled(False)
        self.prompt_editor.setEnabled(False)

        def operation(job):
            job.status.emit("Připravuji profesionální prompt…")
            job.progress.emit(10)
            result = professionalize_prompt(
                OpenAIClient(key), model, original, reporter=lambda line: job.logline.emit(line)
            )
            job.progress.emit(100)
            job.status.emit("Profesionální prompt vytvořen.")
            return result

        def receive(result):
            self._last_prompt_before = original
            self._last_human_prompt = result.original_prompt
            self._last_professional_prompt = result.professional_prompt
            self._prompt_response_id = result.response_id
            self._prompt_model_used = result.model
            self.prompt_editor.setPlainText(result.professional_prompt)
            self.btn_undo_prompt.setEnabled(True)
            self.prompt_meta.setText(
                f"Prompt vylepšen přes Responses API · {result.model} · response {result.response_id or 'bez ID'}"
            )

        def finished():
            self.prompt_editor.setEnabled(True)
            self.btn_professionalize.setEnabled(bool(self.prompt_model.count()))

        self.jobs.start("Vylepšení promptu", operation, receive, popup=True, on_finished=finished)

    def undo_prompt(self):
        if self._last_prompt_before:
            self.prompt_editor.setPlainText(self._last_prompt_before)
            self.prompt_meta.setText("Vrácen původní text před vylepšením.")
            self.btn_undo_prompt.setEnabled(False)

    def choose_output_dir(self):
        directory = QFileDialog.getExistingDirectory(self, "Vybrat výstupní složku", self.output_dir.text())
        if directory:
            self.output_dir.setText(directory)

    def submit_batch(self):
        paths = self._selected_paths()
        prompt = self.prompt_editor.toPlainText().strip()
        model = self.image_model.currentText().strip()
        output_dir = self.output_dir.text().strip()
        if not paths:
            QMessageBox.warning(self, "Photo BATCH", "Vyberte alespoň jednu fotografii.")
            return
        if not prompt:
            QMessageBox.warning(self, "Photo BATCH", "Prompt pro úpravu je prázdný.")
            return
        if not model:
            QMessageBox.warning(self, "Photo BATCH", "Není dostupný model pro Image Edit BATCH.")
            return
        if not output_dir:
            QMessageBox.warning(self, "Photo BATCH", "Vyberte výstupní složku.")
            return
        key = self.api_key_provider()
        if not key:
            QMessageBox.warning(self, "Photo BATCH", "Nejprve nastavte OpenAI API klíč.")
            return
        summary = (
            f"Fotografie: {len(paths)}\nModel: {model}\nKvalita: {self.quality.currentText()}\n"
            f"Velikost: {self.size.currentText()}\nFormát: {self.output_format.currentText()}\n"
            f"Prompt: {len(prompt)} znaků\nVýstup: {output_dir}\n\n"
            "Fotografie budou upraveny výhradně přes pracovní BATCH /v1/images/edits. Pokračovat?"
        )
        if QMessageBox.question(self, "Odeslat do BATCH", summary, QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        professional = self._last_professional_prompt if self._last_professional_prompt else ""
        human = self._last_human_prompt if self._last_human_prompt else prompt
        if professional:
            source = "professionalized" if prompt == professional else "professionalized_then_edited"
        elif self._active_template_id:
            source = "template"
        else:
            source = "manual"
        try:
            batch_job = new_job(
                source_paths=paths,
                human_prompt=human,
                professional_prompt=professional,
                final_prompt=prompt,
                prompt_source=source,
                template_id=self._active_template_id,
                prompt_model=self._prompt_model_used,
                prompt_response_id=self._prompt_response_id,
                image_model=model,
                quality=self.quality.currentText(),
                size=self.size.currentText(),
                output_format=self.output_format.currentText(),
                output_dir=output_dir,
            )
            save_job(batch_job, self.settings.log_dir)
        except Exception as exc:
            QMessageBox.warning(self, "Photo BATCH", str(exc))
            return
        self.btn_submit.setEnabled(False)

        def operation(worker):
            return prepare_and_submit(
                OpenAIClient(key),
                batch_job,
                self.settings.log_dir,
                reporter=lambda line: worker.logline.emit(line),
                progress=lambda value: worker.progress.emit(value),
            )

        def receive(result):
            self.logline.emit(f"Photo BATCH odeslán: {result.batch_id}")
            self.refresh_jobs(select_job_id=result.job_id)

        def finished():
            self.btn_submit.setEnabled(bool(self.image_model.count()))

        self.jobs.start("Photo BATCH – příprava a odeslání", operation, receive, popup=True, upload=True, on_finished=finished)

    def refresh_jobs(self, select_job_id: str = ""):
        current = select_job_id or self._selected_job_id()
        self._job_records = load_jobs(self.settings.log_dir)
        self.job_list.clear()
        for record in self._job_records:
            item = QListWidgetItem(
                f"{record.created_at[:19].replace('T', ' ')} · {record.status}\n"
                f"{record.batch_id or record.job_id} · {record.request_completed}/{record.request_total} hotovo · {record.request_failed} chyb"
            )
            item.setData(Qt.UserRole, record.job_id)
            self.job_list.addItem(item)
            if record.job_id == current:
                self.job_list.setCurrentItem(item)
        if self.job_list.count() and not self.job_list.currentItem():
            self.job_list.setCurrentRow(0)

    def _selected_job_id(self):
        item = self.job_list.currentItem() if hasattr(self, "job_list") else None
        return str(item.data(Qt.UserRole)) if item else ""

    def _selected_job(self):
        job_id = self._selected_job_id()
        return next((job for job in self._job_records if job.job_id == job_id), None)

    def _job_selected(self, *_):
        job = self._selected_job()
        self.result_table.setRowCount(0)
        if not job:
            return
        self.result_table.setRowCount(len(job.items))
        for row_index, item in enumerate(job.items):
            for col, value in enumerate((item.source_name, item.status, item.output_path, item.error_message)):
                cell = QTableWidgetItem(str(value or ""))
                cell.setData(Qt.UserRole, item.item_id)
                self.result_table.setItem(row_index, col, cell)
        if job.items:
            self.result_table.selectRow(0)

    def refresh_selected_job(self):
        job_record = self._selected_job()
        if not job_record:
            return
        if not job_record.batch_id:
            QMessageBox.information(self, "Photo BATCH", "Tento job ještě nemá batch_id.")
            return
        key = self.api_key_provider()
        if not key:
            QMessageBox.warning(self, "Photo BATCH", "Chybí OpenAI API klíč.")
            return

        def operation(worker):
            worker.status.emit("Načítám stav Photo BATCH…")
            worker.progress.emit(20)
            result = refresh_job(OpenAIClient(key), job_record, self.settings.log_dir)
            worker.progress.emit(100)
            return result

        self.jobs.start(
            "Obnovení Photo BATCH",
            operation,
            lambda result: self.refresh_jobs(select_job_id=result.job_id),
            popup=True,
        )

    def download_selected_job(self):
        job_record = self._selected_job()
        if not job_record:
            return
        key = self.api_key_provider()
        if not key:
            QMessageBox.warning(self, "Photo BATCH", "Chybí OpenAI API klíč.")
            return

        def operation(worker):
            return download_results(
                OpenAIClient(key), job_record, self.settings.log_dir,
                reporter=lambda line: worker.logline.emit(line),
                progress=lambda value: worker.progress.emit(value),
            )

        self.jobs.start(
            "Stažení Photo BATCH",
            operation,
            lambda result: self.refresh_jobs(select_job_id=result.job_id),
            popup=True,
            upload=True,
        )

    def cancel_selected_job(self):
        job_record = self._selected_job()
        if not job_record or not job_record.batch_id:
            return
        if QMessageBox.question(self, "Zrušit Photo BATCH", f"Opravdu zrušit {job_record.batch_id}?") != QMessageBox.Yes:
            return
        key = self.api_key_provider()
        if not key:
            QMessageBox.warning(self, "Photo BATCH", "Chybí OpenAI API klíč.")
            return

        def operation(worker):
            worker.status.emit("Ruším Photo BATCH…")
            client = OpenAIClient(key)
            payload = client.cancel_batch(job_record.batch_id)
            from ..core.photo_batch import apply_batch_status
            apply_batch_status(job_record, payload)
            save_job(job_record, self.settings.log_dir)
            return job_record

        self.jobs.start(
            "Zrušení Photo BATCH",
            operation,
            lambda result: self.refresh_jobs(select_job_id=result.job_id),
            popup=True,
        )

    def open_selected_output(self):
        job = self._selected_job()
        if job and Path(job.output_dir).exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(job.output_dir))

    def _update_result_preview(self):
        job = self._selected_job()
        rows = self.result_table.selectionModel().selectedRows() if self.result_table.selectionModel() else []
        if not job or not rows:
            return
        row_index = rows[0].row()
        if row_index >= len(job.items):
            return
        item = job.items[row_index]
        self._show_image(self.result_source_preview, item.source_path, "Originál není dostupný")
        self._show_image(self.result_output_preview, item.output_path, "Výsledek ještě není stažen")


def install_photo_studio(window):
    """Připojí Photo Studio k existujícímu MainWindow bez duplikace jeho pracovního workflow."""
    if "photos" in window.pages:
        return window.pages["photos"][0]
    panel = PhotoStudioPanel(
        window.s,
        api_key_provider=lambda: window.api_key,
        model_provider=lambda: list(window.all_models),
        parent=window,
    )
    panel.logline.connect(window.log)
    window.photo_panel = panel
    from .design import scroll as make_scroll
    displayed = make_scroll(panel)
    window.pages["photos"] = (
        displayed,
        "Fotografie",
        "Profesionální hromadné úpravy fotografií přes Image Edit BATCH.",
    )
    window.stack.addWidget(displayed)

    run_button = window.navigation.get("run")
    parent = run_button.parentWidget() if run_button else None
    nav_layout = parent.layout() if parent else None
    nav_button = button("Fotografie")
    nav_button.setCheckable(True)

    def open_photos(_checked=False):
        panel.refresh_models()
        panel.refresh_templates()
        panel.refresh_jobs()
        window.select_page("photos")

    nav_button.clicked.connect(open_photos)
    window.navigation["photos"] = nav_button
    if nav_layout is not None and run_button is not None:
        nav_layout.insertWidget(nav_layout.indexOf(run_button) + 1, nav_button)
    return panel
