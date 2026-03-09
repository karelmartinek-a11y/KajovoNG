from __future__ import annotations

import time
import re
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QPlainTextEdit,
    QProgressBar,
    QGroupBox,
    QGridLayout,
    QListWidget,
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QTextCursor
from .widgets import style_progress_bar
from .theme import DARK_STYLESHEET

class ProgressDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("RUN")
        self.setModal(False)
        self.resize(720, 520)
        self.setStyleSheet(DARK_STYLESHEET)
        self._start = time.time()
        self._last_activity_ts = self._start
        self._mode = "RUN"
        self._a3_seen_paths = set()
        self._a3_stats = {
            "planned": 0,
            "done": 0,
            "errors": 0,
            "skip_images": 0,
            "skip_ext": 0,
            "skip_completed": 0,
            "parallel_active": 0,
            "parallel_max": 0,
        }

        v = QVBoxLayout(self)
        v.setContentsMargins(12, 12, 12, 12)
        v.setSpacing(10)

        self.lbl = QLabel("Running...")
        v.addWidget(self.lbl)

        self.lbl_health = QLabel("Stav: čekám na data...")
        v.addWidget(self.lbl_health)

        self.pb = QProgressBar()
        self.pb_sub = QProgressBar()
        style_progress_bar(self.pb)
        style_progress_bar(self.pb_sub)
        v.addWidget(self.pb)
        v.addWidget(self.pb_sub)

        stage_group = QGroupBox("Fáze běhu")
        sg = QGridLayout(stage_group)
        self.lbl_mode = QLabel("Mode: RUN")
        self.lbl_stage = QLabel("Aktuální krok: -")
        self.lbl_file = QLabel("Soubor: -")
        self.lbl_a3 = QLabel("A3: čekám")
        sg.addWidget(self.lbl_mode, 0, 0)
        sg.addWidget(self.lbl_stage, 0, 1)
        sg.addWidget(self.lbl_file, 1, 0, 1, 2)
        sg.addWidget(self.lbl_a3, 2, 0, 1, 2)
        v.addWidget(stage_group)

        a3_group = QGroupBox("A3 přehled")
        ag = QGridLayout(a3_group)
        self.lbl_a3_planned = QLabel("Plán: 0")
        self.lbl_a3_done = QLabel("Hotovo: 0")
        self.lbl_a3_skips = QLabel("Přeskočeno: img 0 / ext 0 / done 0")
        self.lbl_a3_parallel = QLabel("Paralelně: 0 aktivní (max 0)")
        self.lbl_a3_errors = QLabel("Chyby: 0")
        ag.addWidget(self.lbl_a3_planned, 0, 0)
        ag.addWidget(self.lbl_a3_done, 0, 1)
        ag.addWidget(self.lbl_a3_skips, 1, 0, 1, 2)
        ag.addWidget(self.lbl_a3_parallel, 2, 0)
        ag.addWidget(self.lbl_a3_errors, 2, 1)
        v.addWidget(a3_group)

        self.timeline = QListWidget()
        self.timeline.setMaximumHeight(110)
        v.addWidget(self.timeline)

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
        self._dot_timer = QTimer(self)
        self._dot_timer.setInterval(1000)
        self._dot_timer.timeout.connect(self._pulse_log_line)
        self._dot_timer.start()

    def set_mode(self, mode: str):
        self._mode = str(mode or "RUN")
        self.lbl_mode.setText(f"Mode: {self._mode}")

    def set_progress(self, p: int):
        self.pb.setValue(p)
        self.mark_activity()
        self._update_eta()

    def set_subprogress(self, p: int):
        self.pb_sub.setValue(p)
        self.mark_activity()

    def set_status(self, s: str):
        self.lbl.setText(s)
        self._parse_status(s)
        self.mark_activity()
        self._update_eta()

    def add_log(self, line: str):
        self.log.appendPlainText(line)
        self._parse_log_line(line)
        self._push_timeline(line)
        self.mark_activity()
        self._pulse_log_line(reset=True)

    def mark_activity(self):
        self._last_activity_ts = time.time()
        self._update_health(stalled=False)

    def set_stalled(self, stalled: bool):
        self._update_health(stalled=stalled)

    def _pulse_log_line(self, reset: bool = False):
        self._update_health(stalled=False)
        doc = self.log.document()
        if doc.blockCount() == 0:
            return
        if reset:
            return
        scroll = self.log.verticalScrollBar()
        at_bottom = scroll.value() >= scroll.maximum()
        block = doc.lastBlock()
        if not block.isValid():
            return
        text = block.text()
        cursor = QTextCursor(block)
        cursor.select(QTextCursor.BlockUnderCursor)
        cursor.insertText(text + ".")
        if at_bottom:
            scroll.setValue(scroll.maximum())

    def _update_eta(self):
        p = self.pb.value()
        if p <= 1:
            return
        elapsed = time.time() - self._start
        total = elapsed * (100.0 / p)
        eta = max(0.0, total - elapsed)
        self.setWindowTitle(f"RUN — ETA {int(eta)}s")

    def _update_health(self, stalled: bool):
        idle = int(time.time() - self._last_activity_ts)
        if stalled or idle > 5:
            self.lbl_health.setText(f"Stav: čekání na odpověď... poslední aktivita před {idle}s")
            self.lbl_health.setStyleSheet("color:#ffcc66;")
        else:
            self.lbl_health.setText(f"Stav: běží ({idle}s od poslední aktivity)")
            self.lbl_health.setStyleSheet("color:#7ce38b;")

    def _parse_status(self, s: str):
        txt = str(s or "")
        self.lbl_stage.setText(f"Aktuální krok: {txt or '-'}")
        m = re.search(r"(A\d|B\d|QA|QFILE|C)[: ]", txt)
        if m:
            phase = m.group(1)
            self.lbl_a3.setText(f"Fáze: {phase}")
        mf = re.search(r"A3: FILE\s+(.+?)\s+\((\d+)/(\d+)\)", txt)
        if mf:
            path, idx, total = mf.group(1), int(mf.group(2)), int(mf.group(3))
            self.lbl_file.setText(f"Soubor: {path}")
            self._a3_stats["planned"] = max(self._a3_stats["planned"], total)
            self._a3_stats["done"] = max(self._a3_stats["done"], max(0, idx - 1))
            self._refresh_a3_labels()

    def _parse_log_line(self, line: str):
        txt = str(line or "")
        if "A3 parallel workers:" in txt:
            m = re.search(r"A3 parallel workers:\s*(\d+)", txt)
            if m:
                self._a3_stats["parallel_max"] = int(m.group(1))
        elif "A3_FILE_START" in txt:
            path = txt.split("A3_FILE_START", 1)[1].strip()
            if path and path not in self._a3_seen_paths:
                self._a3_seen_paths.add(path)
            self._a3_stats["planned"] = max(self._a3_stats["planned"], len(self._a3_seen_paths))
            self._a3_stats["parallel_active"] += 1
            self.lbl_file.setText(f"Soubor: {path}")
        elif "A3_FILE_DONE" in txt:
            self._a3_stats["done"] += 1
            self._a3_stats["parallel_active"] = max(0, self._a3_stats["parallel_active"] - 1)
        elif "A3_FILE_ERROR" in txt:
            self._a3_stats["errors"] += 1
            self._a3_stats["parallel_active"] = max(0, self._a3_stats["parallel_active"] - 1)
        elif "A3: skipping generated image extension" in txt:
            self._a3_stats["skip_images"] += 1
        elif "A3: skipping due to extension" in txt:
            self._a3_stats["skip_ext"] += 1
        elif "A3: skipping already completed" in txt:
            self._a3_stats["skip_completed"] += 1
        self._refresh_a3_labels()

    def _refresh_a3_labels(self):
        planned = int(self._a3_stats["planned"])
        done = int(self._a3_stats["done"])
        self.lbl_a3_planned.setText(f"Plán: {planned}")
        self.lbl_a3_done.setText(f"Hotovo: {done}")
        self.lbl_a3_skips.setText(
            f"Přeskočeno: img {self._a3_stats['skip_images']} / ext {self._a3_stats['skip_ext']} / done {self._a3_stats['skip_completed']}"
        )
        self.lbl_a3_parallel.setText(
            f"Paralelně: {self._a3_stats['parallel_active']} aktivní (max {self._a3_stats['parallel_max']})"
        )
        self.lbl_a3_errors.setText(f"Chyby: {self._a3_stats['errors']}")
        pending = max(0, planned - done)
        self.lbl_a3.setText(f"A3: {done}/{planned} hotovo, čeká {pending}")

    def _push_timeline(self, line: str):
        if not line:
            return
        self.timeline.addItem(line)
        while self.timeline.count() > 8:
            self.timeline.takeItem(0)
