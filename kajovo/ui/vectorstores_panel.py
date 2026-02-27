from __future__ import annotations
from .widgets import msg_info, msg_warning, msg_critical, msg_question, dialog_input_text

import time
from typing import List, Optional, Dict, Any

from PySide6.QtCore import Signal, QThread
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QListWidget, QListWidgetItem,
    QLineEdit, QLabel, QMessageBox, QSplitter, QDialog, QDialogButtonBox, QPlainTextEdit
)

from ..core.openai_client import OpenAIClient
from ..core.retry import with_retry, CircuitBreaker
from .widgets import BusyPopup
from .task_progress_dialog import TaskProgressDialog


class VectorStoresDeleteWorker(QThread):
    progress = Signal(int)
    subprogress = Signal(int)
    status = Signal(str)
    logline = Signal(str)
    finished = Signal(object, list, int, int)  # stores or None, errors, deleted, total

    def __init__(self, api_key: str, store_ids: List[str], retry_cfg, breaker_failures: int, breaker_cooldown_s: int):
        super().__init__()
        self.api_key = api_key
        self.store_ids = list(store_ids)
        self.retry_cfg = retry_cfg
        self.breaker_failures = breaker_failures
        self.breaker_cooldown_s = breaker_cooldown_s

    def run(self):
        stores: List[dict] | None = None
        errors: List[str] = []
        deleted = 0
        total = len(self.store_ids)
        if not self.api_key:
            self.finished.emit(stores, ["Chybí OPENAI_API_KEY."], deleted, total)
            return
        client = OpenAIClient(self.api_key)
        breaker = CircuitBreaker(self.breaker_failures, self.breaker_cooldown_s)
        self.progress.emit(0)
        self.subprogress.emit(0)
        for idx, vs_id in enumerate(self.store_ids):
            step = idx + 1
            self.status.emit(f"Store {step}/{total}: {vs_id}")
            self.logline.emit(f"Načítám soubory ve store {vs_id}...")
            files: List[Dict[str, Any]] = []
            try:
                files = with_retry(lambda: client.list_vector_store_files(vs_id), self.retry_cfg, breaker)
            except Exception as e:
                errors.append(f"{vs_id}: nelze načíst soubory ({e})")
                self.logline.emit(f"Chyba při načítání souborů {vs_id}: {e}")
                self.progress.emit(int(step * 100 / max(1, total)))
                continue

            if files:
                self.logline.emit(f"Mažu {len(files)} souborů ve store {vs_id}...")
            for f_idx, f in enumerate(files):
                fid = f.get("id")
                if not fid:
                    continue
                self.status.emit(f"Mažu soubor {f_idx + 1}/{len(files)} ve store {vs_id}")
                self.subprogress.emit(int((f_idx + 1) * 100 / max(1, len(files))))
                try:
                    with_retry(lambda fid=fid: client.delete_vector_store_file(vs_id, fid), self.retry_cfg, breaker)
                except Exception as e:
                    errors.append(f"{vs_id}/{fid}: {e}")
                    self.logline.emit(f"Chyba při mazání souboru {fid}: {e}")

            if files:
                self.logline.emit(f"Removed {len(files)} files from vector store {vs_id}")
            self.status.emit(f"Mažu vector store {vs_id}")
            try:
                with_retry(lambda: client.delete_vector_store(vs_id), self.retry_cfg, breaker)
                deleted += 1
                self.logline.emit(f"Deleted vector store: {vs_id}")
            except Exception as e:
                errors.append(f"{vs_id}: {e}")
                self.logline.emit(f"Chyba při mazání store {vs_id}: {e}")
            self.subprogress.emit(0)
            self.progress.emit(int(step * 100 / max(1, total)))

        try:
            stores = with_retry(lambda: client.list_vector_stores(), self.retry_cfg, breaker)
        except Exception as e:
            self.logline.emit(f"Refresh failed: {e}")
        self.finished.emit(stores, errors, deleted, total)


class FilesSelectorDialog(QDialog):
    def __init__(self, client: OpenAIClient, retry_cfg, breaker: CircuitBreaker, parent=None):
        super().__init__(parent)
        self.client = client
        self.retry_cfg = retry_cfg
        self.breaker = breaker
        self.setWindowTitle("Vybrat soubory z Files API")
        self.resize(700, 500)
        from .theme import DARK_STYLESHEET
        self.setStyleSheet(DARK_STYLESHEET)

        v = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel("Filtrovat:"))
        self.ed_filter = QLineEdit()
        self.ed_filter.setPlaceholderText("text v id/filename")
        top.addWidget(self.ed_filter, 1)
        self.btn_refresh = QPushButton("Refresh")
        top.addWidget(self.btn_refresh)
        v.addLayout(top)

        self.lst = QListWidget()
        self.lst.setSelectionMode(QListWidget.MultiSelection)
        v.addWidget(self.lst, 1)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        v.addWidget(btns)

        self.ed_filter.textChanged.connect(self.apply_filter)
        self.btn_refresh.clicked.connect(self.load_files)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)

        self._all_files: List[Dict[str, Any]] = []
        self.load_files()

    def load_files(self):
        with BusyPopup(self, "Načítám Files API..."):
            try:
                self._all_files = with_retry(lambda: self.client.list_files(), self.retry_cfg, self.breaker)
            except Exception as e:
                msg_critical(self, "Files API", str(e))
                self._all_files = []
        self.apply_filter()

    def apply_filter(self):
        text = (self.ed_filter.text() or "").lower()
        self.lst.clear()
        for f in self._all_files:
            fid = f.get("id", "")
            name = f.get("filename", "")
            label = f"{fid}  |  {name}"
            if text and text not in fid.lower() and text not in name.lower():
                continue
            it = QListWidgetItem(label)
            it.setData(32, fid)
            self.lst.addItem(it)

    def selected_ids(self) -> List[str]:
        return [it.data(32) for it in self.lst.selectedItems() if it.data(32)]


class VectorStoresPanel(QWidget):
    logline = Signal(str)
    attached_changed = Signal(list)  # list[str]

    def __init__(self, settings, api_key: str, parent=None):
        super().__init__(parent)
        self.s = settings
        self.api_key = api_key
        self.client: Optional[OpenAIClient] = None
        self.breaker = CircuitBreaker(self.s.retry.circuit_breaker_failures, self.s.retry.circuit_breaker_cooldown_s)
        self._delete_worker: VectorStoresDeleteWorker | None = None
        self._delete_dialog: TaskProgressDialog | None = None

        v = QVBoxLayout(self)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(8)

        top = QHBoxLayout()
        self.btn_refresh = QPushButton("Refresh")
        self.btn_create = QPushButton("Create")
        self.btn_delete = QPushButton("Delete")
        self.btn_delete_all = QPushButton("Del ALL")
        self.btn_refresh.setToolTip("Načte seznam vector store.")
        self.btn_create.setToolTip("Vytvoří novou vector store.")
        self.btn_delete.setToolTip("Smaže vybrané vector store (včetně souborů).")
        self.btn_delete_all.setToolTip("Smaže všechny vector store (včetně souborů).")
        top.addWidget(self.btn_refresh)
        top.addWidget(self.btn_create)
        top.addWidget(self.btn_delete)
        top.addWidget(self.btn_delete_all)
        top.addStretch(1)
        v.addLayout(top)

        split = QSplitter()
        v.addWidget(split, 1)

        # Left: vector stores
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.addWidget(QLabel("Vector stores"))
        self.lst_vs = QListWidget()
        self.lst_vs.setSelectionMode(QListWidget.MultiSelection)
        lv.addWidget(self.lst_vs, 1)

        attach_row = QHBoxLayout()
        self.btn_attach = QPushButton("Attach →")
        self.btn_attach.setToolTip("Připojí vybrané vector_store_id do RUN requestu.")
        self.btn_detach = QPushButton("← Detach")
        self.btn_detach.setToolTip("Odebere vybrané vector_store_id z RUN requestu.")
        attach_row.addStretch(1)
        attach_row.addWidget(self.btn_attach)
        attach_row.addWidget(self.btn_detach)
        lv.addLayout(attach_row)

        lv.addWidget(QLabel("Attached to RUN"))
        self.lst_attached = QListWidget()
        self.lst_attached.setSelectionMode(QListWidget.MultiSelection)
        lv.addWidget(self.lst_attached, 1)

        # Middle: files in selected vector store
        middle = QWidget()
        rv = QVBoxLayout(middle)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.addWidget(QLabel("Files in selected store"))

        file_row = QHBoxLayout()
        self.ed_file_id = QLineEdit()
        self.ed_file_id.setPlaceholderText("file_id (from Files API)")
        self.ed_file_id.setToolTip("Zadej file_id z Files API, které chceš přidat do vybrané vector store.")
        self.btn_add_file = QPushButton("Add file")
        self.btn_add_file.setToolTip("Přidá file_id do vybrané vector store.")
        self.btn_add_from_files = QPushButton("Add z Files API")
        self.btn_add_from_files.setToolTip("Otevře výběr souborů z Files API a přidá je do store.")
        self.btn_list_files = QPushButton("List files")
        self.btn_list_files.setToolTip("Načte soubory ve vybrané vector store.")
        self.btn_remove_file = QPushButton("Remove selected")
        self.btn_remove_file.setToolTip("Odebere vybrané soubory z store.")
        file_row.addWidget(self.ed_file_id, 1)
        file_row.addWidget(self.btn_add_file)
        file_row.addWidget(self.btn_add_from_files)
        file_row.addWidget(self.btn_list_files)
        file_row.addWidget(self.btn_remove_file)
        rv.addLayout(file_row)

        self.lst_files = QListWidget()
        rv.addWidget(self.lst_files, 1)

        # Right: details of selected file
        detail = QWidget()
        dv = QVBoxLayout(detail)
        dv.setContentsMargins(0, 0, 0, 0)
        dv.addWidget(QLabel("Detaily souboru"))

        self.lbl_detail = QLabel("—")
        dv.addWidget(self.lbl_detail)

        dv.addWidget(QLabel("Atributy ve Vector Store (JSON):"))
        self.ed_vs_attrs = QPlainTextEdit()
        self.ed_vs_attrs.setPlaceholderText('{"klic": "hodnota"}')
        dv.addWidget(self.ed_vs_attrs, 1)

        dv.addWidget(QLabel("Informace z Files API (jen čtení):"))
        self.ed_file_info = QPlainTextEdit()
        self.ed_file_info.setReadOnly(True)
        dv.addWidget(self.ed_file_info, 1)

        btn_detail = QHBoxLayout()
        self.btn_save_attrs = QPushButton("Uložit atributy")
        self.btn_save_attrs.setToolTip("Uloží JSON atributy k vybranému souboru ve store.")
        self.btn_refresh_detail = QPushButton("Obnovit detail")
        self.btn_refresh_detail.setToolTip("Znovu načte detail vybraného souboru (store + Files API).")
        btn_detail.addWidget(self.btn_save_attrs)
        btn_detail.addWidget(self.btn_refresh_detail)
        dv.addLayout(btn_detail)

        split.addWidget(left)
        split.addWidget(middle)
        split.addWidget(detail)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 2)
        split.setStretchFactor(2, 2)

        self.btn_refresh.clicked.connect(self.refresh)
        self.btn_create.clicked.connect(self.create_store)
        self.btn_delete.clicked.connect(self.delete_selected_store)
        self.btn_delete_all.clicked.connect(self.delete_all_stores)
        self.btn_list_files.clicked.connect(self.list_files)
        self.btn_add_file.clicked.connect(self.add_file)
        self.btn_add_from_files.clicked.connect(self.add_files_from_api)
        self.btn_remove_file.clicked.connect(self.remove_selected_file)
        self.btn_save_attrs.clicked.connect(self.save_selected_attrs)
        self.btn_refresh_detail.clicked.connect(self.show_selected_file_details)
        self.btn_attach.clicked.connect(self.attach_selected)
        self.btn_detach.clicked.connect(self.detach_selected)

        self.lst_vs.itemSelectionChanged.connect(self.list_files)
        self.lst_vs.itemSelectionChanged.connect(self._update_controls)
        self.lst_files.itemSelectionChanged.connect(self.show_selected_file_details)
        self.lst_files.itemSelectionChanged.connect(self._update_controls)
        self.ed_file_id.textChanged.connect(self._update_controls)
        self._update_controls()

        self.refresh()

        self._cur_files: Dict[str, Dict[str, Any]] = {}


    def _update_controls(self) -> None:
        has_key = bool(self.api_key)
        has_vs = bool(self._selected_vs_id())
        has_vs_sel_any = bool(self.lst_vs.selectedItems())
        has_file_sel = bool(self.lst_files.selectedItems())
        file_id_ok = bool((self.ed_file_id.text() or "").strip())

        self.btn_refresh.setEnabled(has_key)
        self.btn_create.setEnabled(has_key)
        self.btn_delete.setEnabled(has_key and has_vs_sel_any)
        self.btn_delete_all.setEnabled(has_key and self.lst_vs.count() > 0)

        self.btn_list_files.setEnabled(has_key and has_vs)
        self.btn_add_file.setEnabled(has_key and has_vs and file_id_ok)
        self.btn_add_from_files.setEnabled(has_key and has_vs)
        self.btn_remove_file.setEnabled(has_key and has_vs and has_file_sel)

        self.btn_save_attrs.setEnabled(has_key and has_vs and has_file_sel)
        self.btn_refresh_detail.setEnabled(has_key and has_vs and has_file_sel)

        self.btn_attach.setEnabled(has_key and has_vs_sel_any)
        self.btn_detach.setEnabled(has_key and bool(self.lst_attached.selectedItems()))

    def set_api_key(self, api_key: str):
        self.api_key = api_key
        self.client = None
        self.refresh()

    def _need_client(self) -> bool:
        if not self.api_key:
            msg_warning(self, "OpenAI", "Nejdřív nastav OPENAI_API_KEY.")
            return False
        if self.client is None:
            self.client = OpenAIClient(self.api_key)
        return True

    def refresh(self):
        self.lst_vs.clear()
        self.lst_files.clear()
        self.lbl_detail.setText("—")
        self.ed_vs_attrs.setPlainText("")
        self.ed_file_info.setPlainText("")
        if not self._need_client():
            return
        with BusyPopup(self, "Načítám vector stores..."):
            try:
                data = with_retry(lambda: self.client.list_vector_stores(), self.s.retry, self.breaker)
                for vs in data:
                    vs_id = vs.get("id", "")
                    name = vs.get("name", "") or ""
                    created = vs.get("created_at")
                    created_s = ""
                    try:
                        if created:
                            created_s = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(created)))
                    except Exception:
                        created_s = str(created or "")
                    label = f"{vs_id}  |  {name}  |  {created_s}"
                    it = QListWidgetItem(label)
                    it.setData(32, vs_id)
                    self.lst_vs.addItem(it)
                self.logline.emit(f"Vector stores: {len(data)}")
                self._prune_attached()
            except Exception as e:
                msg_critical(self, "Vector stores", str(e))

        self._update_controls()

    def _apply_vector_store_list(self, data: List[dict]) -> None:
        self.lst_vs.clear()
        self.lst_files.clear()
        self.lbl_detail.setText("—")
        self.ed_vs_attrs.setPlainText("")
        self.ed_file_info.setPlainText("")
        for vs in data or []:
            vs_id = vs.get("id", "")
            name = vs.get("name", "") or ""
            created = vs.get("created_at")
            created_s = ""
            try:
                if created:
                    created_s = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(created)))
            except Exception:
                created_s = str(created or "")
            label = f"{vs_id}  |  {name}  |  {created_s}"
            it = QListWidgetItem(label)
            it.setData(32, vs_id)
            self.lst_vs.addItem(it)
        self.logline.emit(f"Vector stores: {len(data or [])}")
        self._prune_attached()

    def _set_delete_controls_enabled(self, enabled: bool) -> None:
        self.btn_refresh.setEnabled(enabled)
        self.btn_create.setEnabled(enabled)
        self.btn_delete.setEnabled(enabled)
        self.btn_delete_all.setEnabled(enabled)
        self.btn_list_files.setEnabled(enabled)
        self.btn_add_file.setEnabled(enabled)
        self.btn_add_from_files.setEnabled(enabled)
        self.btn_remove_file.setEnabled(enabled)
        self.btn_save_attrs.setEnabled(enabled)
        self.btn_refresh_detail.setEnabled(enabled)
        self.btn_attach.setEnabled(enabled)
        self.btn_detach.setEnabled(enabled)

    def _start_delete_worker(self, store_ids: List[str], title: str) -> None:
        if self._delete_worker:
            msg_info(self, "Delete", "Mazání už běží.")
            return
        dialog = TaskProgressDialog(title, self, show_subprogress=True)
        dialog.set_status(f"Připravuji mazání {len(store_ids)} store...")
        dialog.add_log(f"Počet store: {len(store_ids)}")
        worker = VectorStoresDeleteWorker(
            self.api_key,
            store_ids,
            self.s.retry,
            self.s.retry.circuit_breaker_failures,
            self.s.retry.circuit_breaker_cooldown_s,
        )
        self._delete_worker = worker
        self._delete_dialog = dialog
        self._set_delete_controls_enabled(False)

        worker.progress.connect(dialog.set_progress)
        worker.subprogress.connect(dialog.set_subprogress)
        worker.status.connect(dialog.set_status)
        worker.logline.connect(dialog.add_log)

        def on_done(stores: List[dict] | None, errors: List[str], deleted: int, total: int):
            dialog.mark_done(f"Mazání dokončeno ({deleted}/{total}).")
            if stores is not None:
                self._apply_vector_store_list(stores)
            if errors:
                msg_warning(self, "Delete", f"Smazáno: {deleted} / {total}\nChyby:\n" + "\n".join(errors[:6]))
            else:
                msg_info(self, "Delete", f"Smazáno {deleted} vector store.")
            worker.deleteLater()
            self._delete_worker = None
            self._delete_dialog = None
            self._set_delete_controls_enabled(True)

        worker.finished.connect(on_done)
        dialog.show()
        worker.start()

    def _selected_vs_id(self) -> Optional[str]:
        sel = self.lst_vs.selectedItems()
        if not sel:
            return None
        # If multiple selected, use the first one for single actions.
        return sel[0].data(32)

    def create_store(self):
        if not self._need_client():
            return
        name, ok = dialog_input_text(self, "Create vector store", "Name")
        if not ok or not name.strip():
            return
        with BusyPopup(self, "Vytvářím vector store..."):
            try:
                vs = with_retry(lambda: self.client.create_vector_store(name.strip()), self.s.retry, self.breaker)
                self.logline.emit(f"Created vector store: {vs.get('id')} ({vs.get('name')})")
                self.refresh()
            except Exception as e:
                msg_critical(self, "Create", str(e))

    def delete_selected_store(self):
        if not self._need_client():
            return
        vs_id = self._selected_vs_id()
        if not vs_id:
            return
        # Preload files so we can clean them up before deletion (API often rejects delete if files remain).
        files: List[Dict[str, Any]] = []
        try:
            files = with_retry(lambda: self.client.list_vector_store_files(vs_id), self.s.retry, self.breaker)
        except Exception as e:
            msg_critical(self, "Delete", f"Nelze načíst soubory ve store {vs_id}: {e}")
            return

        if msg_question(
            self,
            "Delete",
            f"Smazat vector store {vs_id} (soubory: {len(files)})?",
        ) != QMessageBox.Yes:
            return
        self._start_delete_worker([vs_id], "Mažu vector store...")

    def delete_all_stores(self):
        if not self._need_client():
            return
        count = self.lst_vs.count()
        if count == 0:
            msg_info(self, "Delete", "Nenalezeny žádné vector store.")
            return
        if msg_question(
            self,
            "Delete",
            f"Smazat všech {count} vector store včetně souborů?",
        ) != QMessageBox.Yes:
            return
        ids: List[str] = []
        for i in range(count):
            it = self.lst_vs.item(i)
            if it and it.data(32):
                ids.append(it.data(32))
        if ids:
            self._start_delete_worker(ids, "Mažu všechny vector stores...")

    def list_files(self):
        self.lst_files.clear()
        self._cur_files = {}
        if not self._need_client():
            return
        vs_id = self._selected_vs_id()
        if not vs_id:
            return
        with BusyPopup(self, "Načítám soubory ve store..."):
            try:
                files = with_retry(lambda: self.client.list_vector_store_files(vs_id), self.s.retry, self.breaker)
                for f in files:
                    fid = f.get("id", "")
                    if fid:
                        self._cur_files[fid] = f
                    vs_file_id = f.get("id", "")
                    file_id = f.get("file_id", "")
                    status = f.get("status", "")
                    label = f"{vs_file_id}  |  file_id={file_id}  |  {status}"
                    it = QListWidgetItem(label)
                    it.setData(32, vs_file_id)
                    self.lst_files.addItem(it)
                self.logline.emit(f"Vector store {vs_id}: files={len(files)}")
                self.show_selected_file_details()
            except Exception as e:
                msg_critical(self, "List files", str(e))

        self._update_controls()

    def add_file(self):
        if not self._need_client():
            return
        vs_id = self._selected_vs_id()
        if not vs_id:
            msg_info(self, "Add file", "Nejdřív vyber vector store.")
            return
        file_id = (self.ed_file_id.text() or "").strip()
        if not file_id:
            msg_info(self, "Add file", "Zadej file_id.")
            return
        with BusyPopup(self, "Přidávám soubor..."):
            try:
                res = with_retry(lambda: self.client.add_file_to_vector_store(vs_id, file_id), self.s.retry, self.breaker)
                self.logline.emit(f"Added file to vector store: vs={vs_id} file_id={file_id} vs_file_id={res.get('id')}")
                self.list_files()
            except Exception as e:
                msg_critical(self, "Add file", str(e))

    def add_files_from_api(self):
        if not self._need_client():
            return
        vs_id = self._selected_vs_id()
        if not vs_id:
            msg_info(self, "Add files", "Nejdřív vyber vector store.")
            return
        dlg = FilesSelectorDialog(self.client, self.s.retry, self.breaker, self)
        if dlg.exec() != QDialog.Accepted:
            return
        ids = dlg.selected_ids()
        if not ids:
            return
        with BusyPopup(self, "Přidávám soubory..."):
            ok_cnt = 0
            failures: List[str] = []
            for fid in ids:
                try:
                    res = with_retry(lambda f=fid: self.client.add_file_to_vector_store(vs_id, f), self.s.retry, self.breaker)
                    ok_cnt += 1
                    self.logline.emit(f"Added file to vector store: vs={vs_id} file_id={fid} vs_file_id={res.get('id')}")
                except Exception as e:
                    failures.append(f"{fid}: {e}")
            self.list_files()
            if failures:
                msg_warning(self, "Add files", f"Přidáno: {ok_cnt}/{len(ids)}\nChyby:\n" + "\n".join(failures[:5]))
            else:
                msg_info(self, "Add files", f"Přidáno {ok_cnt} souborů do {vs_id}.")

    def remove_selected_file(self):
        if not self._need_client():
            return
        vs_id = self._selected_vs_id()
        if not vs_id:
            return
        sel = self.lst_files.selectedItems()
        if not sel:
            return
        vs_file_id = sel[0].data(32)
        if msg_question(self, "Remove file", f"Odstranit {vs_file_id} z vector store {vs_id}?") != QMessageBox.Yes:
            return
        with BusyPopup(self, "Odebírám soubor..."):
            try:
                with_retry(lambda: self.client.delete_vector_store_file(vs_id, vs_file_id), self.s.retry, self.breaker)
                self.logline.emit(f"Removed vector store file: {vs_file_id}")
                self.list_files()
            except Exception as e:
                msg_critical(self, "Remove file", str(e))

    # ---------- Attach / detach ----------
    def attach_selected(self):
        sel = self.lst_vs.selectedItems()
        for it in sel:
            vs_id = it.data(32)
            if not vs_id:
                continue
            if not any(self.lst_attached.item(i).data(32) == vs_id for i in range(self.lst_attached.count())):
                ni = QListWidgetItem(vs_id)
                ni.setData(32, vs_id)
                self.lst_attached.addItem(ni)
        self._emit_attached()
        self._update_controls()

    def detach_selected(self):
        for it in self.lst_attached.selectedItems():
            row = self.lst_attached.row(it)
            self.lst_attached.takeItem(row)
        self._emit_attached()
        self._update_controls()

    def attached_ids(self) -> List[str]:
        return [self.lst_attached.item(i).data(32) for i in range(self.lst_attached.count()) if self.lst_attached.item(i).data(32)]

    def set_attached(self, ids: List[str]):
        self.lst_attached.clear()
        for vid in ids or []:
            if not vid:
                continue
            it = QListWidgetItem(vid)
            it.setData(32, vid)
            self.lst_attached.addItem(it)
        self._emit_attached()

    def clear_attached(self):
        self.lst_attached.clear()
        self._emit_attached()

    def _prune_attached(self):
        valid = {self.lst_vs.item(i).data(32) for i in range(self.lst_vs.count())}
        changed = False
        for i in reversed(range(self.lst_attached.count())):
            vid = self.lst_attached.item(i).data(32)
            if vid not in valid:
                self.lst_attached.takeItem(i)
                changed = True
        if changed:
            self._emit_attached()

    def _emit_attached(self):
        self.attached_changed.emit(self.attached_ids())

    def _selected_vs_file_id(self) -> Optional[str]:
        sel = self.lst_files.selectedItems()
        if not sel:
            return None
        return sel[0].data(32)

    def show_selected_file_details(self):
        vs_file_id = self._selected_vs_file_id()
        if not vs_file_id:
            self.lbl_detail.setText("—")
            self.ed_vs_attrs.setPlainText("")
            self.ed_file_info.setPlainText("")
            return
        vs = self._cur_files.get(vs_file_id, {})
        file_id = vs.get("file_id", "")
        attrs = vs.get("attributes") or {}
        import json
        try:
            attrs_text = json.dumps(attrs, indent=2, ensure_ascii=False)
        except Exception:
            attrs_text = str(attrs)
        self.ed_vs_attrs.setPlainText(attrs_text)

        file_info = {}
        if file_id:
            with BusyPopup(self, "Načítám detail souboru..."):
                try:
                    file_info = with_retry(lambda: self.client.retrieve_file(file_id), self.s.retry, self.breaker)
                except Exception as e:
                    file_info = {"error": str(e)}
        try:
            info_text = json.dumps(file_info, indent=2, ensure_ascii=False)
        except Exception:
            info_text = str(file_info)

        self.lbl_detail.setText(f"vs_file_id: {vs_file_id} | file_id: {file_id}")
        self.ed_file_info.setPlainText(info_text)

    def save_selected_attrs(self):
        if not self._need_client():
            return
        vs_id = self._selected_vs_id()
        vs_file_id = self._selected_vs_file_id()
        if not vs_id or not vs_file_id:
            return
        import json
        try:
            new_attrs = json.loads(self.ed_vs_attrs.toPlainText() or "{}")
            if not isinstance(new_attrs, dict):
                raise ValueError("Atributy musí být objekt (JSON).")
        except Exception as e:
            msg_warning(self, "Atributy", f"Neplatný JSON: {e}")
            return
        try:
            with_retry(lambda: self.client.update_vector_store_file_attributes(vs_id, vs_file_id, new_attrs), self.s.retry, self.breaker)
            self.logline.emit(f"Atributy uloženy: {vs_file_id}")
            self.list_files()
        except Exception as e:
            msg_critical(self, "Atributy", str(e))
