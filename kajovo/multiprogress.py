"""Živá mapa průběhu sestavená pouze z doložených zpráv pracovního procesu."""

from __future__ import annotations

from collections import OrderedDict
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QListWidget, QPlainTextEdit, QProgressBar, QScrollArea,
    QToolButton, QVBoxLayout, QWidget,
)

from kajovo.core.progress import TERMINAL_RUN_STATES
from kajovo.core.progress_display import stage_title


TERMINAL = TERMINAL_RUN_STATES - {"response_pending", "batch_pending"}
SUCCESS = {"completed", "closed"}
FAILURE = {"failed", "error", "corrupt_state", "expired"}
UNCERTAIN = {"submission_unknown", "unknown", "response_pending", "batch_pending"}

STATE_TEXT = {
    "active": "Práce probíhá", "preparing": "Připravuje se",
    "waiting": "Čekáme na odpověď služby", "validating_result": "Ověřuje se výsledek",
    "repairing": "Opravuje se podklad", "completed": "Hotovo",
    "closed": "Hotovo", "failed": "Nepodařilo se dokončit",
    "error": "Nepodařilo se dokončit", "cancelled": "Zastaveno",
    "stopped": "Zastaveno", "cancelling": "Čekáme na potvrzení zastavení",
    "submission_unknown": "Odeslání se nepodařilo potvrdit",
    "response_pending": "Služba ještě zpracovává odpověď",
    "batch_pending": "Hromadná úloha ještě běží",
    "partial": "Dokončeno jen částečně",
    "files_complete_unverified": "Soubory čekají na závěrečné ověření",
    "completed_unverified": "Výsledek čeká na ověření",
    "needs_clarification": "Je třeba upřesnit zadání",
    "waiting_manual_resource": "Čekáme na váš soubor",
    "dry_run": "Návrh byl připraven",
    "plan_ready": "Plán je připraven",
    "qfile_plan_ready": "Návrh čeká na potvrzení",
    "expired": "Vypršel čas služby",
    "unknown": "Stav se nepodařilo ověřit",
    "corrupt_state": "Stav se nepodařilo bezpečně načíst",
}


def step_name(stage: str) -> str:
    return stage_title(stage)


def step_states(events):
    """Dokončení kroku potvrzuje jen jeho vlastní poslední událost."""
    stages = OrderedDict()
    for event in events:
        if event.stage != "RUN":
            stages[event.stage] = event.state
    final = next((e.state for e in reversed(events) if e.stage == "RUN"), "")
    current = next((e.stage for e in reversed(events) if e.stage != "RUN"), "")
    result = []
    for stage, state in stages.items():
        if state == "completed":
            visual = "done"
        elif stage == current and final in FAILURE:
            visual = "error"
        elif stage == current and final in UNCERTAIN:
            visual = "blocked"
        elif final in TERMINAL:
            visual = "unconfirmed"
        else:
            visual = "current" if stage == current else "unconfirmed"
        result.append((stage, visual))
    return result


class ProgressRing(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(224, 224)
        self.done = 0
        self.total = 0
        self.caption = "Čekáme na zprávu"
        self.setAccessibleName("Průběh práce")

    def set_progress(self, done, total, caption):
        self.done, self.total, self.caption = done, total, caption
        self.setAccessibleDescription(f"{done} z {total} potvrzených kroků. {caption}" if total else caption)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self.rect().adjusted(15, 15, -15, -15)
        painter.setPen(QPen(QColor("#455570"), 13, Qt.SolidLine, Qt.RoundCap))
        painter.drawArc(rect, 0, 360 * 16)
        if self.total:
            painter.setPen(QPen(QColor("#75dcb6"), 13, Qt.SolidLine, Qt.RoundCap))
            painter.drawArc(rect, 90 * 16, -round(self.done / self.total * 360 * 16))
        painter.setPen(QColor("#f2f6fc"))
        font = painter.font()
        font.setPointSize(32)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(self.rect().adjusted(0, -22, 0, 4), Qt.AlignCenter,
                         f"{self.done} / {self.total}" if self.total else "…")
        font.setPointSize(11)
        font.setBold(False)
        painter.setFont(font)
        painter.drawText(self.rect().adjusted(0, 50, 0, -12), Qt.AlignCenter, "potvrzených kroků")


class MultiProgressView(QWidget):
    """Zobrazuje doložené kroky; počet neohlášených kroků není znám."""

    def __init__(self, title="Průběh práce", parent=None):
        super().__init__(parent)
        self.events = []
        self.setObjectName("MultiProgressView")
        shell = QVBoxLayout(self)
        shell.setContentsMargins(16, 16, 16, 16)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        shell.addWidget(self.scroll)
        surface = QWidget()
        surface.setObjectName("MultiProgressSurface")
        self.scroll.setWidget(surface)
        layout = QVBoxLayout(surface)
        layout.setSpacing(18)
        self.title_label = QLabel(title)
        self.title_label.setObjectName("PageTitle")
        self.title_label.setWordWrap(True)
        layout.addWidget(self.title_label)
        self.state_label = QLabel("Připravuje se")
        self.state_label.setObjectName("State")
        self.state_label.setWordWrap(True)
        layout.addWidget(self.state_label)
        top = QHBoxLayout()
        self.ring = ProgressRing()
        top.addWidget(self.ring, 0, Qt.AlignTop)
        current = QVBoxLayout()
        self.section_caption = QLabel("PRÁVĚ SE DĚJE")
        self.section_caption.setObjectName("Caption")
        current.addWidget(self.section_caption)
        self.phase_label = QLabel("Čekáme na první zprávu procesu")
        self.phase_label.setObjectName("Phase")
        self.phase_label.setWordWrap(True)
        current.addWidget(self.phase_label)
        self.activity_label = QLabel("Jakmile program potvrdí krok, zobrazí se zde.")
        self.activity_label.setWordWrap(True)
        current.addWidget(self.activity_label)
        self.provider_label = QLabel("")
        self.provider_label.setWordWrap(True)
        current.addWidget(self.provider_label)
        self.time_label = QLabel("")
        self.time_label.setObjectName("Muted")
        current.addWidget(self.time_label)
        current.addStretch()
        top.addLayout(current, 1)
        layout.addLayout(top)
        self.progress_note = QLabel("Zatím není potvrzen žádný krok.")
        self.progress_note.setObjectName("Muted")
        self.progress_note.setWordWrap(True)
        layout.addWidget(self.progress_note)
        self.unit_progress = QProgressBar()
        self.unit_progress.setAccessibleName("Potvrzený počet zpracovaných položek")
        self.unit_progress.hide()
        layout.addWidget(self.unit_progress)
        self.steps = QListWidget()
        self.steps.setObjectName("Steps")
        self.steps.setAccessibleName("Kroky potvrzené programem")
        self.steps.setMinimumHeight(140)
        layout.addWidget(self.steps)
        self.tech_toggle = QToolButton()
        self.tech_toggle.setText("Zobrazit technické podrobnosti")
        self.tech_toggle.setCheckable(True)
        layout.addWidget(self.tech_toggle)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(2000)
        self.log.setVisible(False)
        self.tech_toggle.toggled.connect(self.log.setVisible)
        layout.addWidget(self.log)
        self.setStyleSheet("""
            QWidget#MultiProgressView { background: #101a2a; color: #f2f6fc; font-size: 16px; }
            QWidget#MultiProgressSurface, QScrollArea, QScrollArea > QWidget > QWidget {
                background: #101a2a; color: #f2f6fc; }
            QLabel { color: #f2f6fc; background: transparent; }
            QLabel#PageTitle { font-size: 27px; font-weight: bold; }
            QLabel#Phase { font-size: 24px; font-weight: bold; }
            QLabel#State { color: #8ce6c3; font-size: 19px; font-weight: bold; }
            QLabel#Caption { color: #b5c6e3; font-size: 14px; font-weight: bold; }
            QLabel#Muted { color: #c6d3e4; }
            QListWidget#Steps, QPlainTextEdit { background: #1a2940; color: #f2f6fc;
                border: 1px solid #60758f; border-radius: 10px; padding: 12px; font-size: 16px; }
            QProgressBar { background: #1a2940; color: #f2f6fc; border: 1px solid #60758f;
                min-height: 25px; text-align: center; font-size: 15px; }
            QProgressBar::chunk { background: #78ddba; }
            QToolButton { background: transparent; border: 0; color: #c6d3e4;
                font-size: 16px; text-align: left; }
        """)

    def set_context(self, **values):
        # Metadata mohou být citlivá; zobrazí se pouze ve výslovně otevřených podrobnostech.
        self.context = values

    def set_title(self, title):
        self.title_label.setText(title)

    def append_log(self, message):
        if message:
            self.log.insertPlainText(f"{datetime.now():%H:%M:%S}  {message}\n")

    def on_event(self, event, clock=None):
        self.events.append(event)
        self.events[:] = self.events[-2000:]
        self.append_log(f"{event.stage} | {event.state} | {event.detail}")
        self.refresh(clock)

    def refresh(self, clock=None):
        if not self.events:
            return
        event = self.events[-1]
        state = event.state
        self.state_label.setText(STATE_TEXT.get(state, "Program zpracovává krok"))
        color = "#ffb3bc" if state in FAILURE else "#ffda93" if state in UNCERTAIN else "#8ce6c3"
        self.state_label.setStyleSheet(f"color: {color};")
        self.section_caption.setText(
            "VÝSLEDEK PRÁCE" if event.stage == "RUN" and state in TERMINAL_RUN_STATES
            else "PRÁVĚ SE DĚJE"
        )
        actual = next((e for e in reversed(self.events) if e.stage != "RUN"), None)
        if event.stage == "RUN" and state in TERMINAL_RUN_STATES:
            self.phase_label.setText(STATE_TEXT.get(state, "Práce skončila; stav ověřte v podrobnostech"))
        elif actual:
            self.phase_label.setText(step_name(actual.stage))
        if state == "submission_unknown":
            self.activity_label.setText("Odeslání se nepodařilo potvrdit. Úlohu zatím znovu neposíláme.")
        elif event.stage == "RUN" and state in TERMINAL_RUN_STATES:
            self.activity_label.setText("Práce na tomto počítači skončila. Stav jednotlivých kroků zůstává podle potvrzených zpráv.")
        elif state == "waiting":
            self.activity_label.setText("Čekáme na odpověď služby. Krok bude hotový až po potvrzení výsledku.")
        elif state in FAILURE:
            self.activity_label.setText("Tento krok se nepodařilo dokončit. Otevřete podrobnosti a požádejte o pomoc.")
        else:
            self.activity_label.setText("Program pracuje na uvedeném kroku. Dokončení potvrdí až po kontrole výsledku.")
        provider = event.provider_state if event.stage != "RUN" else ""
        self.provider_label.setText(
            "Stav vzdálené služby: " + STATE_TEXT.get(provider, "služba zpracovává požadavek")
            if provider else ""
        )
        states = step_states(self.events)
        done = sum(visual == "done" for _, visual in states)
        self.ring.set_progress(done, len(states), "Počet již ohlášených kroků")
        self.progress_note.setText(
            f"Potvrzeno {done} z {len(states)} dosud ohlášených kroků. "
            "Další kroky se zobrazí, jakmile je program ohlásí. Nejde o odhad času."
        )
        measured = next(
            (e for e in reversed(self.events) if e.stage != "RUN" and e.total is not None
             and e.total > 0 and e.completed is not None), None,
        )
        if measured:
            value = max(0, min(measured.completed, measured.total))
            self.unit_progress.setRange(0, measured.total)
            self.unit_progress.setValue(value)
            self.unit_progress.setFormat(f"{value} z {measured.total} {measured.unit}".strip())
            self.unit_progress.show()
            self.progress_note.setText(
                self.progress_note.text()
                + f" Poslední doložený počet v kroku „{step_name(measured.stage)}“: "
                  f"{value} z {measured.total} {measured.unit}.".rstrip()
            )
        else:
            self.unit_progress.hide()
        self.steps.clear()
        symbols = {"done": "✓", "current": "●", "error": "!", "blocked": "?", "unconfirmed": "○"}
        for stage, visual in states:
            self.steps.addItem(f"{symbols[visual]}  {step_name(stage)} — " + {
                "done": "Hotovo", "current": "Právě se děje", "error": "Chyba",
                "blocked": "Nelze potvrdit", "unconfirmed": "Dokončení nepotvrzeno",
            }[visual])
        if clock:
            _, age, _ = clock.times()
            self.time_label.setText(
                f"Poslední zpráva před {int(age)} s" if age < 30
                else f"Čekáme na další zprávu · poslední před {int(age)} s"
            )
