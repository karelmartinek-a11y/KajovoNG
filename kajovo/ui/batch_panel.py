from __future__ import annotations
from .widgets import msg_info, msg_warning, msg_critical, msg_question, dialog_select_dir

import json
import os
import time
from typing import Any, Dict, List, Optional
from PySide6.QtCore import Signal, Qt, QObject, QRunnable, QThreadPool, QTimer, Slot
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

from .background import OpenAIClient, with_retry
from ..core.retry import CircuitBreaker
from ..core.utils import safe_join_under_root, atomic_write_text
from ..core.contracts import validate_paths, ContractError, extract_text_from_response, parse_json_strict
from ..core.contracts import validate_chunk_metadata
from ..core.receipt import Receipt, ReceiptDB
from ..core.pricing import PriceTable, compute_cost
from .widgets import BusyPopup
from .widgets import dialog_input_text
from ..core.generate_batch import process_saved_batch, repeat_saved_batch


class _BatchOperationTask(QRunnable):
    def __init__(self, api_key, operation):
        super().__init__()
        self.api_key, self.operation = api_key, operation
        self.signals = _BatchRefreshSignals()

    def run(self):
        client = OpenAIClient(self.api_key)
        try:
            result = {"result": self.operation(client)}
        except Exception as exc:
            result = {"error": str(exc)}
        finally:
            client.session.close()
            if client._sdk is not None:
                client._sdk.close()
        self.signals.result.emit(result)


class _BatchRefreshSignals(QObject):
    result = Signal(object)


class _BatchRefreshTask(QRunnable):
    def __init__(self, api_key: str, db_path=None):
        super().__init__()
        self.api_key = api_key
        self.db_path = db_path
        self.signals = _BatchRefreshSignals()

    def run(self):
        client = OpenAIClient(self.api_key)
        try:
            result = {"key": self.api_key, "batches": client.list_batches()}
            if self.db_path:
                from ..core.batch_costs import finalize_batches
                result["receipts"] = finalize_batches(client, self.db_path)
        except Exception as exc:
            result = {"key": self.api_key, "error": str(exc)}
        finally:
            client.session.close()
            if client._sdk is not None:
                client._sdk.close()
        self.signals.result.emit(result)


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
        self._refresh_task = None
        self._monitor_started = None
        self._poll_timer = QTimer(self)
        self._poll_timer.setSingleShot(True)
        self._poll_timer.timeout.connect(lambda: self.load(automatic=True))

        v = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel("BATCH requests"))
        top.addStretch(1)
        self.btn_refresh = QPushButton("Obnovit")
        self.btn_download = QPushButton("Stáhnout")
        self.btn_cancel = QPushButton("Zrušit zpracování")
        self.btn_repeat = QPushButton("Opakovat soubory…")
        self.btn_repair = QPushButton("Opravit podle připomínky…")
        top.addWidget(self.btn_refresh)
        top.addWidget(self.btn_download)
        top.addWidget(self.btn_cancel)
        top.addWidget(self.btn_repeat)
        top.addWidget(self.btn_repair)
        v.addLayout(top)
        self.lbl_poll = QLabel("Stav dávky dosud nebyl ověřen.")
        self.lbl_poll.setWordWrap(True)
        v.addWidget(self.lbl_poll)
        self._last_poll = None
        self._poll_clock = QTimer(self)
        self._poll_clock.setInterval(1000)
        self._poll_clock.timeout.connect(self._update_poll_label)
        self._poll_clock.start()

        self.tbl = QTableWidget(0, 8)
        self.tbl.setHorizontalHeaderLabels(["id", "status", "created_at", "endpoint", "input_file_id", "output_file_id", "errors", "Zpracováno / celkem (chyby)"])
        self.tbl.setSelectionBehavior(QTableWidget.SelectRows)
        self.tbl.setSelectionMode(QTableWidget.SingleSelection)
        v.addWidget(self.tbl, 1)

        self.btn_refresh.clicked.connect(self.load)
        self.btn_download.clicked.connect(self.download)
        self.btn_cancel.clicked.connect(self.cancel)
        self.btn_repeat.clicked.connect(lambda: self.repeat_selected(False))
        self.btn_repair.clicked.connect(lambda: self.repeat_selected(True))
        self._operation_task = None

        if self.api_key:
            self.load()

    def set_out_dir(self, out_dir: str):
        self.out_dir = out_dir or ""

    def _update_poll_label(self):
        age = f"před {int(time.monotonic() - self._last_poll)} s" if self._last_poll is not None else "dosud neproběhlo"
        next_check = f"za {max(0, self._poll_timer.remainingTime()) // 1000} s" if self._poll_timer.isActive() else "není naplánována"
        checking = " · Ověřuji stav…" if self._refresh_task is not None else ""
        self.lbl_poll.setText(f"Poslední úspěšné ověření: {age} · Další kontrola: {next_check}{checking}\n"
                              "ETA fronty nelze určit; dokončení API ještě nepotvrzuje uložení souborů do OUT.")

    def set_api_key(self, api_key: str):
        self.api_key = api_key
        self.client = OpenAIClient(api_key) if api_key else None
        self._poll_timer.stop()
        self._monitor_started = None
        self.tbl.setRowCount(0)
        if api_key:
            self.load()

    def _need_client(self) -> bool:
        if not self.api_key:
            msg_warning(self, "Batch", "Chybí OPENAI_API_KEY.")
            return False
        if self.client is None:
            self.client = OpenAIClient(self.api_key)
        return True

    def load(self, checked=False, *, automatic=False):
        if self._refresh_task is not None:
            return
        if not self._need_client():
            return
        if not automatic:
            self._monitor_started = time.monotonic()
        self._poll_timer.stop()
        self.btn_refresh.setEnabled(False)
        self._refresh_task = _BatchRefreshTask(self.api_key, self.s.db_path)
        self._refresh_task.signals.result.connect(self._on_refreshed, Qt.QueuedConnection)
        QThreadPool.globalInstance().start(self._refresh_task)

    @Slot(object)
    def _on_refreshed(self, result):
        self._refresh_task = None
        self.btn_refresh.setEnabled(True)
        if result.get("receipts") and result["key"] == self.api_key:
            from .cost_dialog import show_final_receipt
            from ..core.batch_costs import mark_seen
            for scope in dict.fromkeys(r["scope"] for r in result["receipts"]):
                show_final_receipt(self, ReceiptDB(self.s.db_path), scope)
            mark_seen(self.s.db_path, [r["id"] for r in result["receipts"]])
        if result["key"] != self.api_key:
            if self.api_key:
                self.load()
            return
        if "error" in result:
            self.logline.emit("Načtení dávek selhalo: " + result["error"])
            self.lbl_poll.setToolTip("Poslední ověření selhalo: " + result["error"])
            return
        batches = result["batches"]
        self._last_poll = time.monotonic()
        self.lbl_poll.setToolTip("")
        if batches is not None:
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
                counts = b.get("request_counts") or {}
                if counts.get("total") is not None:
                    done, failed = counts.get("completed", 0), counts.get("failed", 0)
                    self.tbl.setItem(i, 7, item(f"{done + failed}/{counts['total']} ({failed})"))
                else:
                    self.tbl.setItem(i, 7, item("Počet není dostupný"))
            self.tbl.resizeColumnsToContents()
            self.logline.emit(f"Batches loaded: {len(batches)}")
        pending = any(b.get("status") in ("validating", "in_progress", "finalizing", "cancelling") for b in batches)
        if pending:
            elapsed = time.monotonic() - (self._monitor_started or time.monotonic())
            if elapsed < self.s.batch_timeout_s:
                self._poll_timer.start(max(500, int(self.s.batch_poll_interval_s * 1000)))
            else:
                self.logline.emit("Časový limit sledování dávky vypršel; vzdálené zpracování pokračuje.")

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
            if state.get("batch_id") == batch_id or batch_id in state.get("generate_batches", {}):
                return {"run_id": run_id, "run_dir": run_path, "state": state, "out_dir": state.get("out_dir", "")}
        return None

    def _batch_run_info(self, batch_id: str) -> Optional[Dict[str, Any]]:
        if not batch_id:
            return None
        info = self._load_run_state_for_batch(batch_id)
        self._batch_run_state_cache[batch_id] = info
        return info

    def _extract_texts(self, body) -> list:
        if isinstance(body, dict) and ("output" in body or "output_text" in body or "status" in body):
            return [extract_text_from_response(body)]
        texts = []
        if not isinstance(body, dict):
            return texts
        if isinstance(body.get("output_text"), str):
            return [body["output_text"]]
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
            # Text odpovědi může být uvnitř vnořené zprávy.
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

    def _record_batch_receipt(self, batch_id: str, body: dict, run_info, raw_path: str):
        if not body.get("id") or not isinstance(body.get("usage"), dict):
            return
        usage = body["usage"]
        model = body.get("model") or ""
        prices = PriceTable(os.path.join(self.s.cache_dir, "price_table.json"))
        prices.load_cache()
        row = prices.get(model) or PriceTable.builtin_fallback().get(model)
        inp, out = int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0)
        total, tool, storage = compute_cost(row, inp, out, is_batch=True, usage=usage)
        state = (run_info or {}).get("state") or {}
        ReceiptDB(self.s.db_path).insert(Receipt(
            run_id=(run_info or {}).get("run_id") or batch_id, created_at=time.time(),
            project=state.get("project") or "UNKNOWN", model=model, mode="C", flow_type="C_FILES_ALL",
            response_id=body["id"], batch_id=batch_id, input_tokens=inp, output_tokens=out,
            total_cost=total, tool_cost=tool, storage_cost=storage,
            pricing_verified=prices.is_verified(model), notes="Batch output",
            log_paths={"batch_output": raw_path}, usage=usage,
        ))

    def _write_files_from_bundle(self, bundle: dict, target_root: str, reserved_paths=None) -> list:
        written = []
        if not isinstance(bundle, dict):
            return written
        root = bundle.get("root", "")
        files = bundle.get("files")
        if not isinstance(root, str):
            raise ContractError("Kořen souborového balíčku musí být text.")
        dest_root = target_root
        if root:
            dest_root = safe_join_under_root(target_root, root)
        if not isinstance(files, list):
            raise ContractError("files musí být seznam.")
        validate_paths(files)
        reserved = {os.path.abspath(path).casefold() for path in reserved_paths or []}
        for row in files:
            destination = safe_join_under_root(dest_root, row["path"])
            if os.path.abspath(destination).casefold() in reserved:
                raise ContractError("Více výsledků dávky zapisuje stejný soubor.")
            if not isinstance(row.get("content"), str):
                raise ContractError("Obsah souboru musí být text.")
        os.makedirs(dest_root, exist_ok=True)
        for f in files:
            path = f.get("path")
            content = f.get("content", "")
            if not isinstance(path, str) or not path:
                continue
            try:
                dest = safe_join_under_root(dest_root, path)
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                atomic_write_text(dest, content)
                written.append(dest)
            except Exception as e:
                raise RuntimeError(f"Zápis souboru {path} selhal: {e}") from e
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
            written.extend(self._write_files_from_bundle(payload, target_dir, reserved_paths=written))
            return
        if contract == "A3_FILE":
            path = payload.get("path")
            if not isinstance(path, str) or not path:
                errors.append("A3_FILE payload missing path.")
                return
            content = payload.get("content")
            chunking = payload.get("chunking") or {}
            if not isinstance(content, str) or not isinstance(chunking, dict):
                errors.append(f"Invalid chunk content for {path}")
                chunked_files.setdefault(path, {})["invalid"] = True
                return
            index = chunking.get("chunk_index", 0)
            entry = chunked_files.setdefault(path, {"parts": {}, "chunk_count": 0})
            entry.setdefault("parts", {})
            entry.setdefault("chunk_count", 0)
            try:
                validate_chunk_metadata(chunking)
                count = chunking.get("chunk_count", 0)
                if count and entry["chunk_count"] and count != entry["chunk_count"]:
                    raise ContractError("Počet částí souboru se mezi odpověďmi změnil.")
                if chunking["has_more"] is False and entry.get("terminal", index) != index:
                    raise ContractError("Soubor obsahuje více koncových částí.")
            except ContractError as exc:
                errors.append(f"{path}: {exc}")
                entry["invalid"] = True
                return
            if type(index) is not int or index < 0 or index in entry["parts"]:
                errors.append(f"Invalid or duplicate chunk index for {path}")
                entry["invalid"] = True
                return
            if chunking.get("has_more") is False:
                entry["terminal"] = index
            entry["parts"][index] = content
            chunk_count = chunking.get("chunk_count")
            if isinstance(chunk_count, int) and chunk_count > entry["chunk_count"]:
                entry["chunk_count"] = chunk_count
            return
        errors.append(f"Nepodporovaný nebo chybějící kontrakt: {contract}")

    def _finalize_chunked_files(
        self,
        chunked_files: Dict[str, Dict[str, Any]],
        target_dir: str,
        written: List[str],
        errors: List[str],
    ) -> None:
        try:
            validate_paths([{"path": path} for path in chunked_files])
        except ContractError as exc:
            errors.append(str(exc))
            return
        for path, info in chunked_files.items():
            if info.get("invalid"):
                continue
            parts: Dict[int, str] = info.get("parts", {})
            if not parts:
                continue
            indexes = sorted(parts.keys())
            chunk_count = info.get("chunk_count") or len(indexes)
            if indexes != list(range(chunk_count)) or info.get("terminal") != chunk_count - 1:
                errors.append(f"Incomplete chunks for {path}: {len(parts)}/{chunk_count}")
                continue
            content = "".join(parts[idx] for idx in indexes)
            try:
                dest = safe_join_under_root(target_dir, path)
                if os.path.abspath(dest).casefold() in {os.path.abspath(item).casefold() for item in written}:
                    raise ContractError("Více výsledků dávky zapisuje stejný soubor.")
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                atomic_write_text(dest, content)
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
        run_info = self._batch_run_info(bid)
        if run_info and run_info["state"].get("generate_batch"):
            self._start_operation(lambda client: process_saved_batch(client, run_info["run_dir"], bid, self.s))
            return
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
                # Uložení nezpracovaného JSONL pro samostatnou kontrolu.
                raw_path = safe_join_under_root(target_dir, f"batch_{bid}_output.jsonl")
                with open(raw_path, "wb") as f:
                    f.write(raw)
                self.logline.emit(f"Batch {bid}: raw JSONL saved to {raw_path}")
                written: List[str] = []
                errors: List[str] = []
                chunked_files: Dict[str, Dict[str, Any]] = {}
                try:
                    lines = raw.decode("utf-8").splitlines()
                    for line in lines:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            data = parse_json_strict(line)
                        except Exception as e:
                            errors.append(f"JSON decode: {e}")
                            continue
                        body = None
                        if isinstance(data, dict):
                            response = data.get("response")
                            if data.get("error") or isinstance(response, dict) and response.get("status_code", 200) >= 400:
                                errors.append(f"Dávkový požadavek {data.get('custom_id', '')} selhal.")
                                continue
                            body = response.get("body") if isinstance(response, dict) else data.get("body")
                        if not body:
                            errors.append("Dávková položka neobsahuje odpověď.")
                            continue
                        if not isinstance(body, dict):
                            errors.append("Batch response body must be an object.")
                            continue
                        self._record_batch_receipt(bid, body, run_info, raw_path)
                        try:
                            texts = self._extract_texts(body)
                        except ContractError as exc:
                            errors.append(str(exc))
                            continue
                        for txt in texts:
                            txt = txt.strip()
                            if not txt:
                                continue
                            try:
                                payload = parse_json_strict(txt)
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

    def _start_operation(self, operation):
        if self._operation_task is not None:
            return
        self._operation_task = _BatchOperationTask(self.api_key, operation)
        self._operation_task.signals.result.connect(self._operation_finished, Qt.QueuedConnection)
        for button in (self.btn_download, self.btn_repeat, self.btn_repair):
            button.setEnabled(False)
        QThreadPool.globalInstance().start(self._operation_task)

    @Slot(object)
    def _operation_finished(self, payload):
        self._operation_task = None
        for button in (self.btn_download, self.btn_repeat, self.btn_repair):
            button.setEnabled(True)
        self._batch_run_state_cache.clear()
        if payload.get("error"):
            msg_warning(self, "GENERATE BATCH", payload["error"])
            return
        result = payload["result"]
        if result.get("batch_id"):
            msg_info(self, "GENERATE BATCH", f"Odesláno {result['files']} souborů: {result['batch_id']}")
            self.load()
            return
        errors = "\n".join(f"{key}: {value}" for key, value in result.get("file_errors", result["errors"]).items())
        state = "Soubory kompletní, funkčnost neověřena." if result["status"] == "files_complete_unverified" else "Výsledek je částečný."
        msg_info(self, "GENERATE BATCH", f"{state}\nZapsáno: {len(result['written'])}\n{errors}\n"
                 f"Nedodané prostředky: {', '.join(result.get('omitted', [])) or 'žádné'}\n"
                 "Sestavení a integrační testy spusťte ručně podle plánu A1 uloženého v evidenci běhu.")

    def repeat_selected(self, repair=False):
        if not self._need_client():
            return
        selected = self._selected_batch()
        info = self._batch_run_info(selected[0]) if selected else None
        if not info or not info["state"].get("generate_batch"):
            msg_info(self, "GENERATE BATCH", "Vyberte dávku vytvořenou hybridním GENERATE.")
            return
        state, bid = info["state"], selected[0]
        manifest = state.get("generate_batches", {}).get(bid, state["generate_batch"])
        failed = state.get("batch_imports", {}).get(bid, {}).get("errors", {})
        defaults = [path for cid, path in manifest["expected"].items() if cid in failed]
        raw, ok = dialog_input_text(self, "Výběr souborů", "JSON pole relativních cest; odešle placenou dávku.", json.dumps(defaults, ensure_ascii=False))
        if not ok or not raw.strip():
            return
        feedback = ""
        if repair:
            feedback, ok = dialog_input_text(self, "Oprava", "Popište chybu nebo vložte výsledek testu:")
            if not ok or not feedback.strip():
                return
        try:
            paths = json.loads(raw)
            if not isinstance(paths, list) or not paths or not all(isinstance(p, str) for p in paths):
                raise ValueError("Vyžaduje se neprázdné JSON pole cest.")
        except ValueError as exc:
            msg_warning(self, "Výběr souborů", str(exc))
            return
        from .cost_dialog import CostController
        prices = PriceTable(os.path.join(self.s.cache_dir, "price_table.json"))
        prices.load_cache()
        self._cost_control = CostController(ReceiptDB(self.s.db_path), prices, state["run_id"], self)
        with self._cost_control.ledger.connect() as con:
            saved = con.execute("SELECT limit_usd FROM cost_scopes WHERE id=?", (state["run_id"],)).fetchone()
            if saved:
                self._cost_control.limit = saved[0]
        self._start_operation(lambda client: repeat_saved_batch(client, info["run_dir"], bid, paths, feedback, self._cost_control))

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
