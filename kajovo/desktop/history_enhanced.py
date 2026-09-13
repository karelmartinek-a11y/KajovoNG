"""Doplňující UX Run Exploreru: intervaly a přímé vazby artefaktů."""
from __future__ import annotations

import difflib
from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QLineEdit, QVBoxLayout

from .design import button
from .dialogs import msg_info, msg_warning
from .history_run_explorer import (
    ResponseRequestPanel as BaseResponseRequestPanel,
    StepDetailDialog,
    TechnicalViewer,
    _parse_iso,
)

MAX_TEXT_COMPARE_BYTES = 5 * 1024 * 1024


class EvidenceDialog(QDialog):
    """Jednoznačný raw detail requestu/response bez obsahové transformace."""

    def __init__(self, title: str, value, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(1050, 760)
        layout = QVBoxLayout(self)
        viewer = TechnicalViewer()
        viewer.set_value(value)
        layout.addWidget(viewer, 1)
        layout.addWidget(button("Zavřít", self.accept))


class ResponseRequestPanel(BaseResponseRequestPanel):
    """Finální Run Explorer s vlastním časovým intervalem a provenance akcemi."""

    def _build_ui(self):
        super()._build_ui()
        index = self.period.findText("Vlastní datum")
        if index >= 0:
            self.period.setItemText(index, "Vlastní interval")
        elif self.period.findText("Vlastní interval") < 0:
            self.period.addItem("Vlastní interval")

        self.ed_date.hide()
        self.ed_date_from = QLineEdit()
        self.ed_date_from.setPlaceholderText("Od DD.MM.YYYY")
        self.ed_date_to = QLineEdit()
        self.ed_date_to.setPlaceholderText("Do DD.MM.YYYY")
        self.ed_date_from.returnPressed.connect(self.apply_filters)
        self.ed_date_to.returnPressed.connect(self.apply_filters)

        root_layout = self.layout()
        first_row = root_layout.itemAt(0).layout() if root_layout and root_layout.count() else None
        if first_row is not None:
            first_row.insertWidget(2, QLabel("Od"))
            first_row.insertWidget(3, self.ed_date_from)
            first_row.insertWidget(4, QLabel("Do"))
            first_row.insertWidget(5, self.ed_date_to)
        self.period.currentTextChanged.connect(self._toggle_range_fields)
        self._toggle_range_fields(self.period.currentText())

    def _build_files_tab(self):
        super()._build_files_tab()
        page = self.tabs.widget(self.tabs.count() - 1)
        body = page.layout()
        provenance = QHBoxLayout()
        self.btn_compare_artifacts = button(
            "Porovnat vybrané", self.compare_selected_artifacts
        )
        self.btn_source_request = button(
            "Zdrojový request", self.show_source_request
        )
        self.btn_source_response = button(
            "Zdrojová response", self.show_source_response
        )
        provenance.addWidget(self.btn_compare_artifacts)
        provenance.addWidget(self.btn_source_request)
        provenance.addWidget(self.btn_source_response)
        provenance.addStretch(1)
        body.insertLayout(max(0, body.count() - 1), provenance)

    def _toggle_range_fields(self, value: str) -> None:
        visible = value == "Vlastní interval"
        self.ed_date_from.setVisible(visible)
        self.ed_date_to.setVisible(visible)

    @staticmethod
    def _parse_user_date(value: str):
        value = value.strip()
        if not value:
            return None
        for pattern in ("%d.%m.%Y", "%Y-%m-%d", "%d%m%Y"):
            try:
                return datetime.strptime(value, pattern).date()
            except ValueError:
                continue
        return None

    def _period_match(self, record: dict) -> bool:
        if self.period.currentText() != "Vlastní interval":
            return super()._period_match(record)
        created = _parse_iso(str(record.get("created_at") or ""))
        if not created:
            _display, legacy_date, _timestamp = self._parse_run_date(
                str(record.get("run_id") or "")
            )
            if legacy_date:
                try:
                    created = datetime.strptime(legacy_date, "%Y-%m-%d").replace(
                        tzinfo=timezone.utc
                    )
                except ValueError:
                    created = None
        if not created:
            return False
        start = self._parse_user_date(self.ed_date_from.text())
        end = self._parse_user_date(self.ed_date_to.text())
        if start is None and end is None:
            return False
        if start and end and start > end:
            start, end = end, start
        current = created.astimezone().date()
        if start and current < start:
            return False
        if end and current > end:
            return False
        return True

    def reset_filters(self):
        if hasattr(self, "ed_date_from"):
            self.ed_date_from.clear()
            self.ed_date_to.clear()
        super().reset_filters()

    def _selected_artifacts(self) -> list[dict]:
        if not self.artifact_table.selectionModel():
            return []
        records = []
        for index in self.artifact_table.selectionModel().selectedRows():
            item = self.artifact_table.item(index.row(), 0)
            value = item.data(0x0100) if item else None  # Qt.UserRole
            if isinstance(value, dict):
                records.append(value)
        return records

    def compare_selected_artifacts(self) -> None:
        records = self._selected_artifacts()
        if len(records) != 2:
            msg_info(
                self,
                "Porovnání artefaktů",
                "Vyberte právě dva artefakty, které chcete porovnat.",
            )
            return
        first, second = records
        first_path = self._artifact_path(first)
        second_path = self._artifact_path(second)
        if not first_path or not second_path:
            msg_warning(
                self,
                "Porovnání artefaktů",
                "Alespoň jeden z vybraných artefaktů již není lokálně dostupný.",
            )
            return
        if first.get("sha256") and first.get("sha256") == second.get("sha256"):
            self.artifact_preview.set_value(
                {
                    "výsledek": "Artefakty jsou obsahově identické podle SHA-256.",
                    "první": first,
                    "druhý": second,
                }
            )
            return
        if (
            first_path.stat().st_size > MAX_TEXT_COMPARE_BYTES
            or second_path.stat().st_size > MAX_TEXT_COMPARE_BYTES
        ):
            self.artifact_preview.set_value(
                {
                    "výsledek": "Soubory se liší; textový diff nebyl načten, protože alespoň jeden přesahuje 5 MiB.",
                    "první": first,
                    "druhý": second,
                }
            )
            return
        try:
            left = first_path.read_text(encoding="utf-8").splitlines(keepends=True)
            right = second_path.read_text(encoding="utf-8").splitlines(keepends=True)
        except UnicodeDecodeError:
            self.artifact_preview.set_value(
                {
                    "výsledek": "Binární artefakty se liší; porovnejte SHA-256, velikost a provenance.",
                    "první": first,
                    "druhý": second,
                }
            )
            return
        except OSError as exc:
            msg_warning(self, "Porovnání artefaktů", str(exc))
            return
        diff = "".join(
            difflib.unified_diff(
                left,
                right,
                fromfile=str(first.get("display_name") or first_path.name),
                tofile=str(second.get("display_name") or second_path.name),
            )
        )
        self.artifact_preview.set_value(diff or "Textový obsah je shodný.")

    def _show_evidence(self, kind: str) -> None:
        if not self._selected_artifact or not self._adapter:
            return
        key = "source_request_id" if kind == "request" else "source_response_id"
        identifier = str(self._selected_artifact.get(key) or "")
        if not identifier:
            msg_info(
                self,
                "Provenance artefaktu",
                f"Zdrojový {kind} není u tohoto artefaktu evidován.",
            )
            return
        records = self._adapter.requests() if kind == "request" else self._adapter.responses()
        id_keys = (
            ("request_record_id", "remote_request_id")
            if kind == "request"
            else ("response_record_id", "response_id")
        )
        record = next(
            (
                item
                for item in records
                if any(str(item.get(name) or "") == identifier for name in id_keys)
            ),
            None,
        )
        if record is None:
            msg_warning(
                self,
                "Provenance artefaktu",
                f"Evidence {kind} {identifier} nebyla v Run Bundle nalezena.",
            )
            return
        EvidenceDialog(
            f"Zdrojový {kind} · {identifier}", record, self
        ).exec()

    def show_source_request(self) -> None:
        self._show_evidence("request")

    def show_source_response(self) -> None:
        self._show_evidence("response")


__all__ = ["ResponseRequestPanel", "StepDetailDialog", "TechnicalViewer"]
