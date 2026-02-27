from __future__ import annotations
from .widgets import msg_info, msg_warning, msg_critical, msg_question, dialog_open_file

import os
from typing import List
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QListWidget, QListWidgetItem, QMessageBox
from PySide6.QtCore import Signal, QThread

from ..core.openai_client import OpenAIClient
from ..core.retry import with_retry, CircuitBreaker
from .widgets import BusyPopup
from .task_progress_dialog import TaskProgressDialog


class FilesDeleteWorker(QThread):
    progress = Signal(int)
    status = Signal(str)
    logline = Signal(str)
    finished = Signal(object, list)  # files or None, failures

    def __init__(self, api_key: str, file_ids: List[str], retry_cfg, breaker_failures: int, breaker_cooldown_s: int):
        super().__init__()
        self.api_key = api_key
        self.file_ids = list(file_ids)
        self.retry_cfg = retry_cfg
        self.breaker_failures = breaker_failures
        self.breaker_cooldown_s = breaker_cooldown_s

    def run(self):
        failures: List[str] = []
        files: List[dict] | None = None
        if not self.api_key:
            self.finished.emit(files, ["Chybí OPENAI_API_KEY."])
            return
        client = OpenAIClient(self.api_key)
        breaker = CircuitBreaker(self.breaker_failures, self.breaker_cooldown_s)
        total = len(self.file_ids)
        self.progress.emit(0)
        for idx, fid in enumerate(self.file_ids):
            step = idx + 1
            self.status.emit(f"Mažu soubor {step}/{total}: {fid}")
            try:
                with_retry(lambda f=fid: client.delete_file(f), self.retry_cfg, breaker)
                self.logline.emit(f"Deleted file {fid}")
            except Exception as e:
                msg = f"{fid}: {e}"
                failures.append(msg)
                self.logline.emit(f"Delete failed {fid}: {e}")
            self.progress.emit(int(step * 100 / max(1, total)))
        try:
            files = with_retry(lambda: client.list_files(), self.retry_cfg, breaker)
        except Exception as e:
            self.logline.emit(f"Refresh failed: {e}")
        self.finished.emit(files, failures)

class FilesPanel(QWidget):
    attached_changed = Signal(list)  # list[str]
    logline = Signal(str)

    def __init__(self, settings, api_key: str, parent=None):
        super().__init__(parent)
        self.s = settings
        self.api_key = api_key
        self.client = OpenAIClient(api_key) if api_key else None
        self.breaker = CircuitBreaker(settings.retry.circuit_breaker_failures, settings.retry.circuit_breaker_cooldown_s)
        self._delete_worker: FilesDeleteWorker | None = None
        self._delete_dialog: TaskProgressDialog | None = None

        v = QVBoxLayout(self)

        top = QHBoxLayout()
        top.addWidget(QLabel("Files API"))
        top.addStretch(1)
        self.btn_refresh = QPushButton("Refresh")
        self.btn_upload = QPushButton("Upload")
        self.btn_delete = QPushButton("Delete")
        self.btn_delete_all = QPushButton("Del ALL")
        self.btn_refresh.setToolTip("Načte seznam souborů z OpenAI Files API.")
        self.btn_upload.setToolTip("Nahraje lokální soubor do OpenAI Files API (purpose=user_data).")
        self.btn_delete.setToolTip("Smaže vybrané soubory z OpenAI Files API.")
        self.btn_delete_all.setToolTip("Smaže všechny soubory z OpenAI Files API.")
        top.addWidget(self.btn_refresh)
        top.addWidget(self.btn_upload)
        top.addWidget(self.btn_delete)
        top.addWidget(self.btn_delete_all)
        v.addLayout(top)

        lists = QHBoxLayout()
        self.lst_files = QListWidget()
        self.lst_files.setToolTip("Seznam souborů dostupných v OpenAI Files API.")
        self.lst_files.setSelectionMode(QListWidget.MultiSelection)
        self.lst_attached = QListWidget()
        self.lst_attached.setToolTip("Souborové ID, které se přidají do příštího RUN requestu.")
        self.lst_attached.setSelectionMode(QListWidget.MultiSelection)
        lists.addWidget(self._wrap("Files", self.lst_files))
        lists.addWidget(self._wrap("Attached to request", self.lst_attached))
        v.addLayout(lists, 1)

        btns = QHBoxLayout()
        self.btn_attach = QPushButton("Attach →")
        self.btn_attach.setToolTip("Připojí vybrané file_id do RUN requestu.")
        self.btn_detach = QPushButton("← Detach")
        self.btn_detach.setToolTip("Odebere vybrané file_id z RUN requestu.")
        btns.addStretch(1)
        btns.addWidget(self.btn_attach)
        btns.addWidget(self.btn_detach)
        v.addLayout(btns)

        self.btn_refresh.clicked.connect(self.refresh)
        self.btn_upload.clicked.connect(self.upload)
        self.btn_delete.clicked.connect(self.delete_selected)
        self.btn_delete_all.clicked.connect(self.delete_all)
        self.btn_attach.clicked.connect(self.attach_selected)
        self.btn_detach.clicked.connect(self.detach_selected)

        self.lst_files.itemSelectionChanged.connect(self._update_controls)
        self.lst_attached.itemSelectionChanged.connect(self._update_controls)
        self._update_controls()

        self.refresh()

    def _wrap(self, title: str, widget: QWidget) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0,0,0,0)
        v.addWidget(QLabel(title))
        v.addWidget(widget, 1)
        return w

    def set_api_key(self, api_key: str):
        self.api_key = api_key
        self.client = OpenAIClient(api_key) if api_key else None

    def _need_client(self) -> bool:
        if not self.api_key:
            msg_warning(self, "Files", "Chybí OPENAI_API_KEY.")
            return False
        if self.client is None:
            self.client = OpenAIClient(self.api_key)
        return True

    def refresh(self):
        if not self._need_client():
            return
        with BusyPopup(self, "Načítám Files API..."):
            try:
                files = with_retry(lambda: self.client.list_files(), self.s.retry, self.breaker)
            except Exception as e:
                msg_critical(self, "Files", str(e))
                return

            self.lst_files.clear()
            for f in files:
                fid = f.get("id","")
                name = f.get("filename","")
                item = QListWidgetItem(f"{fid}  |  {name}")
                item.setData(32, fid)
                self.lst_files.addItem(item)
            self._update_controls()

    def upload(self):
        if not self._need_client():
            return
        fp, _ = dialog_open_file(self, "Upload file", "", "All (*.*)")
        if not fp:
            return
        with BusyPopup(self, "Nahrávám soubor..."):
            try:
                up = with_retry(lambda: self.client.upload_file(fp, purpose="user_data"), self.s.retry, self.breaker)
                self.logline.emit(f"Uploaded {os.path.basename(fp)} -> {up.get('id')}")
                self.refresh()
            except Exception as e:
                msg_critical(self, "Upload", str(e))

    def _apply_files_list(self, files: List[dict]) -> None:
        self.lst_files.clear()
        for f in files or []:
            fid = f.get("id", "")
            name = f.get("filename", "")
            item = QListWidgetItem(f"{fid}  |  {name}")
            item.setData(32, fid)
            self.lst_files.addItem(item)

    def _set_delete_controls_enabled(self, enabled: bool) -> None:
        self.btn_refresh.setEnabled(enabled)
        self.btn_upload.setEnabled(enabled)
        self.btn_delete.setEnabled(enabled)
        self.btn_delete_all.setEnabled(enabled)
        self.btn_attach.setEnabled(enabled)
        self.btn_detach.setEnabled(enabled)

    def _start_delete_worker(self, file_ids: List[str], title: str) -> None:
        if self._delete_worker:
            msg_info(self, "Delete", "Mazání už běží.")
            return
        dialog = TaskProgressDialog(title, self, show_subprogress=False)
        dialog.set_status(f"Připravuji mazání {len(file_ids)} souborů...")
        dialog.add_log(f"Počet souborů: {len(file_ids)}")
        worker = FilesDeleteWorker(
            self.api_key,
            file_ids,
            self.s.retry,
            self.s.retry.circuit_breaker_failures,
            self.s.retry.circuit_breaker_cooldown_s,
        )
        self._delete_worker = worker
        self._delete_dialog = dialog
        self._set_delete_controls_enabled(False)

        worker.progress.connect(dialog.set_progress)
        worker.status.connect(dialog.set_status)
        worker.logline.connect(dialog.add_log)

        def on_done(files: List[dict] | None, failures: List[str]):
            dialog.mark_done("Mazání dokončeno.")
            if files is not None:
                self._apply_files_list(files)
            if failures:
                msg_warning(self, "Delete", "Dokončeno s chybami:\n" + "\n".join(failures[:5]))
            worker.deleteLater()
            self._delete_worker = None
            self._delete_dialog = None
            self._set_delete_controls_enabled(True)

        worker.finished.connect(on_done)
        dialog.show()
        worker.start()

    def delete_selected(self):
        if not self._need_client():
            return
        sel = self.lst_files.selectedItems()
        if not sel:
            return
        if msg_question(self, "Delete", "Smazat vybrané soubory z OpenAI Files?") != QMessageBox.Yes:
            return
        ids = [it.data(32) for it in sel if it.data(32)]
        if ids:
            self._start_delete_worker(ids, "Mažu soubory...")

    def delete_all(self):
        if not self._need_client():
            return
        if self.lst_files.count() == 0:
            msg_info(self, "Files", "Žádné soubory k odstranění.")
            return
        if msg_question(self, "Delete ALL", "Smazat VŠECHNY soubory z OpenAI Files?") != QMessageBox.Yes:
            return
        ids = []
        for i in range(self.lst_files.count()):
            it = self.lst_files.item(i)
            if it and it.data(32):
                ids.append(it.data(32))
        if ids:
            self._start_delete_worker(ids, "Mažu všechny soubory...")



    def _update_controls(self) -> None:
        has_key = bool(self.api_key)
        has_files_sel = bool(self.lst_files.selectedItems())
        has_attached_sel = bool(self.lst_attached.selectedItems())
        has_any_files = self.lst_files.count() > 0

        self.btn_refresh.setEnabled(has_key)
        self.btn_upload.setEnabled(has_key)
        self.btn_delete.setEnabled(has_key and has_files_sel)
        self.btn_delete_all.setEnabled(has_key and has_any_files)
        self.btn_attach.setEnabled(has_key and has_files_sel)
        self.btn_detach.setEnabled(has_key and has_attached_sel)

    def attach_selected(self):
        sel = self.lst_files.selectedItems()
        if not sel:
            return
        for it in sel:
            fid = it.data(32)
            if not fid:
                continue
            if not any(self.lst_attached.item(i).data(32) == fid for i in range(self.lst_attached.count())):
                ni = QListWidgetItem(f"{fid}")
                ni.setData(32, fid)
                self.lst_attached.addItem(ni)
        self._emit_attached()
        self._update_controls()

    def detach_selected(self):
        sel = self.lst_attached.selectedItems()
        if not sel:
            return
        for it in sel:
            row = self.lst_attached.row(it)
            self.lst_attached.takeItem(row)
        self._emit_attached()
        self._update_controls()

    def attached_ids(self) -> List[str]:
        out: List[str] = []
        for i in range(self.lst_attached.count()):
            it = self.lst_attached.item(i)
            if it and it.data(32):
                out.append(it.data(32))
        return out

    def set_attached(self, ids: List[str]) -> None:
        self.lst_attached.clear()
        for fid in ids or []:
            if not fid:
                continue
            item = QListWidgetItem(f"{fid}")
            item.setData(32, fid)
            self.lst_attached.addItem(item)
        self._emit_attached()
        self._update_controls()

    def clear_attached(self) -> None:
        self.lst_attached.clear()
        self._emit_attached()
        self._update_controls()

    def _emit_attached(self):
        self.attached_changed.emit(self.attached_ids())
