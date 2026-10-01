"""Galerie a skutečné dávkové úpravy fotografií s editovatelnými šablonami."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QListWidget, QListWidgetItem,
    QPlainTextEdit, QTabWidget, QWidget,
)

from kajovo.core import photo_batch
from kajovo.core.filesystem_metadata import is_appledouble_metadata
from kajovo.core.photo_prompt import manual_photo_plan, professionalize_prompt
from kajovo.core.photo_templates import PhotoTemplateStore
from .components import Form, PathInput, action, actions, caption, confirm, friendly_error, scroll, vertical
from .resources import ValueDialog
from .evidence import VALUES
from .file_dialogs import get_existing_directory, get_open_file_names
from .job_cards import build_job_card, clear_cards, format_started, photo_failure_summary


class PhotoList(QListWidget):
    def __init__(self):
        super().__init__()
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setViewMode(QListWidget.IconMode)
        self.setIconSize(QSize(132, 100))
        self.setGridSize(QSize(176, 148))
        self.setResizeMode(QListWidget.Adjust)
        self.setMovement(QListWidget.Static)
        self.setWordWrap(True)


class PhotosPage(QWidget):
    def __init__(self, context, parent=None):
        super().__init__(parent)
        self.context = context
        self.template_store = PhotoTemplateStore(Path(context.settings.cache_dir) / "photo_templates.json")
        self.professional = None
        self.pending_professional = None
        self._revision = 0
        self.busy = False
        self._context_generation = 0
        self.jobs = []
        self._jobs_layout = None
        self._job_buttons = []
        root = vertical(self, 0)
        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)
        gallery = QWidget()
        body = vertical(gallery)
        body.addWidget(caption("Fotografie připravené k úpravě", "section"))
        body.addWidget(caption("Každá přidaná fotografie projde úpravou.", "muted"))
        self.photos = PhotoList()
        self.photos.setObjectName("photos.sources")
        self.photos.setAccessibleName("Vstupní fotografie")
        body.addWidget(self.photos, 1)
        body.addWidget(actions(action("photos.folder", "Přidat fotografie ze složky", self.pick_folder),
                               action("photos.add", "Přidat fotografie", self.pick_files),
                               action("photos.remove", "Odebrat fotografie ze seznamu", self.remove, "danger")))
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
        body.addWidget(actions(action("photos.prompt.improve", "Vylepšit zadání", self.improve),
                               action("photos.prompt.restore", "Vrátit původní zadání", self.restore_prompt)))
        self.pending_preview = QPlainTextEdit()
        self.pending_preview.setReadOnly(True)
        self.pending_preview.setAccessibleName("Odložený návrh zadání")
        body.addWidget(self.pending_preview)
        self.pending_use = action("photos.prompt.pending.use", "Použít odložený návrh", self.use_pending)
        self.pending_discard = action("photos.prompt.pending.discard", "Zahodit odložený návrh", self.discard_pending)
        body.addWidget(actions(self.pending_use, self.pending_discard))
        self._show_pending()
        self.template_save = action("photos.template.save", "Uložit šablonu", self.save_template)
        self.template_update = action("photos.template.update", "Upravit vybranou šablonu", self.update_template)
        self.template_copy = action("photos.template.copy", "Vytvořit kopii šablony", self.duplicate_template)
        self.template_delete = action("photos.template.delete", "Odstranit šablonu", self.delete_template, "danger")
        body.addWidget(actions(self.template_save, self.template_update, self.template_copy, self.template_delete))
        self.tabs.addTab(scroll(prompt_page), "Zadání a šablony")
        parameters = QWidget()
        body = vertical(parameters)
        self.options = Form()
        self.image_model = self.options.choice("photos.image_model", "Model pro fotografie", [])
        self.quality = self.options.choice("photos.quality", "Kvalita", [("Automatická", "auto"), ("Základní", "low"), ("Střední", "medium"), ("Vysoká", "high")], "high")
        self.size = self.options.choice("photos.size", "Velikost obrázku", [("Automaticky", "auto"), ("Čtverec · 1024 × 1024 bodů", "1024x1024"), ("Na šířku · 1536 × 1024 bodů", "1536x1024"), ("Na výšku · 1024 × 1536 bodů", "1024x1536")])
        self.format = self.options.choice("photos.format", "Druh souboru", [("Obrázek PNG (.png)", "png"), ("Fotografie JPEG (.jpeg)", "jpeg"), ("Obrázek WebP (.webp)", "webp")])
        self.image_model.currentIndexChanged.connect(self.refresh_image_options)
        self.output = self.options.add("photos.output", "Složka pro upravené fotografie", PathInput(directories=True))
        body.addWidget(self.options)
        body.addWidget(action("photos.output.browse", "Vybrat složku pro fotografie", self.pick_output))
        body.addStretch()
        self.tabs.addTab(scroll(parameters), "Výstup")
        jobs = QWidget()
        body = vertical(jobs)
        body.addWidget(caption("Průběh úprav fotografií", "section"))
        body.addWidget(actions(action("photos.jobs.refresh", "Obnovit", self.refresh_jobs)))
        cards = QWidget()
        self._jobs_layout = vertical(cards, 0)
        self._jobs_layout.addStretch()
        body.addWidget(scroll(cards), 1)
        self.tabs.addTab(scroll(jobs), "Průběh úprav")
        self.notice = caption("Vlastní úpravy se odesílají jako placená dávka fotografií.", "muted")
        root.addWidget(self.notice)
        self.start_button = action("photos.start", "Odeslat úpravy fotografií", self.start, "primary")
        root.addWidget(actions(self.start_button))
        context.models_changed.connect(self.refresh_models)
        context.key_changed.connect(self._reset_context)
        context.settings_changed.connect(self._reset_context)
        self.prompt.textChanged.connect(self._edited)
        self.prompt.textChanged.connect(self._update_template_actions)
        self.template.currentIndexChanged.connect(self.template_changed)
        self.form.changed.connect(self._edited)
        self.options.changed.connect(self._edited)
        self.refresh_templates()
        self.refresh_models()

    def _reset_context(self):
        self._context_generation += 1
        self.jobs = []
        self._render_jobs()

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
                existing.add(path)
            except (ValueError, OSError) as error:
                from kajovo.core.user_errors import describe_error

                errors.append(describe_error(error, operation="Přidání fotografie").message)
        self.notice.setText("\n".join(errors) if errors else f"Fotografií připravených k úpravě: {len(existing)}")

    def pick_files(self):
        paths, _ = get_open_file_names(self, "Vložit fotografie", "", "Fotografie (*.png *.jpg *.jpeg *.webp)")
        self.add_paths(paths)

    def pick_folder(self):
        path = get_existing_directory(self, "Vložit adresář fotografií")
        if path:
            candidates = (
                candidate
                for candidate in Path(path).rglob("*")
                if not candidate.is_symlink()
                and candidate.is_file()
                and candidate.suffix.lower() in photo_batch.SUPPORTED_IMAGE_EXTENSIONS
                and not is_appledouble_metadata(candidate)
            )
            self.add_paths([str(candidate) for candidate in candidates])

    def remove(self):
        for item in self.photos.selectedItems():
            self.photos.takeItem(self.photos.row(item))

    def pick_output(self):
        path = get_existing_directory(self, "Výstupní adresář", self.output.text())
        if path:
            self.output.setText(path)

    def refresh_models(self, *_):
        for widget, usage in (
            (self.image_model, "photo_edit_batch"),
            (self.prompt_model, "photo_prompt"),
        ):
            values = self.context.models_for_usage(usage)
            recommended = self.context.recommended_model(usage)
            from .model_selection import refill_models

            refill_models(widget, values, recommended)
        self.refresh_image_options()
        if self.context.models and not self.image_model.currentData():
            self.notice.setText("Pro tento účet není dostupný model pro dávkovou úpravu fotografií. Obnovte seznam modelů nebo ověřte přístup ke službě.")

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
                from .presentation import image_size_name
                widget.addItem(labels.get(value, value) if widget is self.quality else image_size_name(value), value)
            if selected and widget.findData(selected) < 0:
                widget.addItem(str(selected) + " · nepodporované", selected)
            widget.setCurrentIndex(max(0, widget.findData(selected)))
            widget.blockSignals(False)
        self.size.setEditable(options["custom_size"])

    def refresh_templates(self):
        selected = self.template.currentData()
        self.template.blockSignals(True)
        self.template.clear()
        self.template.addItem("Bez šablony", "")
        for template in self.template_store.list():
            self.template.addItem(template.name, template.template_id)
        if selected:
            index = self.template.findData(selected)
            self.template.setCurrentIndex(index if index >= 0 else 0)
        else:
            self.template.setCurrentIndex(0)
        self.template.blockSignals(False)
        self._update_template_actions()

    def template_changed(self, *_):
        identifier = self.template.currentData()
        if not identifier:
            self._update_template_actions()
            return
        try:
            template = self.template_store.get(identifier)
            self.professional = None
            self.discard_pending()
            self.prompt.setPlainText(template.prompt)
        except (ValueError, OSError, KeyError) as error:
            self.notice.setText(friendly_error(error, "Načtení šablony"))
        self._update_template_actions()

    def _update_template_actions(self, *_):
        identifier = self.template.currentData()
        try:
            template = self.template_store.get(identifier) if identifier else None
        except (ValueError, OSError, KeyError):
            template = None
        self.template_save.setEnabled(bool(self.prompt.toPlainText().strip()))
        self.template_update.setEnabled(bool(template and not template.builtin))
        self.template_copy.setEnabled(template is not None)
        self.template_delete.setEnabled(bool(template and not template.builtin))

    def save_template(self):
        dialog = ValueDialog("Uložit šablonu", "Povinný název šablony", parent=self)
        if dialog.exec() == QDialog.Accepted:
            try:
                template = self.template_store.create(dialog.value, self.prompt.toPlainText())
                self.refresh_templates(template.template_id)
            except (ValueError, OSError) as error:
                self.notice.setText(friendly_error(error, "Uložení šablony"))

    def update_template(self):
        try:
            identifier = self.template.currentData()
            if not identifier:
                return
            template = self.template_store.get(identifier)
            self.template_store.update(
                template.template_id,
                name=template.name,
                description=template.description,
                category=template.category,
                prompt=self.prompt.toPlainText(),
            )
            self.notice.setText("Vybraná šablona byla upravena.")
        except (ValueError, OSError, KeyError) as error:
            self.notice.setText(friendly_error(error, "Úprava šablony"))

    def duplicate_template(self):
        identifier = self.template.currentData()
        if not identifier:
            return
        dialog = ValueDialog("Vytvořit kopii šablony", "Povinný název nové šablony", parent=self)
        if dialog.exec() != QDialog.Accepted:
            return
        try:
            template = self.template_store.duplicate(identifier, dialog.value)
            self.refresh_templates(template.template_id)
        except (ValueError, OSError, KeyError) as error:
            self.notice.setText(friendly_error(error, "Duplikace šablony"))

    def delete_template(self):
        identifier = self.template.currentData()
        if not identifier:
            return
        if confirm(self, "Odstranit šablonu", "Odstranit vybranou vlastní šablonu?"):
            try:
                self.template_store.delete(identifier)
                self.refresh_templates()
            except (ValueError, OSError, KeyError) as error:
                self.notice.setText(friendly_error(error, "Odstranění šablony"))

    def execute(self, title, function, receive=None, output_dir=None, popup=True, identifier=None):
        if self.busy:
            return
        try:
            client = self.context.client()
            self.context.operations.assert_output_available(output_dir)
        except ValueError as error:
            self.notice.setText(friendly_error(error))
            return
        self.busy = True
        self.start_button.setEnabled(False)
        key = self.context.api_key
        log_dir = self.context.settings.log_dir
        generation = self._context_generation

        def accept(value):
            if (receive and generation == self._context_generation
                    and key == self.context.api_key and log_dir == self.context.settings.log_dir):
                receive(value)

        record = self.context.operations.start(
            title,
            lambda task: function(client, task),
            accept if receive else None,
            output_dir=output_dir,
            popup=popup,
            identifier=identifier,
        )

        def release():
            self.busy = False
            self.start_button.setEnabled(True)

        self.context.operations.on_finished(record, release)
        return record

    def improve(self):
        if self.pending_professional is not None:
            self.notice.setText("Nejprve použijte nebo zahoďte odložený návrh.")
            return
        if not self.context.models:
            self.context.ensure_models()
            self.notice.setText("Načítám katalog modelů účtu.")
            return
        prompt = self.prompt.toPlainText()
        model = self.prompt_model.currentData()
        if not prompt.strip() or model not in self.context.models:
            self.notice.setText("Vyplňte zadání a vyberte dostupný textový model.")
            return

        request_prompt = prompt
        revision = self._revision
        log_dir = self.context.settings.log_dir

        def receive(value):
            # Asynchronní výsledek smí změnit editor pouze tehdy, pokud
            # uživatel mezitím nezměnil vstup. Jinak zůstává jen návrhem.
            if self._revision != revision or self.prompt.toPlainText() != request_prompt:
                self.pending_professional = value
                self._show_pending()
                self.notice.setText(
                    "Vylepšený návrh je připraven, ale nebyl aplikován, "
                    "protože zadání bylo mezitím změněno."
                )
                return
            self.professional = value
            self.pending_professional = None
            self._show_pending()
            self.prompt.setPlainText(value.professional_prompt)

        self.execute(
            "Vylepšení zadání fotografie",
            lambda client, task: professionalize_prompt(
                client,
                model,
                request_prompt,
                log_dir,
                task.logline.emit,
            ),
            receive,
        )

    def _edited(self, *_):
        self._revision += 1

    def _show_pending(self):
        value = self.pending_professional
        self.pending_preview.setPlainText(value.professional_prompt if value else "")
        for widget in (self.pending_preview, self.pending_use, self.pending_discard):
            widget.setVisible(value is not None)

    def use_pending(self):
        if self.pending_professional is not None:
            self.professional = self.pending_professional
            self.prompt.setPlainText(self.professional.professional_prompt)
            self.discard_pending()

    def discard_pending(self):
        self.pending_professional = None
        self._show_pending()

    def restore_prompt(self):
        if self.professional:
            original = self.professional.original_prompt
            self.professional = None
            self.prompt.setPlainText(original)

    def start(self):
        if not self.context.models:
            self.context.ensure_models()
            self.notice.setText("Načítám katalog modelů účtu.")
            return
        paths = [self.photos.item(index).data(Qt.UserRole) for index in range(self.photos.count())]
        model = self.image_model.currentData()
        prompt = self.prompt.toPlainText()
        output = self.output.text().strip()
        if not paths or not prompt.strip() or model not in self.context.models:
            self.notice.setText("Přidejte fotografie, napište zadání a vyberte dostupný model.")
            return
        professional = (
            self.professional
            if self.professional
            and prompt.strip() == self.professional.professional_prompt.strip()
            else None
        )
        options = dict(
            source_paths=paths,
            human_prompt=professional.original_prompt if professional else prompt,
            professional_prompt=professional.professional_prompt if professional else "",
            final_prompt=prompt,
            prompt_source="professional" if professional else "human",
            template_id=self.template.currentData() or "",
            prompt_model=professional.model if professional else "",
            prompt_response_id=professional.response_id if professional else "",
            image_model=model,
            quality=self.quality.currentData(),
            size=self.size.currentData() or self.size.currentText().strip(),
            output_format=self.format.currentData(),
            output_dir=output,
            photo_plan=(
                professional.photo_plan
                if professional
                else manual_photo_plan(prompt)
            ),
        )

        log_dir = self.context.settings.log_dir

        def submit(client, task):
            job = photo_batch.new_job(**options)
            return photo_batch.prepare_and_submit(
                client,
                job,
                log_dir,
                reporter=task.logline.emit,
                progress=task.progress_event.emit,
            )

        self.execute("Dávkové úpravy fotografií", submit, lambda job: self.page_activated())

    def load_jobs(self):
        errors = []
        self.jobs = photo_batch.load_jobs(self.context.settings.log_dir, errors=errors)
        self._render_jobs()
        if errors:
            self.notice.setText("\n".join(errors))

    def page_activated(self):
        try:
            self.load_jobs()
        except (OSError, ValueError) as error:
            self.notice.setText(friendly_error(error, "Načtení fotografických dávek"))
            return
        if self.context.api_key:
            self.refresh_jobs(automatic=True)

    def refresh_jobs(self, checked=False, automatic=False):
        key = self.context.api_key
        log_dir = self.context.settings.log_dir

        def refresh(client, task):
            remote = {row["id"]: row for row in client.list_batches() if row.get("id")}
            markers = photo_batch.deleted_job_markers(log_dir)
            hidden_ids = {row["batch_id"] for row in markers if row["batch_id"]}
            hidden_files = {row["input_file_id"] for row in markers if row["input_file_id"]}
            errors = []
            for original in photo_batch.load_jobs(log_dir, errors=errors):
                if original.batch_id in hidden_ids or original.input_file_id in hidden_files:
                    continue
                # Neúplná příprava není neurčitý submit. Ani jedna vadná úloha
                # nesmí zabránit převzetí stavů ostatních nezávislých dávek.
                job = deepcopy(original)
                try:
                    if job.batch_id and job.batch_id in remote:
                        photo_batch.refresh_job(client, job, log_dir, batch=remote[job.batch_id])
                    elif not job.batch_id and job.input_file_id and job.status == "submission_unknown":
                        photo_batch.refresh_job(client, job, log_dir, progress=task.progress_event.emit)
                except Exception as error:
                    message = f"Dávka {job.job_id}: {friendly_error(error, 'Obnovení stavu')}"
                    errors.append(message)
                    task.logline.emit(message)
            jobs = photo_batch.load_jobs(log_dir, errors=errors)
            return jobs, hidden_ids, hidden_files, list(dict.fromkeys(errors))

        def receive(result):
            if key != self.context.api_key:
                return
            jobs, hidden_ids, hidden_files, errors = result
            self.jobs = [
                job for job in jobs
                if job.batch_id not in hidden_ids and job.input_file_id not in hidden_files
            ]
            self._render_jobs()
            self.notice.setText(
                "Část dávek se nepodařilo obnovit. Ostatní výsledky zůstávají dostupné.\n"
                + "\n".join(errors)
                if errors else "Přehled fotografických dávek je aktualizovaný."
            )

        self.execute(
            "Obnovení přehledu fotografických dávek",
            refresh,
            receive,
            popup=not automatic,
            identifier="photos.jobs.refresh",
        )

    def _render_jobs(self):
        clear_cards(self._jobs_layout)
        self._jobs_layout.addStretch()
        self._job_buttons = []
        for job in self.jobs:
            status = VALUES.get(job.status, "Stav není rozpoznán")
            downloaded = sum(item.status == "downloaded" for item in job.items)
            failed = sum(item.status == "failed" for item in job.items)
            summary = f"{job.request_total} fotografií · {downloaded} uloženo · {failed} se nepodařilo"
            reason = photo_failure_summary(job)
            if reason:
                summary += f" · {reason}"
            can_save = bool(job.output_file_id or job.error_file_id) and job.status in {
                "completed", "failed", "expired", "cancelled", "downloaded", "partial"
            }
            card, buttons = build_job_card(
                "Úpravy fotografií", status, format_started(job.created_at), summary,
                [
                    ("photos.jobs.save", "Uložit výsledné fotografie do adresáře", lambda _=False, current=job: self.download_job(current), "primary", can_save),
                    ("photos.jobs.delete", "Odebrat z přehledu", lambda _=False, current=job: self.delete_job(current), "danger", True),
                ],
                f"photos.job.{job.job_id}",
            )
            self._jobs_layout.insertWidget(max(0, self._jobs_layout.count() - 1), card)
            self._job_buttons.extend(buttons)
        if not self.jobs:
            self._jobs_layout.insertWidget(0, caption("Zatím nebyla spuštěna žádná úprava fotografií.", "muted"))

    def download_job(self, job):
        directory = job.output_dir or job.saved_output_dir or self.output.text().strip()
        if not directory:
            directory = get_existing_directory(self, "Uložit výsledné fotografie do adresáře")
        if not directory:
            self.notice.setText("Uložení bylo zrušeno. Výsledky zůstaly připravené ke stažení.")
            return

        log_dir = self.context.settings.log_dir

        def save(client, task):
            return photo_batch.download_results(
                client,
                job,
                log_dir,
                reporter=task.logline.emit,
                progress=task.progress_event.emit,
                output_dir=directory,
            )

        self.execute(
            "Uložení upravených fotografií",
            save,
            lambda value: self.page_activated(),
            output_dir=directory,
        )

    def delete_job(self, job):
        if self.busy:
            return
        if not confirm(
            self,
            "Odebrat fotografickou dávku z přehledu",
            "Odstranit místní evidenci této dávky? Již uložené fotografie zůstanou zachovány.",
        ):
            return

        log_dir = self.context.settings.log_dir
        key = self.context.api_key
        client = self.context.client() if key else None

        def remove(client, task):
            cancel_error = None
            if job.batch_id and job.status in {"validating", "in_progress", "finalizing"}:
                try:
                    if client is None:
                        raise ValueError("Zrušení vzdálené dávky vyžaduje přístupový klíč.")
                    client.cancel_batch(job.batch_id)
                except Exception as error:
                    cancel_error = error
            photo_batch.delete_job(job, log_dir)
            return cancel_error

        def receive(cancel_error):
            if key != self.context.api_key or log_dir != self.context.settings.log_dir:
                return
            self.load_jobs()
            self.notice.setText(
                friendly_error(cancel_error, "Zrušení vzdálené dávky")
                + " Místní záznam byl smazán; vzdálená dávka může pokračovat."
                if cancel_error
                else "Místní evidence fotografické dávky byla smazána."
            )

        self.busy = True
        self.start_button.setEnabled(False)
        record = self.context.operations.start(
            "Smazání fotografické dávky",
            lambda task: remove(client, task),
            receive,
            popup=False,
        )

        def release():
            self.busy = False
            self.start_button.setEnabled(True)

        self.context.operations.on_finished(record, release)
