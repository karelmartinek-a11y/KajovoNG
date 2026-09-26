"""Kruhová mapa potvrzených kroků s odděleným technickým záznamem."""

import math
from collections import deque
from dataclasses import asdict
import json

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QBoxLayout, QFrame, QLabel, QListWidget, QListWidgetItem, QPlainTextEdit,
    QProgressBar, QScrollArea, QToolButton, QVBoxLayout, QWidget,
)

from kajovo.core.progress_model import BLOCKED, ERRORS, PROVIDER, STATES, ProgressModel, step_name


COLORS = {"done": "#a5efd7", "current": "#b6a5ff", "pending": "#8292ae",
          "error": "#ffadb7", "blocked": "#ffd17d", "unconfirmed": "#aebbd2", "skipped": "#8292ae", "not_run": "#aebbd2"}
ROW_TEXT = {"done": "Hotovo", "current": "Právě se děje", "pending": "Čeká",
            "error": "Chyba", "blocked": "Nelze potvrdit", "unconfirmed": "Dokončení nepotvrzeno",
            "skipped": "Přeskočeno", "not_run": "Provedení nepotvrzeno"}


def label(text="", role="", parent=None):
    widget = QLabel(text, parent)
    widget.setTextFormat(Qt.PlainText)
    widget.setWordWrap(True)
    widget.setProperty("role", role)
    return widget


class StepRing(QWidget):
    def __init__(self):
        super().__init__()
        self.rows = []
        self.done = self.total = 0
        self.caption = "Čekáme na zprávu"
        self.running = False
        self.setMinimumSize(270, 270)
        self.setAccessibleName("Kruhová mapa potvrzených kroků")

    def set_running(self, active, reduced_motion=False):
        self.running = bool(active)

    def display(self, rows, caption):
        self.rows = rows
        self.done = sum(state == "done" for _, state in rows)
        self.total = len(rows)
        self.caption = caption
        self.setAccessibleDescription(f"Hotovo {self.done} z {self.total} kroků. {caption}")
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        size = min(self.width(), self.height()) - 32
        center = QPointF(self.width() / 2, self.height() / 2)
        radius = size / 2
        painter.setPen(QPen(QColor("#52617c"), 3))
        painter.drawEllipse(center, radius, radius)
        for index, (_, state) in enumerate(self.rows):
            angle = index / max(1, self.total) * math.tau - math.pi / 2
            point = center + QPointF(math.cos(angle) * radius, math.sin(angle) * radius)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(COLORS[state]))
            dot = min(7, max(2, radius * math.pi / max(1, self.total) / 2))
            painter.drawEllipse(point, dot, dot)
            if state == "current":
                painter.setBrush(Qt.NoBrush)
                painter.setPen(QPen(QColor(COLORS[state]), 2))
                painter.drawEllipse(point, dot + 5, dot + 5)
        rect = QRectF(center.x() - radius * .70, center.y() - 62, radius * 1.4, 124)
        painter.setBrush(QColor("#2d3c58"))
        painter.setPen(QPen(QColor("#53627e"), 1))
        painter.drawRoundedRect(rect, 15, 15)
        painter.setPen(QColor("#b6a5ff"))
        painter.setFont(QFont(self.font().family(), 25, QFont.Bold))
        painter.drawText(rect.adjusted(4, 8, -4, -55), Qt.AlignCenter,
                         f"{self.done:02d} / {self.total:02d}" if self.total else "— / —")
        painter.setPen(QColor("#f4f5fb"))
        painter.setFont(QFont(self.font().family(), 11))
        painter.drawText(rect.adjusted(10, 62, -10, -8), Qt.AlignCenter | Qt.TextWordWrap,
                         "potvrzených kroků" if self.total else "Čekáme na plán práce")


class MultiProgressView(QWidget):
    def __init__(self, title="Průběh práce", parent=None):
        super().__init__(parent)
        self.model = ProgressModel()
        self.events = deque(maxlen=2000)
        self.context = {}
        self.setObjectName("circularMap")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        outer.addWidget(self.scroll)
        page = QWidget()
        page.setObjectName("progressPage")
        self.scroll.setWidget(page)
        body = QVBoxLayout(page)
        body.setContentsMargins(24, 20, 24, 20)
        body.setSpacing(16)
        body.addWidget(label("KÁJOVO NG   /   KRUHOVÁ MAPA", "eyebrow"))
        self.title_label = label(title, "title")
        body.addWidget(self.title_label)
        self.columns = QBoxLayout(QBoxLayout.LeftToRight)
        self.columns.setSpacing(24)
        self.ring = StepRing()
        self.columns.addWidget(self.ring, 2)
        card = QFrame()
        self.card = card
        card.setObjectName("currentCard")
        card_body = QVBoxLayout(card)
        card_body.setContentsMargins(22, 22, 22, 22)
        card_body.setSpacing(16)
        self.section_caption = label("PRÁVĚ SE DĚJE", "eyebrow")
        self.phase_label = label("Čekáme na první zprávu procesu", "phase")
        self.state_label = label("Připravuje se", "state")
        self.activity_label = label("Program zatím nepotvrdil žádný krok.")
        self.provider_label = label("", "muted")
        self.time_label = label("", "muted")
        self.next_label = label("Další krok zatím není znám.", "muted")
        for item in (self.section_caption, self.phase_label, self.state_label, self.activity_label,
                     self.provider_label, self.time_label, self.next_label):
            card_body.addWidget(item)
        card_body.addStretch()
        self.columns.addWidget(card, 3)
        body.addLayout(self.columns)
        self.progress_note = label("Zatím není potvrzen žádný krok.", "muted")
        body.addWidget(self.progress_note)
        self.unit_progress = QProgressBar()
        self.unit_progress.setAccessibleName("Poslední potvrzený počet zpracovaných položek")
        self.unit_progress.hide()
        body.addWidget(self.unit_progress)
        self.steps = QListWidget()
        self.steps.setWordWrap(True)
        self.steps.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.steps.setAccessibleName("Plán a potvrzené kroky")
        self.steps.setMinimumHeight(150)
        body.addWidget(self.steps)
        self.tech_toggle = QToolButton()
        self.tech_toggle.setCheckable(True)
        self.tech_toggle.setText("Zobrazit technické podrobnosti")
        self.tech_toggle.toggled.connect(self.toggle_details)
        body.addWidget(self.tech_toggle)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(2000)
        self.log.setMinimumHeight(150)
        self.log.hide()
        body.addWidget(self.log)
        self.setStyleSheet('''
            #circularMap, #progressPage, QScrollArea { background: #1e2b43; }
            QLabel { background: transparent; color: #f4f5fb; font-size: 16px; }
            QLabel[role="eyebrow"] { color: #b6a5ff; font-weight: bold; font-size: 15px; }
            QLabel[role="title"] { font-size: 25px; font-weight: bold; }
            QLabel[role="phase"] { font-size: 23px; font-weight: bold; }
            QLabel[role="muted"] { color: #c3cee2; font-size: 14px; }
            QLabel[role="state"] { color: #a5efd7; font-weight: bold; }
            #currentCard { background: #2d3c58; border: 1px solid #53627e; border-radius: 16px; }
            QListWidget, QPlainTextEdit { color: #f4f5fb; background: #26354f;
                border: 1px solid #53627e; border-radius: 9px; padding: 8px; font-size: 15px; }
            QListWidget::item { padding: 6px; }
            QToolButton { color: #c9bdff; background: transparent; border: 0; padding: 6px; font-size: 15px; }
            QProgressBar { color: #f4f5fb; background: #384863; border: 0;
                min-height: 26px; text-align: center; }
            QProgressBar::chunk { background: #426f68; }
        ''')

    def resizeEvent(self, event):
        narrow = self.width() < 760
        if getattr(self, "_narrow", None) != narrow:
            self._narrow = narrow
            self.columns.removeWidget(self.ring)
            self.columns.removeWidget(self.card)
            self.columns.setDirection(QBoxLayout.TopToBottom if narrow else QBoxLayout.LeftToRight)
            self.columns.addWidget(self.card if narrow else self.ring, 3 if narrow else 2)
            self.columns.addWidget(self.ring if narrow else self.card, 2 if narrow else 3)
        self.ring.setMaximumHeight(290 if narrow else 360)
        super().resizeEvent(event)

    def toggle_details(self, checked):
        self.log.setVisible(checked)
        self.tech_toggle.setText("Skrýt technické podrobnosti" if checked else "Zobrazit technické podrobnosti")

    def set_context(self, **values):
        self.context = values
        self.append_log(json.dumps(values, ensure_ascii=False))

    def set_title(self, title):
        self.title_label.setText(title)

    def append_log(self, message):
        if message:
            self.log.appendPlainText(str(message))

    def on_event(self, event, clock=None):
        self.events.append(event)
        self.model.update(event)
        self.append_log(json.dumps(asdict(event), ensure_ascii=False))
        self.refresh(clock)

    def reset(self, title):
        self.model = ProgressModel()
        self.events.clear()
        self.context.clear()
        self.log.clear()
        self.steps.clear()
        self._rows = None
        self.ring.display([], "Čekáme na zprávu")
        self.set_title(title)
        self.phase_label.setText("Čekáme na první zprávu procesu")
        self.state_label.setText("Připravuje se")
        self.state_label.show()
        self.phase_label.setStyleSheet("")
        self.activity_label.setText("Program zatím nepotvrdil žádný krok.")
        self.provider_label.clear()
        self.time_label.clear()
        self.next_label.setText("Další krok zatím není znám.")
        self.progress_note.setText("Zatím není potvrzen žádný krok.")
        self.section_caption.setText("PRÁVĚ SE DĚJE")
        self.unit_progress.hide()
        self.tech_toggle.setChecked(False)

    def refresh(self, clock=None):
        model = self.model
        event = model.last
        rows = model.rows()
        self.ring.display(rows, "Potvrzené kroky")
        if event is None:
            self.render_rows(rows)
            return
        state = model.terminal or event.state
        text = STATES.get(state, "Stav tohoto kroku zatím není potvrzen")
        self.state_label.setText(text)
        attention = BLOCKED | {"partial", "completed_unverified", "files_complete_unverified",
                               "cancelling", "unfinished_record", "qfile_plan_ready", "ready_to_import"}
        color = (COLORS["error"] if state in ERRORS else COLORS["blocked"] if state in attention
                 else COLORS["done"] if state in {"completed", "closed", "dry_run", "plan_ready"}
                 else COLORS["unconfirmed"] if state in {"cancelled", "stopped"} else COLORS["current"])
        self.state_label.setStyleSheet(f"color: {color}")
        self.state_label.setVisible(not bool(model.terminal))
        self.phase_label.setStyleSheet(f"color: {color}" if model.terminal else "")
        self.section_caption.setText("VÝSLEDEK PRÁCE" if model.received else "PRÁVĚ SE DĚJE")
        self.phase_label.setText(text if model.terminal else step_name(model.current) if model.current else "Příprava práce")
        if state == "submission_unknown":
            sentence = "Odeslání se nepodařilo potvrdit. Úlohu zatím znovu neposíláme. Nejprve je nutné dohledat původní požadavek."
        elif state in {"response_pending", "batch_pending"}:
            sentence = "Vzdálená práce zatím nemá potvrzený místní výsledek. Stav lze znovu ověřit; dokončení se nepředpokládá."
        elif state in ERRORS:
            sentence = "Tento krok se nepodařilo dokončit. Otevřete podrobnosti chyby a postupujte podle doporučeného řešení."
        elif state == "cancelling":
            sentence = "Žádost o zastavení byla předána. Čekáme, až pracovní proces potvrdí bezpečné ukončení."
        elif state in {"files_complete_unverified", "completed_unverified"}:
            sentence = "Soubory jsou připravené, jejich funkčnost však není potvrzena. Před použitím je zkontrolujte."
        elif state == "needs_clarification":
            sentence = "Otevřete výsledek, doplňte požadované údaje a poté spusťte navazující práci."
        elif state == "waiting_manual_resource":
            sentence = "Dodejte požadovaný soubor prostřednictvím historie běhu. Práce bez něj nemůže pokračovat."
        elif state == "partial":
            sentence = "Část práce není dokončena. Před dalším postupem zkontrolujte výsledek a jednotlivé kroky."
        elif model.terminal:
            sentence = ("Pracovní proces skončil. Jednotlivé kroky zachovávají svůj potvrzený stav."
                        if model.received else "Proces ohlásil výsledek. Čekáme na ukončení pracovníka a jeho převzetí.")
        elif state == "completed":
            sentence = "Tento krok je potvrzený. Celá operace ještě nemá potvrzený konec."
        elif state == "waiting":
            sentence = "Čekáme na odpověď služby. Krok bude hotový až po kontrole výsledku."
        else:
            sentence = "Program provádí uvedený krok. Dokončení oznámí po provedení a kontrole této části práce."
        self.activity_label.setText(sentence)
        self.provider_label.setText("Stav vzdálené služby: " + PROVIDER.get(event.provider_state, "neznámý stav; viz technické podrobnosti")
                                    if event.provider_state else "")
        done = self.ring.done
        pending = sum(s == "pending" for _, s in rows)
        unconfirmed = sum(s in {"unconfirmed", "not_run", "blocked", "error"} for _, s in rows)
        current = sum(s == "current" for _, s in rows)
        note = f"Hotovo {done} · Právě se děje {current} · Čeká {pending} · Nepotvrzeno {unconfirmed}. "
        skipped = sum(s == "skipped" for _, s in rows)
        if skipped:
            note += f"Přeskočeno {skipped}. "
        note += "Plán se může rozšířit podle výsledků. " if model.declared else "Zobrazeny pouze dosud ohlášené kroky. "
        note += "Kruh nevyjadřuje zbývající čas."
        measurement = model.measurement
        if measurement is not None:
            completed, total = measurement.completed, measurement.total
            if 0 <= completed <= total and total > 0:
                self.unit_progress.setRange(0, total)
                self.unit_progress.setValue(completed)
                self.unit_progress.setFormat(f"{completed} z {total} {measurement.unit}")
                self.unit_progress.show()
                note += f" Poslední doložený počet: {step_name(measurement.stage)}."
            else:
                self.unit_progress.hide()
                note += f" Potvrzený údaj procesu: {completed} z {total} {measurement.unit}."
        self.progress_note.setText(note)
        following = event.next_step
        if not following:
            following = next((step_name(key) for key, s in rows if s == "pending"), "")
        self.next_label.setText("Další plánovaný krok: " + following if following and not model.terminal
                                else "Další postup vychází z výsledku operace." if model.terminal
                                else "Další krok zatím není znám.")
        if clock:
            elapsed, age, eta = clock.times()
            if model.received:
                self.time_label.setText(f"Doba místního zpracování: {int(elapsed)} s\nMístní pracovní proces skončil.")
            else:
                self.time_label.setText(f"Doba práce: {int(elapsed)} s · Poslední zpráva před {int(age)} s\n"
                                       + (f"Odhad zbývajícího času aktuálního kroku: {int(eta)} s" if eta is not None
                                          else "Odhad zbývajícího času: zatím nelze určit"))
        self.render_rows(rows)

    def render_rows(self, rows):
        signature = tuple(rows)
        if getattr(self, "_rows", None) == signature:
            return
        self._rows = signature
        position = self.steps.verticalScrollBar().value()
        self.steps.clear()
        for index, (key, state) in enumerate(rows, 1):
            item = QListWidgetItem(f"{index:02d}   {step_name(key)} — {ROW_TEXT[state]}")
            item.setForeground(QColor(COLORS[state]))
            self.steps.addItem(item)
        self.steps.verticalScrollBar().setValue(position)
