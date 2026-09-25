"""Explicitní operace nad vzdálenými soubory a vyhledávacími úložišti."""

from __future__ import annotations

import json

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QFileDialog, QListWidget, QListWidgetItem,
    QPlainTextEdit, QTabWidget, QWidget,
)


from .components import DetailDialog, action, actions, caption, confirm, vertical


def selected_ids(widget):
    return [item.data(Qt.UserRole) for item in widget.selectedItems()]


def fill_records(widget, records):
    selected = selected_ids(widget)
    widget.clear()
    for record in records:
        identifier = record.get("id", "")
        title = record.get("filename") or record.get("name") or identifier
        item = QListWidgetItem(f"{title}\n{identifier}")
        item.setData(Qt.UserRole, identifier)
        item.setData(Qt.UserRole + 1, record)
        widget.addItem(item)
        item.setSelected(identifier in selected)


class ValueDialog(QDialog):
    def __init__(self, title, label, initial="", parent=None, structured=False):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(540, 400)
        root = vertical(self)
        root.addWidget(caption(label))
        self.editor = QPlainTextEdit(initial)
        self.editor.setAccessibleName(label)
        root.addWidget(self.editor, 1)
        self.notice = caption("", "error")
        root.addWidget(self.notice)
        self.structured = structured
        self.value = None
        root.addWidget(actions(action("value.cancel", "Zrušit", self.reject), action("value.save", "Potvrdit", self.submit, "primary")))

    def submit(self):
        try:
            from kajovo.core.orchestration.contracts import parse_json_value_strict

            value = parse_json_value_strict(self.editor.toPlainText()) if self.structured else self.editor.toPlainText().strip()
            if not self.structured and not value:
                raise ValueError("Vyplňte hodnotu.")
            self.value = value
        except ValueError as error:
            self.notice.setText(str(error))
            return
        self.accept()


class ResourcesPage(QWidget):
    resource_deleted = Signal(str, str, str)

    def __init__(self, context, parent=None):
        super().__init__(parent)
        self.context = context
        self.resource_deleted.connect(self._prune_deleted)
        self.busy = False
        self.store_selection_generation = 0
        self.controls = []
        root = vertical(self, 0)
        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)
        self.lists = {}
        for kind, title in (("files", "Soubory"), ("stores", "Vyhledávací úložiště")):
            page = QWidget()
            layout = vertical(page)
            layout.addWidget(caption(title, "section"))
            listing = QListWidget()
            listing.setSelectionMode(QAbstractItemView.ExtendedSelection)
            listing.setWordWrap(True)
            listing.setAccessibleName(title)
            self.lists[kind] = listing
            layout.addWidget(listing, 1)
            buttons = [self.button(f"resources.{kind}.refresh", "Obnovit", lambda checked=False, target=kind: self.refresh(target)),
                       self.button(f"resources.{kind}.attach", "Připojit k zadání", lambda checked=False, target=kind: self.attach(target)),
                       self.button(f"resources.{kind}.detach", "Odpojit od zadání", lambda checked=False, target=kind: self.detach(target))]
            layout.addWidget(actions(*buttons))
            layout.addWidget(actions(
                self.button(f"resources.{kind}.create", "Nahrát soubory" if kind == "files" else "Vytvořit úložiště", self.upload if kind == "files" else self.create_store),
                self.button(f"resources.{kind}.delete", "Odstranit vybrané", lambda checked=False, target=kind: self.delete(target), "danger"),
                self.button(f"resources.{kind}.delete_all", "Odstranit všechny", lambda checked=False, target=kind: self.delete(target, all_items=True), "danger")))
            if kind == "stores":
                listing.currentItemChanged.connect(self.clear_store_files)
                layout.addWidget(actions(self.button("resources.store.files", "Načíst soubory úložiště", self.refresh_store_files),
                                         self.button("resources.store.add", "Přidat soubory podle identifikátoru", self.add_store_files),
                                         self.button("resources.store.add_selected", "Přidat vybrané soubory", self.add_selected_files)))
                self.store_files = QListWidget()
                self.store_files.setSelectionMode(QAbstractItemView.ExtendedSelection)
                self.store_files.setWordWrap(True)
                layout.addWidget(self.store_files, 1)
                layout.addWidget(actions(self.button("resources.store.remove", "Odebrat z úložiště", self.remove_store_files, "danger"),
                                         self.button("resources.store.attributes", "Upravit atributy", self.attributes),
                                         self.button("resources.store.detail", "Podrobnosti souboru", self.details)))
            self.tabs.addTab(page, title)
        self.notice = caption("Vyberte soubory nebo úložiště a načtěte jejich stav.", "muted")
        root.addWidget(self.notice)
        context.key_changed.connect(self.clear)
        context.attachments_changed.connect(self.update_summary)

    def button(self, identifier, text, callback, role=""):
        widget = action(identifier, text, callback, role)
        self.controls.append(widget)
        return widget

    def update_summary(self):
        self.notice.setText(f"Připojeno k zadání: {len(self.context.files)} souborů a {len(self.context.stores)} úložišť.")

    def clear(self):
        self.store_selection_generation += 1
        for widget in self.lists.values():
            widget.clear()
        self.store_files.clear()
        self.update_summary()

    def clear_store_files(self, *_):
        self.store_selection_generation += 1
        self.store_files.clear()

    def store_receiver(self, store):
        generation = self.store_selection_generation
        def receive(rows):
            if store == self.current_store() and generation == self.store_selection_generation:
                fill_records(self.store_files, rows)
        return receive

    def execute(self, title, operation, receive=None):
        if self.busy:
            return
        try:
            client = self.context.client()
        except ValueError as error:
            self.notice.setText(str(error))
            return
        key = self.context.api_key
        self.busy = True
        for widget in self.controls:
            widget.setEnabled(False)

        def complete(value):
            if key == self.context.api_key and receive:
                receive(value)

        record = self.context.operations.start(title, lambda task: operation(client, task), complete)

        def release():
            self.busy = False
            for widget in self.controls:
                widget.setEnabled(True)

        record.worker.finished.connect(release)
        return record

    def refresh(self, kind):
        return self.execute("Načtení zdrojů", lambda client, task: client.list_files() if kind == "files" else client.list_vector_stores(),
                            lambda records: fill_records(self.lists[kind], records))

    def attach(self, kind):
        values = list(dict.fromkeys([*getattr(self.context, kind), *selected_ids(self.lists[kind])]))
        setattr(self.context, kind, values)
        self.context.attachments_changed.emit()

    def detach(self, kind):
        values = selected_ids(self.lists[kind])
        setattr(self.context, kind, [value for value in getattr(self.context, kind) if value not in values])
        self.context.attachments_changed.emit()

    def upload(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Nahrát soubory")
        if not paths:
            return

        def upload(client, task):
            from kajovo.core.progress import ProgressEvent
            for index, path in enumerate(paths):
                client.upload_file(path)
                task.progress_event.emit(ProgressEvent("Nahrávání souborů", completed=index + 1, total=len(paths), unit="souborů"))
            return client.list_files()

        self.execute("Nahrávání souborů", upload, lambda records: fill_records(self.lists["files"], records))

    def create_store(self):
        dialog = ValueDialog("Nové úložiště", "Název vyhledávacího úložiště", parent=self)
        if dialog.exec() == QDialog.Accepted:
            name = dialog.value

            def create(client, task):
                client.create_vector_store(name)
                return client.list_vector_stores()

            self.execute("Vytvoření úložiště", create, lambda rows: fill_records(self.lists["stores"], rows))

    def delete(self, kind, all_items=False):
        listing = self.lists[kind]
        identifiers = [listing.item(i).data(Qt.UserRole) for i in range(listing.count())] if all_items else selected_ids(listing)
        if not identifiers or not confirm(self, "Odstranit vzdálené prostředky", f"Trvale odstranit {len(identifiers)} položek ze služby?"):
            return

        key = self.context.api_key

        def remove(client, task):
            for identifier in identifiers:
                (client.delete_file if kind == "files" else client.delete_vector_store)(identifier)
                self.resource_deleted.emit(key, kind, identifier)
            return client.list_files() if kind == "files" else client.list_vector_stores()

        def receive(records):
            fill_records(listing, records)
            setattr(self.context, kind, [value for value in getattr(self.context, kind) if value not in identifiers])
            self.context.attachments_changed.emit()

        self.execute("Odstranění prostředků", remove, receive)

    @Slot(str, str, str)
    def _prune_deleted(self, key, kind, identifier):
        if key != self.context.api_key:
            return
        setattr(self.context, kind, [value for value in getattr(self.context, kind) if value != identifier])
        listing = self.lists[kind]
        for index in reversed(range(listing.count())):
            if listing.item(index).data(Qt.UserRole) == identifier:
                listing.takeItem(index)
        self.context.attachments_changed.emit()

    def current_store(self):
        item = self.lists["stores"].currentItem()
        return item.data(Qt.UserRole) if item else None

    def refresh_store_files(self):
        identifier = self.current_store()
        if identifier:
            self.execute("Načtení souborů úložiště", lambda client, task: client.list_vector_store_files(identifier),
                         self.store_receiver(identifier))

    def add_store_files(self):
        dialog = ValueDialog("Přidání souborů", "Identifikátory souborů, každý na samostatném řádku", parent=self)
        if dialog.exec() == QDialog.Accepted:
            self.add_files(dialog.value.splitlines())

    def add_selected_files(self):
        self.add_files(selected_ids(self.lists["files"]))

    def add_files(self, identifiers):
        store = self.current_store()
        identifiers = list(dict.fromkeys(value.strip() for value in identifiers if value.strip()))
        if not store or not identifiers:
            self.notice.setText("Vyberte úložiště i soubory k přidání.")
            return

        def add(client, task):
            for identifier in identifiers:
                client.add_file_to_vector_store(store, identifier)
            return client.list_vector_store_files(store)

        self.execute("Přidání souborů do úložiště", add, self.store_receiver(store))

    def remove_store_files(self):
        store = self.current_store()
        identifiers = selected_ids(self.store_files)
        if store and identifiers and confirm(self, "Odebrat z úložiště", "Odebrat vybrané soubory z úložiště? Samotné nahrané soubory zůstanou ve službě."):
            def remove(client, task):
                for identifier in identifiers:
                    client.delete_vector_store_file(store, identifier)
                return client.list_vector_store_files(store)

            self.execute("Odebrání z úložiště", remove, self.store_receiver(store))

    def attributes(self):
        store = self.current_store()
        item = self.store_files.currentItem()
        if not store or not item:
            return
        identifier = item.data(Qt.UserRole)
        attrs = item.data(Qt.UserRole + 1).get("attributes", {})
        dialog = ValueDialog("Atributy souboru", "Atributy ve formátu JSON", json.dumps(attrs, ensure_ascii=False, indent=2), self, structured=True)
        if dialog.exec() == QDialog.Accepted:
            if not isinstance(dialog.value, dict):
                self.notice.setText("Atributy musí být JSON objekt.")
                return
            generation = self.store_selection_generation
            def receive(value):
                if store != self.current_store() or generation != self.store_selection_generation:
                    return
                for index in range(self.store_files.count()):
                    current = self.store_files.item(index)
                    if current.data(Qt.UserRole) == identifier:
                        current.setData(Qt.UserRole + 1, value)
            self.execute("Uložení atributů", lambda client, task: client.update_vector_store_file_attributes(store, identifier, dialog.value),
                         receive)

    def details(self):
        store = self.current_store()
        item = self.store_files.currentItem()
        if store and item:
            identifier = item.data(Qt.UserRole)
            self.execute("Načtení podrobností souboru", lambda client, task: client.retrieve_vector_store_file(store, identifier),
                         lambda value: DetailDialog("Soubor v úložišti", identifier, self, value).exec())
