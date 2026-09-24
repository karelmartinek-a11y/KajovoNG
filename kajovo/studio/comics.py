"""Produkční sekce Komiks nad jedinou doménovou službou."""
from __future__ import annotations

import copy
import json
import time
from dataclasses import asdict

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QFormLayout,
                              QInputDialog, QLineEdit, QListWidget, QListWidgetItem,
                              QPlainTextEdit, QSpinBox, QSplitter, QTabWidget, QWidget)

from kajovo.core.comic_service import ComicService
from kajovo.comic_layout import prepare_storyboard_layout
from kajovo.core.comic_types import DEFAULT_STYLE, ComicError, PanelFormat
from kajovo.core.image_runtime import image_capability
from kajovo.core.user_errors import describe_error
from .comic_editor import EntityPromptEdit, OverlayEditor, render_panel
from .components import DetailDialog, action, actions, caption, confirm, scroll, vertical


class ComicsPage(QWidget):
    def __init__(self, context, parent=None):
        super().__init__(parent)
        self.context = context
        self.project_id = None
        self.panel_id = None
        self.panel_record = None
        self.previewed_version = None
        self.project_record = None
        self.loading = False
        self.dirty = False
        self.style_dirty = False
        self.running = set()
        self.pending_settings = False
        self.poll_started = {}
        self.service = ComicService(context.settings, storyboard_layout=prepare_storyboard_layout)
        root = vertical(self, 0)
        top = actions(action("comic.new", "Nový komiks", self.create_project, "primary"),
                      action("comic.refresh", "Obnovit knihovnu", self.refresh_projects),
                      action("comic.duplicate", "Duplikovat komiks", self.duplicate_project),
                      action("comic.trash", "Do koše / Obnovit", self.trash_project))
        root.addWidget(top)
        self.trash = QCheckBox("Zobrazit koš")
        self.trash.toggled.connect(self.refresh_projects)
        root.addWidget(self.trash)
        self.projects = QComboBox()
        self.projects.setAccessibleName("Komiksový projekt")
        self.projects.currentIndexChanged.connect(self.select_project)
        root.addWidget(self.projects)
        self.notice = caption("Založte komiks nebo otevřete uložený projekt.", "muted")
        root.addWidget(self.notice)
        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)
        self.build_panels()
        self.entity_lists = {}
        for kind, label in (("character", "Postavy"), ("environment", "Prostředí")):
            page = QWidget()
            body = vertical(page)
            listing = QListWidget()
            listing.setAccessibleName(label)
            body.addWidget(listing, 1)
            self.entity_lists[kind] = listing
            body.addWidget(actions(
                action("comic." + kind + ".new", "Přidat", lambda checked=False, k=kind: self.add_entity(k)),
                action("comic." + kind + ".generate", "Vytvořit / obnovit referenci", lambda checked=False, k=kind: self.generate_entity(k)),
                action("comic." + kind + ".refs", "Přidat fotografie", lambda checked=False, k=kind: self.add_entity_refs(k)),
                action("comic." + kind + ".edit", "Upravit popis", lambda checked=False, k=kind: self.edit_entity(k)),
                action("comic." + kind + ".remove_refs", "Odebrat fotografie", lambda checked=False, k=kind: self.remove_entity_refs(k)),
                action("comic." + kind + ".show", "Zobrazit referenci", lambda checked=False, k=kind: self.show_entity(k)),
                action("comic." + kind + ".archive", "Archivovat / obnovit", lambda checked=False, k=kind: self.archive_entity(k)),
            ))
            self.tabs.addTab(page, label)
        self.build_style()
        self.build_story_pipeline()
        history = QWidget()
        self.history_page = history
        body = vertical(history)
        self.jobs = QListWidget()
        self.jobs.setAccessibleName("Operace komiksu")
        body.addWidget(self.jobs, 1)
        body.addWidget(actions(action("comic.job.resume", "Obnovit / převzít", self.resume_selected),
                               action("comic.job.retry", "Opakovat chybné panely", self.retry_selected),
                               action("comic.job.cancel", "Zrušit dávku", self.cancel_selected),
                               action("comic.job.details", "Evidence operace", self.job_details)))
        self.tabs.addTab(history, "Historie")
        self.timer = QTimer(self)
        self.timer.setInterval(max(1000, int(context.settings.batch_poll_interval_s * 1000)))
        self.timer.timeout.connect(self.poll)
        self.timer.start()
        context.operations.completed.connect(self.operation_finished)
        context.settings_changed.connect(self.settings_changed)
        self.refresh_projects()

    def build_panels(self):
        page = QWidget()
        body = vertical(page)
        split = QSplitter()
        self.panels = QListWidget()
        self.panels.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.panels.setAccessibleName("Panely komiksu")
        self.panels.currentItemChanged.connect(self.select_panel)
        split.addWidget(self.panels)
        editor = QWidget()
        self.panel_editor = editor
        editor.setEnabled(False)
        form = vertical(editor)
        self.panel_name = QLineEdit()
        self.panel_name.setPlaceholderText("Název panelu")
        self.prompt = EntityPromptEdit()
        self.prompt.setMinimumHeight(120)
        self.prompt.setPlaceholderText("Popište scénu. Postavy a prostředí vložte kliknutím níže.")
        form.addWidget(self.panel_name)
        form.addWidget(self.prompt)
        self.tokens = QComboBox()
        self.tokens.setAccessibleName("Postavy a prostředí k vložení")
        self.tokens.activated.connect(self.insert_token)
        form.addWidget(self.tokens)
        form.addWidget(actions(action("comic.token.insert", "Vložit postavu / prostředí", self.insert_token)))
        sizing = QFormLayout()
        self.preset = QComboBox()
        self.preset.addItems(["Vlastní pixely", "1:1", "4:3", "3:4", "16:9", "9:16", "A4", "A5", "A6", "DL"])
        sizing.addRow("Formát panelu", self.preset)
        self.landscape = QCheckBox("Papír na šířku")
        sizing.addRow(self.landscape)
        self.width_px, self.height_px, self.dpi = QSpinBox(), QSpinBox(), QSpinBox()
        for spin in (self.width_px, self.height_px):
            spin.setRange(1, 16384)
            spin.setValue(2048)
        self.dpi.setRange(150, 600)
        self.dpi.setValue(300)
        sizing.addRow("Cílová šířka px", self.width_px)
        sizing.addRow("Cílová výška px", self.height_px)
        sizing.addRow("DPI papíru", self.dpi)
        self.fit = QComboBox()
        self.fit.addItem("Celý obraz s okraji", "pad")
        self.fit.addItem("Vyplnit ořezem", "crop")
        sizing.addRow("Přizpůsobení cíli", self.fit)
        self.experimental = QCheckBox("Experimentální generování nad 2560 × 1440")
        sizing.addRow(self.experimental)
        form.addLayout(sizing)
        self.format_info = caption("", "muted")
        form.addWidget(self.format_info)
        form.addWidget(actions(action("comic.panel.save", "Uložit panel", self.save_panel, "primary"),
                               action("comic.panel.validate", "Ověřit zadání", self.validate_panel)))
        split.addWidget(editor)
        split.setMinimumHeight(editor.sizeHint().height())
        split.setSizes([200, 550])
        body.addWidget(split)
        body.addWidget(actions(action("comic.panel.new", "Přidat panel", self.add_panel),
                               action("comic.panel.duplicate", "Duplikovat", lambda: self.panel_action("duplicate")),
                               action("comic.panel.up", "Nahoru", lambda: self.panel_action("up")),
                               action("comic.panel.down", "Dolů", lambda: self.panel_action("down")),
                               action("comic.panel.delete", "Odstranit", lambda: self.panel_action("delete"))))
        body.addWidget(actions(action("comic.panel.generate", "Vygenerovat vybrané panely", self.generate_panels, "primary"),
                               action("comic.panel.edit", "Upravit kresbu", self.edit_panel),
                               action("comic.panel.export", "Exportovat panel", self.export_panel)))
        self.versions = QComboBox()
        self.versions.setAccessibleName("Verze panelu")
        self.versions.currentIndexChanged.connect(self.preview_version)
        body.addWidget(self.versions)
        body.addWidget(actions(action("comic.version.accept", "Použít vybranou verzi", self.restore_version)))
        self.overlays = OverlayEditor()
        body.addWidget(self.overlays)
        self.tabs.addTab(scroll(page), "Panely")
        self.preset.currentTextChanged.connect(self.apply_preset)
        self.landscape.toggled.connect(self.apply_preset)
        self.dpi.valueChanged.connect(self.apply_preset)
        for signal in (self.prompt.textChanged, self.panel_name.textChanged, self.overlays.changed,
                       self.width_px.valueChanged, self.height_px.valueChanged, self.fit.currentIndexChanged, self.experimental.toggled):
            signal.connect(self.mark_dirty)

    def build_style(self):
        page = QWidget()
        body = vertical(page)
        form = QFormLayout()
        self.project_name, self.project_description = QLineEdit(), QPlainTextEdit()
        self.project_description.setMaximumHeight(80)
        form.addRow("Název komiksu", self.project_name)
        form.addRow("Popis komiksu", self.project_description)
        self.style_fields = {}
        for field, title, choices in (
            ("description", "Popis vizuálního stylu", None), ("line", "Linka", ["jemná", "standardní", "silná"]),
            ("color", "Barevnost", ["barevná", "černobílá", "vlastní paleta"]),
            ("palette", "Barvy #RRGGBB oddělené čárkou", None), ("palette_description", "Popis palety", None),
            ("balloon", "Bubliny", ["dialogová", "myšlenková", "narativní"]),
            ("typography", "Charakter písma", [DEFAULT_STYLE["typography"], "Výrazné tučné komiksové písmo"]), ("extra", "Další požadavek", None),
        ):
            widget = QComboBox() if choices else QLineEdit()
            if choices:
                widget.addItems(choices)
            self.style_fields[field] = widget
            form.addRow(title, widget)
        self.sfx = QCheckBox("Povolit SFX")
        form.addRow(self.sfx)
        body.addLayout(form)
        self.style_count = caption("Stylové reference: 0 / 16", "muted")
        body.addWidget(self.style_count)
        body.addWidget(actions(action("comic.style.refs", "Přidat reference stylu", self.add_style_refs),
                               action("comic.style.remove_refs", "Odebrat reference stylu", self.remove_style_refs),
                               action("comic.style.save", "Uložit nastavení", self.save_style),
                               action("comic.bible.generate", "Sestavit bibli", self.generate_bible, "primary")))
        self.bible_versions = QComboBox()
        self.bible_versions.currentIndexChanged.connect(self.show_bible)
        body.addWidget(self.bible_versions)
        self.bible_text = QPlainTextEdit()
        self.bible_text.setReadOnly(True)
        body.addWidget(self.bible_text, 1)
        self.tabs.addTab(scroll(page), "Styl / Bible")
        for widget in (self.project_name, self.project_description, *self.style_fields.values()):
            (widget.currentTextChanged if isinstance(widget, QComboBox) else widget.textChanged).connect(self.mark_style_dirty)
        self.sfx.toggled.connect(self.mark_style_dirty)

    def build_story_pipeline(self):
        page = QWidget()
        body = vertical(page)
        body.addWidget(
            caption(
                "Textová výrobní osa komiksu: Story → Script → Storyboard → "
                "Continuity. Panely lze vytvořit až z continuity PASS.",
                "muted",
            )
        )
        body.addWidget(
            actions(
                action(
                    "comic.story.generate",
                    "Vytvořit Story",
                    self.generate_story,
                    "primary",
                ),
                action(
                    "comic.script.generate",
                    "Vytvořit Script",
                    self.generate_script,
                ),
                action(
                    "comic.storyboard.generate",
                    "Vytvořit Storyboard",
                    self.generate_storyboard,
                ),
                action(
                    "comic.continuity.generate",
                    "Zkontrolovat Continuity",
                    self.generate_continuity,
                ),
                action(
                    "comic.storyboard.materialize",
                    "Převést schválený storyboard na panely",
                    self.materialize_storyboard,
                ),
            )
        )
        self.story_pipeline_text = QPlainTextEdit()
        self.story_pipeline_text.setReadOnly(True)
        self.story_pipeline_text.setPlaceholderText(
            "Nejprve sestavte bibli, potom vytvořte Story."
        )
        body.addWidget(self.story_pipeline_text, 1)
        self.tabs.addTab(scroll(page), "Příběh / Storyboard")

    def _start_story_stage(self, stage):
        try:
            project = self.require_project()
            starter = {
                "story": self.service.start_story,
                "script": self.service.start_script,
                "storyboard": self.service.start_storyboard,
                "continuity": self.service.start_continuity,
            }[stage]
            self.execute_operation(starter(project))
        except Exception as exc:
            self.fail(exc)

    def generate_story(self):
        self._start_story_stage("story")

    def generate_script(self):
        self._start_story_stage("script")

    def generate_storyboard(self):
        self._start_story_stage("storyboard")

    def generate_continuity(self):
        self._start_story_stage("continuity")

    def materialize_storyboard(self):
        try:
            project = self.require_project()
            self.launch(
                "Převod storyboardu na panely",
                lambda service: service.materialize_storyboard(project),
                lambda _value: self.refresh_project(),
                network=False,
            )
        except Exception as exc:
            self.fail(exc)

    def refresh_story_pipeline(self):
        if not self.project_id:
            self.story_pipeline_text.clear()
            return
        sections = []
        labels = (
            ("story", "STORY"),
            ("script", "SCRIPT"),
            ("storyboard", "STORYBOARD"),
            ("continuity", "CONTINUITY"),
        )
        for kind, label in labels:
            document = self.service.latest_document(self.project_id, kind)
            if document is None:
                sections.append(f"## {label}\n— zatím nevytvořeno —")
                continue
            sections.append(
                f"## {label} · {document['created_at']}\n"
                + json.dumps(
                    document["result"],
                    ensure_ascii=False,
                    indent=2,
                )
            )
        self.story_pipeline_text.setPlainText("\n\n".join(sections))

    def mark_style_dirty(self, *_):
        if not self.loading:
            self.style_dirty = True

    def save_pending(self):
        if self.dirty and not self.save_panel():
            return False
        if self.style_dirty and not self.save_style():
            return False
        return True

    def fail(self, error):
        value = describe_error(error)
        self.notice.setText(f"{getattr(error, 'code', None) or value.code}: {error}")

    def service_for(self, task, network=True):
        return ComicService(self.context.settings, self.context.client() if network else None,
                            emit=task.progress_event.emit, stopped=task.isInterruptionRequested,
                            storyboard_layout=prepare_storyboard_layout)

    def launch(self, title, function, receive=None, network=True, identifier=None, popup=True):
        settings = copy.deepcopy(self.context.settings)
        settings.comic_library_dir = str(self.service.store.root)
        try:
            client = self.context.client() if network else None
            def work(task):
                service = ComicService(settings, client, task.progress_event.emit, task.isInterruptionRequested,
                                       storyboard_layout=prepare_storyboard_layout)
                return function(service)
            self.context.operations.start(title, work, receive, cancellable=True, identifier=identifier, popup=popup)
        except Exception as exc:
            self.fail(exc)

    def require_project(self):
        if not self.project_id:
            raise ComicError("missing_project", "Nejprve otevřete nebo založte komiks.")
        return self.project_id

    def refresh_projects(self, *_):
        try:
            selected = self.project_id
            self.loading = True
            self.projects.clear()
            self.projects.addItem("Vyberte komiks", None)
            for project in self.service.store.rows("projects", "deleted=?", (int(self.trash.isChecked()),), order="updated_at DESC"):
                self.projects.addItem(project["name"], project["id"])
            self.projects.setCurrentIndex(max(0, self.projects.findData(selected)))
            self.loading = False
            self.select_project()
        except Exception as exc:
            self.loading = False
            self.fail(exc)

    def select_project(self, *_):
        if self.loading:
            return
        if self.style_dirty and not self.save_style():
            self.projects.blockSignals(True)
            self.projects.setCurrentIndex(max(0, self.projects.findData(self.project_id)))
            self.projects.blockSignals(False)
            return
        if self.dirty and not self.save_panel():
            self.loading = True
            self.projects.setCurrentIndex(max(0, self.projects.findData(self.project_id)))
            self.loading = False
            return
        self.project_id = self.projects.currentData()
        self.panel_id = None
        self.panel_record = None
        self.tabs.setEnabled(bool(self.project_id) and not self.trash.isChecked())
        if self.project_id:
            self.refresh_project()

    def refresh_project(self):
        if not self.project_id:
            return
        try:
            if self.style_dirty and not self.save_style():
                return
            self.loading = True
            self.project_record = self.service.store.get("projects", self.project_id)
            self.project_name.setText(self.project_record["name"])
            self.project_description.setPlainText(self.project_record["description"])
            style = self.project_record["style"]
            for field, widget in self.style_fields.items():
                value = ", ".join(style[field]) if field == "palette" else style[field]
                if isinstance(widget, QComboBox):
                    widget.setCurrentText(value)
                else:
                    widget.setText(value)
            self.sfx.setChecked(style["sfx"])
            self.overlays.default_bold = "tučné" in style["typography"]
            self.overlays.kind.setCurrentIndex(self.overlays.kind.findData({"dialogová": "dialog", "myšlenková": "thought", "narativní": "caption"}[style["balloon"]]))
            self.overlays.kind.setToolTip("SFX lze uložit pouze při povolení SFX ve stylu komiksu.")
            self.style_count.setText(f"Stylové reference: {len(self.service.store.references(self.project_id))} / {image_capability()['max_references']}")
            self.loading = True
            self.bible_versions.clear()
            for i, bible in enumerate(self.service.store.rows("bibles", "project_id=?", (self.project_id,)), 1):
                self.bible_versions.addItem(f"Bible {i} · {bible['created_at']}" + (" · aktivní" if bible["id"] == self.project_record["bible_id"] else ""), bible["id"])
            self.bible_versions.setCurrentIndex(self.bible_versions.findData(self.project_record["bible_id"]))
            self.loading = False
            self.show_bible()
            self.refresh_story_pipeline()
            self.refresh_entities()
            self.refresh_panels()
            self.refresh_jobs()
            self.notice.setText("Uloženo. Konzistence je řízena referencemi; generativní model nezaručuje totožnost každého detailu.")
        except Exception as exc:
            self.loading = False
            self.fail(exc)

    def create_project(self):
        if (self.dirty and not self.save_panel()) or (self.style_dirty and not self.save_style()):
            return
        name, ok = QInputDialog.getText(self, "Nový komiks", "Název komiksu")
        if not ok:
            return
        try:
            self.project_id = self.service.store.project(name, style=DEFAULT_STYLE)
            self.trash.setChecked(False)
            self.refresh_projects()
            self.tabs.setCurrentIndex(3)
            self.notice.setText("Doplňte volitelný styl a reference, potom zvolte Sestavit bibli.")
        except Exception as exc:
            self.fail(exc)

    def save_style(self):
        try:
            self.require_project()
            style = {key: widget.currentText() if isinstance(widget, QComboBox) else widget.text() for key, widget in self.style_fields.items()}
            style["palette"] = [s.strip() for s in style["palette"].split(",") if s.strip()]
            style["sfx"] = self.sfx.isChecked()
            current = self.project_record
            self.service.store.update_project(self.project_id, current["revision"], self.project_name.text(), self.project_description.toPlainText(), style)
            self.project_record = self.service.store.get("projects", self.project_id)
            self.style_dirty = False
            self.projects.setItemText(self.projects.findData(self.project_id), self.project_name.text())
            self.notice.setText("Nastavení uloženo. Po změně stylu sestavte novou bibli.")
            return True
        except Exception as exc:
            self.fail(exc)
            return False

    def files(self):
        return QFileDialog.getOpenFileNames(self, "Referenční obrázky", "", "Obrázky (*.png *.jpg *.jpeg *.webp)")[0]

    def add_style_refs(self):
        if not self.project_id:
            return
        if self.style_dirty and not self.save_style():
            return
        paths = self.files()
        project = self.project_id
        if paths:
            self.launch("Import stylových referencí", lambda s: s.import_references(project, paths), lambda _: self.refresh_project(), network=False)

    def remove_style_refs(self):
        if not self.project_id or not confirm(self, "Odebrat reference", "Odebrat všechny aktivní reference stylu? Historické podklady zůstanou zachované."):
            return
        try:
            if self.style_dirty and not self.save_style():
                return
            self.service.store.remove_references(self.project_id)
            self.refresh_project()
        except Exception as exc:
            self.fail(exc)

    def generate_bible(self):
        if self.save_style():
            try:
                self.execute_operation(self.service.start_bible(self.project_id))
            except Exception as exc:
                self.fail(exc)

    def show_bible(self, *_):
        if self.loading:
            return
        identifier = self.bible_versions.currentData()
        if not identifier:
            self.bible_text.setPlainText("Bible ještě nebyla sestavena.")
            return
        bible = self.service.store.get("bibles", identifier)
        self.bible_text.setPlainText("\n\n".join(f"{name.replace('_', ' ').upper()}\n{text}" for name, text in bible["result"]["rules"].items()))

    def refresh_entities(self):
        self.entity_records = self.service.store.rows("entities", "project_id=?", (self.project_id,))
        self.tokens.clear()
        for listing in self.entity_lists.values():
            listing.clear()
        for entity in self.entity_records:
            state = "archivováno" if entity["archived"] else "připraveno" if entity["active_revision"] else "čeká na referenci"
            refs = self.service.store.references(self.project_id, entity["id"])
            count = sum(ref["role"] == "working" for ref in refs)
            item = QListWidgetItem(f"{entity['name']} · {state}\nFotografie: {count} / {image_capability()['max_references']}")
            item.setData(Qt.UserRole, entity["id"])
            self.entity_lists[entity["kind"]].addItem(item)
            if not entity["archived"]:
                self.tokens.addItem(f"{entity['name']} · {state}", entity["id"])

    def selected_entity(self, kind):
        item = self.entity_lists[kind].currentItem()
        if not item:
            raise ComicError("missing_entity", "Vyberte entitu v seznamu.")
        return item.data(Qt.UserRole)

    def add_entity(self, kind):
        if not self.project_id:
            return
        name, ok = QInputDialog.getText(self, "Nová postava" if kind == "character" else "Nové prostředí", "Název")
        if not ok:
            return
        description, ok = QInputDialog.getMultiLineText(self, "Popis reference", "Vzhled, oblečení, dispozice, co zachovat (volitelné)")
        if not ok:
            return
        try:
            entity = self.service.store.entity(self.project_id, kind, name, description)
            self.refresh_entities()
            paths = self.files()
            project = self.project_id
            if paths:
                self.launch("Import referencí", lambda s: s.import_references(project, paths, entity), lambda _: self.refresh_entities(), network=False)
        except Exception as exc:
            self.fail(exc)

    def add_entity_refs(self, kind):
        try:
            entity = self.selected_entity(kind)
            paths = self.files()
            project = self.project_id
            if paths:
                self.launch("Import referencí", lambda s: s.import_references(project, paths, entity), lambda _: self.refresh_entities(), network=False)
        except Exception as exc:
            self.fail(exc)

    def edit_entity(self, kind):
        try:
            entity = self.service.store.get("entities", self.selected_entity(kind))
            name, ok = QInputDialog.getText(self, "Upravit entitu", "Název", text=entity["name"])
            if not ok:
                return
            description, ok = QInputDialog.getMultiLineText(self, "Upravit entitu", "Popis; po uložení znovu vytvořte referenci", entity["description"])
            if ok:
                self.service.store.update_entity(entity["id"], entity["revision"], name, description)
                self.refresh_entities()
        except Exception as exc:
            self.fail(exc)

    def remove_entity_refs(self, kind):
        try:
            entity = self.selected_entity(kind)
            if confirm(self, "Odebrat fotografie", "Odebrat aktivní podklady? Historické soubory zůstanou zachované; přidejte nové a vytvořte referenci."):
                self.service.store.remove_references(self.project_id, entity)
                self.refresh_entities()
        except Exception as exc:
            self.fail(exc)

    def generate_entity(self, kind):
        try:
            self.execute_operation(self.service.start_entity(self.selected_entity(kind)))
        except Exception as exc:
            self.fail(exc)

    def show_entity(self, kind):
        try:
            entity = self.service.store.get("entities", self.selected_entity(kind))
            if not entity["active_revision"]:
                raise ComicError("missing_reference", "Entita nemá vygenerovanou referenci.")
            revision = self.service.store.get("entity_revisions", entity["active_revision"])
            from PySide6.QtWidgets import QDialog, QLabel
            from PySide6.QtGui import QPixmap
            dialog = QDialog(self)
            dialog.setWindowTitle(entity["name"])
            body = vertical(dialog)
            label = QLabel()
            label.setPixmap(QPixmap(str(self.service.store.asset_path(revision["asset_id"]))).scaled(850, 600, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            body.addWidget(label)
            body.addWidget(caption(revision["descriptor"]))
            dialog.exec()
        except Exception as exc:
            self.fail(exc)

    def archive_entity(self, kind):
        try:
            entity = self.service.store.get("entities", self.selected_entity(kind))
            self.service.store.archive_entity(entity["id"], not entity["archived"])
            self.refresh_entities()
        except Exception as exc:
            self.fail(exc)

    def insert_token(self, *_):
        identifier = self.tokens.currentData()
        if identifier:
            self.prompt.insert_entity(self.service.store.get("entities", identifier))

    def refresh_panels(self):
        selected = self.panel_id
        self.loading = True
        self.panels.clear()
        for panel in self.service.store.rows("panels", "project_id=? AND deleted=0", (self.project_id,), order="position,created_at"):
            jobs = self.service.store.rows("batch_items", "panel_id=?", (panel["id"],), order="rowid DESC")
            state = {"prepared": "připraveno k odeslání", "submitted": "odesláno", "received": "čeká na místní zpracování", "completed": "výsledek uložen", "failed": "chyba"}.get(jobs[0]["status"], jobs[0]["status"]) if jobs else "zadání"
            item = QListWidgetItem(panel["name"] + " · " + state)
            if jobs and jobs[0]["error"]:
                detail = jobs[0]["error"]
                item.setText(item.text() + "\n" + str(detail.get("message") or detail.get("code") or detail))
            item.setData(Qt.UserRole, panel["id"])
            self.panels.addItem(item)
            if panel["id"] == selected:
                self.panels.setCurrentItem(item)
        if self.panels.currentRow() < 0 and self.panels.count():
            self.panels.setCurrentRow(0)
        self.loading = False
        if not self.dirty:
            self.select_panel(self.panels.currentItem())

    def select_panel(self, item, previous=None):
        if self.loading:
            return
        if self.dirty and not self.save_panel():
            self.loading = True
            self.panels.setCurrentItem(previous)
            self.loading = False
            return
        self.panel_id = item.data(Qt.UserRole) if item else None
        self.panel_record = None
        self.loading = True
        if self.panel_id:
            panel = self.service.store.get("panels", self.panel_id)
            self.panel_record = panel
            self.panel_name.setText(panel["name"])
            self.prompt.load_document(self.service.store.get("prompts", panel["prompt_id"])["document"], self.entity_records)
            self.preset.setCurrentIndex(0)
            self.width_px.setValue(panel["format"]["width"])
            self.height_px.setValue(panel["format"]["height"])
            self.dpi.setValue(panel["format"]["dpi"])
            self.fit.setCurrentIndex(self.fit.findData(panel["format"]["fit"]))
            self.experimental.setChecked(panel["format"]["experimental"])
            self.versions.clear()
            for i, version in enumerate(self.service.store.rows("panel_versions", "panel_id=?", (self.panel_id,)), 1):
                self.versions.addItem(f"Verze {i}" + (" · aktivní" if version["id"] == panel["active_version"] else " · kandidát"), version["id"])
            self.versions.setCurrentIndex(self.versions.findData(panel["active_version"]))
            self.preview_version()
        else:
            self.prompt.clear()
            self.panel_name.clear()
            self.versions.clear()
            self.overlays.load(None, [])
        self.loading = False
        self.dirty = False
        self.panel_editor.setEnabled(bool(self.panel_id))
        self.overlays.setEnabled(bool(self.panel_id))
        self.update_format_info()

    def mark_dirty(self, *_):
        if not self.loading and self.panel_id:
            self.dirty = True
            self.update_format_info()

    def format(self):
        return PanelFormat(self.width_px.value(), self.height_px.value(), self.dpi.value(), self.fit.currentData(), self.experimental.isChecked())

    def update_format_info(self):
        try:
            fmt = self.format()
            self.format_info.setText(f"Generování: {fmt.native_size(image_capability())} px → výsledný panel: {fmt.width} × {fmt.height} px. Bez deformace.")
        except Exception as exc:
            self.format_info.setText(str(exc))

    def apply_preset(self, *_):
        if self.loading:
            return
        name = self.preset.currentText()
        if ":" in name:
            w, h = map(int, name.split(":"))
            factor = 2048 / max(w, h)
            self.width_px.setValue(round(w * factor))
            self.height_px.setValue(round(h * factor))
        elif name != "Vlastní pixely":
            fmt = PanelFormat.paper(name, self.landscape.isChecked(), self.dpi.value())
            self.width_px.setValue(fmt.width)
            self.height_px.setValue(fmt.height)
        self.mark_dirty()

    def save_panel(self):
        if not self.dirty:
            return True
        if not self.panel_id:
            return not self.dirty
        try:
            if self.previewed_version and self.previewed_version != self.panel_record["active_version"]:
                raise ComicError("inactive_version", "Nejprve použijte vybranou verzi; teprve potom upravte její texty.")
            self.service.store.save_panel(self.panel_id, self.panel_record["revision"], self.panel_name.text(),
                                          self.prompt.prompt_document(), asdict(self.format()), self.overlays.layers)
            self.panel_record = self.service.store.get("panels", self.panel_id)
            self.previewed_version = self.panel_record["active_version"]
            self.versions.blockSignals(True)
            if self.previewed_version and self.versions.findData(self.previewed_version) < 0:
                self.versions.addItem("Uložená verze textů", self.previewed_version)
            self.versions.setCurrentIndex(self.versions.findData(self.previewed_version))
            self.versions.blockSignals(False)
            self.dirty = False
            self.notice.setText("Panel uložen.")
            return True
        except Exception as exc:
            self.fail(exc)
            return False

    def add_panel(self):
        try:
            self.require_project()
            if self.dirty and not self.save_panel():
                return
            self.panel_id = self.service.store.panel(self.project_id, f"Panel {self.panels.count() + 1}")
            self.refresh_panels()
        except Exception as exc:
            self.fail(exc)

    def panel_action(self, name):
        try:
            if not self.panel_id:
                raise ComicError("missing_panel", "Vyberte panel.")
            if name == "delete" and not confirm(self, "Odstranit panel", "Odstranit panel z pracovního seznamu? Historie zůstane zachována."):
                return
            if self.dirty and not self.save_panel():
                return
            new = self.service.store.panel_action(self.panel_id, name)
            if new:
                self.panel_id = new
            self.refresh_panels()
        except Exception as exc:
            self.fail(exc)

    def validate_panel(self):
        if self.save_panel():
            try:
                snap = self.service.compile_panel(self.panel_id)
                self.notice.setText(f"Zadání ověřeno: {len(snap['assets'])} / 16 obrazových referencí, {snap['body']['size']} px. Žádné API volání.")
            except Exception as exc:
                self.fail(exc)

    def generate_panels(self):
        if not self.save_panel():
            return
        try:
            ids = [item.data(Qt.UserRole) for item in self.panels.selectedItems()]
            self.execute_operation(self.service.start_panels(self.require_project(), ids))
        except Exception as exc:
            self.fail(exc)

    def edit_panel(self):
        if not self.save_panel():
            return
        text, ok = QInputDialog.getMultiLineText(self, "Upravit kresbu", "Co změnit? Vše ostatní se má zachovat.")
        if ok:
            try:
                self.execute_operation(self.service.start_panels(self.require_project(), [self.panel_id], text))
            except Exception as exc:
                self.fail(exc)

    def preview_version(self, *_):
        if not self.panel_id:
            return
        if not self.loading and self.dirty:
            if not self.save_panel():
                return
        identifier = self.versions.currentData()
        try:
            self.previewed_version = identifier
            if identifier:
                version = self.service.store.get("panel_versions", identifier)
                layers = self.panel_record["overlays"] if identifier == self.panel_record["active_version"] else version["overlays"]
                self.overlays.load(self.service.store.asset_path(version["asset_id"]).read_bytes(), layers)
            else:
                self.overlays.load(None, self.panel_record["overlays"])
        except Exception as exc:
            self.fail(exc)

    def restore_version(self):
        identifier = self.versions.currentData()
        if identifier:
            try:
                self.service.store.restore_version(self.panel_id, identifier)
                self.dirty = False
                self.refresh_panels()
            except Exception as exc:
                self.fail(exc)

    def export_panel(self):
        if not self.save_panel():
            return
        try:
            identifier = self.versions.currentData()
            if not identifier:
                raise ComicError("missing_version", "Nejprve vygenerujte panel.")
            version = self.service.store.get("panel_versions", identifier)
            image = render_panel(self.service.store.asset_path(version["asset_id"]).read_bytes(), self.overlays.layers)
            path, _ = QFileDialog.getSaveFileName(self, "Exportovat jednotlivý panel", self.panel_name.text() + ".png", "PNG (*.png);;JPEG (*.jpg);;WebP (*.webp)")
            if path:
                from pathlib import Path
                from PySide6.QtCore import QSaveFile
                destination = Path(path).resolve()
                if destination.is_relative_to(self.service.store.root):
                    raise ComicError("protected_path", "Export uložte mimo interní knihovnu komiksů.")
                output = QSaveFile(str(destination))
                if not output.open(QSaveFile.WriteOnly):
                    raise ComicError("storage_failure", "Export se nepodařilo zapsat.")
                if not image.save(output, destination.suffix.removeprefix(".").upper()) or not output.commit():
                    output.cancelWriting()
                    raise ComicError("storage_failure", "Export se nepodařilo dokončit; původní soubor zůstal zachován.")
                with self.service.store.transaction() as db:
                    self.service.store.event(db, self.project_id, "export_panel", {"version_id": identifier, "path": path})
                self.notice.setText("Panel exportován s přesnými textovými vrstvami.")
        except Exception as exc:
            self.fail(exc)

    def execute_operation(self, identifier, popup=True):
        if identifier in self.running:
            return
        self.running.add(identifier)
        self.poll_started.setdefault(identifier, time.monotonic())
        self.launch("Komiks – zpracování operace", lambda s: s.run(identifier),
                    lambda _: self.refresh_after_operation(), identifier="comic_" + identifier, popup=popup)
        if "comic_" + identifier not in self.context.operations.records:
            self.running.discard(identifier)
        self.refresh_jobs()

    def operation_finished(self, identifier, result):
        if identifier.startswith("comic_"):
            self.running.discard(identifier.removeprefix("comic_"))
            if self.pending_settings and not self.running:
                self.settings_changed()
                return
            self.refresh_after_operation()

    def refresh_after_operation(self):
        if self.project_id:
            self.refresh_story_pipeline()
            self.refresh_entities()
            self.refresh_jobs()
            if not self.dirty:
                self.refresh_project()

    def refresh_jobs(self):
        selected = self.jobs.currentItem().data(Qt.UserRole) if self.jobs.currentItem() else None
        self.jobs.clear()
        if not self.project_id:
            return
        for op in self.service.store.rows("operations", "project_id=?", (self.project_id,), order="created_at DESC"):
            batches = self.service.store.rows("batches", "operation_id=?", (op["id"],))
            counts = [b["payload"].get("request_counts", {}) for b in batches]
            done = sum(c.get("completed", 0) for c in counts)
            failed = sum(c.get("failed", 0) for c in counts)
            total = sum(c.get("total", 0) for c in counts)
            text = f"{op['kind']} · {op['status']} · {op['created_at']}"
            if batches:
                text += f"\nDávek: {len(batches)} · hotovo {done}/{total} · chyb {failed}"
            if op["error"]:
                text += "\n" + op["error"].get("message", "")
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, op["id"])
            self.jobs.addItem(item)
            if op["id"] == selected:
                self.jobs.setCurrentItem(item)

    def selected_job(self):
        item = self.jobs.currentItem()
        if not item:
            raise ComicError("missing_operation", "Vyberte operaci v historii komiksu.")
        return item.data(Qt.UserRole)

    def resume_selected(self):
        try:
            identifier = self.selected_job()
            self.poll_started[identifier] = time.monotonic()
            self.execute_operation(identifier)
        except Exception as exc:
            self.fail(exc)

    def retry_selected(self):
        try:
            self.execute_operation(self.service.retry_failed(self.selected_job()))
        except Exception as exc:
            self.fail(exc)

    def cancel_selected(self):
        try:
            identifier = self.selected_job()
            if confirm(self, "Zrušit dávku", "Požádat OpenAI o zrušení? Již hotové výsledky budou převzaty."):
                self.launch("Rušení komiksové dávky", lambda s: s.cancel(identifier), lambda _: self.refresh_after_operation())
        except Exception as exc:
            self.fail(exc)

    def job_details(self):
        try:
            op = self.service.store.get("operations", self.selected_job())
            batches = self.service.store.rows("batches", "operation_id=?", (op["id"],))
            items = self.service.store.rows("batch_items", "batch_id IN (SELECT id FROM batches WHERE operation_id=?)", (op["id"],))
            evidence = {"operation": op, "batches": batches, "items": items}
            DetailDialog("Evidence komiksu", "Uložené snapshoty, výsledky, usage a chyby jednotlivých panelů.", self, evidence).exec()
        except Exception as exc:
            self.fail(exc)

    def poll(self):
        if not self.context.api_key:
            return
        try:
            for op in self.service.store.rows("operations", "status='batch_pending'"):
                started = self.poll_started.setdefault(op["id"], time.monotonic())
                if time.monotonic() - started < self.context.settings.batch_timeout_s:
                    self.execute_operation(op["id"], popup=False)
                elif op["project_id"] == self.project_id:
                    self.notice.setText("Místní sledování dosáhlo limitu. Vzdálená dávka pokračuje; v Historii použijte Obnovit / převzít.")
        except Exception as exc:
            self.fail(exc)

    def trash_project(self):
        try:
            if not self.save_pending():
                return
            self.require_project()
            if confirm(self, "Koš komiksů", "Obnovit komiks?" if self.trash.isChecked() else "Přesunout komiks do obnovitelného koše?"):
                self.service.store.trash(self.project_id, not self.trash.isChecked())
                self.project_id = None
                self.refresh_projects()
        except Exception as exc:
            self.fail(exc)

    def duplicate_project(self):
        try:
            if (self.dirty and not self.save_panel()) or (self.style_dirty and not self.save_style()):
                return
            project = self.require_project()
            self.launch("Duplikace komiksu", lambda s: s.duplicate_project(project), self.open_duplicate, network=False)
        except Exception as exc:
            self.fail(exc)

    def open_duplicate(self, identifier):
        if not self.save_pending():
            return
        self.project_id = identifier
        self.refresh_projects()

    def settings_changed(self):
        if self.running:
            self.pending_settings = True
            self.notice.setText("Nové umístění knihovny se použije po dokončení místního zpracování.")
            return
        if self.dirty and not self.save_panel():
            return
        if self.style_dirty and not self.save_style():
            return
        self.pending_settings = False
        self.service = ComicService(self.context.settings, storyboard_layout=prepare_storyboard_layout)
        self.project_id = None
        self.panel_id = None
        self.panel_record = None
        self.timer.setInterval(max(1000, int(self.context.settings.batch_poll_interval_s * 1000)))
        self.refresh_projects()
