"""Sledování dávek, přenos výsledků a opakování vybraných souborů."""

import json
import time
from pathlib import Path
from PySide6.QtCore import QTimer, Signal, Qt
from PySide6.QtWidgets import QWidget, QTableWidgetItem, QMessageBox
from ..core.openai_client import OpenAIClient
from ..core.batch_costs import finalize_batches, mark_seen
from ..core.generate_batch import process_saved_batch, repeat_saved_batch
from ..core.utils import safe_join_under_root, atomic_write_text
from ..core.contracts import (
    parse_json_strict,
    extract_text_from_response,
    validate_paths,
    validate_chunk_metadata,
    ContractError,
)
from ..core.pricing import PriceTable, price_response
from ..core.receipt import Receipt, ReceiptDB
from .design import column, row, label, button, table
from .dialogs import msg_info, msg_warning, msg_question, dialog_select_dir, dialog_input_text
from .finance import CostController, show_final_receipt
from .jobs import Jobs


def import_bundle(raw, target):
    """Neúplný nebo kolidující soubor se nezapisuje; raw je uložen samostatně."""
    bundles, chunks, errors = [], {}, []
    for line in raw.decode("utf-8").splitlines():
        if not line.strip():
            continue
        try:
            entry = parse_json_strict(line)
            response = entry.get("response") or {}
            if entry.get("error") or response.get("status_code", 200) >= 400:
                raise ContractError(f"Položka {entry.get('custom_id', '')} selhala.")
            body = response.get("body") or entry.get("body")
            if not isinstance(body, dict):
                raise ContractError("Chybí objekt odpovědi.")
            payload = parse_json_strict(extract_text_from_response(body))
            contract = payload.get("contract")
            if contract == "C_FILES_ALL":
                files = payload.get("files")
                validate_paths(files)
                root = payload.get("root", "")
                if not isinstance(root, str) or not isinstance(files, list):
                    raise ContractError("Kořen musí být text a files seznam.")
                base = safe_join_under_root(target, root) if root else target
                if any(not isinstance(record.get("content"), str) for record in files):
                    raise ContractError("Obsah souboru musí být text.")
                bundles.extend(
                    [
                        (safe_join_under_root(base, record["path"]), record["content"])
                        for record in files
                    ]
                )
            elif contract == "A3_FILE":
                name = payload.get("path", "")
                safe_join_under_root(target, name)
                info = chunks.setdefault(
                    name, {"parts": {}, "count": None, "end": None, "invalid": False}
                )
                try:
                    metadata = payload.get("chunking") or {}
                    validate_chunk_metadata(metadata)
                    index = metadata["chunk_index"]
                    count = metadata.get("chunk_count") or None
                    if index in info["parts"] or count and info["count"] not in (None, count):
                        raise ContractError("Duplicitní část nebo rozdílný počet částí.")
                    if not isinstance(payload.get("content"), str):
                        raise ContractError("Část souboru není text.")
                    if metadata["has_more"] is False:
                        if info["end"] is not None:
                            raise ContractError("Více koncových částí.")
                        info["end"] = index
                    info["count"] = count or info["count"]
                    info["parts"][index] = payload["content"]
                except Exception:
                    info["invalid"] = True
                    raise
            else:
                raise ContractError(f"Nepodporovaný kontrakt {contract}.")
        except Exception as exc:
            errors.append(str(exc))
    try:
        validate_paths([{"path": name} for name in chunks])
    except ContractError as exc:
        return {"written": [], "errors": [*errors, str(exc)], "status": "partial"}
    for name, info in chunks.items():
        count = info["count"] or len(info["parts"])
        if (
            info["invalid"]
            or sorted(info["parts"]) != list(range(count))
            or info["end"] != count - 1
        ):
            errors.append(f"{name}: neúplné nebo neplatné části.")
        else:
            bundles.append(
                (
                    safe_join_under_root(target, name),
                    "".join(info["parts"][index] for index in range(count)),
                )
            )
    counts = {}
    for destination, _content in bundles:
        counts[destination.casefold()] = counts.get(destination.casefold(), 0) + 1
    written = []
    for destination, content in bundles:
        if counts[destination.casefold()] != 1:
            errors.append(f"Kolize cílové cesty: {destination}")
            continue
        try:
            Path(destination).parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(destination, content)
            written.append(destination)
        except OSError as exc:
            errors.append(str(exc))
    return {
        "written": written,
        "errors": errors,
        "status": "partial" if errors else "files_complete_unverified",
    }


class BatchPanel(QWidget):
    logline = Signal(str)

    def __init__(self, settings, api_key, parent=None):
        super().__init__(parent)
        self.s, self.api_key = settings, api_key
        self.client = None
        self.out_dir = ""
        self.jobs = Jobs(self)
        self._refresh_task = self._operation_task = None
        self._monitor_started = self._last_poll = None
        layout = column(self)
        self.lbl_poll = label("Stav dávky dosud nebyl ověřen.", "Hint")
        layout.addWidget(self.lbl_poll)
        self.btn_refresh = button("Obnovit stav", self.load)
        self.btn_download = button("Stáhnout výsledky", self.download, "Primary")
        self.btn_cancel = button("Zrušit zpracování", self.cancel, "Danger")
        layout.addWidget(row(self.btn_refresh, self.btn_download, self.btn_cancel))
        self.tbl = table(
            [
                "ID dávky",
                "Stav",
                "Vytvořeno",
                "Endpoint",
                "Vstupní file_id",
                "Výstupní file_id",
                "Chyby",
                "Dokončeno / celkem",
            ]
        )
        self.tbl.setColumnWidth(0, 220)
        layout.addWidget(self.tbl, 1)
        self.btn_repeat = button("Opakovat soubory", lambda: self.repeat_selected(False))
        self.btn_repair = button("Opravit s připomínkou", lambda: self.repeat_selected(True))
        layout.addWidget(row(self.btn_repeat, self.btn_repair))
        layout.addWidget(
            label(
                "Dokončení API ještě neznamená uložení souborů. Stažený výsledek má vlastní kontrolu úplnosti; funkčnost souborů ověřte sestavením a testy.",
                "Hint",
            )
        )
        self._poll_timer = QTimer(self)
        self._poll_timer.setSingleShot(True)
        self._poll_timer.timeout.connect(lambda: self.load(automatic=True))
        self.clock = QTimer(self)
        self.clock.timeout.connect(self._update_poll_label)
        self.clock.start(1000)

    def set_out_dir(self, path):
        self.out_dir = path

    def set_api_key(self, key):
        self.api_key = key
        self.client = None
        self._poll_timer.stop()
        self._monitor_started = self._last_poll = None
        self.tbl.setRowCount(0)

    def _update_poll_label(self):
        age = (
            f"před {int(time.monotonic() - self._last_poll)} s"
            if self._last_poll is not None
            else "dosud neproběhlo"
        )
        next_poll = (
            f"za {max(0, self._poll_timer.remainingTime()) // 1000} s"
            if self._poll_timer.isActive()
            else "není naplánována"
        )
        self.lbl_poll.setText(
            f"Poslední ověření: {age} · Další kontrola: {next_poll}\nETA fronty nelze určit."
            + (" Ověřuji stav…" if self._refresh_task else "")
        )

    def load(self, checked=False, *, automatic=False):
        if self._refresh_task:
            return
        if not self.api_key:
            if not automatic:
                msg_info(self, "Dávky", "Nejdříve uložte API klíč.")
            return
        if not automatic:
            self._monitor_started = time.monotonic()
        if (
            automatic
            and self._monitor_started is not None
            and time.monotonic() - self._monitor_started >= self.s.batch_timeout_s
        ):
            self._poll_timer.stop()
            return
        key = self.api_key
        self._poll_timer.stop()
        self.btn_refresh.setEnabled(False)

        def execute(job):
            client = self.client or OpenAIClient(key)
            return {
                "key": key,
                "batches": client.list_batches(),
                "receipts": finalize_batches(client, self.s.db_path),
            }

        job = self.jobs.start("Načítání dávek", execute, self._on_refreshed, popup=False)

        def finished():
            self._refresh_task = None
            self.btn_refresh.setEnabled(True)

        job.finished.connect(finished)
        self._refresh_task = job

    def _on_refreshed(self, result):
        if result["key"] != self.api_key:
            return
        self._last_poll = time.monotonic()
        records = result["batches"]
        self.tbl.setRowCount(len(records))
        for index, record in enumerate(records):
            count = record.get("request_counts") or {}
            values = [
                record.get(key, "")
                for key in (
                    "id",
                    "status",
                    "created_at",
                    "endpoint",
                    "input_file_id",
                    "output_file_id",
                    "error_file_id",
                )
            ]
            values.append(
                f"{count.get('completed', 0)} / {count.get('total', 0)} (chyby {count.get('failed', 0)})"
            )
            for col, value in enumerate(values):
                item = QTableWidgetItem(str(value or ""))
                item.setData(Qt.UserRole, record)
                self.tbl.setItem(index, col, item)
        receipts = result.get("receipts") or []
        for scope in dict.fromkeys(receipt["scope"] for receipt in receipts):
            show_final_receipt(self, ReceiptDB(self.s.db_path), scope)
        mark_seen(self.s.db_path, [receipt["id"] for receipt in receipts])
        active = any(
            record.get("status") not in ("completed", "failed", "cancelled", "expired")
            for record in records
        )
        if (
            active
            and self._monitor_started is not None
            and time.monotonic() - self._monitor_started < self.s.batch_timeout_s
        ):
            self._poll_timer.start(int(self.s.batch_poll_interval_s * 1000))

    def selected(self):
        item = self.tbl.item(self.tbl.currentRow(), 0)
        if not item:
            msg_info(self, "Dávky", "Vyberte dávku v tabulce.")
            return None
        return item.data(Qt.UserRole)

    def _batch_run_info(self, batch_id):
        for path in sorted(Path(self.s.log_dir).glob("RUN_*/run_state.json"), reverse=True):
            try:
                state = json.loads(path.read_text(encoding="utf-8"))
                if state.get("batch_id") == batch_id or batch_id in state.get(
                    "generate_batches", {}
                ):
                    return dict(
                        run_id=path.parent.name,
                        run_dir=str(path.parent),
                        state=state,
                        out_dir=state.get("out_dir", ""),
                    )
            except (OSError, ValueError):
                continue
        return None

    def _start_operation(self, operation):
        if self._operation_task:
            return
        key = self.api_key

        def receive(result):
            if key != self.api_key:
                return
            msg_info(
                self,
                "Výsledek operace BATCH",
                f"Zapsáno souborů: {len(result.get('written', []))}\nStav: {result.get('status', result.get('batch_id', 'dokončeno'))}\nFunkčnost ověřte sestavením a testy podle plánu A1.",
                result,
            )
            self.load()

        for action in (self.btn_download, self.btn_repeat, self.btn_repair, self.btn_cancel):
            action.setEnabled(False)
        self._operation_task = self.jobs.start(
            "Zpracování výsledků BATCH",
            lambda job: operation(self.client or OpenAIClient(key)),
            receive,
        )

        def finished():
            self._operation_task = None
            for action in (self.btn_download, self.btn_repeat, self.btn_repair, self.btn_cancel):
                action.setEnabled(True)

        self._operation_task.finished.connect(finished)

    def download(self):
        record = self.selected()
        if not record:
            return
        bid = record["id"]
        info = self._batch_run_info(bid)
        if info and info["state"].get("generate_batch"):
            self._start_operation(
                lambda client: process_saved_batch(client, info["run_dir"], bid, self.s)
            )
            return
        file_id = record.get("output_file_id")
        if not file_id:
            msg_info(self, "Výsledky nejsou dostupné", "Dávka dosud nemá output_file_id.")
            return
        target = (
            (info or {}).get("out_dir")
            or self.out_dir
            or dialog_select_dir(self, "OUT pro výsledky BATCH")
        )
        if not target:
            return

        def execute(client):
            raw = client.file_content(file_id)
            Path(target).mkdir(parents=True, exist_ok=True)
            raw_path = safe_join_under_root(target, f"batch_{bid}_output.jsonl")
            Path(raw_path).write_bytes(raw)
            # Účetní spotřeba se zaznamená i při neplatném souborovém výstupu.
            for line in raw.decode("utf-8").splitlines():
                try:
                    entry = json.loads(line)
                    body = (entry.get("response") or {}).get("body") or {}
                    self._record_batch_receipt(bid, body, info, raw_path)
                except (ValueError, TypeError, KeyError):
                    continue
            return dict(import_bundle(raw, target), raw_path=raw_path)

        self._start_operation(execute)

    def _record_batch_receipt(self, bid, body, info, raw_path):
        if not body.get("id") or not isinstance(body.get("usage"), dict):
            return
        prices = PriceTable(str(Path(self.s.cache_dir) / "price_table.json"))
        prices.bootstrap()
        row = prices.get(body.get("model", ""))
        total, tool, rates, reason = price_response(row, body, batch=True)
        usage = dict(body["usage"], _pricing_reason=reason)
        self.db_record = ReceiptDB(self.s.db_path).insert(
            Receipt(
                (info or {}).get("run_id", bid),
                time.time(),
                (info or {}).get("state", {}).get("project", ""),
                body.get("model", ""),
                "C",
                "C_FILES_ALL",
                body["id"],
                bid,
                usage.get("input_tokens", 0),
                usage.get("output_tokens", 0),
                float(tool) if tool is not None else None,
                0.0,
                float(total) if total is not None else None,
                bool(rates and rates.verified_at),
                "Výstup BATCH",
                {"batch_output": raw_path},
                usage,
                pricing_snapshot=rates.snapshot() if rates else {},
            )
        )

    def cancel(self):
        record = self.selected()
        if (
            record
            and msg_question(
                self,
                "Zrušit dávku?",
                f"Zrušit zpracování {record['id']}? Již dokončené požadavky mohou být účtovány.",
            )
            == QMessageBox.Yes
        ):
            self._start_operation(lambda client: client.cancel_batch(record["id"]))

    def repeat_selected(self, repair=False):
        record = self.selected()
        info = self._batch_run_info(record["id"]) if record else None
        if not info or not info["state"].get("generate_batch"):
            msg_info(self, "Opakování dávky", "Vyberte dávku vytvořenou režimem GENERATE.")
            return
        raw, ok = dialog_input_text(
            self, "Výběr souborů", "JSON pole relativních cest. Odešle novou placenou dávku.", "[]"
        )
        if not ok:
            return
        try:
            paths = json.loads(raw)
            if (
                not isinstance(paths, list)
                or not paths
                or not all(isinstance(path, str) for path in paths)
            ):
                raise ValueError("Vyžaduje se neprázdné pole cest.")
        except ValueError as exc:
            msg_warning(self, "Výběr souborů", str(exc))
            return
        feedback = ""
        if repair:
            feedback, ok = dialog_input_text(
                self, "Připomínka k opravě", "Popište problém nebo vložte výsledek testů."
            )
            if not ok or not feedback.strip():
                return
        prices = PriceTable(str(Path(self.s.cache_dir) / "price_table.json"))
        prices.bootstrap()
        self._cost_control = CostController(ReceiptDB(self.s.db_path), prices, info["run_id"], self)
        self._start_operation(
            lambda client: repeat_saved_batch(
                client, info["run_dir"], record["id"], paths, feedback, self._cost_control
            )
        )
