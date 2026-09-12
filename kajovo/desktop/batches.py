"""Sledování dávek, přenos výsledků a opakování vybraných souborů."""

import inspect
import json
import time
from datetime import datetime
from pathlib import Path
from PySide6.QtCore import QTimer, Signal, Qt
from PySide6.QtWidgets import QWidget, QTableWidgetItem, QMessageBox, QMenu
from ..core.openai_client import OpenAIClient
from ..core.generate_batch import repeat_saved_batch
from ..core.batch_completion import (
    import_bundle, complete_saved_batch, local_batches, pending_batch_ids, read_state, CANCELLABLE,
    can_continue_preflight, read_batch_statuses, save_batch_statuses, recover_unknown_submission,
)
from .batch_view import project_name, saved_record, server_label
from ..core.utils import safe_join_under_root

from .design import column, row, label, button, table
from .dialogs import msg_info, msg_warning, msg_question, dialog_select_dir, dialog_input_text
from .jobs import Jobs


class BatchPanel(QWidget):
    continue_preflight = Signal(str)
    logline = Signal(str)
    runs_changed = Signal()
    operation_changed = Signal()

    def __init__(self, settings, api_key, parent=None):
        super().__init__(parent)
        self.s, self.api_key = settings, api_key
        self.client = None
        self.busy_run_id = None
        self.active_runs = set()
        self.run_guard = lambda run_id, target: None
        self._records = []
        self.out_dir = ""
        self.jobs = Jobs(self)
        self._refresh_task = self._operation_task = None
        self._monitor_started = self._last_poll = None
        self._monitor_paused_reason = None
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
                "Odesláno",
                "Endpoint",
                "Vstupní file_id",
                "Výstupní file_id",
                "Chyby",
                "Dokončeno / celkem",
                "Projekt",
                "Uložení do OUT",
                "Akce",
                "Typ dávky",
                "Běh",
            ]
        )
        self.tbl.setColumnWidth(0, 200)
        self.tbl.setColumnWidth(2, 160)
        self.tbl.setColumnWidth(8, 125)
        self.tbl.setColumnWidth(9, 155)
        self.tbl.setColumnWidth(10, 105)
        self.tbl.setColumnWidth(1, 100)
        self.tbl.setColumnWidth(11, 95)
        self.tbl.verticalHeader().setDefaultSectionSize(44)
        header = self.tbl.horizontalHeader()
        for visual, logical in enumerate((11, 8, 2, 1, 9, 10, 12, 0, 7, 3, 4, 5, 6)):
            header.moveSection(header.visualIndex(logical), visual)
        self.tbl.itemSelectionChanged.connect(self._update_actions)
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
        self.btn_cancel.setToolTip("Zruší zpracování. OpenAI neumožňuje smazat samotný záznam dávky.")
        self._update_actions()
        self.render_records()

    def set_out_dir(self, path):
        self.out_dir = path

    def set_api_key(self, key):
        self.api_key = key
        self.client = None
        self._poll_timer.stop()
        self._monitor_started = self._last_poll = None
        self._monitor_paused_reason = None
        self._records = []
        self.tbl.setRowCount(0)
        self._update_actions()

    def _update_poll_label(self):
        age = f"před {int(time.monotonic() - self._last_poll)} s" if self._last_poll is not None else "dosud neproběhlo"
        next_poll = (f"za {max(0, self._poll_timer.remainingTime()) // 1000} s" if self._poll_timer.isActive() else "není naplánována")
        reason = ""
        if self._monitor_paused_reason == "timeout":
            reason = " Automatické sledování bylo pozastaveno po dosažení časového limitu; vzdálená dávka pokračuje u OpenAI."
        elif self._monitor_paused_reason == "no_active_batches":
            reason = " Žádná známá dávka nyní nevyžaduje automatické sledování."
        self.lbl_poll.setText(
            f"Poslední ověření: {age} · Další kontrola: {next_poll}\nETA fronty nelze určit."
            + reason + (" Ověřuji stav…" if self._refresh_task else "")
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
            self._monitor_paused_reason = "timeout"
            self._update_poll_label()
            return
        if not automatic:
            self._monitor_paused_reason = None
        key = self.api_key
        self._poll_timer.stop()
        self.btn_refresh.setEnabled(False)

        def execute(job):
            client = self.client or OpenAIClient(key)
            return {
                "key": key,
                "batches": client.list_batches(),
            }

        def finished():
            self._refresh_task = None
            self.btn_refresh.setEnabled(True)

        self._refresh_task = self.jobs.start(
            "Načítání dávek", execute, self._on_refreshed, popup=False, on_finished=finished
        )

    def _on_refreshed(self, result):
        if result["key"] != self.api_key:
            return
        self._last_poll = time.monotonic()
        records = result["batches"]
        # Ruční/periodický refresh může bezpečně dohledat neurčitý pracovní submit,
        # ale nikdy sám neodesílá novou pracovní dávku.
        for run_dir in Path(self.s.log_dir).glob("RUN_*"):
            try:
                recover_unknown_submission(str(run_dir), records)
            except Exception as exc:
                self.logline.emit(f"Recovery neurčitého BATCH submitu: {run_dir.name}: {exc}")
        save_batch_statuses(self.s.log_dir, records)
        snapshots = read_batch_statuses(self.s.log_dir)
        self._records = [snapshots.get(record["id"], record) for record in records]
        self.render_records()
        self.runs_changed.emit()
        active = any(
            record.get("status") not in ("completed", "failed", "cancelled", "expired")
            for record in records
        )
        self._monitor_paused_reason = None if active else "no_active_batches"
        if (
            active
            and self._monitor_started is not None
            and time.monotonic() - self._monitor_started < self.s.batch_timeout_s
        ):
            self._poll_timer.start(int(self.s.batch_poll_interval_s * 1000))

    @staticmethod
    def format_sent_at(value):
        try:
            if type(value) not in (int, float) or value < 0:
                return "Není dostupné"
            return datetime.fromtimestamp(value).strftime("%d.%m.%Y %H:%M:%S")
        except (ValueError, OverflowError, OSError):
            return "Není dostupné"

    def render_records(self):
        from .dialogs import STATES
        item = self.tbl.item(self.tbl.currentRow(), 0)
        selected_id = item.data(Qt.UserRole).get("id") if item else None
        local = local_batches(self.s.log_dir)
        snapshots = read_batch_statuses(self.s.log_dir)
        records_by_id = {record["id"]: record for record in self._records}
        for bid, info in local.items():
            records_by_id[bid] = {**records_by_id.get(bid, {}), **saved_record(info["state"], bid, snapshots)}
        records = list(records_by_id.values())
        self.tbl.setRowCount(0)
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
            info = local.get(record.get("id"))
            state = info["state"] if info else {}
            trial = bool(info and info["kind"] == "preflight")
            links = info["runs"] if info else []
            saved = (state.get("batch_records") or {}).get(record.get("id"), {})
            stamp = record.get("created_at")
            values[2] = self.format_sent_at(stamp if stamp is not None else saved.get("created_at"))
            values[1] = STATES.get(record.get("status"), record.get("status", ""))
            project = ", ".join(dict.fromkeys(project_name(link["state"]) for link in links)) if info else "Bez místního běhu"
            imported = (state.get("batch_imports") or {}).get(record.get("id"), {})
            status = imported.get("import_status", imported.get("status", ""))
            values.extend([project, "Nevytváří soubory" if trial else
                           (STATES.get(status, status) if status else
                            ("Čeká na převzetí" if info else "—")), "",
                           "Zkušební" if trial else ("Pracovní" if info else "Externí"),
                           ", ".join(link["run_id"] for link in links)])
            for col, value in enumerate(values):
                item = QTableWidgetItem(str(value or ""))
                item.setToolTip(str(value or ""))
                item.setData(Qt.UserRole, record)
                self.tbl.setItem(index, col, item)
            if trial:
                self.tbl.item(index, 1).setToolTip(server_label(record, any(can_continue_preflight(link["state"]) for link in links)) + "\n" +
                    json.dumps(record.get("errors") or {}, ensure_ascii=False))
                eligible = [link for link in links if can_continue_preflight(link["state"])]
                if eligible:
                    action = button("Pokračovat", role="Primary")
                    action.setToolTip("Převzít ověření a při úspěchu odeslat pracovní dávku.")
                    if len(eligible) == 1:
                        action.clicked.connect(lambda checked=False, rid=eligible[0]["run_id"]:
                                               self.continue_run(rid))
                        action.setEnabled(not self._operation_task and eligible[0]["run_id"] not in self.active_runs)
                    else:
                        menu = QMenu(action)
                        for link in eligible:
                            entry = menu.addAction(project_name(link["state"]) + " · " + link["run_id"])
                            entry.triggered.connect(lambda checked=False, rid=link["run_id"]: self.continue_run(rid))
                            entry.setEnabled(link["run_id"] not in self.active_runs)
                        action.setMenu(menu)
                        action.setEnabled(not self._operation_task)
                    self.tbl.setCellWidget(index, 10, action)
            elif info and record["id"] in pending_batch_ids(state):
                action = button("Dokončit", lambda checked=False, rid=info["run_id"], bid=record["id"]:
                                self.complete_run(rid, bid), "Primary")
                action.setEnabled(not self._operation_task and info["run_id"] not in self.active_runs)
                self.tbl.setCellWidget(index, 10, action)
            if record.get("id") == selected_id:
                self.tbl.selectRow(index)
        self._update_actions()

    def _update_actions(self):
        item = self.tbl.item(self.tbl.currentRow(), 0)
        record = item.data(Qt.UserRole) if item else {}
        info = self._batch_run_info(record.get("id")) if record else None
        trial = bool(info and info["kind"] == "preflight")
        busy = bool(self._operation_task or (info and any(link["run_id"] in self.active_runs for link in info["runs"])))
        self.btn_cancel.setEnabled(not busy and record.get("status") in CANCELLABLE)
        self.btn_download.setEnabled(bool(record) and not busy and not trial)
        for action in (self.btn_repeat, self.btn_repair):
            action.setEnabled(bool(info and info["state"].get("generate_batch")) and not busy and not trial)

    def selected(self):
        item = self.tbl.item(self.tbl.currentRow(), 0)
        if not item:
            msg_info(self, "Dávky", "Vyberte dávku v tabulce.")
            return None
        return item.data(Qt.UserRole)

    def _batch_run_info(self, batch_id):
        return local_batches(self.s.log_dir).get(batch_id)

    def continue_run(self, run_id):
        if self._operation_task:
            msg_info(self, "Pokračování BATCH", "Již probíhá operace s dávkou. Vyčkejte na její dokončení.")
            return
        try:
            if run_id in self.active_runs:
                raise ValueError("Běh nebo jeho sdílenou zkoušku právě zpracovává aktivní práce.")
            state = read_state(safe_join_under_root(self.s.log_dir, run_id))
            self.run_guard(run_id, state.get("out_dir", ""))
            if not can_continue_preflight(state):
                raise ValueError("Běh již nemá zkušební dávku čekající na pokračování.")
        except ValueError as exc:
            msg_warning(self, "Pokračování BATCH", str(exc))
            return
        self.continue_preflight.emit(run_id)

    def complete_run(self, run_id, batch_id=""):
        if self._operation_task:
            msg_info(self, "Dokončení BATCH", "Již probíhá operace s dávkou. Vyčkejte na její dokončení.")
            return
        if not self.api_key:
            msg_info(self, "Dokončení BATCH", "Nejdříve uložte API klíč.")
            return
        try:
            run_dir = safe_join_under_root(self.s.log_dir, run_id)
            state = read_state(run_dir)
            bids = [batch_id] if batch_id else pending_batch_ids(state)
            if not bids:
                msg_info(self, "Dokončení BATCH", "Běh nemá dávku čekající na převzetí.")
                return
            self.run_guard(run_id, state.get("out_dir", ""))
        except ValueError as exc:
            msg_warning(self, "Dokončení BATCH", str(exc))
            return

        def execute(client, job):
            results = [complete_saved_batch(client, run_dir, bid, self.s, progress=job.progress_event.emit) for bid in bids]
            if len(results) == 1:
                return results[0]
            return {"written": [path for result in results for path in result.get("written", [])],
                    "status": "batch_pending" if any(result["status"] == "batch_pending" for result in results)
                    else read_state(run_dir).get("status", "partial"), "batches": results}

        self._start_operation("Dokončení a import BATCH", execute, run_id=run_id)

    def _validated_client(self, key):
        client = self.client or OpenAIClient(key, timeout_s=self.s.response_timeout_s)
        if not hasattr(client, "_policy"):
            client.configure_validation(self.s)
        return client

    @staticmethod
    def _invoke_operation(operation, client, job):
        """Volá starší operation(client) i novější operation(client, job) bez maskování TypeError uvnitř operace."""
        try:
            signature = inspect.signature(operation)
        except (TypeError, ValueError):
            return operation(client, job)
        params = list(signature.parameters.values())
        positional = [p for p in params if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
        if any(p.kind == p.VAR_POSITIONAL for p in params) or len(positional) >= 2:
            return operation(client, job)
        return operation(client)

    def _start_operation(self, title_or_operation, operation=None, *, run_id=None):
        if operation is None:
            operation = title_or_operation
            title = "Zpracování výsledků BATCH"
        else:
            title = str(title_or_operation)
        if self._operation_task:
            return
        if run_id:
            try:
                state = read_state(safe_join_under_root(self.s.log_dir, run_id))
                self.run_guard(run_id, state.get("out_dir", ""))
            except ValueError as exc:
                msg_warning(self, title, str(exc))
                return
        key = self.api_key
        self.busy_run_id = run_id

        def receive(result):
            if key != self.api_key:
                return
            if result.get("id"):
                save_batch_statuses(self.s.log_dir, [result])
                self._records = [({**record, **result} if record.get("id") == result["id"] else record) for record in self._records]
            from .dialogs import STATES
            status = result.get("status", "")
            message = result.get("detail") or f"Stav: {STATES.get(status, status or result.get('batch_id', 'dokončeno'))}"
            if "written" in result:
                message += f"\nZapsáno souborů: {len(result['written'])}"
            if result.get("written"):
                message += "\nFunkčnost souborů ověřte sestavením a testy."
            msg_info(self, "Výsledek operace BATCH", message, result)
            self.load()

        def finished():
            self._operation_task = None
            self.busy_run_id = None
            for action in (self.btn_download, self.btn_repeat, self.btn_repair):
                action.setEnabled(True)
            self.render_records()
            self.runs_changed.emit()
            self.operation_changed.emit()

        for action in (self.btn_download, self.btn_repeat, self.btn_repair, self.btn_cancel):
            action.setEnabled(False)
        self._operation_task = self.jobs.start(
            title,
            lambda job: self._invoke_operation(operation, self._validated_client(key), job),
            receive,
            on_finished=finished,
        )
        self.render_records()
        self.operation_changed.emit()

    def download(self):
        record = self.selected()
        if not record:
            return
        bid = record["id"]
        info = self._batch_run_info(bid)
        if info and info["kind"] == "preflight":
            msg_info(self, "Zkušební dávka", "Výsledek ověření převezměte tlačítkem Pokračovat u běhu.")
            return
        if info:
            self.complete_run(info["run_id"], bid)
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

        def execute(client, job):
            job.status.emit("Stahuji výstupní JSONL.")
            raw = client.file_content(file_id)
            Path(target).mkdir(parents=True, exist_ok=True)
            raw_path = safe_join_under_root(target, f"batch_{bid}_output.jsonl")
            Path(raw_path).write_bytes(raw)
            return dict(import_bundle(raw, target, progress=job.progress_event.emit), raw_path=raw_path)

        self._start_operation("Stažení a import výsledků BATCH", execute)


    def cancel(self):
        record = self.selected()
        info = self._batch_run_info(record["id"]) if record else None
        if self._operation_task or (info and any(link["run_id"] in self.active_runs for link in info["runs"])):
            msg_info(self, "Zrušení dávky", "Dávku právě používá aktivní práce. Vyčkejte na její dokončení.")
            return
        if (
            record
            and record.get("status") in CANCELLABLE
            and msg_question(
                self,
                "Zrušit dávku?",
                f"Zrušit zpracování {record['id']}? Již dokončené požadavky mohou být účtovány.",
            )
            == QMessageBox.Yes
        ):
            self._start_operation(
                "Ruším vzdálenou dávku",
                lambda client, job: (job.status.emit(f"Odesílám požadavek na zrušení {record['id']}…") or client.cancel_batch(record["id"])),
                run_id=info["run_id"] if info else None,
            )

    def repeat_selected(self, repair=False):
        record = self.selected()
        info = self._batch_run_info(record["id"]) if record else None
        if not info or info["kind"] == "preflight" or not info["state"].get("generate_batch"):
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
        title = "Oprava souborů – odesílám novou dávku" if repair else "Opakování souborů – odesílám novou dávku"
        self._start_operation(
            title,
            lambda client, job: repeat_saved_batch(client, info["run_dir"], record["id"], paths, feedback),
            run_id=info["run_id"],
        )
