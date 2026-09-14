"""Galerie a skutečné dávkové úpravy fotografií s editovatelnými šablonami."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QFileDialog, QLabel, QListWidget, QListWidgetItem,
    QPlainTextEdit, QTabWidget, QWidget,
)

from kajovo.core import photo_batch
from kajovo.core.photo_prompt import professionalize_prompt
from kajovo.core.photo_templates import PhotoTemplateStore
from .components import DetailDialog, Form, PathInput, action, actions, caption, confirm, scroll, vertical
from .resources import ValueDialog
from .evidence import VALUES


class PhotoList(QListWidget):
    paths_dropped = Signal(list)

    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setIconSize(QSize(110, 80))
        self.setWordWrap(True)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() and any(url.isLocalFile() for url in event.mimeData().urls()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        self.dragEnterEvent(event)

    def dropEvent(self, event):
        self.paths_dropped.emit([url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()])
        event.acceptProposedAction()


class PhotosPage(QWidget):
    def __init__(self, context, parent=None):
        super().__init__(parent)
        self.context = context
        self.template_store = PhotoTemplateStore(Path(context.settings.cache_dir) / "photo_templates.json")
        self.professional = None
        self.busy = False
        self.jobs = []
        root = vertical(self, 0)
        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)
        gallery = QWidget()
        body = vertical(gallery)
        body.addWidget(caption("Fotografie připravené k úpravě", "section"))
        body.addWidget(caption("Přetáhněte fotografie nebo použijte výběr souborů.", "muted"))
        self.photos = PhotoList()
        self.photos.setObjectName("photos.sources")
        self.photos.setAccessibleName("Vstupní fotografie")
        self.photos.paths_dropped.connect(self.add_paths)
        self.photos.currentItemChanged.connect(self.preview_source)
        self.photos.itemSelectionChanged.connect(self.update_count)
        body.addWidget(self.photos, 1)
        body.addWidget(actions(action("photos.add", "Přidat fotografie", self.pick_files),
                               action("photos.folder", "Přidat složku", self.pick_folder),
                               action("photos.remove", "Odebrat vybrané", self.remove)))
        body.addWidget(actions(action("photos.select.all", "Vybrat všechny", self.photos.selectAll),
                               action("photos.select.none", "Zrušit výběr", self.photos.clearSelection),
                               action("photos.clear", "Vyprázdnit seznam", self.photos.clear)))
        self.preview = QLabel()
        self.preview.setAccessibleName("Náhled původní fotografie")
        self.preview.setMinimumHeight(180)
        self.preview.setAlignment(Qt.AlignCenter)
        body.addWidget(self.preview)
        self.tabs.addTab(gallery, "Fotografie")
        prompt_page = QWidget()
        body = vertical(prompt_page)
        self.form = Form()
        self.template = self.form.choice("photos.template", "Šablona zadání", [])
        self.prompt_model = self.form.choice("photos.prompt_model", "Model pro úpravu zadání", [])
        body.addWidget(self.form)
        self.prompt = QPlainTextEdit()
        self.prompt.setAccessibleName("Zadání úprav fotografií")
        self.prompt.setMinimumHeight(220)
        body.addWidget(self.prompt, 1)
        body.addWidget(actions(action("photos.template.use", "Použít šablonu", self.use_template),
                               action("photos.prompt.improve", "Vylepšit zadání", self.improve),
                               action("photos.prompt.restore", "Vrátit původní zadání", self.restore_prompt)))
        body.addWidget(actions(action("photos.template.save", "Uložit novou šablonu", self.save_template),
                               action("photos.template.update", "Upravit šablonu", self.update_template),
                               action("photos.template.copy", "Duplikovat šablonu", self.duplicate_template),
                               action("photos.template.delete", "Odstranit šablonu", self.delete_template, "danger")))
        self.tabs.addTab(scroll(prompt_page), "Zadání a šablony")
        parameters = QWidget()
        body = vertical(parameters)
        self.options = Form()
        self.image_model = self.options.choice("photos.image_model", "Model pro fotografie", [])
        self.quality = self.options.choice("photos.quality", "Kvalita", [("Automatická", "auto"), ("Základní", "low"), ("Střední", "medium"), ("Vysoká", "high")], "high")
        self.size = self.options.choice("photos.size", "Rozměry", ["auto", "1024x1024", "1536x1024", "1024x1536"])
        self.format = self.options.choice("photos.format", "Formát souboru", ["png", "jpeg", "webp"])
        self.image_model.currentIndexChanged.connect(self.refresh_image_options)
        self.output = self.options.add("photos.output", "Výstupní adresář", PathInput(directories=True))
        body.addWidget(self.options)
        body.addWidget(action("photos.output.browse", "Vybrat výstupní adresář", self.pick_output))
        body.addStretch()
        self.tabs.addTab(scroll(parameters), "Výstup")
        jobs = QWidget()
        body = vertical(jobs)
        self.job_list = QListWidget()
        self.job_list.setWordWrap(True)
        body.addWidget(self.job_list, 1)
        body.addWidget(actions(action("photos.jobs.list", "Načíst uložené úlohy", self.load_jobs),
                               action("photos.jobs.refresh", "Ověřit stav ve službě", self.refresh_job),
                               action("photos.jobs.download", "Převzít výsledky", self.download),
                               action("photos.jobs.cancel", "Zrušit dávku", self.cancel_job, "danger")))
        body.addWidget(actions(action("photos.jobs.details", "Podrobnosti a výsledky", self.job_details),
                               action("photos.jobs.compare", "Porovnat fotografie", self.compare),
                               action("photos.jobs.output", "Otevřít výstupní složku", self.open_output)))
        self.tabs.addTab(jobs, "Průběh úprav")
        self.notice = caption("Vlastní úpravy se odesílají jako placená dávka fotografií.", "muted")
        root.addWidget(self.notice)
        self.start_button = action("photos.start", "Odeslat úpravy fotografií", self.start, "primary")
        root.addWidget(actions(self.start_button))
        context.models_changed.connect(self.refresh_models)
        self.refresh_templates()

    def add_paths(self, paths):
        existing = {self.photos.item(i).data(Qt.UserRole) for i in range(self.photos.count())}
        errors = []
        for value in paths:
            try:
                path = str(photo_batch.validate_source_image(value))
                if path in existing:
                    continue
                item = QListWidgetItem(QIcon(path), Path(path).name)
                item.setData(Qt.UserRole, path)
                item.setToolTip(path)
                self.photos.addItem(item)
                item.setSelected(True)
                existing.add(path)
            except (ValueError, OSError) as error:
                errors.append(str(error))
        self.notice.setText("\n".join(errors) if errors else f"Připraveno fotografií: {len(existing)}")

    def update_count(self):
        self.notice.setText(f"Fotografií v seznamu: {self.photos.count()} · vybráno k úpravě: {len(self.photos.selectedItems())}")

    def pick_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Přidat fotografie", "", "Fotografie (*.png *.jpg *.jpeg *.webp)")
        self.add_paths(paths)

    def pick_folder(self):
        path = QFileDialog.getExistingDirectory(self, "Vybrat složku fotografií")
        if path:
            self.add_paths([str(p) for p in Path(path).iterdir() if p.suffix.lower() in photo_batch.SUPPORTED_IMAGE_EXTENSIONS])

    def remove(self):
        for item in self.photos.selectedItems():
            self.photos.takeItem(self.photos.row(item))

    def preview_source(self, item, previous=None):
        pixmap = QPixmap(item.data(Qt.UserRole)) if item else QPixmap()
        self.preview.setPixmap(pixmap.scaled(420, 240, Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def pick_output(self):
        path = QFileDialog.getExistingDirectory(self, "Výstupní adresář")
        if path:
            self.output.setText(path)

    def refresh_models(self):
        for widget, values in ((self.image_model, photo_batch.image_edit_model_ids(self.context.models)),
                               (self.prompt_model, photo_batch.response_prompt_models(self.context.models))):
            selected = widget.currentData() or (widget.currentText().strip() if widget.isEditable() else None)
            widget.clear()
            for value in values if self.context.models else []:
                widget.addItem(value, value)
            if selected and widget.findData(selected) < 0:
                widget.addItem(str(selected) + " · nedostupný", selected)
            if selected:
                widget.setCurrentIndex(widget.findData(selected))
        self.refresh_image_options()

    def refresh_image_options(self):
        model = self.image_model.currentData()
        if not model:
            return
        try:
            options = photo_batch.image_edit_options(model)
        except ValueError:
            return
        labels = {"auto": "Automatická", "low": "Základní", "medium": "Střední", "high": "Vysoká", "xhigh": "Velmi vysoká", "max": "Maximální"}
        for widget, values in ((self.quality, options["quality"]), (self.size, options["sizes"])):
            selected = widget.currentData() or (widget.currentText().strip() if widget.isEditable() else None)
            widget.blockSignals(True)
            widget.clear()
            for value in values:
                widget.addItem(labels.get(value, value) if widget is self.quality else value, value)
            if selected and widget.findData(selected) < 0:
                widget.addItem(str(selected) + " · nepodporované", selected)
            widget.setCurrentIndex(max(0, widget.findData(selected)))
            widget.blockSignals(False)
        self.size.setEditable(options["custom_size"])

    def refresh_templates(self):
        selected = self.template.currentData()
        self.template.clear()
        for template in self.template_store.list():
            self.template.addItem(template.name, template.template_id)
        if selected:
            self.template.setCurrentIndex(self.template.findData(selected))

    def use_template(self):
        if self.template.currentData():
            self.prompt.setPlainText(self.template_store.get(self.template.currentData()).prompt)

    def save_template(self):
        dialog = ValueDialog("Nová šablona", "Název šablony", parent=self)
        if dialog.exec() == QDialog.Accepted:
            try:
                self.template_store.create(dialog.value, self.prompt.toPlainText())
                self.refresh_templates()
            except (ValueError, OSError) as error:
                self.notice.setText(str(error))

    def update_template(self):
        try:
            template = self.template_store.get(self.template.currentData())
            import json
            value = {"name": template.name, "description": template.description, "category": template.category, "prompt": self.prompt.toPlainText()}
            dialog = ValueDialog("Upravit šablonu", "Název, popis, kategorie a zadání šablony", json.dumps(value, ensure_ascii=False, indent=2), self, structured=True)
            if dialog.exec() != QDialog.Accepted:
                return
            value = dialog.value
            if not isinstance(value, dict) or set(value) != {"name", "description", "category", "prompt"} or not all(isinstance(item, str) for item in value.values()):
                raise ValueError("Vyplňte název, popis, kategorii a zadání jako textová pole.")
            self.template_store.update(template.template_id, **value)
            self.refresh_templates()
        except (ValueError, OSError, KeyError) as error:
            self.notice.setText(str(error))

    def duplicate_template(self):
        try:
            self.template_store.duplicate(self.template.currentData())
            self.refresh_templates()
        except (ValueError, OSError, KeyError) as error:
            self.notice.setText(str(error))

    def delete_template(self):
        if confirm(self, "Odstranit šablonu", "Odstranit vybranou vlastní šablonu?"):
            try:
                self.template_store.delete(self.template.currentData())
                self.refresh_templates()
            except (ValueError, OSError, KeyError) as error:
                self.notice.setText(str(error))

    def execute(self, title, function, receive=None, output_dir=None):
        if self.busy:
            return
        try:
            client = self.context.client()
            self.context.operations.assert_output_available(output_dir)
        except ValueError as error:
            self.notice.setText(str(error))
            return
        self.busy = True
        self.start_button.setEnabled(False)
        record = self.context.operations.start(title, lambda task: function(client, task), receive, output_dir=output_dir)

        def release():
            self.busy = False
            self.start_button.setEnabled(True)

        record.worker.finished.connect(release)
        return record

    def improve(self):
        prompt = self.prompt.toPlainText()
        model = self.prompt_model.currentData()
        if not prompt.strip() or model not in self.context.models:
            self.notice.setText("Vyplňte zadání a vyberte dostupný textový model.")
            return

        def receive(value):
            self.professional = value
            self.prompt.setPlainText(value.professional_prompt)

        self.execute("Vylepšení zadání fotografie", lambda client, task: professionalize_prompt(client, model, prompt, task.logline.emit), receive)

    def restore_prompt(self):
        if self.professional:
            self.prompt.setPlainText(self.professional.original_prompt)

    def start(self):
        paths = [item.data(Qt.UserRole) for item in self.photos.selectedItems()]
        model = self.image_model.currentData()
        prompt = self.prompt.toPlainText()
        output = self.output.text().strip()
        if not paths or not prompt.strip() or not output or model not in self.context.models:
            self.notice.setText("Vyberte fotografie, dostupný model, zadání a výstupní adresář.")
            return
        options = dict(source_paths=paths, human_prompt=self.professional.original_prompt if self.professional else prompt,
                       professional_prompt=self.professional.professional_prompt if self.professional else "",
                       final_prompt=prompt, prompt_source="professional" if self.professional else "human",
                       template_id=self.template.currentData() or "", prompt_model=self.professional.model if self.professional else "",
                       prompt_response_id=self.professional.response_id if self.professional else "",
                       image_model=model, quality=self.quality.currentData(), size=self.size.currentData() or self.size.currentText().strip(),
                       output_format=self.format.currentData(), output_dir=output)

        def submit(client, task):
            job = photo_batch.new_job(**options)
            return photo_batch.prepare_and_submit(client, job, self.context.settings.log_dir, reporter=task.logline.emit)

        self.execute("Dávkové úpravy fotografií", submit, lambda job: self.load_jobs())

    def load_jobs(self):
        self.jobs = photo_batch.load_jobs(self.context.settings.log_dir)
        self.job_list.clear()
        for job in self.jobs:
            item = QListWidgetItem(f"{job.created_at} · {VALUES.get(job.status, 'Stav není rozpoznaný')}\n{job.request_completed} z {job.request_total} fotografií · {job.job_id}")
            item.setData(Qt.UserRole, job)
            self.job_list.addItem(item)

    def selected_job(self):
        item = self.job_list.currentItem()
        return item.data(Qt.UserRole) if item else None

    def refresh_job(self):
        job = self.selected_job()
        if job:
            self.execute("Ověření dávky fotografií", lambda client, task: photo_batch.refresh_job(client, job, self.context.settings.log_dir), lambda value: self.load_jobs())

    def download(self):
        job = self.selected_job()
        if job:
            self.execute("Převzetí upravených fotografií", lambda client, task: photo_batch.download_results(client, job, self.context.settings.log_dir, reporter=task.logline.emit), lambda value: self.load_jobs(), output_dir=job.output_dir)

    def cancel_job(self):
        job = self.selected_job()
        if job and job.batch_id and confirm(self, "Zrušit dávku fotografií", "Požádat službu o zrušení dávky? Již zpracované položky mohou být účtované."):
            self.execute("Zrušení dávky fotografií", lambda client, task: client.cancel_batch(job.batch_id), lambda value: self.load_jobs())

    def job_details(self):
        job = self.selected_job()
        if job:
            DetailDialog("Výsledky fotografií", f"Zpracováno {job.request_completed} z {job.request_total}; chyb {job.request_failed}.", self, asdict(job)).exec()

    def open_output(self):
        job = self.selected_job()
        if job:
            QDesktopServices.openUrl(QUrl.fromLocalFile(job.output_dir))

    def compare(self):
        job = self.selected_job()
        if not job:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Původní a upravené fotografie")
        dialog.resize(760, 700)
        root = vertical(dialog)
        choice = Form()
        selection = choice.choice("photos.compare.item", "Fotografie", [(item.source_name, index) for index, item in enumerate(job.items)])
        root.addWidget(choice)
        contents = QWidget()
        body = vertical(contents)
        labels = []
        for title in ("Původní fotografie", "Upravená fotografie"):
            body.addWidget(caption(title, "section"))
            label = QLabel()
            label.setAlignment(Qt.AlignCenter)
            label.setMinimumHeight(220)
            body.addWidget(label)
            labels.append(label)
        root.addWidget(scroll(contents), 1)
        root.addWidget(action("photos.compare.close", "Zavřít porovnání", dialog.accept))

        def render():
            index = selection.currentData()
            if index is None:
                return
            item = job.items[index]
            for label, path in zip(labels, (item.source_path, item.output_path), strict=True):
                pixmap = QPixmap(path)
                if pixmap.isNull():
                    label.setText("Fotografie zatím není dostupná.")
                else:
                    label.setPixmap(pixmap.scaled(500, 260, Qt.KeepAspectRatio, Qt.SmoothTransformation))

        selection.currentIndexChanged.connect(render)
        render()
        bounds = self.screen().availableGeometry()
        dialog.resize(min(760, bounds.width()), min(700, bounds.height()))
        dialog.exec()
