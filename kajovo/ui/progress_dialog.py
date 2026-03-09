from __future__ import annotations

import re
import time
from typing import Dict, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from .theme import DARK_STYLESHEET
from .widgets import style_progress_bar


class ProgressDialog(QDialog):
    _A3_FILE_RE = re.compile(r"A3:\\s*FILE\\s+(.+?)\\s+\\((\\d+)\\s*/\\s*(\\d+)\\)", re.IGNORECASE)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("RUN")
        self.setModal(False)
        self.resize(760, 620)
        self.setStyleSheet(DARK_STYLESHEET)
        self._start = time.time()
        self._steps: Dict[str, QLabel] = {}
        self._active_step: Optional[str] = None

        v = QVBoxLayout(self)
        v.setContentsMargins(12, 12, 12, 12)
        v.setSpacing(10)

        self.lbl = QLabel("Running...")
        self.lbl.setWordWrap(True)
        v.addWidget(self.lbl)

        self.lbl_file = QLabel("A3 files: waiting for file list")
        self.lbl_file.setTextInteractionFlags(Qt.TextSelectableByMouse)
        v.addWidget(self.lbl_file)

        self._build_steps(v)

        self.pb = QProgressBar()
        self.pb_sub = QProgressBar()
        style_progress_bar(self.pb)
        style_progress_bar(self.pb_sub)
        self.pb.setFormat("Overall: %p%")
        self.pb_sub.setFormat("Current step: %p%")
        v.addWidget(self.pb)
        v.addWidget(self.pb_sub)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        v.addWidget(self.log, 1)

        row = QHBoxLayout()
        self.btn_stop = QPushButton("STOP")
        self.chk_bzz = QCheckBox("BZZonEND")
        self.btn_close = QPushButton("Hide")
        row.addWidget(self.btn_stop)
        row.addWidget(self.chk_bzz)
        row.addStretch(1)
        row.addWidget(self.btn_close)
        v.addLayout(row)

        self.btn_close.clicked.connect(self.hide)

    def _build_steps(self, layout: QVBoxLayout) -> None:
        title = QLabel("Sequence")
        title.setStyleSheet("font-weight: 600;")
        layout.addWidget(title)

        for key, text in [
            ("prep", "1) Preparation / diagnostics"),
            ("a1", "2) A1 plan"),
            ("a2", "3) A2 structure"),
            ("a3", "4) A3 file generation"),
            ("save", "5) Save output files"),
            ("done", "6) Done"),
        ]:
            lbl = QLabel(f"[ ] {text}")
            lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self._steps[key] = lbl
            layout.addWidget(lbl)

    def set_progress(self, p: int):
        self.pb.setValue(p)
        if p >= 80 and self._active_step == "a3":
            self._set_step("a3", "done")
            self._set_step("save", "active")
        if p >= 100:
            self._set_step("save", "done")
            self._set_step("done", "done")
        self._update_eta()

    def set_subprogress(self, p: int):
        self.pb_sub.setValue(p)

    def set_status(self, s: str):
        self.lbl.setText(s)
        self._apply_status_to_steps(s)
        self._apply_file_progress_from_text(s)
        self._update_eta()

    def add_log(self, line: str):
        normalized = self._normalize_log_line(line)
        if normalized:
            self.log.appendPlainText(normalized)
        self._apply_status_to_steps(line)
        self._apply_file_progress_from_text(line)

    def _normalize_log_line(self, line: str) -> str:
        txt = str(line or "").strip()
        if not txt:
            return ""

        m = re.match(r"^(\d{8}\s\d{6})\s*\|\s*(.*)$", txt)
        if m:
            raw_ts, body = m.group(1), m.group(2).strip()
            ts = f"{raw_ts[9:11]}:{raw_ts[11:13]}:{raw_ts[13:15]}"
        else:
            body = txt
            ts = time.strftime("%H:%M:%S")

        stage = self._extract_stage(body)
        return f"{ts} | {stage} | {body}"

    def _extract_stage(self, body: str) -> str:
        head = (body.split("|", 1)[0] or "").strip()
        if ":" in head:
            return (head.split(":", 1)[0] or "RUN").strip().upper()

        up = head.upper()
        for token in ("A1", "A2", "A3", "B1", "B2", "B3", "QA", "QFILE", "C", "VERSING"):
            if up.startswith(token):
                return token
        return "RUN"

    def _set_step(self, key: str, state: str) -> None:
        lbl = self._steps.get(key)
        if lbl is None:
            return

        text = lbl.text()
        base = text[text.find("]") + 2 :] if "]" in text else text

        marker = "[ ]"
        if state == "active":
            marker = "[>]"
            self._active_step = key
        elif state == "done":
            marker = "[x]"
            if self._active_step == key:
                self._active_step = None
        elif state == "skip":
            marker = "[-]"

        lbl.setText(f"{marker} {base}")

    def _apply_status_to_steps(self, text: str) -> None:
        t = (text or "").lower()

        if "rerun: using existing a2 structure" in t:
            self._set_step("prep", "done")
            self._set_step("a1", "skip")
            self._set_step("a2", "skip")
            self._set_step("a3", "active")
            return

        if "a1:" in t:
            self._set_step("prep", "done")
            self._set_step("a1", "active")
            return

        if "a2:" in t:
            self._set_step("prep", "done")
            self._set_step("a1", "done")
            self._set_step("a2", "active")
            return

        if "a3:" in t:
            self._set_step("prep", "done")
            self._set_step("a1", "done")
            self._set_step("a2", "done")
            self._set_step("a3", "active")
            return

        if "versing:" in t or "snapshot before write" in t:
            self._set_step("a3", "done")
            self._set_step("save", "active")
            return

        if "run completed" in t:
            self._set_step("prep", "done")
            self._set_step("a1", "done")
            self._set_step("a2", "done")
            self._set_step("a3", "done")
            self._set_step("save", "done")
            self._set_step("done", "done")

    def _apply_file_progress_from_text(self, text: str) -> None:
        m = self._A3_FILE_RE.search(str(text or ""))
        if not m:
            return

        path, idx, total = m.group(1).strip(), m.group(2), m.group(3)
        self.lbl_file.setText(f"A3 files: {idx}/{total} | {path}")

    def _update_eta(self):
        p = self.pb.value()
        if p <= 1:
            return
        elapsed = time.time() - self._start
        total = elapsed * (100.0 / p)
        eta = max(0.0, total - elapsed)
        self.setWindowTitle(f"RUN | ETA {int(eta)}s")
