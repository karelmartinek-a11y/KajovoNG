"""Správa souborů a vyhledávacích úložišť s explicitním výběrem příloh."""

import json
from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import (
    QWidget,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QTabWidget,
    QPushButton,
)
from ..core.openai_client import OpenAIClient
from .design import column, row, card, button, label, text, editor
from .dialogs import msg_info, msg_warning, msg_question, dialog_open_file, dialog_input_text
from .jobs import Jobs


class ResourcePage(QWidget):
    attached_changed = Signal(list)
    logline = Signal(str)

    def __init__(self, settings, api_key, parent=None):
        super().__init__(parent)
        self.s = settings
        self.api_key = api_key
        self.jobs = Jobs(self)
        self.jobs.busy_changed.connect(self._busy)
        self.client = None
        self.attached = []

    def _busy(self, active):
        for action in self.findChildren(QPushButton):
            if action.window() is self.window():
                action.setEnabled(not active)

    def set_api_key(self, api_key):
        self.api_key = api_key
        self.client = None
        self.clear_attached()
        self.lst_files.clear()
        if hasattr(self, "lst_vs"):
            self.lst_vs.clear()

    def client_for(self, key):
        return self.client or OpenAIClient(key, timeout_s=self.s.response_timeout_s)

    def operation(self, title, call, done=lambda result: None, cancellable=False, upload=False):
        if self.jobs.active:
            return
        if not self.api_key:
            msg_warning(self, title, "Nejdříve uložte API klíč v Nastavení.")
            return
        key = self.api_key

        def execute(job):
            client = self.client_for(key)
            return call(client, job)

        def receive(result):
            if key == self.api_key:
                done(result)

        self.jobs.start(title, execute, receive, cancellable=cancellable, upload=upload)

    @staticmethod
    def fill(widget, records):
        selected = {item.data(Qt.UserRole) for item in widget.selectedItems()}
        widget.clear()
        for record in records:
            identifier = record.get("id", "")
            title = record.get("filename") or record.get("name") or identifier
            status = record.get("status")
            item = QListWidgetItem(f"{title}\n{identifier}" + (f" · {status}" if status else ""))
            item.setData(Qt.UserRole, identifier)
            widget.addItem(item)
            item.setSelected(identifier in selected)

    def attached_ids(self):
        return list(self.attached)

    def set_attached(self, values):
        self.attached = list(dict.fromkeys(values))
        self.lst_attached.clear()
        self.lst_attached.addItems(self.attached)
        self.attached_changed.emit(self.attached_ids())

    def clear_attached(self):
        self.set_attached([])

    def detach_selected(self):
        remove = {item.text() for item in self.lst_attached.selectedItems()}
        self.set_attached([value for value in self.attached if value not in remove])


class FilesPanel(ResourcePage):
    def __init__(self, settings, api_key, parent=None):
        super().__init__(settings, api_key, parent)
        layout = column(self)
        box, body = card(
            "Soubory API",
            "Nahrané soubory můžete připojit k zadání nebo vložit do vektorového úložiště.",
        )
        self.btn_refresh = button("Obnovit", self.refresh)
        self.btn_upload = button("Nahrát soubor", self.upload, "Primary")
        self.btn_delete = button("Smazat vybrané", self.delete_selected, "Danger")
        self.btn_delete_all = button("Smazat všechny", self.delete_all, "Danger")
        body.addWidget(row(self.btn_refresh, self.btn_upload))
        self.lst_files = QListWidget()
        self.lst_files.setSelectionMode(QListWidget.ExtendedSelection)
        body.addWidget(self.lst_files, 1)
        body.addWidget(row(self.btn_delete, self.btn_delete_all))
        layout.addWidget(box, 2)
        attached, controls = card(
            "Připojeno k zadání", "Připojení nepřesouvá ani nemaže soubor na serveru."
        )
        self.lst_attached = QListWidget()
        self.lst_attached.setMaximumHeight(140)
        self.lst_attached.setSelectionMode(QListWidget.ExtendedSelection)
        controls.addWidget(self.lst_attached)
        self.btn_attach = button("Připojit vybrané", self.attach_selected)
        self.btn_detach = button("Odpojit vybrané", self.detach_selected)
        controls.addWidget(row(self.btn_attach, self.btn_detach))
        layout.addWidget(attached, 1)

    def refresh(self):
        self.operation(
            "Načtení souborů", lambda client, job: client.list_files(), self._apply_files_list
        )

    def _apply_files_list(self, records):
        self.fill(self.lst_files, records)
        existing = {record["id"] for record in records}
        self.set_attached([value for value in self.attached if value in existing])

    def attach_selected(self):
        self.set_attached(
            self.attached + [item.data(Qt.UserRole) for item in self.lst_files.selectedItems()]
        )

    def upload(self):
        path, _ = dialog_open_file(self, "Nahrát soubor")
        if path:

            def execute(client, job):
                job.check_stop()
                job.status.emit(f"Nahrávám soubor: {path}")
                result = client.upload_file(path, purpose="user_data")
                job.logline.emit(f"Nahráno: {result.get('id', 'viz seznam souborů')}")
                return result

            self.operation("Nahrávání souboru", execute, lambda result: self.refresh(), True, True)

    def delete_selected(self):
        self._delete([item.data(Qt.UserRole) for item in self.lst_files.selectedItems()])

    def delete_all(self):
        self._delete(
            [
                self.lst_files.item(index).data(Qt.UserRole)
                for index in range(self.lst_files.count())
            ]
        )

    def _delete(self, identifiers):
        if not identifiers:
            msg_info(self, "Mazání souborů", "Vyberte soubory v seznamu.")
            return
        if (
            msg_question(
                self,
                "Smazat soubory na serveru?",
                f"Počet souborů: {len(identifiers)}. Smazání není možné vrátit.",
                identifiers,
            )
            != QMessageBox.Yes
        ):
            return

        def execute(client, job):
            errors = []
            for index, identifier in enumerate(identifiers):
                job.check_stop()
                job.status.emit(f"Mažu {index + 1}/{len(identifiers)}: {identifier}")
                try:
                    client.delete_file(identifier)
                except Exception as exc:
                    errors.append(f"{identifier}: {exc}")
                job.progress.emit(round(100 * (index + 1) / len(identifiers)))
            return client.list_files(), errors

        def receive(result):
            records, errors = result
            self._apply_files_list(records)
            if errors:
                msg_warning(self, "Mazání dokončeno s chybami", "\n".join(errors))

        self.operation("Mazání souborů", execute, receive, True)


class VectorStoresPanel(ResourcePage):
    def __init__(self, settings, api_key, parent=None):
        super().__init__(settings, api_key, parent)
        layout = column(self)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        stores = QWidget()
        body = column(stores, 12)
        body.addWidget(
            label("Úložiště pro hledání v souborech. Model musí podporovat file_search.", "Hint")
        )
        body.addWidget(
            row(
                button("Obnovit", self.refresh),
                button("Nové úložiště", self.create_store, "Primary"),
            )
        )
        self.lst_vs = QListWidget()
        self.lst_vs.setSelectionMode(QListWidget.ExtendedSelection)
        body.addWidget(self.lst_vs, 1)
        body.addWidget(
            row(
                button("Smazat vybraná", self.delete_selected_store, "Danger"),
                button("Smazat všechna", self.delete_all_stores, "Danger"),
            )
        )
        self.lst_attached = QListWidget()
        self.lst_attached.setSelectionMode(QListWidget.ExtendedSelection)
        self.lst_attached.setMaximumHeight(100)
        body.addWidget(label("Připojeno k zadání"))
        body.addWidget(self.lst_attached)
        body.addWidget(
            row(
                button("Připojit", self.attach_selected),
                button("Odpojit", self.detach_selected),
                button("Otevřít soubory", self.list_files),
            )
        )
        self.tabs.addTab(stores, "Úložiště")
        files = QWidget()
        fl = column(files, 12)
        self.selected_store = label("Nejdříve vyberte úložiště.")
        fl.addWidget(self.selected_store)
        self.ed_file_id = text(placeholder="file_id ze Soubory API")
        fl.addWidget(self.ed_file_id)
        fl.addWidget(
            row(
                button("Přidat podle ID", self.add_file),
                button("Vybrat z API", self.add_files_from_api),
                button("Obnovit soubory", self.list_files),
            )
        )
        self.lst_files = QListWidget()
        fl.addWidget(self.lst_files, 1)
        fl.addWidget(
            row(
                button("Podrobnosti a atributy", self.show_selected_file_details),
                button("Odebrat z úložiště", self.remove_selected_file, "Danger"),
            )
        )
        self.tabs.addTab(files, "Soubory úložiště")
        detail = QWidget()
        dl = column(detail, 12)
        self.txt_detail = editor(readonly=True)
        self.txt_attrs = editor("{}")
        dl.addWidget(label("Podrobnosti souboru"))
        dl.addWidget(self.txt_detail, 1)
        dl.addWidget(label("Atributy (JSON objekt)"))
        dl.addWidget(self.txt_attrs, 1)
        dl.addWidget(button("Uložit atributy", self.save_selected_attrs, "Primary"))
        self.tabs.addTab(detail, "Detail souboru")
        self._detail_ids = None

    def refresh(self):
        self.operation(
            "Načtení úložišť",
            lambda client, job: client.list_vector_stores(),
            self._apply_vector_store_list,
        )

    def _apply_vector_store_list(self, records):
        self.fill(self.lst_vs, records)
        existing = {record["id"] for record in records}
        self.set_attached([value for value in self.attached if value in existing])

    def _selected_vs_id(self):
        item = self.lst_vs.currentItem()
        return item.data(Qt.UserRole) if item else None

    def _selected_vs_file_id(self):
        item = self.lst_files.currentItem()
        return item.data(Qt.UserRole) if item else None

    def attach_selected(self):
        self.set_attached(
            self.attached + [item.data(Qt.UserRole) for item in self.lst_vs.selectedItems()]
        )

    def create_store(self):
        name, accepted = dialog_input_text(self, "Nové úložiště", "Název úložiště:")
        if accepted and name.strip():
            self.operation(
                "Vytvoření úložiště",
                lambda client, job: client.create_vector_store(name.strip()),
                lambda result: self.refresh(),
            )

    def list_files(self):
        identifier = self._selected_vs_id()
        if not identifier:
            msg_info(self, "Úložiště", "Vyberte úložiště.")
            return
        self.selected_store.setText(identifier)
        self.operation(
            "Soubory úložiště",
            lambda client, job: client.list_vector_store_files(identifier),
            lambda records: self.fill(self.lst_files, records),
        )
        self.tabs.setCurrentIndex(1)

    def add_file(self):
        self._add([self.ed_file_id.text().strip()])

    def _add(self, identifiers):
        store = self._selected_vs_id()
        if not store or not identifiers or not all(identifiers):
            msg_warning(self, "Přidání souborů", "Vyberte úložiště a platné file_id.")
            return

        def execute(client, job):
            errors = []
            for index, identifier in enumerate(identifiers):
                job.check_stop()
                job.status.emit(f"Přidávám {identifier}")
                try:
                    client.add_file_to_vector_store(store, identifier)
                except Exception as exc:
                    errors.append(f"{identifier}: {exc}")
                job.progress.emit(round(100 * (index + 1) / len(identifiers)))
            return errors

        def receive(errors):
            self.list_files()
            if errors:
                msg_warning(self, "Přidání souborů", "\n".join(errors))

        self.operation("Přidávání do úložiště", execute, receive, True)

    def add_files_from_api(self):
        def receive(records):
            from PySide6.QtWidgets import QDialog, QDialogButtonBox

            dialog = QDialog(self)
            dialog.setWindowTitle("Vybrat soubory z API")
            dialog.resize(650, 500)
            body = column(dialog, 20)
            query = text(placeholder="Filtrovat název nebo ID")
            listing = QListWidget()
            listing.setSelectionMode(QListWidget.ExtendedSelection)
            self.fill(listing, records)

            def filter_items(value):
                for index in range(listing.count()):
                    item = listing.item(index)
                    item.setHidden(value.lower() not in item.text().lower())

            query.textChanged.connect(filter_items)
            body.addWidget(query)
            body.addWidget(listing, 1)
            buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
            buttons.accepted.connect(dialog.accept)
            buttons.rejected.connect(dialog.reject)
            body.addWidget(buttons)
            if dialog.exec() == QDialog.Accepted:
                self._add([item.data(Qt.UserRole) for item in listing.selectedItems()])

        self.operation("Načtení souborů API", lambda client, job: client.list_files(), receive)

    def remove_selected_file(self):
        store, identifier = self._selected_vs_id(), self._selected_vs_file_id()
        if (
            store
            and identifier
            and msg_question(
                self,
                "Odebrat soubor?",
                f"Odebrat {identifier} z {store}? Původní soubor v API zůstane zachován.",
            )
            == QMessageBox.Yes
        ):
            self.operation(
                "Odebrání souboru",
                lambda client, job: client.delete_vector_store_file(store, identifier),
                lambda result: self.list_files(),
            )

    def show_selected_file_details(self):
        store, identifier = self._selected_vs_id(), self._selected_vs_file_id()
        if not store or not identifier:
            msg_info(self, "Detail souboru", "Vyberte soubor úložiště.")
            return

        def receive(record):
            self._detail_ids = (store, identifier)
            self.txt_detail.setPlainText(json.dumps(record, ensure_ascii=False, indent=2))
            self.txt_attrs.setPlainText(
                json.dumps(record.get("attributes") or {}, ensure_ascii=False, indent=2)
            )
            self.tabs.setCurrentIndex(2)

        self.operation(
            "Podrobnosti souboru",
            lambda client, job: client.retrieve_vector_store_file(store, identifier),
            receive,
        )

    def save_selected_attrs(self):
        try:
            attrs = json.loads(self.txt_attrs.toPlainText())
            if not isinstance(attrs, dict) or not self._detail_ids:
                raise ValueError("Načtěte detail souboru a zadejte JSON objekt atributů.")
        except ValueError as exc:
            msg_warning(self, "Atributy", str(exc))
            return
        store, identifier = self._detail_ids
        self.operation(
            "Uložení atributů",
            lambda client, job: client.update_vector_store_file_attributes(
                store, identifier, attrs
            ),
            lambda result: msg_info(self, "Atributy", "Atributy uloženy."),
        )

    def delete_selected_store(self):
        self._delete([item.data(Qt.UserRole) for item in self.lst_vs.selectedItems()])

    def delete_all_stores(self):
        self._delete(
            [self.lst_vs.item(index).data(Qt.UserRole) for index in range(self.lst_vs.count())]
        )

    def _delete(self, identifiers):
        if not identifiers:
            msg_info(self, "Mazání úložišť", "Vyberte úložiště.")
            return
        if (
            msg_question(
                self,
                "Smazat úložiště?",
                f"Počet úložišť: {len(identifiers)}. Odeberou se jejich soubory a smažou se úložiště.",
                identifiers,
            )
            != QMessageBox.Yes
        ):
            return

        def execute(client, job):
            errors = []
            for index, identifier in enumerate(identifiers):
                job.check_stop()
                try:
                    for record in client.list_vector_store_files(identifier):
                        job.check_stop()
                        job.status.emit(f"{identifier}: odebírám {record['id']}")
                        client.delete_vector_store_file(identifier, record["id"])
                    client.delete_vector_store(identifier)
                except Exception as exc:
                    errors.append(f"{identifier}: {exc}")
                job.progress.emit(round(100 * (index + 1) / len(identifiers)))
            return client.list_vector_stores(), errors

        def receive(result):
            records, errors = result
            self._apply_vector_store_list(records)
            if errors:
                msg_warning(self, "Mazání dokončeno s chybami", "\n".join(errors))

        self.operation("Mazání úložišť", execute, receive, True)
