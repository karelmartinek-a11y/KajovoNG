"""Dlaždicový přehled vzdálených dávkových běhů."""

from __future__ import annotations

import json
import time
from copy import deepcopy
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QScrollArea, QWidget

from kajovo.core import photo_batch
from kajovo.core.contracts import ContractError
from kajovo.core.batch_completion import (
    CANCELLABLE,
    batch_ids,
    complete_saved_batch,
    pending_batch_ids,
    read_state,
    remember_remote_batch_state,
)
from kajovo.core.utils import atomic_write_text
from .components import action, actions, caption, confirm, friendly_error, vertical
from .evidence import VALUES
from .file_dialogs import get_existing_directory
from .job_cards import build_job_card, clear_cards, format_started, photo_failure_summary


MODE_LABELS = {
    "GENERATE": "Tvorba souborů",
    "MODIFY": "Úprava souborů",
    "QA": "Kontrola souborů",
    "QFILE": "Dotaz k souboru",
    "COMIC": "Komiks",
    "PHOTO": "Úpravy fotografií",
}


def _hidden_path(log_dir):
    return Path(log_dir) / "hidden_batches.json"


def _hidden_batches(log_dir):
    path = _hidden_path(log_dir)
    if not path.exists():
        return set()
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("Místní seznam smazaných dávek nelze načíst.") from error
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise ValueError("Místní seznam smazaných dávek má neplatný formát.")
    return set(value)


def _hide_batch(log_dir, identifier):
    hidden = _hidden_batches(log_dir)
    hidden.add(identifier)
    atomic_write_text(str(_hidden_path(log_dir)), json.dumps(sorted(hidden), ensure_ascii=False, indent=2) + "\n")


def _timestamp_value(value):
    try:
        if isinstance(value, (int, float)):
            return float(value)
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (OSError, OverflowError, TypeError, ValueError):
        return 0.0


def _read_overview_state(directory, errors):
    try:
        return read_state(directory)
    except (ContractError, OSError, ValueError) as error:
        errors.append(f"Běh {directory.name} se nepodařilo načíst: {friendly_error(error, 'Načtení evidence')}")
        return None


class BatchesPage(QWidget):
    def __init__(self, context, parent=None):
        super().__init__(parent)
        self.context = context
        self.records = []
        self.busy = False
        self.poll_started = 0
        self.focus_batch_id = ""
        self.card_widgets = {}
        self.action_buttons = []
        root = vertical(self)
        root.addWidget(caption("Dávky", "section"))
        self.refresh_button = action("batches.refresh", "Obnovit", self.refresh)
        root.addWidget(actions(self.refresh_button))
        self.cards_container = QWidget()
        self.cards_layout = vertical(self.cards_container, 0)
        self.cards_layout.addStretch()
        self.cards_scroll = QScrollArea()
        self.cards_scroll.setWidgetResizable(True)
        self.cards_scroll.setWidget(self.cards_container)
        root.addWidget(self.cards_scroll, 1)
        self.notice = caption("Přehled se aktualizuje při otevření této stránky.", "muted")
        root.addWidget(self.notice)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(lambda: self.refresh(automatic=True))
        context.key_changed.connect(self.reset)

    def reset(self):
        self.timer.stop()
        self.records = []
        self.render()
        self.poll_started = 0

    def execute(self, title, function, receive=None, popup=True, output_dir=None, identifier=None):
        if self.busy:
            return
        try:
            client = self.context.client()
            self.context.operations.assert_output_available(output_dir)
        except ValueError as error:
            self.notice.setText(friendly_error(error, title))
            return
        self.busy = True
        key = self.context.api_key
        log_dir = self.context.settings.log_dir
        self.refresh_button.setEnabled(False)
        for widget in self.action_buttons:
            widget.setEnabled(False)

        def accept(value):
            if receive and key == self.context.api_key and log_dir == self.context.settings.log_dir:
                receive(value)

        record = self.context.operations.start(
            title,
            lambda task: function(client, task),
            accept if receive else None,
            popup=popup,
            output_dir=output_dir,
            identifier=identifier,
        )

        def release():
            self.busy = False
            self.refresh_button.setEnabled(True)
            self.render()

        record.worker.finished.connect(release)
        return record

    def page_activated(self):
        if self.context.api_key:
            self.refresh(quiet=True)
        else:
            try:
                self.records = self._local_records()
                self.render()
            except (OSError, ValueError) as error:
                self.notice.setText(friendly_error(error, "Načtení dávek"))

    def _local_records(self):
        root = Path(self.context.settings.log_dir)
        hidden = _hidden_batches(root)
        records = []
        errors = []
        if root.exists():
            for directory in root.iterdir():
                if not directory.is_dir() or not directory.name.startswith("RUN_"):
                    continue
                state = _read_overview_state(directory, errors)
                if state is None:
                    continue
                for identifier in batch_ids(state):
                    if identifier in hidden:
                        continue
                    records.append({
                        "id": identifier,
                        "remote": {},
                        "state": state,
                        "run_dir": str(directory),
                        "kind": str(state.get("mode") or "BATCH"),
                        "photo": None,
                        "started_at": state.get("started_at") or state.get("created_at"),
                    })
        markers = photo_batch.deleted_job_markers(root)
        hidden_photo_ids = {item["batch_id"] for item in markers if item["batch_id"]}
        hidden_photo_files = {item["input_file_id"] for item in markers if item["input_file_id"]}
        for job in photo_batch.load_jobs(root, errors=errors):
            if job.batch_id in hidden or job.batch_id in hidden_photo_ids or job.input_file_id in hidden_photo_files:
                continue
            records.append({
                "id": job.batch_id or job.job_id,
                "remote": {},
                "state": {},
                "run_dir": "",
                "kind": "PHOTO",
                "photo": job,
                "started_at": job.created_at,
            })
        if errors:
            self.notice.setText("Část dávek se nepodařilo načíst.\n" + "\n".join(errors))
        return sorted(records, key=lambda row: _timestamp_value(row["started_at"]), reverse=True)

    def refresh(self, checked=False, automatic=False, quiet=False):
        if not automatic:
            self.poll_started = time.monotonic()
        if automatic and time.monotonic() - self.poll_started >= self.context.settings.batch_timeout_s:
            self.notice.setText("Automatické sledování skončilo. Vzdálené dávky pokračují; klepnutím na Obnovit zobrazíte nový stav.")
            return
        key = self.context.api_key
        root = Path(self.context.settings.log_dir)
        errors = []

        def fetch(client, task):
            remote = {row["id"]: row for row in client.list_batches() if row.get("id")}
            hidden = _hidden_batches(root)
            markers = photo_batch.deleted_job_markers(root)
            hidden_photo_ids = {item["batch_id"] for item in markers if item["batch_id"]}
            hidden_photo_files = {item["input_file_id"] for item in markers if item["input_file_id"]}
            records = []
            if root.exists():
                for directory in root.iterdir():
                    if not directory.is_dir() or not directory.name.startswith("RUN_"):
                        continue
                    state = _read_overview_state(directory, errors)
                    if state is None:
                        continue
                    for identifier in batch_ids(state):
                        if identifier in hidden:
                            remote.pop(identifier, None)
                            continue
                        remote_record = remote.pop(identifier, {})
                        if remote_record:
                            remember_remote_batch_state(directory, remote_record)
                            state = read_state(directory)
                        records.append({
                            "id": identifier,
                            "remote": remote_record,
                            "state": state,
                            "run_dir": str(directory),
                            "kind": str(state.get("mode") or "BATCH"),
                            "photo": None,
                            "started_at": state.get("started_at") or state.get("created_at") or remote_record.get("created_at"),
                        })
            for original in photo_batch.load_jobs(root, errors=errors):
                job = deepcopy(original)
                if job.batch_id in hidden or job.batch_id in hidden_photo_ids or job.input_file_id in hidden_photo_files:
                    if job.batch_id:
                        remote.pop(job.batch_id, None)
                    continue
                remote_record = remote.pop(job.batch_id, {}) if job.batch_id else {}
                if remote_record:
                    try:
                        photo_batch.refresh_job(client, job, root, batch=remote_record)
                    except Exception as error:
                        message = f"Dávka {job.job_id}: {friendly_error(error, 'Obnovení stavu')}"
                        errors.append(message)
                        task.logline.emit(message)
                        job = original
                records.append({
                    "id": job.batch_id or job.job_id,
                    "remote": remote_record,
                    "state": {},
                    "run_dir": "",
                    "kind": "PHOTO",
                    "photo": job,
                    "started_at": job.created_at,
                })
            for identifier, value in remote.items():
                if identifier in hidden or identifier in hidden_photo_ids or value.get("input_file_id") in hidden_photo_files:
                    continue
                metadata = value.get("metadata")
                metadata = metadata if isinstance(metadata, dict) else {}
                records.append({
                    "id": identifier,
                    "remote": value,
                    "state": {},
                    "run_dir": "",
                    "kind": str(metadata.get("kajovo_mode") or "BATCH"),
                    "photo": None,
                    "started_at": value.get("created_at"),
                })
            return sorted(records, key=lambda row: _timestamp_value(row["started_at"]), reverse=True)

        def receive(records):
            if key != self.context.api_key or root != Path(self.context.settings.log_dir):
                return
            self.records = records
            self.render()
            active = any(record["remote"].get("status") in CANCELLABLE for record in records)
            if active:
                interval = self.context.settings.batch_poll_interval_s
                self.timer.start(max(1, int(interval * 1000)))
            self.notice.setText(
                "Část dávek se nepodařilo obnovit. Ostatní výsledky zůstávají dostupné.\n"
                + "\n".join(dict.fromkeys(errors)) if errors else "Stavy dávek jsou aktualizované."
            )

        self.execute(
            "Obnovení přehledu dávek",
            fetch,
            receive,
            popup=not (automatic or quiet),
            identifier="batches.monitor",
        )

    def _status(self, record):
        if record["photo"] is not None:
            return VALUES.get(record["photo"].status, "Stav není rozpoznán")
        remote = record["remote"]
        if remote.get("status"):
            return VALUES.get(remote["status"], "Stav není rozpoznán")
        state = record["state"]
        return VALUES.get(state.get("status"), "Stav není dostupný")

    def _summary(self, record):
        photo = record["photo"]
        if photo is not None:
            downloaded = sum(item.status == "downloaded" for item in photo.items)
            failed = sum(item.status == "failed" for item in photo.items)
            summary = (f"{len(photo.items)} fotografií · "
                       f"{downloaded} uloženo · {failed} se nepodařilo")
            reason = photo_failure_summary(photo)
            return f"{summary} · {reason}" if reason else summary
        counts = record["remote"].get("request_counts") or {}
        total = counts.get("total", 0)
        complete = counts.get("completed", 0)
        failed = counts.get("failed", 0)
        local_state = record["state"]
        local_result = VALUES.get(local_state.get("status"), "") if local_state else ""
        if record["id"] in pending_batch_ids(local_state):
            local_result = "Čeká na stažení výsledků"
        summary = f"{complete + failed} z {total} úloh · {failed} chyb"
        imported = (local_state.get("batch_imports") or {}).get(record["id"], {}) if local_state else {}
        errors = imported.get("errors") or imported.get("completed_errors") or {}
        if isinstance(errors, dict) and errors:
            from kajovo.core.user_errors import describe_recorded_error

            first = next(iter(errors.values()))
            report = describe_recorded_error(first, operation="Zpracování dávky")
            summary += f" · {report.message}"
        return f"{summary} · {local_result}" if local_result else summary

    def render(self):
        clear_cards(self.cards_layout)
        self.cards_layout.addStretch()
        self.card_widgets = {}
        self.action_buttons = []
        for record in self.records:
            identifier = record["id"]
            status = self._status(record)
            title = MODE_LABELS.get(record["kind"], "Dávkové zpracování")
            photo = record["photo"]
            download_enabled = (
                bool(photo and (photo.output_file_id or photo.error_file_id)
                     and photo.status in {"completed", "failed", "expired", "cancelled", "downloaded", "partial"})
                or bool(record["run_dir"] and record["remote"].get("output_file_id"))
            )
            can_cancel = record["remote"].get("status") in CANCELLABLE
            specs = [
                (f"batches.download.{identifier}", "Uložit výsledné fotografie do adresáře" if photo is not None else "Stáhnout", lambda _=False, current=record: self.download(current), "primary", download_enabled),
                (f"batches.cancel.{identifier}", "Zrušit dávku", lambda _=False, current=record: self.cancel(current), "danger", can_cancel),
                (f"batches.delete.{identifier}", "Smazat dávku", lambda _=False, current=record: self.delete(current), "danger", True),
            ]
            card, buttons = build_job_card(
                title,
                status,
                format_started(record["started_at"]),
                self._summary(record),
                specs,
                f"batches.card.{identifier}",
            )
            self.cards_layout.insertWidget(max(0, self.cards_layout.count() - 1), card)
            self.card_widgets[identifier] = card
            self.action_buttons.extend(buttons)
        if not self.records:
            self.cards_layout.insertWidget(0, caption("Zatím nejsou dostupné žádné dávky.", "muted"))
        if self.busy:
            for widget in self.action_buttons:
                widget.setEnabled(False)
        if self.focus_batch_id:
            card = self.card_widgets.get(self.focus_batch_id)
            if card:
                self.cards_scroll.ensureWidgetVisible(card)
            self.focus_batch_id = ""

    def focus_batch(self, identifier):
        self.focus_batch_id = str(identifier or "")
        self.refresh()

    def download(self, record):
        log_dir = self.context.settings.log_dir
        settings = deepcopy(self.context.settings)
        if record["photo"] is not None:
            job = record["photo"]
            directory = job.output_dir or job.saved_output_dir
            if not directory:
                directory = get_existing_directory(self, "Uložit výsledné fotografie do adresáře")
            if not directory:
                self.notice.setText("Uložení bylo zrušeno. Výsledky zůstaly připravené ke stažení.")
                return

            def save_photo(client, task):
                return photo_batch.download_results(
                    client,
                    job,
                    log_dir,
                    reporter=task.logline.emit,
                    progress=task.progress_event.emit,
                    output_dir=directory,
                )

            self.execute("Uložení upravených fotografií", save_photo,
                         lambda value: self.refresh(), output_dir=directory)
            return
        if not record["run_dir"]:
            self.notice.setText("Tato dávka nemá v tomto počítači uložené podklady potřebné ke stažení.")
            return
        root = record["run_dir"]
        identifier = record["id"]

        def receive(value):
            self.notice.setText("Výsledky dávky byly staženy a bezpečně ověřeny.")
            self.refresh()

        self.execute(
            "Stažení výsledků dávky",
            lambda client, task: complete_saved_batch(
                client, root, identifier, settings, progress=task.progress_event.emit
            ),
            receive,
            output_dir=record["state"].get("out_dir"),
        )

    def cancel(self, record):
        if record["remote"].get("status") not in CANCELLABLE:
            return
        if not confirm(self, "Zrušit dávku", "Požádat službu o zrušení dávky? Již zpracované úlohy mohou být účtované."):
            return

        def receive(value):
            self.notice.setText("Žádost o zrušení byla přijata. Stav se znovu ověří.")
            self.refresh()

        self.execute("Zrušení dávky", lambda client, task: client.cancel_batch(record["id"]), receive)

    def delete(self, record):
        if not confirm(
            self,
            "Smazat dávku z přehledu",
            "Odebrat tuto dávku z místního přehledu? Výstupní soubory zůstanou zachovány. Pokud právě běží, program se ji nejprve pokusí zrušit.",
        ):
            return

        log_dir = self.context.settings.log_dir

        def remove(client, task):
            cancel_error = None
            if record["remote"].get("status") in CANCELLABLE:
                try:
                    client.cancel_batch(record["id"])
                except Exception as error:
                    cancel_error = error
            if record["photo"] is not None:
                photo_batch.delete_job(record["photo"], log_dir)
            else:
                _hide_batch(log_dir, record["id"])
            return cancel_error

        def receive(cancel_error):
            self.records = [item for item in self.records if item["id"] != record["id"]]
            self.render()
            if cancel_error:
                self.notice.setText(
                    friendly_error(cancel_error, "Zrušení vzdálené dávky")
                    + " Místní záznam byl odebrán; vzdálená dávka může pokračovat."
                )
            else:
                self.notice.setText("Dávka byla odebrána z místního přehledu. Výstupní soubory zůstaly zachovány.")

        self.execute("Smazání dávky z přehledu", remove, receive, popup=False)
