from __future__ import annotations

from pathlib import Path
from typing import Iterable, Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QResizeEvent
from PySide6.QtGui import QMovie, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QStackedLayout,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from kajovospend.ui.design_contract import BRAND_HOST_RULE, BreakpointName, PRIMARY_VIEW_STATES, StateName, breakpoint_for_width
from kajovospend.ui.tokens import COLORS, SPACING


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def signage_path() -> Path:
    candidates = [
        _repo_root() / 'brand' / 'signace' / 'signace.png',
        _repo_root() / 'brand' / 'signace' / 'signace.svg',
        _repo_root() / 'signace' / 'signace.svg',
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


class Card(QFrame):
    def __init__(self, title: str = '', subtitle: str = '') -> None:
        super().__init__()
        self.setProperty('card', True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        root = QVBoxLayout(self)
        root.setContentsMargins(SPACING.lg, SPACING.lg, SPACING.lg, SPACING.lg)
        root.setSpacing(SPACING.md)
        self.header_widget = QWidget()
        header_layout = QVBoxLayout(self.header_widget)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(2)
        self.title_label = QLabel(title)
        self.title_label.setObjectName('SectionTitle')
        self.subtitle_label = QLabel(subtitle)
        self.subtitle_label.setProperty('muted', True)
        self.subtitle_label.setWordWrap(True)
        header_layout.addWidget(self.title_label)
        header_layout.addWidget(self.subtitle_label)
        root.addWidget(self.header_widget)
        self.body = QWidget()
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        self.body_layout.setSpacing(SPACING.md)
        root.addWidget(self.body)
        if not title and not subtitle:
            self.header_widget.hide()

    def set_title(self, title: str, subtitle: str = '') -> None:
        self.title_label.setText(title)
        self.subtitle_label.setText(subtitle)
        self.header_widget.setVisible(bool(title or subtitle))


class StatCard(Card):
    def __init__(self, title: str, *, tone: str = 'neutral') -> None:
        super().__init__(title)
        self.setProperty('statCard', True)
        self.setProperty('tone', tone)
        metric_row = QHBoxLayout()
        metric_row.setContentsMargins(0, 0, 0, 0)
        metric_row.setSpacing(SPACING.sm)
        self.metric_label = QLabel('0')
        self.metric_label.setObjectName('StatValue')
        self.suffix_label = QLabel('')
        self.suffix_label.setProperty('muted', True)
        metric_row.addWidget(self.metric_label)
        metric_row.addWidget(self.suffix_label)
        metric_row.addStretch(1)
        row = QWidget()
        row.setLayout(metric_row)
        self.body_layout.addWidget(row)
        self.meta_label = QLabel('')
        self.meta_label.setProperty('muted', True)
        self.meta_label.setWordWrap(True)
        self.body_layout.addWidget(self.meta_label)

    def set_metric(self, value: str, suffix: str = '', meta: str = '') -> None:
        self.metric_label.setText(value)
        self.suffix_label.setText(suffix)
        self.suffix_label.setVisible(bool(suffix))
        self.meta_label.setText(meta)
        self.meta_label.setVisible(bool(meta))


class StatusDot(QLabel):
    def __init__(self, tone: str = 'neutral') -> None:
        super().__init__('•')
        self.setProperty('signalLight', True)
        self.setProperty('signalState', tone)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)

    def set_tone(self, tone: str) -> None:
        self.setProperty('signalState', tone)
        self.style().unpolish(self)
        self.style().polish(self)


class BrandLockup(QWidget):
    def __init__(self, app_name: str = 'KajovoSpendNG') -> None:
        super().__init__()
        self.setProperty('brandHost', True)
        self.setProperty('brandElementCount', BRAND_HOST_RULE.minimum_brand_elements)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACING.md)
        self.signage = QLabel()
        self.signage.setObjectName('BrandSignageImage')
        self.signage.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.signage.setMaximumHeight(40)
        self._load_signage()
        self.wordmark = QLabel(app_name)
        self.wordmark.setObjectName('BrandWordmark')
        self.wordmark.setWordWrap(True)
        layout.addWidget(self.signage)
        layout.addWidget(self.wordmark)
        layout.addStretch(1)

    def _load_signage(self) -> None:
        pixmap = QPixmap(str(signage_path()))
        if pixmap.isNull():
            self.signage.setText('KÁJOVO')
            self.signage.setObjectName('Signage')
            return
        self.signage.setPixmap(pixmap.scaledToHeight(28, Qt.TransformationMode.SmoothTransformation))


class NavButton(QPushButton):
    def __init__(self, label: str, *, compact: bool = False) -> None:
        super().__init__(label)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setProperty('navButton', True)
        self.setProperty('compact', compact)
        self.setMinimumHeight(44 if not compact else 36)


class PillLabel(QLabel):
    def __init__(self, text: str = '', tone: str = 'neutral') -> None:
        super().__init__(text)
        self.setProperty('pill', True)
        self.setProperty('tone', tone)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setWordWrap(False)

    def set_tone(self, tone: str) -> None:
        self.setProperty('tone', tone)
        self.style().unpolish(self)
        self.style().polish(self)


class StateCard(Card):
    def __init__(self, state: StateName, title: str, description: str, action_text: str | None = None) -> None:
        super().__init__()
        self.setProperty('stateCard', True)
        self.setProperty('stateName', state.value)
        self.setProperty('brandHost', True)
        self.setProperty('brandElementCount', BRAND_HOST_RULE.minimum_brand_elements)
        self.body_layout.addWidget(BrandLockup('KajovoSpendNG'), alignment=Qt.AlignmentFlag.AlignCenter)
        self.icon = QLabel(_state_icon(state))
        self.icon.setObjectName('StateIcon')
        self.icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.body_layout.addWidget(self.icon, alignment=Qt.AlignmentFlag.AlignCenter)
        self.title = QLabel(title)
        self.title.setObjectName('EmptyStateTitle')
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title.setWordWrap(True)
        self.body_layout.addWidget(self.title)
        self.description = QLabel(description)
        self.description.setProperty('muted', True)
        self.description.setWordWrap(True)
        self.description.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.body_layout.addWidget(self.description)
        self.action = QPushButton(action_text or '')
        self.action.setVisible(bool(action_text))
        self.body_layout.addWidget(self.action, alignment=Qt.AlignmentFlag.AlignCenter)

    def set_content(self, *, title: str, description: str, action_text: str | None = None) -> None:
        self.title.setText(title)
        self.description.setText(description)
        self.action.setText(action_text or '')
        self.action.setVisible(bool(action_text))


class EmptyState(StateCard):
    def __init__(self, title: str, description: str, action_text: str | None = None) -> None:
        super().__init__(StateName.EMPTY, title, description, action_text)


class LoadingState(StateCard):
    def __init__(self, title: str = 'Načítání', description: str = 'Zobrazení se připravuje.') -> None:
        super().__init__(StateName.LOADING, title, description)
        self.spinner = QLabel()
        self.spinner.setObjectName('StateSpinner')
        self.spinner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        movie = QMovie()
        self.spinner.setMovie(movie)
        self.body_layout.insertWidget(2, self.spinner, alignment=Qt.AlignmentFlag.AlignCenter)


class ErrorState(StateCard):
    def __init__(self, title: str = 'Nastala chyba', description: str = 'Zkuste akci zopakovat nebo zkontrolujte vstupy.') -> None:
        super().__init__(StateName.ERROR, title, description)


class OfflineState(StateCard):
    def __init__(self, title: str = 'Projekt není připojen', description: str = 'Připojte projekt, aby bylo možné pokračovat.') -> None:
        super().__init__(StateName.OFFLINE, title, description)


class MaintenanceState(StateCard):
    def __init__(self, title: str = 'Zobrazení je dočasně omezené', description: str = 'Probíhá servisní režim nebo příprava dat.') -> None:
        super().__init__(StateName.MAINTENANCE, title, description)


class FallbackState(StateCard):
    def __init__(self, title: str = 'Zobrazení nemá validní data', description: str = 'Aplikace přešla do bezpečného fallbacku.') -> None:
        super().__init__(StateName.FALLBACK, title, description)


class StateHost(QWidget):
    def __init__(self, *, default_title: str, default_description: str) -> None:
        super().__init__()
        self.setProperty('stateHost', True)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self._layout = QStackedLayout()
        root.addLayout(self._layout)
        self._content: QWidget | None = None
        self._state_widgets: dict[StateName, StateCard] = {
            StateName.EMPTY: EmptyState(default_title, default_description),
            StateName.LOADING: LoadingState(),
            StateName.ERROR: ErrorState(),
            StateName.OFFLINE: OfflineState(),
            StateName.MAINTENANCE: MaintenanceState(),
            StateName.FALLBACK: FallbackState(),
        }
        for state in PRIMARY_VIEW_STATES:
            if state == StateName.DEFAULT:
                continue
            self._layout.addWidget(self._state_widgets[state])
        self._current_state = StateName.DEFAULT

    def set_content(self, widget: QWidget) -> None:
        if self._content is None:
            self._content = widget
            self._layout.insertWidget(0, widget)
        else:
            widget.setParent(None)
            self._content = widget
            self._layout.insertWidget(0, widget)
        self.set_state(StateName.DEFAULT)

    def set_state(self, state: StateName, *, title: str | None = None, description: str | None = None, action_text: str | None = None) -> None:
        self._current_state = state
        if state == StateName.DEFAULT:
            self._layout.setCurrentIndex(0)
            return
        widget = self._state_widgets[state]
        if title or description or action_text is not None:
            widget.set_content(
                title=title or widget.title.text(),
                description=description or widget.description.text(),
                action_text=action_text,
            )
        self._layout.setCurrentWidget(widget)

    @property
    def current_state(self) -> StateName:
        return self._current_state


class KeyValueText(QTextEdit):
    def __init__(self) -> None:
        super().__init__()
        self.setReadOnly(True)
        self.setProperty('detailText', True)
        self.setMinimumHeight(120)

    def set_mapping(self, mapping: dict, *, title: str = '') -> None:
        lines: list[str] = []
        if title:
            lines.append(title)
            lines.append('')
        if not mapping:
            lines.append('Žádná data nejsou k dispozici.')
        else:
            for key, value in mapping.items():
                lines.append(f'{_humanize(key)}: {_render_value(value)}')
        self.setPlainText('\n'.join(lines))


def _humanize(value: str) -> str:
    return str(value).replace('_', ' ').strip().capitalize()


def _render_value(value) -> str:
    if isinstance(value, float):
        return f'{value:,.2f}'.replace(',', ' ').replace('.', ',')
    if value in (None, ''):
        return '—'
    return str(value)


def _state_icon(state: StateName) -> str:
    return {
        StateName.EMPTY: '◌',
        StateName.LOADING: '…',
        StateName.ERROR: '×',
        StateName.OFFLINE: '⛔',
        StateName.MAINTENANCE: '⚙',
        StateName.FALLBACK: '◇',
    }.get(state, '•')


class DataTable(QTableWidget):
    selectionChangedSignal = Signal()

    def __init__(self) -> None:
        super().__init__(0, 0)
        self.setProperty('dataTable', True)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setAlternatingRowColors(True)
        self.setSortingEnabled(False)
        self.verticalHeader().setVisible(False)
        self.setShowGrid(False)
        self.horizontalHeader().setStretchLastSection(True)
        self.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.itemSelectionChanged.connect(self.selectionChangedSignal.emit)
        self._records: list[dict] = []
        self._key_field = 'id'

    def set_records(self, records: Sequence[dict], columns: Sequence[tuple[str, str]], *, key_field: str = 'id') -> None:
        self._records = [dict(record) for record in records]
        self._key_field = key_field
        self.clearContents()
        self.setRowCount(len(records))
        self.setColumnCount(len(columns))
        self.setHorizontalHeaderLabels([label for _, label in columns])
        for row_index, record in enumerate(self._records):
            for col_index, (field, _) in enumerate(columns):
                item = QTableWidgetItem(_render_value(record.get(field)))
                item.setData(Qt.ItemDataRole.UserRole, record)
                self.setItem(row_index, col_index, item)
        self.resizeColumnsToContents()
        if self.columnCount():
            self.horizontalHeader().setStretchLastSection(True)
        if self.rowCount():
            self.selectRow(0)

    def current_record(self) -> dict | None:
        row = self.currentRow()
        if row < 0 or row >= len(self._records):
            return None
        return dict(self._records[row])


class Pager(QWidget):
    pageDelta = Signal(int)

    def __init__(self) -> None:
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACING.xs)
        self.prev_button = QPushButton('‹ Předchozí')
        self.next_button = QPushButton('Další ›')
        self.info_label = QLabel('Strana 1 / 1')
        self.info_label.setProperty('muted', True)
        self.prev_button.clicked.connect(lambda: self.pageDelta.emit(-1))
        self.next_button.clicked.connect(lambda: self.pageDelta.emit(1))
        layout.addWidget(self.prev_button)
        layout.addWidget(self.info_label)
        layout.addWidget(self.next_button)
        layout.addStretch(1)

    def update_state(self, *, page: int, page_size: int, total_count: int) -> None:
        total_pages = max(1, (max(total_count, 0) + page_size - 1) // max(page_size, 1))
        page = min(max(page, 1), total_pages)
        self.info_label.setText(f'Strana {page} / {total_pages} · {total_count} záznamů')
        self.prev_button.setEnabled(page > 1)
        self.next_button.setEnabled(page < total_pages)


class SummaryGrid(QWidget):
    def __init__(self, columns: int = 4) -> None:
        super().__init__()
        self.layout = QGridLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setHorizontalSpacing(SPACING.md)
        self.layout.setVerticalSpacing(SPACING.md)
        self.columns = max(1, columns)
        self._cards: list[QWidget] = []

    def set_cards(self, cards: Iterable[QWidget]) -> None:
        self._cards = list(cards)
        self._rebuild()

    def resizeEvent(self, event: QResizeEvent) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._rebuild()

    def _effective_columns(self) -> int:
        breakpoint_name = breakpoint_for_width(max(self.width(), 1))
        if breakpoint_name in {BreakpointName.SM, BreakpointName.MD}:
            return 1
        if breakpoint_name == BreakpointName.LG:
            return min(self.columns, 2)
        return self.columns

    def _rebuild(self) -> None:
        while self.layout.count():
            item = self.layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
        columns = max(1, self._effective_columns())
        for index, widget in enumerate(self._cards):
            row = index // columns
            col = index % columns
            self.layout.addWidget(widget, row, col)
