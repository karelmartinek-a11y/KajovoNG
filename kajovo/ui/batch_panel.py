from __future__ import annotations
from .widgets import msg_info, msg_warning, msg_critical, msg_question, dialog_select_dir

import json
import os
import time
from typing import Any, Dict, List, Optional
from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QMessageBox,
)

from ..core.openai_client import OpenAIClient
from ..core.retry import with_retry, CircuitBreaker
from ..core.utils import safe_join_under_root
from .widgets import BusyPopup


class BatchPanel(QWidget):
    logline = Signal(str)

    def __init__(self, settings, api_key: str, parent=None):
        super().__init__(parent)
        self.s = settings
        self.api_key = api_key
        self.client: Optional[OpenAIClient] = OpenAIClient(api_key) if api_key else None
        self.breaker = CircuitBreaker(self.s.retry.circuit_breaker_failures, self.s.retry.circuit_breaker_cooldown_s)
        self.out_dir: str = ""
        self._batch_run_state_cache: Dict[str, Optional[Dict[str, Any]]] = {}

        v = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel("BATCH requests"))
        top.addStretch(1)
        self.btn_refresh = QPushButton("Refresh")
        self.btn_download = QPushButton("Download")
        self.btn_cancel = QPushButton("Smazat")
        top.addWidget(self.btn_refresh)
        top.addWidget(self.btn_download)
        top.addWidget(self.btn_cancel)
        v.addLayout(top)

        self.tbl = QTableWidget(0, 7)
        self.tbl.setHorizontalHeaderLabels(["id", "status", "created_at", "endpoint", "input_file_id", "output_file_id", "errors"])
        self.tbl.setSelectionBehavior(QTableWidget.SelectRows)
        self.tbl.setSelectionMode(QTableWidget.SingleSelection)
        v.addWidget(self.tbl, 1)

        self.btn_refresh.clicked.connect(self.load)
        self.btn_download.clicked.connect(self.download)
        self.btn_cancel.clicked.connect(self.cancel)

        self.load()

    def set_out_dir(self, out_dir: str):
        self.out_dir = out_dir or ""

    def set_api_key(self, api_key: str):
        self.api_key = api_key
        self.client = OpenAIClient(api_key) if api_key else None
        self.load()

    def _need_client(self) -> bool:
        if not self.api_key:
            msg_warning(self, "Batch", "Chybí OPENAI_API_KEY.")
            return False
        if self.client is None:
            self.client = OpenAIClient(self.api_key)
        return True

    def load(self):
        self.tbl.setRowCount(0)
        if not self._need_client():
            return
        with BusyPopup(self, "Načítám batch seznam..."):
            try:
                batches = with_retry(lambda: self.client.list_batches(), self.s.retry, self.breaker)
            except Exception as e:
                msg_critical(self, "Batch", str(e))
                return

            self.tbl.setRowCount(len(batches))
            for i, b in enumerate(batches):
                def item(x):
                    it = QTableWidgetItem(str(x))
                    it.setFlags(it.flags() ^ Qt.ItemIsEditable)
                    return it
                self.tbl.setItem(i, 0, item(b.get("id", "")))
                self.tbl.setItem(i, 1, item(b.get("status", "")))
                ca = b.get("created_at")
                self.tbl.setItem(i, 2, item(time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ca)) if isinstance(ca, (int, float)) else ca))
                self.tbl.setItem(i, 3, item(b.get("endpoint", "")))
                self.tbl.setItem(i, 4, item(b.get("input_file_id", "")))
                self.tbl.setItem(i, 5, item(b.get("output_file_id", "")))
                self.tbl.setItem(i, 6, item(b.get("error", "") or b.get("errors", "")))
            self.tbl.resizeColumnsToContents()
            self.logline.emit(f"Batches loaded: {len(batches)}")

    def _selected_batch(self):
        sel = self.tbl.selectionModel().selectedRows()
        if not sel:
            return None
        row = sel[0].row()
        bid = self.tbl.item(row, 0).text()
        ofid = self.tbl.item(row, 5).text()
        return bid, ofid

    def _log_directory(self) -> str:
        log_dir = getattr(self.s, "log_dir", "LOG") or "LOG"
        return os.path.abspath(log_dir)

    def _load_run_state_for_batch(self, batch_id: str) -> Optional[Dict[str, Any]]:
        if not batch_id:
            return None
        log_dir = self._log_directory()
        if not os.path.isdir(log_dir):
            return None
        for run_id in sorted(os.listdir(log_dir), reverse=True):
            run_path = os.path.join(log_dir, run_id)
            if not os.path.isdir(run_path):
                continue
            state_path = os.path.join(run_path, "run_state.json")
            if not os.path.isfile(state_path):
                continue
            try:
                with open(state_path, "r", encoding="utf-8") as fh:
                    state = json.load(fh)
            except Exception:
                continue
            if state.get("batch_id") == batch_id:
                return {"run_id": run_id, "state": state, "out_dir": state.get("out_dir", "")}
        return None

    def _batch_run_info(self, batch_id: str) -> Optional[Dict[str, Any]]:
        if not batch_id:
            return None
        if batch_id in self._batch_run_state_cache:
            return self._batch_run_state_cache[batch_id]
        info = self._load_run_state_for_batch(batch_id)
        self._batch_run_state_cache[batch_id] = info
        return info

    def _extract_texts(self, body) -> list:
        texts = []
        if not isinstance(body, dict):
            return texts
        outputs = body.get("output") or body.get("outputs") or body.get("data") or []
        for o in outputs:
            if not isinstance(o, dict):
                continue
            if "content" in o and isinstance(o["content"], list):
                for part in o["content"]:
                    if isinstance(part, dict):
                        t = part.get("text") or part.get("value")
                        if t:
                            texts.append(t)
            # Responses API sometimes nests message
            msg = o.get("message") if isinstance(o, dict) else None
            if isinstance(msg, dict):
                content = msg.get("content")
                if isinstance(content, list):
                    for part in content:
                        if isinstance(part, dict):
                            t = part.get("text") or part.get("value")
                            if t:
                                texts.append(t)
        return texts

    def _write_files_from_bundle(self, bundle: dict, target_root: str) -> list:
        written = []
        if not isinstance(bundle, dict):
            return written
        root = bundle.get("root") or ""
        files = bundle.get("files") or []
        dest_root = target_root
        if root:
            dest_root = os.path.join(target_root, root)
        try:
            os.makedirs(dest_root, exist_ok=True)
        except Exception:
            pass
        for f in files:
            path = f.get("path")
            content = f.get("content", "")
            if not path:
                continue
            try:
                dest = safe_join_under_root(dest_root, path)
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with open(dest, "w", encoding="utf-8", newline="\n") as fh:
                    fh.write(content)
                written.append(dest)
            except Exception as e:
                self.logline.emit(f"Write failed {dest}: {e}")
        return written

    def _apply_contract_payload(
        self,
        payload: Any,
        target_dir: str,
        written: List[str],
        chunked_files: Dict[str, Dict[str, Any]],
        errors: List[str],
    ) -> None:
        if not isinstance(payload, dict):
            return
        contract = payload.get("contract")
        if contract == "C_FILES_ALL":
            written.extend(self._write_files_from_bundle(payload, target_dir))
            return
        if contract and contract.startswith("A3_FILE"):
            path = payload.get("path")
            if not path:
                errors.append("A3_FILE payload missing path.")
                return
            content = payload.get("content") or ""
            chunking = payload.get("chunking") or {}
            index = chunking.get("chunk_index", 0)
            index = index if isinstance(index, int) else 0
            entry = chunked_files.setdefault(path, {"parts": {}, "chunk_count": 0})
            entry["parts"][index] = content
            chunk_count = chunking.get("chunk_count")
            if isinstance(chunk_count, int) and chunk_count > entry["chunk_count"]:
                entry["chunk_count"] = chunk_count
            return
        if contract:
            errors.append(f"Unsupported contract: {contract}")

    def _finalize_chunked_files(
        self,
        chunked_files: Dict[str, Dict[str, Any]],
        target_dir: str,
        written: List[str],
        errors: List[str],
    ) -> None:
        for path, info in chunked_files.items():
            parts: Dict[int, str] = info.get("parts", {})
            if not parts:
                continue
            indexes = sorted(parts.keys())
            chunk_count = info.get("chunk_count") or len(indexes)
            if chunk_count and len(parts) < chunk_count:
                errors.append(f"Incomplete chunks for {path}: {len(parts)}/{chunk_count}")
            content = "".join(parts[idx] for idx in indexes)
            try:
                dest = safe_join_under_root(target_dir, path)
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with open(dest, "w", encoding="utf-8", newline="\n") as fh:
                    fh.write(content)
                written.append(dest)
            except Exception as exc:
                errors.append(f"Write chunked file {path}: {exc}")

    def download(self):
        if not self._need_client():
            return
        sb = self._selected_batch()
        if not sb:
            msg_info(self, "Batch", "Vyber batch.")
            return
        bid, ofid = sb
        if not ofid:
            msg_info(self, "Batch", "Batch nemá output_file_id.")
            return
        run_info = self._batch_run_info(bid)
        target_dir = ""
        used_run_dir = False
        if run_info:
            preferred_dir = run_info.get("out_dir") or ""
            if preferred_dir:
                preferred_dir = os.path.abspath(preferred_dir)
                try:
                    os.makedirs(preferred_dir, exist_ok=True)
                except Exception:
                    pass
                if os.path.isdir(preferred_dir):
                    target_dir = preferred_dir
                    used_run_dir = True
        if not target_dir:
            candidate = self.out_dir.strip() if self.out_dir else ""
            if candidate and os.path.isdir(candidate):
                target_dir = candidate
        if not target_dir or not os.path.isdir(target_dir):
            target_dir = dialog_select_dir(self, "Zvol OUT adresář pro uložení výstupu", os.getcwd())
            if not target_dir:
                return
        if used_run_dir and run_info:
            run_id = run_info.get("run_id", "unknown")
            self.logline.emit(f"Batch {bid} downloads to OUT folder from run {run_id}")
        self.logline.emit(f"Downloading batch {bid} output into {target_dir}")
        with BusyPopup(self, "Stahuji výstup batch..."):
            try:
                raw = with_retry(lambda: self.client.file_content(ofid), self.s.retry, self.breaker)
                # always save raw JSONL as backup
                raw_path = os.path.join(target_dir, f"batch_{bid}_output.jsonl")
                with open(raw_path, "wb") as f:
                    f.write(raw)
                self.logline.emit(f"Batch {bid}: raw JSONL saved to {raw_path}")
                written: List[str] = []
                errors: List[str] = []
                chunked_files: Dict[str, Dict[str, Any]] = {}
                try:
                    lines = raw.decode("utf-8", errors="ignore").splitlines()
                    for line in lines:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            data = json.loads(line)
                        except Exception as e:
                            errors.append(f"JSON decode: {e}")
                            continue
                        body = None
                        if isinstance(data, dict):
                            body = data.get("response", {}).get("body") or data.get("body")
                        if not body:
                            continue
                        texts = self._extract_texts(body)
                        for txt in texts:
                            txt = txt.strip()
                            if not txt:
                                continue
                            try:
                                payload = json.loads(txt)
                            except Exception as e:
                                errors.append(f"Parse payload: {e}")
                                continue
                            self._apply_contract_payload(
                                payload, target_dir, written, chunked_files, errors
                            )
                except Exception as e:
                    errors.append(str(e))
                self._finalize_chunked_files(chunked_files, target_dir, written, errors)
                self.logline.emit(f"Batch output saved: {raw_path}")
                if written:
                    self.logline.emit(f"Vygenerováno souborů: {len(written)}")
                    self._show_download_summary(bid, target_dir, raw_path, written, errors)
                else:
                    self._show_download_summary(bid, target_dir, raw_path, written, errors)
                if errors:
                    self.logline.emit("; ".join(errors[:5]))
                if target_dir and os.path.isdir(target_dir):
                    if msg_question(self, "Batch", f"Otevřít OUT složku? ({target_dir})") == QMessageBox.Yes:
                        try:
                            if os.name == "nt":
                                os.startfile(target_dir)  # type: ignore
                            else:
                                import subprocess
                                subprocess.Popen(["xdg-open", target_dir])
                        except Exception:
                            pass
            except Exception as e:
                msg_critical(self, "Batch", str(e))

    def cancel(self):
        if not self._need_client():
            return
        sb = self._selected_batch()
        if not sb:
            msg_info(self, "Batch", "Vyber batch.")
            return
        bid, _ = sb
        if msg_question(self, "Batch", f"Smazat batch {bid}? (cancel)") != QMessageBox.Yes:
            return
        with BusyPopup(self, "Ruším batch..."):
            try:
                with_retry(lambda: self.client.cancel_batch(bid), self.s.retry, self.breaker)
                self.logline.emit(f"Deleted/cancelled batch: {bid}")
                self.load()
            except Exception as e:
                msg_critical(self, "Batch", str(e))

    def _show_download_summary(self, bid: str, target_dir: str, raw_path: str, written: List[str], errors: List[str]) -> None:
        if written:
            text = f"Hotovo. Uloženo {len(written)} souborů do {target_dir}"
            icon = QMessageBox.Information if not errors else QMessageBox.Warning
        else:
            text = f"Raw JSONL uložen: {raw_path}\nSouborový výstup nelze parsovat, zkontroluj obsah."
            icon = QMessageBox.Warning if errors else QMessageBox.Information
        details = "\n".join(errors) if errors else None
        if icon == QMessageBox.Information:
            msg_info(self, "Batch", text, details=details)
        else:
            msg_warning(self, "Batch", text, details=details)
