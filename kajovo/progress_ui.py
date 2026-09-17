"""Společný živý procesní inspektor pro progress dialogy KájovoNG."""

from __future__ import annotations

from datetime import datetime
from typing import Iterable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .core.progress_display import source_title, stage_title, state_title


COLORS = {
    "canvas": "#0B1220",
    "surface": "#131F30",
    "raised": "#1B2C41",
    "text": "#F3F7FC",
    "muted": "#B8C7D9",
    "primary": "#5EEAD4",
    "focus": "#7DBBFF",
    "border": "#465B75",
    "success": "#79E2B0",
    "warning": "#FFD080",
    "danger": "#FF9DAB",
    "log": "#0F1928",
}

DIALOG_STYLE = f"""
QDialog {{ background: {COLORS['canvas']}; color: {COLORS['text']}; }}
QCheckBox {{ color: {COLORS['muted']}; padding: 4px 0; }}
QPushButton {{ background: {COLORS['raised']}; color: {COLORS['text']}; border: 1px solid {COLORS['border']}; border-radius: 8px; padding: 8px 13px; min-height: 20px; }}
QPushButton:hover {{ background: #28405B; }}
QPushButton#Primary {{ background: {COLORS['primary']}; color: {COLORS['canvas']}; border-color: {COLORS['primary']}; font-weight: 700; }}
QPushButton#Danger {{ color: {COLORS['danger']}; border-color: {COLORS['danger']}; font-weight: 700; }}
QPushButton:disabled {{ color: #718198; border-color: #33465E; background: #152235; }}
"""

STATE_META = {
    "created": ("○", "Vytvořeno", "muted"),
    "preparing": ("◌", "Připravuje se", "focus"),
    "running": ("▶", "Běží", "focus"),
    "active": ("▶", "Probíhá", "focus"),
    "waiting": ("◷", "Čeká na odpověď služby", "focus"),
    "validating_result": ("◌", "Ověřuje výsledek", "focus"),
    "repairing": ("↻", "Opravuje podklad", "warning"),
    "response_pending": ("◷", "Čeká na odpověď", "focus"),
    "batch_prepared": ("⚑", "BATCH připraven", "focus"),
    "batch_pending": ("☁", "BATCH běží", "focus"),
    "importing": ("⇩", "Přebírá se", "focus"),
    "ready_to_import": ("⇩", "K převzetí", "warning"),
    "completed": ("✓", "Dokončeno", "success"),
    "closed": ("✓", "Dokončeno / uzavřeno", "success"),
    "dry_run": ("◇", "Dry-run / návrh bez zápisu", "focus"),
    "partial": ("⚠", "Částečně dokončeno", "warning"),
    "files_complete_unverified": ("?", "Soubory převzaty, funkčnost neověřena", "warning"),
    "unfinished_record": ("?", "Konec fáze nezapsán", "warning"),
    "cancelled": ("×", "Zrušeno", "muted"),
    "stopped": ("×", "Zastaveno", "muted"),
    "cancelling": ("×", "Ruší se", "warning"),
    "failed": ("!", "Chyba", "danger"),
    "error": ("!", "Chyba", "danger"),
    "submission_unknown": ("?", "Neznámý výsledek odeslání", "warning"),
    "corrupt_state": ("!", "Chyba evidence", "danger"),
    "unknown": ("?", "Neznámý stav", "warning"),
    "expired": ("!", "Vypršel čas služby", "danger"),
    "not_started": ("○", "Ještě nezačalo", "muted"),
    "blocked": ("▣", "Blokováno", "warning"),
    "skipped": ("»", "Přeskočeno", "muted"),
    "queued": ("○", "Queued", "muted"),
    "validating": ("◌", "Validating", "focus"),
    "in_progress": ("▶", "In progress", "focus"),
    "finalizing": ("◌", "Finalizing", "focus"),
    "pending": ("○", "Pending", "muted"),
    "submitted": ("☁", "Submitted", "focus"),
    "downloaded": ("⇩", "Downloaded", "success"),
}

PROCESS_PRESETS = {
    "GENERATE": ("Lokální validace", "Přílohy / vstupní data", "A0", "A0R", "A1", "A2", "A3", "Validace kontraktů", "Ukládání"),
    "MODIFY": ("Lokální validace", "kontrola IN", "B0R", "B1", "B2", "B3", "Validace kontraktů", "Ukládání"),
    "QA": ("Validace zadání", "Příprava příloh", "Sestavení API požadavku", "Odeslání", "Čekání", "Převzetí odpovědi", "Parsování / validace", "Dokončeno"),
    "QFILE": ("Validace", "Příprava zadání", "API", "Čekání", "Přijetí obsahu", "Sestavení souboru", "Uložení", "Validace souboru", "Dokončeno"),
    "KASKÁDA": ("Analýza vstupu", "Návrh dat", "Generování", "Kontrola", "Export"),
    "BATCH": ("Příprava dávky", "Odeslání", "Remote zpracování", "Převzetí", "Validace", "Uložení"),
    "FOTOGRAFIE": ("Kontrola fotografií", "Upload vstupů", "Image Edit requesty", "Upload JSONL", "Odeslání BATCH", "Remote zpracování", "Download výsledků", "Dekódování", "Uložení fotografií", "Kontrola souborů"),
    "COMIC": ("Bible / descriptor", "Reference", "Panely", "BATCH", "Převzetí", "Postprocessing", "Uložení"),
    "ZDROJE": ("Kontrola souboru", "Upload", "Potvrzení file_id", "Indexace", "Čekání", "Připraveno"),
    "OBNOVA": ("Ověření zdrojového Run Bundle", "Ověření checkpointu", "Rekonstrukce konfigurace", "Převzetí artefaktů", "Aplikace pokynu", "Nová placená část"),
    "LOKÁLNÍ": ("Skenuji vstupy", "Vytvářím zálohu", "Převádím", "Ověřuji", "Ukládám", "Hotovo"),
    "SERVIS": ("Příprava", "Síťový požadavek", "Čekání", "Výsledek"),
}

STAGE_ALIASES = {
    "Lokální validace": "Lokální validace",
    "Přílohy": "Přílohy / vstupní data",
    "Vstupní data": "Přílohy / vstupní data",
    "A0": "A0",
    "A0R": "A0R",
    "A1": "A1",
    "A2": "A2",
    "A2Q": "A2Q",
    "A3": "A3",
    "B0R": "B0R",
    "B1": "B1",
    "B2": "B2",
    "B2Q": "B2Q",
    "B3": "B3",
    "Validace kontraktů": "Validace kontraktů",
    "Ukládání": "Ukládání",
    "Ukládání souborů": "Ukládání",
    "BATCH": "Remote zpracování",
    "Čekání na dávku": "Remote zpracování",
    "Upload": "Upload",
    "Download": "Převzetí",
    "Indexace": "Indexace",
}

PROVIDER_STATES = {"queued", "validating", "in_progress", "finalizing", "completed", "failed", "cancelling", "cancelled", "expired"}
TERMINAL_STATES = {"completed", "closed", "dry_run", "partial", "files_complete_unverified", "unfinished_record", "cancelled", "stopped", "failed", "error", "submission_unknown", "corrupt_state", "unknown", "expired", "response_pending", "batch_pending"}


def infer_kind(title: str = "", events: Iterable = ()) -> str:
    stages = [str(getattr(event, "stage", "")) for event in events]
    upper = " ".join([title, *stages]).upper()
    if any(stage.startswith("A") and stage[:2] in {"A0", "A1", "A2", "A3"} for stage in stages):
        return "GENERATE"
    if any(stage.startswith("B") and stage[:2] in {"B0", "B1", "B2", "B3"} for stage in stages):
        return "MODIFY"
    if "COMIC" in upper or "KOMIK" in upper:
        return "COMIC"
    if "KASK" in upper:
        return "KASKÁDA"
    if "FOTO" in upper or "IMAGE" in upper:
        return "FOTOGRAFIE"
    if "QFILE" in upper:
        return "QFILE"
    if "BATCH" in upper or "DÁVK" in upper or "DAVK" in upper:
        return "BATCH"
    if "VECTOR" in upper or "ZDROJ" in upper or "UPLOAD" in upper or "NAHRÁV" in upper:
        return "ZDROJE"
    if any(word in upper for word in ("CONTINUE", "RERUN", "REPAIR", "OBNOV", "CHECKPOINT")):
        return "OBNOVA"
    if any(word in upper for word in ("SMTP", "KATALOG MODEL", "SERVIS")):
        return "SERVIS"
    if any(word in upper for word in ("PŘEVOD", "PREVOD", "LOKÁLN", "GIT", "INDEX")):
        return "LOKÁLNÍ"
    if "QA" in upper or "DOTAZ" in upper or "ODPOVĚ" in upper:
        return "QA"
    return "SERVIS"


def _tone_color(tone: str) -> str:
    return COLORS.get(tone, COLORS["muted"])


def _state_meta(state: str):
    return STATE_META.get(state, ("?", state_title(state), "muted"))


def _event_code(event) -> str:
    source = getattr(event, "source", "local")
    state = getattr(event, "state", "active")
    detail = (getattr(event, "detail", "") or "").lower()
    if state in {"failed", "error"}:
        return "ERROR"
    if state in {"repairing"}:
        return "REPAIR"
    if state in {"cancelling", "cancelled"}:
        return "WARN"
    if source == "upload":
        return "UPLOAD →"
    if source == "download":
        return "DOWNLOAD ←"
    if source == "disk":
        return "DISK"
    if source == "validation" or state in {"validating", "validating_result"}:
        return "VALIDATE"
    if source in {"api", "files_api", "batch_api"}:
        if state in {"waiting", "response_pending", "batch_pending", "in_progress", "queued", "finalizing"}:
            return "WAIT"
        if "odesíl" in detail or "send" in detail or "submit" in detail:
            return "API →"
        if state == "completed" or "přijat" in detail or "obdrž" in detail or "receive" in detail:
            return "API ←"
        return "API ✓"
    if state == "completed":
        return "DONE"
    return "LOCAL"


class FlowStrip(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(5)

    def set_steps(self, steps):
        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        for index, (title, state) in enumerate(steps):
            symbol = {"done": "✓", "current": "▶", "pending": "○", "error": "!", "blocked": "▣", "skipped": "»"}.get(state, "○")
            tone = {"done": "success", "current": "focus", "error": "danger", "blocked": "warning"}.get(state, "muted")
            chip = QLabel(f"{symbol} {title}")
            chip.setWordWrap(False)
            chip.setStyleSheet(
                f"QLabel {{ color: {_tone_color(tone)}; background: {COLORS['raised']}; "
                f"border: 1px solid {COLORS['border']}; border-radius: 7px; padding: 5px 7px; font-weight: 600; }}"
            )
            self._layout.addWidget(chip)
            if index < len(steps) - 1:
                arrow = QLabel("→")
                arrow.setStyleSheet(f"color: {COLORS['border']}; font-weight: 700;")
                self._layout.addWidget(arrow)
        self._layout.addStretch(1)


class ProcessInspector(QWidget):
    def __init__(self, title="Průběh operace", kind="", parent=None):
        super().__init__(parent)
        self.events = []
        self.kind = kind or infer_kind(title)
        self.context = {}
        self._last_wall_time = ""
        self._build(title)
        self._apply_style()
        self.refresh()

    def _build(self, title):
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 14)
        root.setSpacing(10)

        header = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(2)
        self.title_label = QLabel(title)
        self.title_label.setObjectName("ProcessTitle")
        self.meta_label = QLabel("")
        self.meta_label.setObjectName("Muted")
        titles.addWidget(self.title_label)
        titles.addWidget(self.meta_label)
        header.addLayout(titles, 1)
        self.badge = QLabel("▶ PROBÍHÁ")
        self.badge.setObjectName("StatusBadge")
        header.addWidget(self.badge, 0, Qt.AlignTop)
        root.addLayout(header)

        self.flow = FlowStrip()
        root.addWidget(self.flow)

        current = QFrame()
        current.setObjectName("ProcessCard")
        current_layout = QVBoxLayout(current)
        current_layout.setContentsMargins(14, 12, 14, 12)
        current_layout.setSpacing(7)
        section = QLabel("AKTUÁLNÍ FÁZE")
        section.setObjectName("SectionCaption")
        self.phase_label = QLabel("Příprava")
        self.phase_label.setObjectName("PhaseTitle")
        self.activity_label = QLabel("Připravuji operaci…")
        self.activity_label.setObjectName("ActivityTitle")
        self.activity_label.setWordWrap(True)
        current_layout.addWidget(section)
        current_layout.addWidget(self.phase_label)
        current_layout.addWidget(self.activity_label)

        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(4)
        self.source_label = QLabel("Lokální zpracování")
        self.stage_state_label = QLabel("Probíhá")
        self.micro_label = QLabel("Příprava")
        self.provider_label = QLabel("—")
        for row, (caption, widget) in enumerate((
            ("Zdroj", self.source_label),
            ("Stav fáze", self.stage_state_label),
            ("Mikrooperace", self.micro_label),
            ("Vzdálená služba", self.provider_label),
        )):
            key = QLabel(caption + ":")
            key.setObjectName("Muted")
            widget.setObjectName("StrongValue")
            grid.addWidget(key, row, 0)
            grid.addWidget(widget, row, 1)
        current_layout.addLayout(grid)

        self.micro_flow = FlowStrip()
        current_layout.addWidget(self.micro_flow)
        self.run_progress = QProgressBar()
        self.run_progress.setRange(0, 0)
        self.run_progress.setTextVisible(False)
        self.unit_progress = QProgressBar()
        self.unit_progress.hide()
        self.progress_note = QLabel("Služba neposkytuje měřitelný postup.")
        self.progress_note.setObjectName("Muted")
        current_layout.addWidget(self.run_progress)
        current_layout.addWidget(self.unit_progress)
        current_layout.addWidget(self.progress_note)
        self.time_label = QLabel("")
        self.time_label.setObjectName("Muted")
        current_layout.addWidget(self.time_label)
        root.addWidget(current)

        self.parallel = QFrame()
        self.parallel.setObjectName("ProcessCard")
        parallel_layout = QVBoxLayout(self.parallel)
        parallel_layout.setContentsMargins(14, 10, 14, 10)
        parallel_layout.addWidget(QLabel("VZDÁLENÁ SLUŽBA / LOKÁLNÍ APLIKACE"))
        self.provider_flow_label = QLabel("OpenAI: —")
        self.provider_flow_label.setWordWrap(True)
        self.local_flow_label = QLabel("KájovoNG: —")
        self.local_flow_label.setWordWrap(True)
        parallel_layout.addWidget(self.provider_flow_label)
        parallel_layout.addWidget(self.local_flow_label)
        self.parallel.hide()
        root.addWidget(self.parallel)

        next_card = QFrame()
        next_card.setObjectName("ProcessCard")
        next_layout = QHBoxLayout(next_card)
        next_layout.setContentsMargins(14, 8, 14, 8)
        next_caption = QLabel("DALŠÍ KROK")
        next_caption.setObjectName("SectionCaption")
        self.next_label = QLabel("Bude určen po zahájení běhu")
        self.next_label.setObjectName("StrongValue")
        next_layout.addWidget(next_caption)
        next_layout.addWidget(self.next_label, 1)
        root.addWidget(next_card)

        self.tech_toggle = QToolButton()
        self.tech_toggle.setText("▾ Technický průběh")
        self.tech_toggle.setCheckable(True)
        self.tech_toggle.setChecked(True)
        self.tech_toggle.setToolButtonStyle(Qt.ToolButtonTextOnly)
        root.addWidget(self.tech_toggle)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(2000)
        self.log.setMinimumHeight(115)
        self.log.setMaximumHeight(175)
        self.tech_toggle.toggled.connect(self.log.setVisible)
        root.addWidget(self.log, 1)

    def _apply_style(self):
        self.setObjectName("ProcessInspector")
        self.setStyleSheet(f"""
        QWidget#ProcessInspector {{ background: {COLORS['canvas']}; color: {COLORS['text']}; font-family: Montserrat; font-size: 13px; }}
        QLabel {{ color: {COLORS['text']}; background: transparent; }}
        QLabel#ProcessTitle {{ font-size: 24px; font-weight: 700; }}
        QLabel#PhaseTitle {{ font-size: 17px; font-weight: 700; }}
        QLabel#ActivityTitle {{ font-size: 16px; font-weight: 650; color: {COLORS['text']}; }}
        QLabel#Muted {{ color: {COLORS['muted']}; }}
        QLabel#StrongValue {{ color: {COLORS['text']}; font-weight: 600; }}
        QLabel#SectionCaption {{ color: {COLORS['primary']}; font-size: 10px; font-weight: 700; letter-spacing: 1px; }}
        QLabel#StatusBadge {{ background: {COLORS['raised']}; border: 1px solid {COLORS['border']}; border-radius: 9px; padding: 6px 10px; font-weight: 700; }}
        QFrame#ProcessCard {{ background: {COLORS['surface']}; border: 1px solid {COLORS['border']}; border-radius: 12px; }}
        QProgressBar {{ background: {COLORS['log']}; color: {COLORS['text']}; border: 1px solid {COLORS['border']}; border-radius: 5px; min-height: 17px; text-align: center; }}
        QProgressBar::chunk {{ background: {COLORS['primary']}; border-radius: 4px; }}
        QPlainTextEdit {{ background: {COLORS['log']}; color: {COLORS['muted']}; border: 1px solid {COLORS['border']}; border-radius: 8px; padding: 8px; font-family: Montserrat; }}
        QToolButton {{ color: {COLORS['muted']}; background: transparent; border: 0; padding: 3px 0; font-weight: 650; text-align: left; }}
        """)

    def set_context(self, **values):
        self.context.update({key: value for key, value in values.items() if value not in (None, "")})
        if self.context.get("kind"):
            self.kind = self.context["kind"]
        self.refresh()

    def set_title(self, title):
        self.title_label.setText(title)
        if not self.context.get("kind"):
            self.kind = infer_kind(title, self.events)
        self.refresh()

    def set_status_text(self, text):
        if text:
            self.activity_label.setText(str(text))

    def append_log(self, text):
        if text:
            now = datetime.now().strftime("%H:%M:%S.%f")[:12]
            self.log.appendPlainText(f"{now}  LOCAL       —   {text}")

    def append_event(self, event):
        now = datetime.now().strftime("%H:%M:%S.%f")[:12]
        self._last_wall_time = now
        code = _event_code(event)
        stage = str(getattr(event, "stage", "—"))
        detail = getattr(event, "detail", "") or f"{stage_title(stage)} · {state_title(getattr(event, 'state', 'active'))}"
        suffix = ""
        for key in ("response_id", "batch_id", "file_id"):
            value = getattr(event, key, "")
            if value:
                suffix = f" · {key} {value}"
                break
        self.log.appendPlainText(f"{now}  {code:<11} {stage:<8} {detail}{suffix}")

    def on_event(self, event, clock=None):
        self.events.append(event)
        self.events[:] = self.events[-2000:]
        self.kind = self.context.get("kind") or infer_kind(self.title_label.text(), self.events)
        self.append_event(event)
        self.refresh(clock)

    def _stage_key(self, stage):
        if stage in STAGE_ALIASES:
            return STAGE_ALIASES[stage]
        upper = stage.upper()
        if upper.startswith("COMIC_"):
            return {
                "COMIC_RESPONSES": "Bible / descriptor",
                "COMIC_REFERENCE": "Reference",
                "COMIC_PREPARING": "Panely",
                "COMIC_SUBMITTING": "BATCH",
                "COMIC_RETRIEVING": "Převzetí",
                "COMIC_POSTPROCESSING": "Postprocessing",
                "COMIC_SAVING": "Uložení",
            }.get(upper, stage_title(stage))
        return stage_title(stage)

    def _process_steps(self):
        preset = list(PROCESS_PRESETS.get(self.kind, ()))
        quality_seen = any(getattr(e, "stage", "") in {"A2Q", "B2Q"} for e in self.events)
        if quality_seen:
            marker, before = ("A2Q", "A3") if self.kind == "GENERATE" else ("B2Q", "B3")
            if marker not in preset and before in preset:
                preset.insert(preset.index(before), marker)
        observed = []
        for event in self.events:
            if getattr(event, "stage", "") == "RUN":
                continue
            key = self._stage_key(getattr(event, "stage", ""))
            if key and key not in observed:
                observed.append(key)
            if key and key not in preset:
                preset.append(key)
        current_event = next((e for e in reversed(self.events) if getattr(e, "stage", "") != "RUN"), None)
        current = self._stage_key(getattr(current_event, "stage", "")) if current_event else ""
        completed = {self._stage_key(getattr(e, "stage", "")) for e in self.events if getattr(e, "stage", "") != "RUN" and getattr(e, "state", "") == "completed"}
        steps = []
        current_index = preset.index(current) if current in preset else -1
        for index, key in enumerate(preset):
            if key == current:
                state = "error" if getattr(current_event, "state", "") in {"failed", "error", "blocked"} else "current"
            elif key in completed or (current_index >= 0 and index < current_index):
                state = "done"
            else:
                state = "pending"
            steps.append((stage_title(key) if key in {"A0", "A0R", "A1", "A2", "A2Q", "A3", "B0R", "B1", "B2", "B2Q", "B3"} else key, state))
        return steps

    def _micro_steps(self, event):
        source = getattr(event, "source", "local") if event else "local"
        state = getattr(event, "state", "preparing") if event else "preparing"
        if source in {"api", "files_api", "batch_api"}:
            names = ["Sestavení requestu", "Odeslání API", "Čekání", "Převzetí", "Parsování", "Validace"]
            if state in {"waiting", "response_pending", "batch_pending", "queued", "in_progress", "finalizing"}:
                active = 2
            elif state in {"validating", "validating_result"}:
                active = 5
            elif state in {"completed"}:
                active = 5
            else:
                active = 1
        elif source == "upload":
            names, active = ["Příprava", "Upload", "Potvrzení"], 1
        elif source == "download":
            names, active = ["Příprava", "Download", "Ověření"], 1
        elif source == "disk":
            names, active = ["Příprava", "Zápis na disk", "Ověření"], 1
        elif source == "validation":
            names, active = ["Příprava", "Validace", "Výsledek"], 1
        else:
            names, active = ["Příprava", "Lokální zpracování", "Validace"], 1
        result = []
        for i, name in enumerate(names):
            result.append((name, "done" if i < active else "current" if i == active else "pending"))
        return result

    def _provider_state(self, event):
        if not event:
            return ""
        explicit = getattr(event, "provider_state", "")
        if explicit:
            return explicit
        state = getattr(event, "state", "")
        source = getattr(event, "source", "")
        return state if source == "batch_api" and state in PROVIDER_STATES else ""

    def refresh(self, clock=None):
        event = self.events[-1] if self.events else None
        state = getattr(event, "state", "preparing") if event else "preparing"
        symbol, label, tone = _state_meta(state)
        self.badge.setText(f"{symbol} {label.upper()}")
        self.badge.setStyleSheet(
            f"background: {COLORS['raised']}; color: {_tone_color(tone)}; border: 1px solid {_tone_color(tone)}; border-radius: 9px; padding: 6px 10px; font-weight: 700;"
        )

        meta = [self.kind]
        for key in ("run_id", "model"):
            if self.context.get(key):
                meta.append(str(self.context[key]))
        if getattr(event, "run_id", ""):
            meta.append(str(event.run_id))
        if getattr(event, "model", ""):
            meta.append(str(event.model))
        self.meta_label.setText(" · ".join(dict.fromkeys(meta)))

        stage = getattr(event, "stage", "Příprava") if event else "Příprava"
        self.phase_label.setText(stage_title(stage))
        if event and getattr(event, "detail", ""):
            self.activity_label.setText(event.detail)
        elif event:
            self.activity_label.setText(f"{stage_title(stage)} · {state_title(state)}")
        self.source_label.setText(source_title(getattr(event, "source", "local")) if event else "Lokální zpracování")
        self.stage_state_label.setText(state_title(state))
        code = _event_code(event) if event else "LOCAL"
        self.micro_label.setText(code)
        provider = self._provider_state(event)
        self.provider_label.setText(state_title(provider) if provider else "—")

        steps = self._process_steps()
        self.flow.set_steps(steps)
        self.micro_flow.set_steps(self._micro_steps(event))
        current_index = next((i for i, (_, step_state) in enumerate(steps) if step_state in {"current", "error"}), -1)
        next_step = next((title for title, step_state in steps[current_index + 1:] if step_state == "pending"), "Hotový výsledek") if steps else "Bude určen po zahájení běhu"
        self.next_label.setText(next_step)

        total = getattr(event, "total", None) if event else None
        completed = getattr(event, "completed", None) if event else None
        unit = getattr(event, "unit", "") if event else ""
        measured = next(
            (
                previous
                for previous in reversed(self.events)
                if getattr(previous, "total", None)
                and getattr(previous, "completed", None) is not None
            ),
            None,
        )
        if total is not None and total > 0 and completed is not None:
            self.run_progress.hide()
            self.unit_progress.show()
            self.unit_progress.setRange(0, total)
            self.unit_progress.setValue(completed)
            self.unit_progress.setFormat(f"%v / %m {unit}" if unit else "%v / %m")
            percent = round(completed / total * 100)
            self.progress_note.setText(f"{completed} z {total} {unit} · {percent} %".strip())
        elif state in TERMINAL_STATES:
            self.run_progress.show()
            self.run_progress.setRange(0, 100)
            self.run_progress.setValue(100 if state in {"completed", "closed"} else 0)
            if measured is not None:
                measured_total = int(measured.total)
                measured_completed = int(measured.completed)
                measured_unit = getattr(measured, "unit", "")
                self.unit_progress.show()
                self.unit_progress.setRange(0, measured_total)
                self.unit_progress.setValue(measured_completed)
                self.unit_progress.setFormat(
                    f"%v / %m {measured_unit}" if measured_unit else "%v / %m"
                )
                percent = round(measured_completed / measured_total * 100)
                self.progress_note.setText(
                    f"{measured_completed} z {measured_total} {measured_unit} · {percent} %".strip()
                )
            else:
                self.unit_progress.hide()
                self.progress_note.setText(state_title(state))
        else:
            self.run_progress.show()
            self.run_progress.setRange(0, 0)
            self.unit_progress.hide()
            self.progress_note.setText("Služba neposkytuje měřitelný postup.")

        if clock is not None:
            elapsed, age, eta = clock.times()
            text = f"Celkem {int(elapsed // 60):02d}:{int(elapsed % 60):02d} · poslední událost před {int(age)} s"
            if eta is not None:
                text += f" · odhad zbývá ~{int(eta // 60):02d}:{int(eta % 60):02d}"
            else:
                text += " · odhad není zatím dostupný"
            self.time_label.setText(text)

        is_batch = self.kind == "BATCH" or getattr(event, "source", "") == "batch_api"
        self.parallel.setVisible(is_batch)
        if is_batch:
            provider_text = state_title(provider) if provider else "stav zatím nepotvrzen"
            local_state = state_title(state)
            self.provider_flow_label.setText(f"OPENAI BATCH: {provider_text}")
            self.local_flow_label.setText(f"KÁJOVONG: {local_state}")
