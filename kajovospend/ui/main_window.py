from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QDesktopServices, QGuiApplication, QResizeEvent, QTextDocument
from PySide6.QtPrintSupport import QPrintDialog, QPrinter
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QBoxLayout,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from kajovospend.application.controller import AppController
from kajovospend.branding.theme import stylesheet
from kajovospend.ui.design_contract import BreakpointName, StateName, breakpoint_for_width
from kajovospend.ui.dialogs import (
    CatalogEntryDialog,
    ConfirmDialog,
    DocumentEditorDialog,
    ErrorDialog,
    GroupEditorDialog,
    InfoDialog,
    ManualCompletionDialog,
    OcrInspectorDialog,
    PatternEditorDialog,
    ProjectSetupDialog,
    SupplierEditorDialog,
    TaskProgressDialog,
    WarningDialog,
)
from kajovospend.ui.tokens import COLORS, SPACING
from kajovospend.ui.widgets import (
    BrandLockup,
    Card,
    DataTable,
    DocumentPreviewWidget,
    ErrorState,
    EmptyState,
    FallbackState,
    KeyValueText,
    LoadingState,
    MaintenanceState,
    NavButton,
    OfflineState,
    Pager,
    PillLabel,
    RegionSelectorWidget,
    StateHost,
    StatCard,
    StatusDot,
    SummaryGrid,
)


class BackgroundWorker(QObject):
    progress = Signal(int, int, str)
    result = Signal(object)
    error = Signal(str)
    finished = Signal()

    def __init__(self, fn: Callable[..., Any], kwargs: dict[str, Any] | None = None) -> None:
        super().__init__()
        self.fn = fn
        self.kwargs = kwargs or {}

    @Slot()
    def run(self) -> None:
        try:
            signature = inspect.signature(self.fn)
            if 'progress_callback' in signature.parameters:
                value = self.fn(progress_callback=self.progress.emit, **self.kwargs)
            else:
                value = self.fn(**self.kwargs)
        except Exception as exc:  # pragma: no cover - GUI task error path
            self.error.emit(str(exc))
        else:
            self.result.emit(value)
        finally:
            self.finished.emit()


class MainWindow(QMainWindow):
    PAGE_SIZE = 18
    ITEM_PAGE_SIZE = 24
    PATTERN_PAGE_SIZE = 100

    def __init__(self, controller: AppController) -> None:
        super().__init__()
        self.controller = controller
        self.setWindowTitle('KajovoSpendNG')
        self.resize(1280, 900)
        self.setMinimumSize(360, 640)
        self._threads: list[QThread] = []
        self._view_hosts: dict[str, StateHost] = {}
        self._responsive_splitters: list[tuple[QSplitter, str]] = []
        self._responsive_stacks: list[tuple[QWidget, str]] = []
        self._page_state = {
            'attempts': 1,
            'processing_documents': 1,
            'documents': 1,
            'items': 1,
            'suppliers': 1,
            'supplier_documents': 1,
            'supplier_items': 1,
            'quarantine': 1,
            'unrecognized': 1,
        }
        self._current_document_id: int | None = None
        self._current_item_id: int | None = None
        self._current_supplier_id: int | None = None
        self._current_quarantine_record: dict[str, Any] | None = None
        self._current_unrecognized_record: dict[str, Any] | None = None
        self._current_attempt_record: dict[str, Any] | None = None

        self._build_shell()
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(stylesheet(self.controller.settings))
        self._apply_breakpoint_layout(self.width())
        self.refresh_ui()
        self.refresh_timer = QTimer(self)
        self.refresh_timer.setInterval(15000)
        self.refresh_timer.timeout.connect(self._periodic_refresh)
        self.refresh_timer.start()

    def place_on_primary_screen(self) -> None:
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            self.show()
            return
        self.show()
        frame = self.frameGeometry()
        frame.moveCenter(screen.availableGeometry().center())
        self.move(frame.topLeft())

    def resizeEvent(self, event: QResizeEvent) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._apply_breakpoint_layout(event.size().width())

    def _register_responsive_splitter(self, splitter: QSplitter, layout_key: str) -> None:
        self._responsive_splitters.append((splitter, layout_key))
        self._apply_splitter_layout(splitter, layout_key, breakpoint_for_width(self.width()))

    def _register_responsive_stack(self, widget: QWidget, layout_key: str) -> None:
        self._responsive_stacks.append((widget, layout_key))
        self._apply_stack_layout(widget, layout_key, breakpoint_for_width(self.width()))

    def _apply_breakpoint_layout(self, width: int) -> None:
        breakpoint_name = breakpoint_for_width(width)
        for splitter, layout_key in self._responsive_splitters:
            self._apply_splitter_layout(splitter, layout_key, breakpoint_name)
        for widget, layout_key in self._responsive_stacks:
            self._apply_stack_layout(widget, layout_key, breakpoint_name)

    def _apply_splitter_layout(self, splitter: QSplitter, layout_key: str, breakpoint_name: BreakpointName) -> None:
        compact = breakpoint_name in {BreakpointName.SM, BreakpointName.MD}
        splitter.setOrientation(Qt.Orientation.Vertical if compact else Qt.Orientation.Horizontal)
        size_map: dict[str, dict[BreakpointName, list[int]]] = {
            'documents': {
                BreakpointName.SM: [320, 420, 320],
                BreakpointName.MD: [340, 440, 340],
                BreakpointName.LG: [440, 520, 360],
                BreakpointName.XL: [480, 600, 380],
            },
            'items': {
                BreakpointName.SM: [420, 320],
                BreakpointName.MD: [480, 320],
                BreakpointName.LG: [820, 420],
                BreakpointName.XL: [860, 460],
            },
            'suppliers': {
                BreakpointName.SM: [320, 280, 340],
                BreakpointName.MD: [360, 320, 360],
                BreakpointName.LG: [520, 360, 420],
                BreakpointName.XL: [560, 380, 460],
            },
            'operations': {
                BreakpointName.SM: [360, 380],
                BreakpointName.MD: [420, 420],
                BreakpointName.LG: [760, 520],
                BreakpointName.XL: [820, 560],
            },
            'quarantine': {
                BreakpointName.SM: [360, 380],
                BreakpointName.MD: [420, 420],
                BreakpointName.LG: [760, 520],
                BreakpointName.XL: [820, 560],
            },
            'unrecognized': {
                BreakpointName.SM: [360, 380],
                BreakpointName.MD: [420, 420],
                BreakpointName.LG: [760, 520],
                BreakpointName.XL: [820, 560],
            },
        }
        splitter.setSizes(size_map.get(layout_key, {}).get(breakpoint_name, [420, 420]))

    def _apply_stack_layout(self, widget: QWidget, layout_key: str, breakpoint_name: BreakpointName) -> None:
        layout = widget.layout()
        if not isinstance(layout, QBoxLayout):
            return
        compact = breakpoint_name in {BreakpointName.SM, BreakpointName.MD}
        if layout_key == 'header_top':
            layout.setDirection(QBoxLayout.Direction.TopToBottom if compact else QBoxLayout.Direction.LeftToRight)
        elif layout_key == 'header_actions':
            layout.setDirection(QBoxLayout.Direction.TopToBottom if compact else QBoxLayout.Direction.LeftToRight)
        elif layout_key == 'header_signals':
            layout.setDirection(QBoxLayout.Direction.TopToBottom if compact else QBoxLayout.Direction.LeftToRight)
        elif layout_key == 'project_actions':
            layout.setDirection(QBoxLayout.Direction.TopToBottom if compact else QBoxLayout.Direction.LeftToRight)
        elif layout_key == 'import_actions':
            layout.setDirection(QBoxLayout.Direction.TopToBottom if compact else QBoxLayout.Direction.LeftToRight)
        elif layout_key == 'openai_actions':
            layout.setDirection(QBoxLayout.Direction.TopToBottom if compact else QBoxLayout.Direction.LeftToRight)
        elif layout_key == 'patterns_actions':
            layout.setDirection(QBoxLayout.Direction.TopToBottom if compact else QBoxLayout.Direction.LeftToRight)
        elif layout_key == 'management_actions':
            layout.setDirection(QBoxLayout.Direction.TopToBottom if compact else QBoxLayout.Direction.LeftToRight)
        elif layout_key == 'admin_actions':
            layout.setDirection(QBoxLayout.Direction.TopToBottom if compact else QBoxLayout.Direction.LeftToRight)
        elif layout_key == 'group_actions':
            layout.setDirection(QBoxLayout.Direction.TopToBottom if compact else QBoxLayout.Direction.LeftToRight)
        elif layout_key == 'catalog_actions':
            layout.setDirection(QBoxLayout.Direction.TopToBottom if compact else QBoxLayout.Direction.LeftToRight)

    def _build_shell(self) -> None:
        root = QWidget()
        root.setObjectName('RootShell')
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QWidget()
        header.setObjectName('AppHeader')
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(20, 16, 20, 16)
        header_layout.setSpacing(14)

        top_row = QWidget()
        top_row_layout = QHBoxLayout(top_row)
        top_row_layout.setContentsMargins(0, 0, 0, 0)
        top_row_layout.setSpacing(16)
        self.title_label = QLabel('KájovoSpendNG')
        self.title_label.hide()
        top_row_layout.addWidget(BrandLockup('KajovoSpendNG'), 2)

        self.import_button = QPushButton('IMPORT')
        self.import_button.setObjectName('importButton')
        self.import_button.clicked.connect(self.on_import)
        self.import_button.setObjectName('importButton')
        self.import_button.setProperty('id', 'import_button')
        self.stop_button = QPushButton('STOP')
        self.stop_button.setObjectName('stopButton')
        self.stop_button.clicked.connect(self.on_stop)
        self.exit_button = QPushButton('EXIT')
        self.exit_button.clicked.connect(self.close)
        action_row = QWidget()
        action_row_layout = QHBoxLayout(action_row)
        action_row_layout.setContentsMargins(0, 0, 0, 0)
        action_row_layout.setSpacing(8)
        action_row_layout.addWidget(self.import_button)
        action_row_layout.addWidget(self.stop_button)
        action_row_layout.addWidget(self.exit_button)
        top_row_layout.addWidget(action_row)
        self.header_action_row = action_row

        self.project_label = PillLabel('Projekt: nepřipojen', 'info')
        self.queue_label = PillLabel('Fronta: 0', 'neutral')
        self.last_success = PillLabel('Poslední úspěch: —', 'neutral')
        self.last_error = PillLabel('Poslední chyba: —', 'neutral')
        self.working_db_label = PillLabel('DB: —', 'neutral')
        for widget in [self.project_label, self.queue_label, self.last_success, self.last_error, self.working_db_label]:
            widget.setMaximumHeight(32)
            top_row_layout.addWidget(widget)

        signals = QWidget()
        signals_layout = QHBoxLayout(signals)
        signals_layout.setContentsMargins(0, 0, 0, 0)
        signals_layout.setSpacing(8)
        self.connection_label = StatusDot('neutral')
        self.process_label = StatusDot('neutral')
        self.input_status = StatusDot('neutral')
        self.integrity_label = StatusDot('neutral')
        for dot, text in [
            (self.connection_label, 'DB'),
            (self.process_label, 'RUN'),
            (self.input_status, 'IN'),
            (self.integrity_label, 'OK'),
        ]:
            col = QWidget()
            col_layout = QHBoxLayout(col)
            col_layout.setContentsMargins(0, 0, 0, 0)
            col_layout.setSpacing(4)
            col_layout.addWidget(dot)
            label = QLabel(text)
            label.setProperty('muted', True)
            col_layout.addWidget(label)
            signals_layout.addWidget(col)
        top_row_layout.addWidget(signals)
        self.header_top_row = top_row
        self.header_signals = signals
        self._register_responsive_stack(self.header_top_row, 'header_top')
        self._register_responsive_stack(self.header_action_row, 'header_actions')
        self._register_responsive_stack(self.header_signals, 'header_signals')
        header_layout.addWidget(top_row)

        sub_header = QWidget()
        sub_header.setObjectName('AppSubHeader')
        sub_header_layout = QHBoxLayout(sub_header)
        sub_header_layout.setContentsMargins(20, 0, 20, 0)
        sub_header_layout.setSpacing(4)
        self.main_nav_group = QButtonGroup(self)
        self.main_nav_group.setExclusive(True)
        self.main_pages = QStackedWidget()
        self.main_page_indices: dict[str, int] = {}
        self.main_nav_buttons: dict[str, NavButton] = {}
        nav_specs = [
            ('dashboard', 'DASHBOARD', 'dashboard_tab'),
            ('operations', 'PROVOZNÍ PANEL', 'operations_tab'),
            ('quarantine', 'KARANTÉNA', 'quarantine_root_tab'),
            ('expenses', 'VÝDAJE', 'expenses_tab'),
            ('accounts', 'ÚČTY', 'accounts_tab'),
            ('suppliers', 'DODAVATELÉ', 'suppliers_tab'),
            ('unrecognized', 'NEROZPOZNANÉ', 'unrecognized_tab'),
            ('settings', 'NASTAVENÍ', 'settings_tab'),
        ]
        for key, label, object_name in nav_specs:
            button = NavButton(label)
            button.setObjectName(object_name)
            button.clicked.connect(lambda checked=False, page_key=key: self._switch_main_view(page_key))
            self.main_nav_group.addButton(button)
            self.main_nav_buttons[key] = button
            sub_header_layout.addWidget(button)
        sub_header_layout.addStretch(1)
        self.header_summary = QLabel('Připojte projekt a vyberte vstupní adresář.')
        self.header_summary.setProperty('muted', True)
        sub_header_layout.addWidget(self.header_summary)
        header_layout.addWidget(sub_header)
        layout.addWidget(header)

        content_wrap = QWidget()
        content_layout = QVBoxLayout(content_wrap)
        content_layout.setContentsMargins(20, 20, 20, 20)
        content_layout.setSpacing(20)
        content_layout.addWidget(self.main_pages, 1)
        layout.addWidget(content_wrap, 1)

        footer = QWidget()
        footer.setObjectName('FooterBar')
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(20, 10, 20, 10)
        footer_layout.setSpacing(8)
        self.signage = QLabel('KÁJOVO')
        self.signage.setObjectName('Signage')
        footer_layout.addWidget(self.signage)
        footer_layout.addStretch(1)
        layout.addWidget(footer)

        self._build_dashboard_view()
        self._build_operations_view()
        self._build_quarantine_view()
        self._build_expenses_view()
        self._build_accounts_view()
        self._build_suppliers_view()
        self._build_unrecognized_view()
        self._build_settings_view()
        self._switch_main_view('dashboard')

    def _register_main_page(self, key: str, widget: QWidget) -> None:
        self.main_page_indices[key] = self.main_pages.addWidget(widget)

    def _register_state_page(self, key: str, content: QWidget, *, title: str, description: str) -> None:
        host = StateHost(default_title=title, default_description=description)
        host.set_content(content)
        self._view_hosts[key] = host
        self._register_main_page(key, host)

    def _set_page_state(self, key: str, state: StateName, *, title: str | None = None, description: str | None = None) -> None:
        host = self._view_hosts.get(key)
        if host is None:
            return
        host.set_state(state, title=title, description=description)

    def _switch_main_view(self, key: str) -> None:
        if key not in self.main_page_indices:
            return
        self.main_pages.setCurrentIndex(self.main_page_indices[key])
        for page_key, button in self.main_nav_buttons.items():
            button.setChecked(page_key == key)

    def _scroll_page(self, inner: QWidget) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(inner)
        return scroll

    def _stacked_toolbar(self, object_name: str, rows: list[list[QWidget]], *, stretch_last: bool = True) -> QWidget:
        container = QWidget()
        container.setObjectName(object_name)
        root = QVBoxLayout(container)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)
        for index, widgets in enumerate(rows, 1):
            row = QWidget()
            row.setObjectName(f'{object_name}_row_{index}')
            layout = QHBoxLayout(row)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(8)
            for widget in widgets:
                layout.addWidget(widget)
            if stretch_last:
                layout.addStretch(1)
            root.addWidget(row)
        return container

    def _build_dashboard_view(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        top_grid = SummaryGrid(columns=2)
        self.project_state_card = Card('Stav projektu', 'Napojení projektu, integrita a připravenost importu.')
        self.project_state_card.setObjectName('project_state_card')
        self.project_state_body = QLabel('Projekt není připojen.')
        self.project_state_body.setObjectName('project_state_body')
        self.project_state_body.setWordWrap(True)
        self.project_state_card.body_layout.addWidget(self.project_state_body)
        self.quick_card = Card('Rychlé akce', 'Nejčastější první kroky při onboarding a provozu projektu.')
        self.quick_card.setObjectName('quick_card')
        quick_actions = [
            ('Připojit projektový adresář', self.on_connect_project),
            ('Založit nový projektový adresář', self.on_create_project),
            ('Otevřít nastavení', lambda: self._switch_main_view('settings')),
        ]
        for text, handler in quick_actions:
            button = QPushButton(text)
            button.clicked.connect(handler)
            self.quick_card.body_layout.addWidget(button)
        top_grid.set_cards([self.project_state_card, self.quick_card])
        layout.addWidget(top_grid)

        self.dashboard_primary_card = StatCard('Hlavní provozní přehled', tone='info')
        self.dashboard_primary_card.setObjectName('dashboard_primary_card')
        self.stats_card = Card('Provozní KPI', 'Aktuální fronta, karanténa a kvalita posledního běhu.')
        self.stats_card.setObjectName('stats_card')
        self.stats_label = QTextEdit(); self.stats_label.setReadOnly(True)
        self.stats_label.setObjectName('stats_label')
        self.stats_card.body_layout.addWidget(self.stats_label)
        self.runtime_card = Card('Status a progres programu', 'Stav běhu, aktivní úloha a poslední zpracování.')
        self.runtime_card.setObjectName('runtime_card')
        self.runtime_label = QTextEdit(); self.runtime_label.setReadOnly(True)
        self.runtime_label.setObjectName('runtime_label')
        self.runtime_card.body_layout.addWidget(self.runtime_label)
        self.success_card = Card('Úspěšnost vyčtení', 'Rozpad offline/API/manual úspěchů a blokací.')
        self.success_card.setObjectName('success_card')
        self.success_label = QTextEdit(); self.success_label.setReadOnly(True)
        self.success_label.setObjectName('success_label')
        self.success_card.body_layout.addWidget(self.success_label)
        self.production_card = Card('Sekundární produkční přehled', 'Finální data a trend po promotion.')
        self.production_card.setObjectName('production_card')
        self.production_label = QTextEdit(); self.production_label.setReadOnly(True)
        self.production_label.setObjectName('production_label')
        self.production_card.body_layout.addWidget(self.production_label)
        metrics_grid = SummaryGrid(columns=2)
        metrics_grid.set_cards([
            self.dashboard_primary_card,
            self.stats_card,
            self.runtime_card,
            self.success_card,
            self.production_card,
        ])
        layout.addWidget(metrics_grid)
        self._register_state_page('dashboard', self._scroll_page(page), title='Dashboard čeká na data', description='Zobrazení používá řízené stavy místo textových fallbacků.')

    def _build_expenses_view(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        self.expense_periods_card = Card('Po obdobích', 'Měsíční, kvartální a roční agregace finálních dokladů.')
        self.expense_periods = QTextEdit(); self.expense_periods.setReadOnly(True)
        self.expense_periods.setObjectName('expense_periods')
        self.expense_periods_card.body_layout.addWidget(self.expense_periods)
        self.expense_vat_card = Card('DPH', 'Rozpad finálních položek podle sazeb DPH.')
        self.expense_vat = QTextEdit(); self.expense_vat.setReadOnly(True)
        self.expense_vat.setObjectName('expense_vat')
        self.expense_vat_card.body_layout.addWidget(self.expense_vat)
        self.expense_suppliers_card = Card('Top dodavatelé', 'Dodavatelé s nejvyšším objemem a počtem dokladů.')
        self.expense_suppliers = QTextEdit(); self.expense_suppliers.setReadOnly(True)
        self.expense_suppliers.setObjectName('expense_suppliers')
        self.expense_suppliers_card.body_layout.addWidget(self.expense_suppliers)
        self.expense_review_card = Card('Vyžaduje kontrolu + nerozpoznané', 'Propojení finálních dat s provozními review frontami.')
        self.expense_review = QTextEdit(); self.expense_review.setReadOnly(True)
        self.expense_review.setObjectName('expense_review')
        self.expense_review_card.body_layout.addWidget(self.expense_review)
        grid = SummaryGrid(columns=2)
        grid.set_cards([
            self.expense_periods_card,
            self.expense_vat_card,
            self.expense_suppliers_card,
            self.expense_review_card,
        ])
        layout.addWidget(grid)
        self._register_state_page('expenses', self._scroll_page(page), title='Výdaje čekají na data', description='Reporting se zobrazí po napojení projektu a načtení finálních dat.')

    def _build_accounts_view(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        self.accounts_filters_card = Card('ÚČTY: společné hledání a filtry', 'Jeden filtr může řídit doklady, položky nebo obojí.')
        self.accounts_filters_card.setObjectName('accounts_tab')
        filters = QWidget()
        filters_layout = QHBoxLayout(filters)
        filters_layout.setContentsMargins(0, 0, 0, 0)
        filters_layout.setSpacing(8)
        self.accounts_search = QLineEdit(); self.accounts_search.setObjectName('accounts_search'); self.accounts_search.setPlaceholderText('Jeden fulltext pro doklady i položky')
        self.accounts_period = QLineEdit(); self.accounts_period.setObjectName('accounts_period'); self.accounts_period.setPlaceholderText('YYYY-MM')
        self.accounts_vat = QComboBox(); self.accounts_vat.setObjectName('accounts_vat'); self.accounts_vat.addItems(['', '21', '15', '12', '10', '0'])
        self.accounts_scope = QComboBox(); self.accounts_scope.setObjectName('accounts_scope'); self.accounts_scope.addItems(['Obojí', 'Doklady', 'Položky'])
        apply_btn = QPushButton('Filtrovat'); apply_btn.clicked.connect(self.on_accounts_filter_apply)
        reset_btn = QPushButton('Vyčistit'); reset_btn.clicked.connect(self.on_accounts_filter_reset)
        self.accounts_summary = QLabel('ÚČTY: bez filtru'); self.accounts_summary.setObjectName('accounts_summary'); self.accounts_summary.setProperty('muted', True)
        for widget in [self.accounts_search, self.accounts_period, self.accounts_vat, self.accounts_scope, apply_btn, reset_btn]:
            filters_layout.addWidget(widget)
        filters_layout.addWidget(self.accounts_summary, 1)
        self.accounts_filters_card.body_layout.addWidget(filters)
        layout.addWidget(self.accounts_filters_card)

        nav_row = QWidget()
        nav_layout = QHBoxLayout(nav_row)
        nav_layout.setContentsMargins(0, 0, 0, 0)
        nav_layout.setSpacing(8)
        self.accounts_sections_group = QButtonGroup(self)
        self.accounts_sections_group.setExclusive(True)
        self.documents_tab = NavButton('Doklady', compact=True)
        self.documents_tab.setObjectName('documents_tab')
        self.items_tab = NavButton('Položky', compact=True)
        self.items_tab.setObjectName('items_tab')
        self.accounts_sections_group.addButton(self.documents_tab)
        self.accounts_sections_group.addButton(self.items_tab)
        self.documents_tab.clicked.connect(lambda: self._switch_accounts_section('documents'))
        self.items_tab.clicked.connect(lambda: self._switch_accounts_section('items'))
        nav_layout.addWidget(self.documents_tab)
        nav_layout.addWidget(self.items_tab)
        nav_layout.addStretch(1)
        layout.addWidget(nav_row)

        self.accounts_stack = QStackedWidget()
        self.accounts_pages: dict[str, int] = {}
        layout.addWidget(self.accounts_stack, 1)
        self._build_documents_page()
        self._build_items_page()
        self._switch_accounts_section('documents')
        self._register_state_page('accounts', page, title='Účty čekají na data', description='Master-detail část je přístupná až po načtení produkčních dokladů a položek.')

    def _build_documents_page(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        card = Card('Filtry a akce', 'Produkční doklady, exporty, tisk a editace.')
        card.setObjectName('documents_tab')
        self.doc_search = QLineEdit(); self.doc_search.setObjectName('doc_search'); self.doc_search.setPlaceholderText('Fulltext čísla dokladu, dodavatele, IČO, DPH')
        self.doc_period = QLineEdit(); self.doc_period.setObjectName('doc_period'); self.doc_period.setPlaceholderText('YYYY-MM')
        self.doc_vat = QComboBox(); self.doc_vat.setObjectName('doc_vat'); self.doc_vat.addItems(['', '21', '15', '12', '10', '0'])
        doc_filter_btn = QPushButton('Filtrovat'); doc_filter_btn.clicked.connect(lambda: self.reload_final_documents(reset_page=True))
        export_csv = QPushButton('Export CSV'); export_csv.clicked.connect(lambda: self.on_export('documents', 'csv'))
        export_xlsx = QPushButton('Export XLSX'); export_xlsx.clicked.connect(lambda: self.on_export('documents', 'xlsx'))
        edit_btn = QPushButton('Upravit finální data'); edit_btn.clicked.connect(self.on_edit_document)
        print_btn = QPushButton('Tisk A4'); print_btn.clicked.connect(self.on_print_document)
        card.body_layout.addWidget(
            self._stacked_toolbar(
                'documents_toolbar',
                [
                    [self.doc_search, self.doc_period, self.doc_vat, doc_filter_btn],
                    [export_csv, export_xlsx, edit_btn, print_btn],
                ],
            )
        )
        layout.addWidget(card)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        layout.addWidget(splitter, 1)
        left = QWidget(); left_layout = QVBoxLayout(left); left_layout.setContentsMargins(0, 0, 0, 0); left_layout.setSpacing(8)
        self.docs_table = DataTable(); self.docs_table.setObjectName('docs_table')
        self.docs_table.set_records([], [('id', 'ID')])
        self.docs_table.selectionChangedSignal.connect(self.on_document_selected)
        self.docs_table.itemDoubleClicked.connect(lambda *_: self.on_edit_document())
        left_layout.addWidget(self.docs_table, 1)
        self.doc_page_info = Pager(); self.doc_page_info.pageDelta.connect(self.change_doc_page)
        left_layout.addWidget(self.doc_page_info)
        splitter.addWidget(left)

        middle = QWidget(); middle_layout = QVBoxLayout(middle); middle_layout.setContentsMargins(0, 0, 0, 0); middle_layout.setSpacing(8)
        preview_controls = QWidget(); pc_layout = QHBoxLayout(preview_controls); pc_layout.setContentsMargins(0, 0, 0, 0); pc_layout.setSpacing(8)
        self.preview_zoom_out = QPushButton('-'); self.preview_zoom_out.setObjectName('preview_zoom_out'); self.preview_zoom_out.clicked.connect(self.on_preview_zoom_out)
        self.preview_zoom = QSpinBox(); self.preview_zoom.setObjectName('preview_zoom'); self.preview_zoom.setRange(20, 400); self.preview_zoom.setValue(100); self.preview_zoom.valueChanged.connect(self.on_preview_zoom)
        self.preview_zoom_in = QPushButton('+'); self.preview_zoom_in.setObjectName('preview_zoom_in'); self.preview_zoom_in.clicked.connect(self.on_preview_zoom_in)
        self.preview_fit_button = QPushButton('Na šířku'); self.preview_fit_button.setObjectName('preview_fit_button'); self.preview_fit_button.clicked.connect(self.on_preview_fit_width)
        self.preview_page = QSpinBox(); self.preview_page.setObjectName('preview_page'); self.preview_page.setRange(1, 1); self.preview_page.valueChanged.connect(self.on_preview_page_changed)
        self.preview_page_info = QLabel('1 / 1'); self.preview_page_info.setObjectName('preview_page_info'); self.preview_page_info.setProperty('muted', True)
        for widget in [self.preview_zoom_out, self.preview_zoom, self.preview_zoom_in, self.preview_fit_button, self.preview_page, self.preview_page_info]:
            pc_layout.addWidget(widget)
        pc_layout.addStretch(1)
        middle_layout.addWidget(preview_controls)
        self.doc_preview = DocumentPreviewWidget(); self.doc_preview.setObjectName('doc_preview')
        middle_layout.addWidget(self.doc_preview, 1)
        self.preview_status = QLabel('Vyberte doklad pro náhled'); self.preview_status.setObjectName('preview_status'); self.preview_status.setProperty('muted', True)
        middle_layout.addWidget(self.preview_status)
        splitter.addWidget(middle)

        right = QWidget(); right_layout = QVBoxLayout(right); right_layout.setContentsMargins(0, 0, 0, 0); right_layout.setSpacing(8)
        self.doc_detail = KeyValueText(); self.doc_detail.setObjectName('doc_detail')
        right_layout.addWidget(self.doc_detail, 1)
        self.doc_items = DataTable(); self.doc_items.setObjectName('doc_items')
        self.doc_items.selectionChangedSignal.connect(self._on_document_item_selected)
        self.doc_items.itemDoubleClicked.connect(lambda *_: self.on_document_item_double_clicked())
        right_layout.addWidget(self.doc_items, 1)
        splitter.addWidget(right)
        self._register_responsive_splitter(splitter, 'documents')

        self.accounts_pages['documents'] = self.accounts_stack.addWidget(page)

    def _build_items_page(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        card = Card('Položky a souhrny', 'Finální položky, exporty a drill-down do dokladu.')
        card.setObjectName('items_tab')
        self.item_search = QLineEdit(); self.item_search.setObjectName('item_search'); self.item_search.setPlaceholderText('Fulltext názvu položky, dokladu, dodavatele')
        self.item_period = QLineEdit(); self.item_period.setObjectName('item_period'); self.item_period.setPlaceholderText('YYYY-MM')
        self.item_vat = QComboBox(); self.item_vat.setObjectName('item_vat'); self.item_vat.addItems(['', '21', '15', '12', '10', '0'])
        filter_btn = QPushButton('Filtrovat'); filter_btn.clicked.connect(lambda: self.reload_items(reset_page=True))
        export_csv = QPushButton('Export CSV'); export_csv.clicked.connect(lambda: self.on_export('items', 'csv'))
        export_xlsx = QPushButton('Export XLSX'); export_xlsx.clicked.connect(lambda: self.on_export('items', 'xlsx'))
        card.body_layout.addWidget(
            self._stacked_toolbar(
                'items_toolbar',
                [
                    [self.item_search, self.item_period, self.item_vat, filter_btn],
                    [export_csv, export_xlsx],
                ],
            )
        )
        self.item_summary = QTextEdit(); self.item_summary.setObjectName('item_summary'); self.item_summary.setReadOnly(True)
        card.body_layout.addWidget(self.item_summary)
        layout.addWidget(card)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        self.items_table = DataTable(); self.items_table.setObjectName('items_table')
        self.items_table.selectionChangedSignal.connect(self.on_item_selected)
        self.items_table.itemDoubleClicked.connect(lambda *_: self.on_item_double_clicked())
        left = QWidget(); left_layout = QVBoxLayout(left); left_layout.setContentsMargins(0, 0, 0, 0); left_layout.setSpacing(8)
        left_layout.addWidget(self.items_table, 1)
        self.item_page_info = Pager(); self.item_page_info.pageDelta.connect(self.change_item_page)
        left_layout.addWidget(self.item_page_info)
        splitter.addWidget(left)
        self.item_detail = KeyValueText(); self.item_detail.setObjectName('item_detail')
        splitter.addWidget(self.item_detail)
        self._register_responsive_splitter(splitter, 'items')
        layout.addWidget(splitter, 1)
        self.accounts_pages['items'] = self.accounts_stack.addWidget(page)

    def _switch_accounts_section(self, key: str) -> None:
        self.documents_tab.setChecked(key == 'documents')
        self.items_tab.setChecked(key == 'items')
        self.accounts_stack.setCurrentIndex(self.accounts_pages[key])

    def _build_suppliers_view(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        card = Card('Správa dodavatelů', 'Master-detail přehled finálních dodavatelů, dokladů a položek.')
        card.setObjectName('suppliers_tab')
        self.supplier_search = QLineEdit(); self.supplier_search.setObjectName('supplier_search'); self.supplier_search.setPlaceholderText('Fulltext IČO, názvu, DIČ nebo adresy')
        filter_btn = QPushButton('Filtrovat'); filter_btn.clicked.connect(lambda: self.reload_suppliers(reset_page=True))
        add_btn = QPushButton('Nový dodavatel'); add_btn.clicked.connect(self.on_add_supplier)
        edit_btn = QPushButton('Upravit'); edit_btn.clicked.connect(self.on_edit_supplier)
        delete_btn = QPushButton('Smazat'); delete_btn.clicked.connect(self.on_delete_supplier)
        merge_btn = QPushButton('Sloučit duplicity'); merge_btn.clicked.connect(self.on_merge_suppliers)
        ares_btn = QPushButton('ARES aktualizace podle IČO'); ares_btn.clicked.connect(self.on_supplier_ares_refresh)
        export_csv = QPushButton('Export CSV'); export_csv.clicked.connect(lambda: self.on_export('suppliers', 'csv'))
        export_xlsx = QPushButton('Export XLSX'); export_xlsx.clicked.connect(lambda: self.on_export('suppliers', 'xlsx'))
        card.body_layout.addWidget(
            self._stacked_toolbar(
                'suppliers_toolbar',
                [
                    [self.supplier_search, filter_btn, add_btn, edit_btn, delete_btn],
                    [merge_btn, ares_btn, export_csv, export_xlsx],
                ],
            )
        )
        layout.addWidget(card)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        layout.addWidget(splitter, 1)
        left = QWidget(); left_layout = QVBoxLayout(left); left_layout.setContentsMargins(0, 0, 0, 0); left_layout.setSpacing(8)
        self.suppliers_table = DataTable(); self.suppliers_table.setObjectName('suppliers_table')
        self.suppliers_table.selectionChangedSignal.connect(self.on_supplier_selected)
        self.suppliers_table.itemDoubleClicked.connect(lambda *_: self.on_edit_supplier())
        left_layout.addWidget(self.suppliers_table, 1)
        self.supplier_page_info = Pager(); self.supplier_page_info.pageDelta.connect(self.change_supplier_page)
        left_layout.addWidget(self.supplier_page_info)
        splitter.addWidget(left)

        middle = QWidget(); middle_layout = QVBoxLayout(middle); middle_layout.setContentsMargins(0, 0, 0, 0); middle_layout.setSpacing(8)
        self.supplier_detail = KeyValueText(); self.supplier_detail.setObjectName('supplier_detail')
        middle_layout.addWidget(self.supplier_detail, 1)
        splitter.addWidget(middle)

        right = QWidget(); right_layout = QVBoxLayout(right); right_layout.setContentsMargins(0, 0, 0, 0); right_layout.setSpacing(8)
        self.supplier_documents = DataTable(); self.supplier_documents.setObjectName('supplier_documents')
        self.supplier_documents.itemDoubleClicked.connect(lambda *_: self.on_supplier_document_double_clicked())
        right_layout.addWidget(self.supplier_documents, 1)
        self.supplier_document_page_info = Pager(); self.supplier_document_page_info.pageDelta.connect(self.change_supplier_document_page)
        right_layout.addWidget(self.supplier_document_page_info)
        self.supplier_items = DataTable(); self.supplier_items.setObjectName('supplier_items')
        self.supplier_items.itemDoubleClicked.connect(lambda *_: self.on_supplier_item_double_clicked())
        right_layout.addWidget(self.supplier_items, 1)
        self.supplier_item_page_info = Pager(); self.supplier_item_page_info.pageDelta.connect(self.change_supplier_item_page)
        right_layout.addWidget(self.supplier_item_page_info)
        splitter.addWidget(right)
        self._register_responsive_splitter(splitter, 'suppliers')
        self._register_state_page('suppliers', page, title='Dodavatelé čekají na data', description='View se aktivuje po připojení projektu a načtení produkčních dodavatelů.')

    def _build_operations_view(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        self.operations_overview = QTextEdit(); self.operations_overview.setObjectName('operations_overview'); self.operations_overview.setReadOnly(True)
        overview_card = Card('Souhrn běhu', 'Logy, poslední běh, korelace a operativní přehled.')
        btn_row = QWidget(); btn_row_layout = QHBoxLayout(btn_row); btn_row_layout.setContentsMargins(0, 0, 0, 0); btn_row_layout.setSpacing(8)
        self.open_runtime_log_button = QPushButton('Otevřít runtime log'); self.open_runtime_log_button.clicked.connect(self.on_open_runtime_log)
        self.open_decisions_log_button = QPushButton('Otevřít decisions log'); self.open_decisions_log_button.clicked.connect(self.on_open_decisions_log)
        self.open_logs_folder_button = QPushButton('Otevřít složku logů'); self.open_logs_folder_button.clicked.connect(self.on_open_logs_folder)
        for widget in [self.open_runtime_log_button, self.open_decisions_log_button, self.open_logs_folder_button]:
            btn_row_layout.addWidget(widget)
        btn_row_layout.addStretch(1)
        overview_card.body_layout.addWidget(self.operations_overview)
        overview_card.body_layout.addWidget(btn_row)
        layout.addWidget(overview_card)

        filters_card = Card('Filtry a akce', 'Workflow / provoz / zpracování — oddělené od business vrstvy.')
        self.operations_title = QLabel('Filtry a akce'); self.operations_title.setObjectName('operations_title')
        filters_card.body_layout.addWidget(self.operations_title)
        self.time_filter = QLineEdit(); self.time_filter.setObjectName('time_filter'); self.time_filter.setPlaceholderText('YYYY-MM-DD')
        self.status_filter = QComboBox(); self.status_filter.setObjectName('status_filter'); self.status_filter.addItems(['', 'processing', 'retry_pending', 'final', 'quarantine', 'unrecognized'])
        self.type_filter = QComboBox(); self.type_filter.setObjectName('type_filter'); self.type_filter.addItems(['', 'automatic', 'automatic_local_retry', 'manual', 'openai'])
        self.result_filter = QComboBox(); self.result_filter.setObjectName('result_filter'); self.result_filter.addItems(['', 'success', 'failed', 'running'])
        self.search_filter = QLineEdit(); self.search_filter.setObjectName('search_filter'); self.search_filter.setPlaceholderText('Fulltext: soubor, stav, chyba')
        filter_btn = QPushButton('Filtrovat'); filter_btn.clicked.connect(lambda: self.reload_operations(reset_page=True))
        bulk_retry_btn = QPushButton('Hromadně spustit další pokus'); bulk_retry_btn.clicked.connect(self.on_bulk_retry)
        bulk_quarantine_btn = QPushButton('Hromadně do karantény'); bulk_quarantine_btn.clicked.connect(self.on_bulk_quarantine)
        bulk_unrecognized_btn = QPushButton('Hromadně vyřadit'); bulk_unrecognized_btn.clicked.connect(self.on_bulk_unrecognized)
        pending_btn = QPushButton('Zpracovat čekající'); pending_btn.clicked.connect(self.on_process_pending)
        ocr_btn = QPushButton('Forenzní OCR detail'); ocr_btn.clicked.connect(self.on_open_ocr_inspector)
        filters_card.body_layout.addWidget(
            self._stacked_toolbar(
                'operations_toolbar',
                [
                    [self.time_filter, self.status_filter, self.type_filter, self.result_filter, self.search_filter, filter_btn],
                    [bulk_retry_btn, bulk_quarantine_btn, bulk_unrecognized_btn, pending_btn, ocr_btn],
                ],
            )
        )
        self.operations_summary = QTextEdit(); self.operations_summary.setObjectName('operations_summary'); self.operations_summary.setReadOnly(True)
        filters_card.body_layout.addWidget(self.operations_summary)
        layout.addWidget(filters_card)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        layout.addWidget(splitter, 1)
        left = QWidget(); left_layout = QVBoxLayout(left); left_layout.setContentsMargins(0, 0, 0, 0); left_layout.setSpacing(8)
        self.attempts_table = DataTable(); self.attempts_table.setObjectName('attempts_table')
        self.attempts_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.attempts_table.selectionChangedSignal.connect(self.on_attempt_selected)
        self.attempts_table.itemDoubleClicked.connect(lambda *_: self._jump_from_operation_to_document())
        left_layout.addWidget(self.attempts_table, 1)
        self.attempt_page_info = Pager(); self.attempt_page_info.pageDelta.connect(self.change_attempt_page)
        left_layout.addWidget(self.attempt_page_info)
        splitter.addWidget(left)

        right = QWidget(); right_layout = QVBoxLayout(right); right_layout.setContentsMargins(0, 0, 0, 0); right_layout.setSpacing(8)
        self.attempt_detail = KeyValueText(); self.attempt_detail.setObjectName('attempt_detail')
        right_layout.addWidget(self.attempt_detail, 1)
        self.document_table = DataTable(); self.document_table.setObjectName('document_table')
        self.document_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        right_layout.addWidget(self.document_table, 1)
        self.processing_document_page_info = Pager(); self.processing_document_page_info.pageDelta.connect(self.change_processing_document_page)
        right_layout.addWidget(self.processing_document_page_info)
        splitter.addWidget(right)
        self._register_responsive_splitter(splitter, 'operations')
        self._register_state_page('operations', page, title='Provozní panel čeká na data', description='Workflow a fronty používají samostatné řízené stavy.')

    def _build_quarantine_view(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        filters_card = Card('Karanténa', 'Doklady s blokací, duplicitou, nesouladem DPH nebo identifikace.')
        self.quarantine_category = QComboBox(); self.quarantine_category.setObjectName('quarantine_category'); self.quarantine_category.addItems(['', 'Duplicita', 'Chybí identifikace', 'Nesedí součty', 'Nesedí DPH', 'Atypická sazba DPH', 'Plátce DPH a na dokladu je neplátce DPH'])
        self.quarantine_reason = QComboBox(); self.quarantine_reason.setObjectName('quarantine_reason'); self.quarantine_reason.addItems(['', 'duplicita', 'chybejici identifikace', 'nesoulad souctu', 'nesoulad dph', 'atypicka sazba dph', 'platce dph', 'neplatce'])
        self.quarantine_search = QLineEdit(); self.quarantine_search.setObjectName('quarantine_search'); self.quarantine_search.setPlaceholderText('Fulltext souboru, dokladu, dodavatele, důvodu')
        filter_btn = QPushButton('Filtrovat'); filter_btn.clicked.connect(lambda: self.reload_quarantine(reset_page=True))
        detail_btn = QPushButton('Otevřít detail'); detail_btn.clicked.connect(self.on_open_quarantine_detail)
        manual_btn = QPushButton('Ruční doplnění'); manual_btn.clicked.connect(self.on_quarantine_manual)
        retry_btn = QPushButton('Opětovný pokus'); retry_btn.clicked.connect(self.on_quarantine_retry)
        self.quarantine_openai_button = QPushButton('OpenAI pokus'); self.quarantine_openai_button.setObjectName('quarantine_openai_button'); self.quarantine_openai_button.clicked.connect(self.on_quarantine_openai)
        bulk_q_btn = QPushButton('Hromadně do karantény'); bulk_q_btn.clicked.connect(self.on_bulk_quarantine)
        bulk_un_btn = QPushButton('Hromadně vyřadit'); bulk_un_btn.clicked.connect(self.on_bulk_unrecognized)
        ocr_btn = QPushButton('Forenzní OCR detail'); ocr_btn.clicked.connect(self.on_open_ocr_inspector)
        filters_card.body_layout.addWidget(
            self._stacked_toolbar(
                'quarantine_toolbar',
                [
                    [self.quarantine_category, self.quarantine_reason, self.quarantine_search, filter_btn, detail_btn, manual_btn],
                    [retry_btn, self.quarantine_openai_button, bulk_q_btn, bulk_un_btn, ocr_btn],
                ],
            )
        )
        layout.addWidget(filters_card)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter, 1)
        left = QWidget(); left_layout = QVBoxLayout(left); left_layout.setContentsMargins(0, 0, 0, 0); left_layout.setSpacing(8)
        self.quarantine_table = DataTable(); self.quarantine_table.setObjectName('quarantine_table')
        self.quarantine_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.quarantine_table.selectionChangedSignal.connect(self.on_quarantine_selected)
        left_layout.addWidget(self.quarantine_table, 1)
        self.quarantine_page_info = Pager(); self.quarantine_page_info.pageDelta.connect(self.change_quarantine_page)
        left_layout.addWidget(self.quarantine_page_info)
        splitter.addWidget(left)
        right = QWidget(); right_layout = QVBoxLayout(right); right_layout.setContentsMargins(0, 0, 0, 0); right_layout.setSpacing(8)
        self.quarantine_detail = KeyValueText(); self.quarantine_detail.setObjectName('quarantine_detail')
        right_layout.addWidget(self.quarantine_detail, 1)
        self.quarantine_preview = DocumentPreviewWidget()
        right_layout.addWidget(self.quarantine_preview, 1)
        splitter.addWidget(right)
        self._register_responsive_splitter(splitter, 'quarantine')
        self._register_state_page('quarantine', page, title='Karanténa čeká na data', description='Review fronta používá validní brand a fallback stavy.')

    def _build_unrecognized_view(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        filters_card = Card('Nerozpoznané doklady', 'Doklady, které zůstaly ve frontě NEROZPOZNANÉ.')
        self.unrecognized_search = QLineEdit(); self.unrecognized_search.setObjectName('unrecognized_search'); self.unrecognized_search.setPlaceholderText('Fulltext souboru, chyby, dokladu')
        filter_btn = QPushButton('Filtrovat'); filter_btn.clicked.connect(lambda: self.reload_unrecognized(reset_page=True))
        manual_btn = QPushButton('Ruční vyplnění'); manual_btn.clicked.connect(self.on_unrecognized_manual)
        self.unrecognized_openai_button = QPushButton('Spustit OpenAI pokus'); self.unrecognized_openai_button.setObjectName('unrecognized_openai_button'); self.unrecognized_openai_button.clicked.connect(self.on_unrecognized_openai)
        retry_btn = QPushButton('Znovu zkusit lokální větev'); retry_btn.clicked.connect(self.on_unrecognized_retry)
        ocr_btn = QPushButton('Forenzní OCR detail'); ocr_btn.clicked.connect(self.on_open_ocr_inspector)
        filters_card.body_layout.addWidget(
            self._stacked_toolbar(
                'unrecognized_toolbar',
                [
                    [self.unrecognized_search, filter_btn, manual_btn],
                    [self.unrecognized_openai_button, retry_btn, ocr_btn],
                ],
            )
        )
        layout.addWidget(filters_card)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter, 1)
        left = QWidget(); left_layout = QVBoxLayout(left); left_layout.setContentsMargins(0, 0, 0, 0); left_layout.setSpacing(8)
        self.unrecognized_table = DataTable(); self.unrecognized_table.setObjectName('unrecognized_table')
        self.unrecognized_table.selectionChangedSignal.connect(self.on_unrecognized_selected)
        left_layout.addWidget(self.unrecognized_table, 1)
        self.unrecognized_page_info = Pager(); self.unrecognized_page_info.pageDelta.connect(self.change_unrecognized_page)
        left_layout.addWidget(self.unrecognized_page_info)
        splitter.addWidget(left)
        right = QWidget(); right_layout = QVBoxLayout(right); right_layout.setContentsMargins(0, 0, 0, 0); right_layout.setSpacing(8)
        self.unrecognized_detail = KeyValueText(); self.unrecognized_detail.setObjectName('unrecognized_detail')
        right_layout.addWidget(self.unrecognized_detail, 1)
        self.unrecognized_preview = DocumentPreviewWidget()
        right_layout.addWidget(self.unrecognized_preview, 1)
        splitter.addWidget(right)
        self._register_responsive_splitter(splitter, 'unrecognized')
        self._register_state_page('unrecognized', page, title='Nerozpoznané čekají na data', description='Fronta nerozpoznaných dokladů používá řízené stavy a brand host.')

    def _build_settings_view(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        self.settings_sections = QTabWidget()
        self.settings_sections.setObjectName('settings_sections')
        layout.addWidget(self.settings_sections, 1)

        general = QWidget(); general_layout = QVBoxLayout(general); general_layout.setContentsMargins(0, 0, 0, 0); general_layout.setSpacing(16)
        top_grid = SummaryGrid(columns=2)
        self._build_project_settings_card()
        self._build_import_settings_card()
        top_grid.set_cards([self.project_card, self.import_card])
        general_layout.addWidget(top_grid)
        mid_grid = SummaryGrid(columns=2)
        self._build_openai_settings_card()
        self._build_workflow_settings_card()
        mid_grid.set_cards([self.openai_card, self.workflow_card])
        general_layout.addWidget(mid_grid)
        bottom_grid = SummaryGrid(columns=2)
        self._build_patterns_card()
        self._build_operations_management_card()
        self._build_accessibility_card()
        bottom_grid.set_cards([self.patterns_card, self.management_card, self.accessibility_card])
        general_layout.addWidget(bottom_grid)
        self.settings_sections.addTab(self._scroll_page(general), 'Nastavení')

        service = QWidget(); service_layout = QVBoxLayout(service); service_layout.setContentsMargins(0, 0, 0, 0); service_layout.setSpacing(16)
        self.service_toggle = QPushButton('Zobrazit pokročilé a servisní nástroje')
        self.service_toggle.setObjectName('service_toggle')
        self.service_toggle.setCheckable(True)
        self.service_toggle.setChecked(True)
        self.service_toggle.clicked.connect(self._toggle_service_content)
        service_layout.addWidget(self.service_toggle)
        self.service_content = QWidget(); self.service_content.setObjectName('service_settings_page')
        service_content_layout = QVBoxLayout(self.service_content); service_content_layout.setContentsMargins(0, 0, 0, 0); service_content_layout.setSpacing(16)
        self.admin_sections = QTabWidget(); self.admin_sections.setObjectName('admin_sections')
        self._build_admin_tab()
        self._build_groups_tab()
        self._build_catalog_tab()
        service_content_layout.addWidget(self.admin_sections)
        service_layout.addWidget(self.service_content, 1)
        self.settings_sections.addTab(service, 'Rozšířené / servisní')
        self._register_state_page('settings', page, title='Nastavení čeká na projekt', description='Nastavení a servisní sekce zobrazují řízené stavy podle KDGS.')

    def _build_project_settings_card(self) -> None:
        self.project_card = Card('Projekt', 'Připojení, vytváření a kontrola integrity projektu.')
        form = QWidget(); layout = QVBoxLayout(form); layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(8)
        self.current_project_field = QLineEdit(); self.current_project_field.setObjectName('current_project_field'); self.current_project_field.setReadOnly(True)
        layout.addWidget(self.current_project_field)
        row = QWidget(); row.setObjectName('project_actions'); row_layout = QHBoxLayout(row); row_layout.setContentsMargins(0, 0, 0, 0); row_layout.setSpacing(8)
        for text, handler in [
            ('Připojit projekt', self.on_connect_project),
            ('Nový projekt', self.on_create_project),
            ('Odpojit projekt', self.on_disconnect_project),
            ('Kontrola integrity', self.on_check_integrity),
        ]:
            button = QPushButton(text)
            button.clicked.connect(handler)
            row_layout.addWidget(button)
        self._register_responsive_stack(row, 'project_actions')
        layout.addWidget(row)
        self.integrity_status = QLabel('Kontrola integrity nebyla spuštěna.'); self.integrity_status.setObjectName('integrity_status'); self.integrity_status.setProperty('statusType', 'neutral')
        layout.addWidget(self.integrity_status)
        self.project_card.body_layout.addWidget(form)

    def _build_import_settings_card(self) -> None:
        self.import_card = Card('Import', 'Vstupní a výstupní adresáře, validace a připravené soubory.')
        grid = QWidget(); grid_layout = QVBoxLayout(grid); grid_layout.setContentsMargins(0, 0, 0, 0); grid_layout.setSpacing(8)
        self.input_dir_field = QLineEdit(); self.input_dir_field.setObjectName('input_dir_field'); self.input_dir_field.setReadOnly(True)
        self.output_dir_field = QLineEdit(); self.output_dir_field.setObjectName('output_dir_field'); self.output_dir_field.setReadOnly(True)
        btn_row = QWidget(); btn_row.setObjectName('import_actions'); btn_row_layout = QHBoxLayout(btn_row); btn_row_layout.setContentsMargins(0, 0, 0, 0); btn_row_layout.setSpacing(8)
        self.btn_input = QPushButton('Vybrat vstupní adresář'); self.btn_input.setObjectName('btn_input'); self.btn_input.clicked.connect(self.on_select_input_dir)
        self.btn_output = QPushButton('Vybrat output adresář'); self.btn_output.setObjectName('btn_output'); self.btn_output.clicked.connect(self.on_select_output_dir)
        btn_row_layout.addWidget(self.btn_input); btn_row_layout.addWidget(self.btn_output)
        self._register_responsive_stack(btn_row, 'import_actions')
        self.import_ready_count = QLabel('Připravených podporovaných souborů: 0'); self.import_ready_count.setObjectName('import_ready_count'); self.import_ready_count.setProperty('muted', True)
        self.output_dir_status = QLabel('Output adresář není vybraný.'); self.output_dir_status.setObjectName('output_dir_status'); self.output_dir_status.setProperty('statusType', 'neutral')
        self.output_behavior_hint = QLabel('Finální data jdou do output rootu. Working data zůstávají v projektové struktuře.'); self.output_behavior_hint.setObjectName('output_behavior_hint'); self.output_behavior_hint.setWordWrap(True); self.output_behavior_hint.setProperty('muted', True)
        for widget in [self.input_dir_field, self.output_dir_field, btn_row, self.import_ready_count, self.output_dir_status, self.output_behavior_hint]:
            grid_layout.addWidget(widget)
        self.import_card.body_layout.addWidget(grid)

    def _build_openai_settings_card(self) -> None:
        self.openai_card = Card('OpenAI', 'Klíč, model a politika použití OpenAI větve.')
        form_wrap = QWidget(); layout = QVBoxLayout(form_wrap); layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(8)
        self.openai_enabled = QCheckBox('Použít OpenAI automaticky')
        self.openai_primary = QCheckBox('Vytěžovat rovnou přes OpenAI')
        self.openai_enabled.setObjectName('openai_enabled')
        self.openai_primary.setObjectName('openai_primary')
        self.openai_key = QLineEdit(); self.openai_key.setObjectName('openai_key'); self.openai_key.setPlaceholderText('API key')
        self.openai_model = QComboBox(); self.openai_model.setObjectName('openai_model'); self.openai_model.setEditable(True)
        self.openai_usage_policy = QComboBox(); self.openai_usage_policy.setObjectName('openai_usage_policy'); self.openai_usage_policy.addItems(['openai_only', 'manual_only'])
        self.openai_key_status = QLabel('API key není uložen.'); self.openai_key_status.setObjectName('openai_key_status'); self.openai_key_status.setProperty('statusType', 'neutral')
        action_row = QWidget(); action_row.setObjectName('openai_actions'); action_layout = QHBoxLayout(action_row); action_layout.setContentsMargins(0, 0, 0, 0); action_layout.setSpacing(8)
        for text, handler in [
            ('Zobrazit API key', self.on_show_openai_key),
            ('Uložit API key', self.on_save_openai_key),
            ('Smazat API key', self.on_delete_openai_key),
            ('Načíst modely', self.on_load_openai_models),
            ('Uložit OpenAI nastavení', self.on_save_openai),
        ]:
            button = QPushButton(text)
            button.clicked.connect(handler)
            action_layout.addWidget(button)
        self._register_responsive_stack(action_row, 'openai_actions')
        for widget in [self.openai_enabled, self.openai_primary, self.openai_key, self.openai_model, self.openai_usage_policy, self.openai_key_status, action_row]:
            layout.addWidget(widget)
        self.openai_card.body_layout.addWidget(form_wrap)

    def _build_workflow_settings_card(self) -> None:
        self.workflow_card = Card('Workflow a validace', 'Povinné toggle a limity, které se propisují do chování aplikace.')
        wrap = QWidget(); layout = QVBoxLayout(wrap); layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(8)
        self.automatic_retry_limit = QSpinBox(); self.automatic_retry_limit.setObjectName('automatic_retry_limit'); self.automatic_retry_limit.setRange(0, 10)
        self.manual_retry_limit = QSpinBox(); self.manual_retry_limit.setObjectName('manual_retry_limit'); self.manual_retry_limit.setRange(0, 10)
        self.openai_retry_limit = QSpinBox(); self.openai_retry_limit.setObjectName('openai_retry_limit'); self.openai_retry_limit.setRange(0, 10)
        self.block_without_ares = QCheckBox('Blokovat postup bez ARES validace'); self.block_without_ares.setObjectName('block_without_ares')
        self.require_valid_input_directory = QCheckBox('Vyžadovat validní vstupní adresář'); self.require_valid_input_directory.setObjectName('require_valid_input_directory')
        self.reduced_motion = QCheckBox('Reduced motion'); self.reduced_motion.setObjectName('reduced_motion')
        self.confirm_destructive_actions = QCheckBox('Potvrzovat destruktivní akce'); self.confirm_destructive_actions.setObjectName('confirm_destructive_actions')
        self.pattern_match_fields = QLineEdit(); self.pattern_match_fields.setObjectName('pattern_match_fields'); self.pattern_match_fields.setPlaceholderText('ico, anchor_text')
        self.q_duplicate = QCheckBox('Karanténa: duplicita'); self.q_duplicate.setObjectName('q_duplicate')
        self.q_missing = QCheckBox('Karanténa: chybějící identifikace'); self.q_missing.setObjectName('q_missing')
        self.allow_manual_openai = QCheckBox('Povolit ruční OpenAI pokus z UI'); self.allow_manual_openai.setObjectName('allow_manual_openai')
        self.save_workflow = QPushButton('Uložit workflow pravidla'); self.save_workflow.setObjectName('save_workflow'); self.save_workflow.clicked.connect(self.on_save_workflow)
        for widget in [
            QLabel('Automatické pokusy'), self.automatic_retry_limit,
            QLabel('Ruční pokusy'), self.manual_retry_limit,
            QLabel('OpenAI pokusy'), self.openai_retry_limit,
            self.block_without_ares, self.require_valid_input_directory, self.reduced_motion,
            self.confirm_destructive_actions, QLabel('Pattern match fields'), self.pattern_match_fields,
            self.q_duplicate, self.q_missing, self.allow_manual_openai, self.save_workflow,
        ]:
            layout.addWidget(widget)
        self.workflow_card.body_layout.addWidget(wrap)

    def _build_patterns_card(self) -> None:
        self.patterns_card = Card('Vzory', 'Přehled vizuálních vzorů používaných při rozpoznání.')
        self.patterns_list = QListWidget(); self.patterns_list.setObjectName('patterns_list')
        self.patterns_card.body_layout.addWidget(self.patterns_list)
        row = QWidget(); row.setObjectName('patterns_actions'); row_layout = QHBoxLayout(row); row_layout.setContentsMargins(0, 0, 0, 0); row_layout.setSpacing(8)
        for text, handler in [
            ('Nový vzor', self.on_add_pattern),
            ('Upravit vzor', self.on_edit_pattern),
            ('Deaktivovat/Aktivovat', self.on_toggle_pattern),
            ('Odstranit vzor', self.on_delete_pattern),
        ]:
            button = QPushButton(text)
            button.clicked.connect(handler)
            row_layout.addWidget(button)
        self._register_responsive_stack(row, 'patterns_actions')
        self.patterns_card.body_layout.addWidget(row)

    def _build_operations_management_card(self) -> None:
        self.management_card = Card('Provozní správa', 'Zálohy, diagnostika a servisní výstupy projektu.')
        row = QWidget(); row.setObjectName('management_actions'); layout = QHBoxLayout(row); layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(8)
        for text, handler in [
            ('Vytvořit zálohu', self.on_create_backup),
            ('Obnovit zálohu', self.on_restore_backup),
            ('Export diagnostiky', self.on_export_diagnostics),
        ]:
            button = QPushButton(text)
            button.clicked.connect(handler)
            layout.addWidget(button)
        self._register_responsive_stack(row, 'management_actions')
        self.management_card.body_layout.addWidget(row)

    def _build_accessibility_card(self) -> None:
        self.accessibility_card = Card('Přístupnost a bezpečnost', 'Duální kontrola kritických toggle i mimo workflow kartu.')
        self.reduced_motion_accessibility = QCheckBox('Reduced motion')
        self.confirm_destructive_actions_accessibility = QCheckBox('Potvrzovat destruktivní akce')
        self.reduced_motion_accessibility.stateChanged.connect(lambda value: self.reduced_motion.setChecked(bool(value)))
        self.confirm_destructive_actions_accessibility.stateChanged.connect(lambda value: self.confirm_destructive_actions.setChecked(bool(value)))
        self.accessibility_card.body_layout.addWidget(self.reduced_motion_accessibility)
        self.accessibility_card.body_layout.addWidget(self.confirm_destructive_actions_accessibility)

    def _build_admin_tab(self) -> None:
        page = QWidget(); layout = QVBoxLayout(page); layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(16)
        admin_card = Card('Administrace projektu', 'Resety jednotlivých vrstev a diagnostická integrita projektu.')
        self.admin_status = KeyValueText(); self.admin_status.setObjectName('admin_status')
        admin_card.body_layout.addWidget(self.admin_status)
        row = QWidget(); row.setObjectName('admin_actions'); row_layout = QHBoxLayout(row); row_layout.setContentsMargins(0, 0, 0, 0); row_layout.setSpacing(8)
        for label, area in [('Reset 1. vrstvy', 'processing'), ('Reset 2. vrstvy', 'production'), ('Reset vzoru', 'patterns')]:
            button = QPushButton(label)
            button.clicked.connect(lambda checked=False, area_name=area: self.on_reset_area(area_name))
            row_layout.addWidget(button)
        self._register_responsive_stack(row, 'admin_actions')
        admin_card.body_layout.addWidget(row)
        layout.addWidget(admin_card)
        self.admin_sections.addTab(page, 'Administrace / integrita / resety / diagnostika')

    def _build_groups_tab(self) -> None:
        page = QWidget(); layout = QVBoxLayout(page); layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(16)
        card = Card('Skupiny položek')
        self.group_list = QListWidget(); self.group_list.setObjectName('group_list')
        card.body_layout.addWidget(self.group_list)
        row = QWidget(); row.setObjectName('group_actions'); row_layout = QHBoxLayout(row); row_layout.setContentsMargins(0, 0, 0, 0); row_layout.setSpacing(8)
        for text, handler in [('Nová skupina', self.on_add_group), ('Upravit skupinu', self.on_edit_group), ('Smazat skupinu', self.on_delete_group)]:
            button = QPushButton(text); button.clicked.connect(handler); row_layout.addWidget(button)
        self._register_responsive_stack(row, 'group_actions')
        card.body_layout.addWidget(row)
        layout.addWidget(card)
        self.admin_sections.addTab(page, 'Skupiny položek')

    def _build_catalog_tab(self) -> None:
        page = QWidget(); layout = QVBoxLayout(page); layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(16)
        card = Card('Číselník položek')
        self.catalog_list = QListWidget(); self.catalog_list.setObjectName('catalog_list')
        card.body_layout.addWidget(self.catalog_list)
        row = QWidget(); row.setObjectName('catalog_actions'); row_layout = QHBoxLayout(row); row_layout.setContentsMargins(0, 0, 0, 0); row_layout.setSpacing(8)
        for text, handler in [('Nová položka', self.on_add_catalog_entry), ('Upravit položku', self.on_edit_catalog_entry), ('Smazat položku', self.on_delete_catalog_entry)]:
            button = QPushButton(text); button.clicked.connect(handler); row_layout.addWidget(button)
        self._register_responsive_stack(row, 'catalog_actions')
        card.body_layout.addWidget(row)
        layout.addWidget(card)
        self.admin_sections.addTab(page, 'Číselník položek')

    def _toggle_service_content(self) -> None:
        self.service_content.setVisible(self.service_toggle.isChecked())

    def _periodic_refresh(self) -> None:
        try:
            self.controller.refresh_status()
            self._refresh_header()
            if self.controller.status.is_connected:
                self._refresh_dashboard()
                self._refresh_operations()
        except Exception:
            return

    def refresh_ui(self) -> None:
        self.controller.refresh_status()
        self._refresh_header()
        self._refresh_dashboard()
        self.reload_expenses()
        self.reload_final_documents(reset_page=False)
        self.reload_items(reset_page=False)
        self.reload_suppliers(reset_page=False)
        self.reload_operations(reset_page=False)
        self.reload_quarantine(reset_page=False)
        self.reload_unrecognized(reset_page=False)
        self.refresh_patterns()
        self.refresh_admin_lists()
        self._refresh_settings()

    def _refresh_header(self) -> None:
        status = self.controller.status
        self.project_label.setText(f'Projekt: {status.name if status.is_connected else "nepřipojen"}')
        self.queue_label.setText(f'Fronta: {status.queue_size}')
        self.last_success.setText(f'Poslední úspěch: {status.last_success}')
        self.last_error.setText(f'Poslední chyba: {status.last_error}')
        report = self.controller.project_integrity_report() if status.is_connected else {'ok': False, 'working_db': '—', 'production_db': '—'}
        working = Path(str(report.get('working_db', '—'))).name if report.get('working_db') not in (None, '—') else '—'
        production = Path(str(report.get('production_db', '—'))).name if report.get('production_db') not in (None, '—') else '—'
        self.working_db_label.setText(f'DB: {working} | {production}')
        input_state = status.input_dir_status or 'Nevybrán'
        self.connection_label.set_tone('ok' if status.is_connected else 'neutral')
        self.process_label.set_tone('warning' if status.processing_running else 'neutral')
        self.input_status.set_tone('ok' if input_state == 'Připraven' else 'warning' if input_state != 'Nevybrán' else 'neutral')
        self.integrity_label.set_tone('ok' if report.get('ok') else 'error' if status.is_connected else 'neutral')
        self.header_summary.setText(self._status_summary_text(status, report))
        self.import_button.setEnabled(status.is_connected and (not self.controller.settings.require_valid_input_directory or input_state == 'Připraven'))
        self.stop_button.setEnabled(status.processing_running)

    def _status_summary_text(self, status, report: dict[str, Any]) -> str:
        if not status.is_connected:
            return 'Připojte projekt a vyberte vstupní adresář.'
        bits = ['Připojeno k databázi']
        if status.processing_running:
            bits.append('běží úloha')
        else:
            bits.append('provoz neprobíhá')
        bits.append(f'vstup: {status.input_dir_status}')
        bits.append('integrita v pořádku' if report.get('ok') else 'integrita vyžaduje zásah')
        return ' | '.join(bits)

    def _refresh_dashboard(self) -> None:
        status = self.controller.status
        if not status.is_connected:
            self._set_page_state('dashboard', StateName.OFFLINE, title='Projekt není připojen', description='Připojte projekt nebo založte nový projektový adresář.')
            self.project_state_body.setText('Projekt není připojen. Připojte projekt nebo založte nový projektový adresář.')
            self.dashboard_primary_card.set_metric('0', meta='Bez připojeného workflow a produkčních dat.')
            self.stats_label.setPlainText('Nejsou dostupná provozní data.')
            self.runtime_label.setPlainText('Program čeká na připojení projektu.')
            self.success_label.setPlainText('Bez dat o pokusech a úspěšnosti.')
            self.production_label.setPlainText('Produkční přehled se zobrazí po promotion finálních dat.')
            return
        self._set_page_state('dashboard', StateName.DEFAULT)
        dashboard = self.controller.dashboard_data()
        operations = self.controller.operational_panel_data()
        progress = dashboard.get('runtime', {}).get('progress', {})
        metrics = dashboard.get('metrics', {})
        self.project_state_body.setText(
            f'Projekt {status.name} je připojen. Fronta {status.queue_size}, vstupní adresář: {status.input_dir_status}. '
            f'Poslední úspěch: {status.last_success}. Poslední chyba: {status.last_error}.'
        )
        self.dashboard_primary_card.set_metric(
            str(operations.get('queue_size', 0)),
            'dokladů',
            f"Karanténa {operations.get('quarantine', 0)} · Nerozpoznané {operations.get('unrecognized', 0)} · Duplicity {operations.get('duplicates', 0)}",
        )
        self.stats_label.setPlainText(
            '\n'.join([
                f"Nezpracované soubory: {dashboard.get('operations', {}).get('unprocessed_files', 0)}",
                f"Zpracované soubory: {dashboard.get('operations', {}).get('processed_files', 0)}",
                f"Karanténa: {dashboard.get('operations', {}).get('quarantine_files', 0)}",
                f"Nerozpoznané: {dashboard.get('operations', {}).get('unrecognized_files', 0)}",
            ])
        )
        self.runtime_label.setPlainText(
            '\n'.join([
                f"Status programu: {dashboard.get('runtime', {}).get('program_status', '—')}",
                f"Aktivní úloha: {progress.get('label') or '—'}",
                f"Průběh: {progress.get('current', 0)} / {progress.get('total', 0) or '—'}",
                f"Poslední běh: {operations.get('last_run', {}).get('label', '—')}",
            ])
        )
        success = dashboard.get('success_breakdown', {})
        self.success_label.setPlainText(
            '\n'.join([
                f"Offline úspěchy: {success.get('offline', 0)}",
                f"API úspěchy: {success.get('api', 0)}",
                f"Ruční úspěchy: {success.get('manual', 0)}",
            ])
        )
        trend_lines = [f"{item.get('period')}: {self._format_money(item.get('amount'))}" for item in dashboard.get('monthly_trend', [])[:6]]
        self.production_label.setPlainText(
            '\n'.join([
                f"Dodavatelé: {metrics.get('suppliers', 0)}",
                f"Finální doklady: {metrics.get('final_documents', 0)}",
                f"Objem: {self._format_money(metrics.get('total_amount', 0))}",
                f"Průměr: {self._format_money(metrics.get('avg_amount', 0))}",
                '',
                'Trend:',
                *trend_lines,
            ]).strip()
        )

    def reload_expenses(self) -> None:
        if not self.controller.status.is_connected:
            self._set_page_state('expenses', StateName.OFFLINE, title='Výdaje nejsou dostupné', description='Připojte projekt, aby bylo možné zobrazit reporting a výdaje.')
            message = 'Připojte projekt, aby bylo možné zobrazit výdaje a reporting.'
            for widget in [self.expense_periods, self.expense_vat, self.expense_suppliers, self.expense_review]:
                widget.setPlainText(message)
            return
        self._set_page_state('expenses', StateName.DEFAULT)
        data = self.controller.expense_data()
        self.expense_periods.setPlainText(
            '\n'.join([
                'Po měsících:',
                *[f"{row.get('period')}: {self._format_money(row.get('amount'))}" for row in data.get('by_month', [])[:10]],
                '',
                'Po kvartálech:',
                *[f"{row.get('period')}: {self._format_money(row.get('amount'))}" for row in data.get('by_quarter', [])[:10]],
                '',
                'Po letech:',
                *[f"{row.get('period')}: {self._format_money(row.get('amount'))}" for row in data.get('by_year', [])[:10]],
            ]).strip()
            or 'Zatím nejsou k dispozici finální data.'
        )
        self.expense_vat.setPlainText(
            '\n'.join([f"DPH {row.get('vat_rate')}: {self._format_money(row.get('amount'))}" for row in data.get('vat_breakdown', [])])
            or 'Zatím nejsou k dispozici položky pro rozpad DPH.'
        )
        self.expense_suppliers.setPlainText(
            '\n'.join([
                'Objem:',
                *[f"{row.get('name')}: {self._format_money(row.get('amount'))}" for row in data.get('top_suppliers_by_amount', [])[:10]],
                '',
                'Počet dokladů:',
                *[f"{row.get('name')}: {row.get('count')}" for row in data.get('top_suppliers_by_count', [])[:10]],
            ]).strip()
            or 'Zatím nejsou dodavatelé s finálními daty.'
        )
        self.expense_review.setPlainText(
            '\n'.join([
                f"Vyžaduje kontrolu: {data.get('requires_review_total', 0)}",
                f"Nerozpoznané: {data.get('unrecognized_count', 0)}",
                '',
                str(data.get('requires_review_definition', '')),
                str(data.get('unrecognized_definition', '')),
            ])
        )

    def on_accounts_filter_apply(self) -> None:
        search = self.accounts_search.text().strip()
        period = self.accounts_period.text().strip()
        vat = self.accounts_vat.currentText().strip()
        scope = self.accounts_scope.currentText()
        if scope in {'Obojí', 'Doklady'}:
            self.doc_search.setText(search)
            self.doc_period.setText(period)
            self.doc_vat.setCurrentText(vat)
            self.reload_final_documents(reset_page=True)
        if scope in {'Obojí', 'Položky'}:
            self.item_search.setText(search)
            self.item_period.setText(period)
            self.item_vat.setCurrentText(vat)
            self.reload_items(reset_page=True)
        self.accounts_summary.setText(f'ÚČTY: filtr „{search or "bez fulltextu"}“, období {period or "vše"}, DPH {vat or "vše"}')

    def on_accounts_filter_reset(self) -> None:
        self.accounts_search.clear()
        self.accounts_period.clear()
        self.accounts_vat.setCurrentIndex(0)
        self.accounts_scope.setCurrentIndex(0)
        for widget in [self.doc_search, self.doc_period, self.item_search, self.item_period]:
            widget.clear()
        self.doc_vat.setCurrentIndex(0)
        self.item_vat.setCurrentIndex(0)
        self.accounts_summary.setText('ÚČTY: bez filtru')
        self.reload_final_documents(reset_page=True)
        self.reload_items(reset_page=True)

    def _document_filters(self) -> dict[str, Any]:
        return {
            'search': self.doc_search.text().strip(),
            'period': self.doc_period.text().strip(),
            'vat_rate': self.doc_vat.currentText().strip(),
            'page': self._page_state['documents'],
            'page_size': self.PAGE_SIZE,
        }

    def _item_filters(self) -> dict[str, Any]:
        return {
            'search': self.item_search.text().strip(),
            'period': self.item_period.text().strip(),
            'vat_rate': self.item_vat.currentText().strip(),
            'page': self._page_state['items'],
            'page_size': self.ITEM_PAGE_SIZE,
        }

    def reload_final_documents(self, *, reset_page: bool = False) -> None:
        if reset_page:
            self._page_state['documents'] = 1
        if not self.controller.status.is_connected:
            self._set_page_state('accounts', StateName.OFFLINE, title='Účty nejsou dostupné', description='Připojte projekt pro zobrazení dokladů a položek.')
            self.docs_table.set_records([], [('id', 'ID')])
            self.doc_page_info.update_state(page=1, page_size=self.PAGE_SIZE, total_count=0)
            self.doc_detail.set_mapping({}, title='Finální doklad')
            self.doc_items.set_records([], [('id', 'ID')])
            self.preview_status.setText('Vyberte doklad pro náhled')
            self.doc_preview.load_document(None)
            return
        payload = self.controller.list_final_documents_page(**self._document_filters())
        self._set_page_state('accounts', StateName.DEFAULT if payload['rows'] or payload['total_count'] else StateName.EMPTY, title='Doklady nemají data', description='Aktuální filtr nevrátil žádný finální doklad.')
        rows = [dict(row) for row in payload['rows']]
        self.docs_table.set_records(rows, [
            ('id', 'ID'), ('issued_at', 'Datum'), ('document_number', 'Číslo dokladu'), ('supplier_name', 'Dodavatel'), ('ico', 'IČO'), ('item_count', 'Položky'), ('total_with_vat', 'Celkem'), ('vat_summary', 'DPH')
        ])
        self.doc_page_info.update_state(page=payload['page'], page_size=payload['page_size'], total_count=payload['total_count'])
        if rows:
            self.on_document_selected()
        else:
            self.doc_detail.set_mapping({}, title='Finální doklad')
            self.doc_items.set_records([], [('id', 'ID')])
            self.preview_status.setText(self._empty_table_message(payload['total_count'], self.doc_search.text().strip() or self.doc_period.text().strip() or self.doc_vat.currentText().strip(), 'Nejsou dostupné finální doklady.'))
            self.doc_preview.load_document(None)

    def on_document_selected(self) -> None:
        record = self.docs_table.current_record()
        if not record:
            return
        self._current_document_id = int(record['id'])
        detail = dict(self.controller.get_final_document_detail(self._current_document_id) or {})
        items = [dict(row) for row in self.controller.reporting_service.list_document_items(self.controller.status.path, self._current_document_id)] if self.controller.status.path else []
        self.doc_detail.set_mapping(detail, title='Detail dokladu')
        self.doc_items.set_records(items, [('id', 'ID'), ('name', 'Položka'), ('quantity', 'Množství'), ('total_price', 'Cena'), ('vat_rate', 'DPH')])
        path = detail.get('original_file_path')
        self.doc_preview.load_document(path, page=int(detail.get('preview_page') or 1))
        self._sync_document_preview_ui()
        self.preview_status.setText(str(path) if path else 'Původní soubor pro náhled není u finálního dokladu dostupný.')

    def _on_document_item_selected(self) -> None:
        return None

    def on_document_item_double_clicked(self) -> None:
        record = self.doc_items.current_record()
        if not record:
            return
        self._switch_accounts_section('items')
        self._page_state['items'] = self.controller.final_item_page_for_id(int(record['id']), page_size=self.ITEM_PAGE_SIZE)
        self.reload_items(reset_page=False)

    def reload_items(self, *, reset_page: bool = False) -> None:
        if reset_page:
            self._page_state['items'] = 1
        if not self.controller.status.is_connected:
            self._set_page_state('accounts', StateName.OFFLINE, title='Účty nejsou dostupné', description='Připojte projekt pro zobrazení finálních položek.')
            self.items_table.set_records([], [('id', 'ID')])
            self.item_detail.set_mapping({}, title='Detail položky')
            self.item_page_info.update_state(page=1, page_size=self.ITEM_PAGE_SIZE, total_count=0)
            self.item_summary.setPlainText('Připojte projekt pro zobrazení finálních položek.')
            return
        payload = self.controller.list_final_items_page(**self._item_filters())
        self._set_page_state('accounts', StateName.DEFAULT if payload['rows'] or payload['total_count'] else StateName.EMPTY, title='Položky nemají data', description='Aktuální filtr nevrátil žádnou finální položku.')
        rows = [dict(row) for row in payload['rows']]
        self.items_table.set_records(rows, [
            ('id', 'ID'), ('document_number', 'Doklad'), ('name', 'Položka'), ('supplier_name', 'Dodavatel'), ('issued_at', 'Datum'), ('quantity', 'Množství'), ('total_price', 'Cena'), ('vat_rate', 'DPH')
        ])
        self.item_page_info.update_state(page=payload['page'], page_size=payload['page_size'], total_count=payload['total_count'])
        self.item_summary.setPlainText(
            f"Počet výsledků: {payload['total_count']}\n"
            f"Filtr: {self.item_search.text().strip() or '—'}\n"
            f"Období: {self.item_period.text().strip() or 'vše'}\n"
            f"DPH: {self.item_vat.currentText().strip() or 'vše'}"
        )
        if rows:
            self.on_item_selected()
        else:
            self.item_detail.set_mapping({}, title='Detail položky')

    def on_item_selected(self) -> None:
        record = self.items_table.current_record()
        if not record:
            return
        self._current_item_id = int(record['id'])
        detail = dict(self.controller.get_final_item_detail(self._current_item_id) or {})
        self.item_detail.set_mapping(detail, title='Detail položky')

    def on_item_double_clicked(self) -> None:
        record = self.items_table.current_record()
        if not record:
            return
        self._switch_accounts_section('documents')
        self._page_state['documents'] = self.controller.final_document_page_for_id(int(record['document_id']), page_size=self.PAGE_SIZE)
        self.reload_final_documents(reset_page=False)

    def change_doc_page(self, delta: int) -> None:
        self._page_state['documents'] = max(1, self._page_state['documents'] + delta)
        self.reload_final_documents(reset_page=False)

    def change_item_page(self, delta: int) -> None:
        self._page_state['items'] = max(1, self._page_state['items'] + delta)
        self.reload_items(reset_page=False)

    def on_preview_zoom(self, value: int) -> None:
        self.doc_preview.set_zoom(value)
        self._sync_document_preview_ui()

    def on_preview_zoom_in(self) -> None:
        self.preview_zoom.setValue(min(400, self.preview_zoom.value() + 20))

    def on_preview_zoom_out(self) -> None:
        self.preview_zoom.setValue(max(20, self.preview_zoom.value() - 20))

    def on_preview_fit_width(self) -> None:
        zoom = self.doc_preview.fit_width()
        self.preview_zoom.blockSignals(True)
        self.preview_zoom.setValue(zoom)
        self.preview_zoom.blockSignals(False)
        self._sync_document_preview_ui()

    def on_preview_page_changed(self, value: int) -> None:
        self.doc_preview.set_page(value)
        self._sync_document_preview_ui()

    def _sync_document_preview_ui(self) -> None:
        page_count = max(1, self.doc_preview.page_count)
        self.preview_page.blockSignals(True)
        self.preview_page.setRange(1, page_count)
        self.preview_page.setValue(max(1, min(self.doc_preview.page, page_count)))
        self.preview_page.blockSignals(False)
        self.preview_page_info.setText(f'{self.doc_preview.page} / {page_count}')

    def reload_suppliers(self, *, reset_page: bool = False) -> None:
        if reset_page:
            self._page_state['suppliers'] = 1
        if not self.controller.status.is_connected:
            self._set_page_state('suppliers', StateName.OFFLINE, title='Dodavatelé nejsou dostupní', description='Připojte projekt pro práci s finálními dodavateli.')
            self.suppliers_table.set_records([], [('id', 'ID')])
            self.supplier_detail.set_mapping({}, title='Detail dodavatele')
            self.supplier_documents.set_records([], [('id', 'ID')])
            self.supplier_items.set_records([], [('id', 'ID')])
            self.supplier_page_info.update_state(page=1, page_size=self.PAGE_SIZE, total_count=0)
            return
        payload = self.controller.list_suppliers_page(search=self.supplier_search.text().strip(), page=self._page_state['suppliers'], page_size=self.PAGE_SIZE)
        self._set_page_state('suppliers', StateName.DEFAULT if payload['rows'] or payload['total_count'] else StateName.EMPTY, title='Dodavatelé nemají data', description='Aktuální filtr nevrátil žádného dodavatele.')
        rows = [dict(row) for row in payload['rows']]
        self.suppliers_table.set_records(rows, [
            ('id', 'ID'), ('ico', 'IČO'), ('name', 'Název'), ('dic', 'DIČ'), ('vat_payer', 'Plátce DPH'), ('address', 'Sídlo'), ('document_count', 'Počet dokladů'), ('financial_volume', 'Finanční objem')
        ])
        self.supplier_page_info.update_state(page=payload['page'], page_size=payload['page_size'], total_count=payload['total_count'])
        if rows:
            self.on_supplier_selected()
        else:
            self.supplier_detail.set_mapping({}, title='Detail dodavatele')
            self.supplier_documents.set_records([], [('id', 'ID')])
            self.supplier_items.set_records([], [('id', 'ID')])

    def on_supplier_selected(self) -> None:
        record = self.suppliers_table.current_record()
        if not record:
            return
        self._current_supplier_id = int(record['id'])
        bundle = self.controller.get_supplier_detail(self._current_supplier_id) or {}
        self.supplier_detail.set_mapping(bundle.get('supplier', {}), title='Detail dodavatele')
        self.reload_supplier_documents(reset_page=True)
        self.reload_supplier_items(reset_page=True)

    def reload_supplier_documents(self, *, reset_page: bool = False) -> None:
        if reset_page:
            self._page_state['supplier_documents'] = 1
        if not self._current_supplier_id or not self.controller.status.is_connected:
            self.supplier_documents.set_records([], [('id', 'ID')])
            self.supplier_document_page_info.update_state(page=1, page_size=self.PAGE_SIZE, total_count=0)
            return
        payload = self.controller.list_supplier_documents_page(self._current_supplier_id, page=self._page_state['supplier_documents'], page_size=self.PAGE_SIZE)
        self.supplier_documents.set_records([dict(row) for row in payload['rows']], [('id', 'ID'), ('issued_at', 'Datum'), ('document_number', 'Číslo dokladu'), ('total_with_vat', 'Celkem')])
        self.supplier_document_page_info.update_state(page=payload['page'], page_size=payload['page_size'], total_count=payload['total_count'])

    def reload_supplier_items(self, *, reset_page: bool = False) -> None:
        if reset_page:
            self._page_state['supplier_items'] = 1
        if not self._current_supplier_id or not self.controller.status.is_connected:
            self.supplier_items.set_records([], [('id', 'ID')])
            self.supplier_item_page_info.update_state(page=1, page_size=self.ITEM_PAGE_SIZE, total_count=0)
            return
        payload = self.controller.list_supplier_items_page(self._current_supplier_id, page=self._page_state['supplier_items'], page_size=self.ITEM_PAGE_SIZE)
        self.supplier_items.set_records([dict(row) for row in payload['rows']], [('id', 'ID'), ('name', 'Položka'), ('quantity', 'Množství'), ('total_price', 'Cena'), ('vat_rate', 'DPH')])
        self.supplier_item_page_info.update_state(page=payload['page'], page_size=payload['page_size'], total_count=payload['total_count'])

    def change_supplier_page(self, delta: int) -> None:
        self._page_state['suppliers'] = max(1, self._page_state['suppliers'] + delta)
        self.reload_suppliers(reset_page=False)

    def change_supplier_document_page(self, delta: int) -> None:
        self._page_state['supplier_documents'] = max(1, self._page_state['supplier_documents'] + delta)
        self.reload_supplier_documents(reset_page=False)

    def change_supplier_item_page(self, delta: int) -> None:
        self._page_state['supplier_items'] = max(1, self._page_state['supplier_items'] + delta)
        self.reload_supplier_items(reset_page=False)

    def reload_operations(self, *, reset_page: bool = False) -> None:
        if reset_page:
            self._page_state['attempts'] = 1
            self._page_state['processing_documents'] = 1
        if not self.controller.status.is_connected:
            self._set_page_state('operations', StateName.OFFLINE, title='Provozní panel není dostupný', description='Připojte projekt pro workflow, pokusy a provozní logy.')
            self.operations_overview.setPlainText('Připojte projekt pro provozní panel.')
            self.operations_summary.setPlainText('Bez připojeného projektu nejsou data fronty ani pokusů.')
            self.attempts_table.set_records([], [('id', 'ID')])
            self.document_table.set_records([], [('file_id', 'File ID')])
            self.attempt_detail.set_mapping({}, title='Detail pokusu')
            self.attempt_page_info.update_state(page=1, page_size=self.PAGE_SIZE, total_count=0)
            self.processing_document_page_info.update_state(page=1, page_size=self.PAGE_SIZE, total_count=0)
            return
        self._set_page_state('operations', StateName.DEFAULT)
        overview = self.controller.operational_panel_data()
        self.operations_overview.setPlainText(
            '\n'.join([
                f"Poslední běh: {overview.get('last_run', {}).get('label', '—')}",
                f"Zdroj: {overview.get('last_run', {}).get('source', '—')}",
                f"Queue size: {overview.get('queue_size', 0)}",
                f"Karanténa: {overview.get('quarantine', 0)} · Nerozpoznané: {overview.get('unrecognized', 0)} · Duplicity: {overview.get('duplicates', 0)}",
            ])
        )
        self.operations_summary.setPlainText(
            '\n'.join([
                f"Queue size: {overview.get('queue_size', 0)}",
                f"Poslední běh: {overview.get('last_run', {}).get('label', '—')}",
                f"Chyby za {overview.get('errors_period', {}).get('days', 7)} dní: {overview.get('errors_period', {}).get('count', 0)}",
            ])
        )
        payload = self.controller.list_attempts_page(
            time_filter=self.time_filter.text().strip(),
            status_filter=self.status_filter.currentText().strip(),
            attempt_type=self.type_filter.currentText().strip(),
            result_filter=self.result_filter.currentText().strip(),
            search=self.search_filter.text().strip(),
            page=self._page_state['attempts'],
            page_size=self.PAGE_SIZE,
        )
        self.attempts_table.set_records([dict(row) for row in payload['rows']], [
            ('result', '●'), ('started_at', 'Čas'), ('original_name', 'Soubor / ID'), ('attempt_type', 'Typ pokusu'), ('file_status', 'Status'), ('reason', 'Důvod'), ('branch', 'Vytěžení'), ('correlation_id', 'CID')
        ], key_field='id')
        self.attempt_page_info.update_state(page=payload['page'], page_size=payload['page_size'], total_count=payload['total_count'])
        docs_payload = self.controller.list_documents_page(page=self._page_state['processing_documents'], page_size=self.PAGE_SIZE)
        self.document_table.set_records([dict(row) for row in docs_payload['rows']], [
            ('file_id', 'File ID'), ('original_name', 'Soubor'), ('final_state', 'Stav'), ('reason', 'Výsledek'), ('reason', 'Důvod')
        ], key_field='file_id')
        self.processing_document_page_info.update_state(page=docs_payload['page'], page_size=docs_payload['page_size'], total_count=docs_payload['total_count'])
        if payload['rows']:
            self.on_attempt_selected()
        else:
            self.attempt_detail.set_mapping({}, title='Detail pokusu')

    def on_attempt_selected(self) -> None:
        record = self.attempts_table.current_record()
        if not record:
            return
        self._current_attempt_record = record
        detail = dict(self.controller.get_attempt_detail(int(record['id'])) or {})
        self.attempt_detail.set_mapping(detail, title='Detail pokusu')

    def change_attempt_page(self, delta: int) -> None:
        self._page_state['attempts'] = max(1, self._page_state['attempts'] + delta)
        self.reload_operations(reset_page=False)

    def change_processing_document_page(self, delta: int) -> None:
        self._page_state['processing_documents'] = max(1, self._page_state['processing_documents'] + delta)
        self.reload_operations(reset_page=False)

    def _jump_from_operation_to_document(self) -> None:
        record = self.attempts_table.current_record()
        if not record:
            return
        status = str(record.get('file_status', '')).lower()
        file_id = int(record.get('file_id', 0) or 0)
        if status == 'quarantine' or status == 'duplicate':
            self._switch_main_view('quarantine')
            self.reload_quarantine(reset_page=True)
        elif status == 'unrecognized':
            self._switch_main_view('unrecognized')
            self.reload_unrecognized(reset_page=True)
        else:
            self._show_info('Skok do detailu', f'Pokus je navázaný na soubor #{file_id}. Provozní detail zůstává v provozním panelu.')

    def reload_quarantine(self, *, reset_page: bool = False) -> None:
        if reset_page:
            self._page_state['quarantine'] = 1
        if not self.controller.status.is_connected:
            self._set_page_state('quarantine', StateName.OFFLINE, title='Karanténa není dostupná', description='Připojte projekt pro práci s review frontou.')
            self.quarantine_table.set_records([], [('file_id', 'File ID')])
            self.quarantine_detail.set_mapping({}, title='Detail karantény')
            self.quarantine_preview.load_document(None)
            self.quarantine_page_info.update_state(page=1, page_size=self.PAGE_SIZE, total_count=0)
            return
        payload = self.controller.list_quarantine_documents_page(
            category=self.quarantine_category.currentText().strip(),
            reason=self.quarantine_reason.currentText().strip(),
            search=self.quarantine_search.text().strip(),
            page=self._page_state['quarantine'],
            page_size=self.PAGE_SIZE,
        )
        self._set_page_state('quarantine', StateName.DEFAULT if payload['rows'] or payload['total_count'] else StateName.EMPTY, title='Karanténa je prázdná', description='Aktuální filtr nevrátil žádný doklad v karanténě.')
        self.quarantine_table.set_records([dict(row) for row in payload['rows']], [
            ('file_id', 'File ID'), ('quarantine_category', 'Kategorie'), ('document_number', 'Doklad'), ('original_name', 'Soubor'), ('supplier_name', 'Dodavatel'), ('ico', 'IČO'), ('total_with_vat', 'Celkem'), ('quarantine_reason', 'Důvod')
        ], key_field='file_id')
        self.quarantine_page_info.update_state(page=payload['page'], page_size=payload['page_size'], total_count=payload['total_count'])
        if payload['rows']:
            self.on_quarantine_selected()
        else:
            self.quarantine_detail.set_mapping({}, title='Detail karantény')
            self.quarantine_preview.load_document(None)

    def on_quarantine_selected(self) -> None:
        record = self.quarantine_table.current_record()
        if not record:
            return
        self._current_quarantine_record = record
        detail = dict(self.controller.get_processing_document_detail(int(record['file_id']), document_id=record.get('document_id')) or {})
        self.quarantine_detail.set_mapping(detail, title='Detail karantény')
        self.quarantine_preview.load_document(detail.get('internal_path') or detail.get('original_file_path'))

    def change_quarantine_page(self, delta: int) -> None:
        self._page_state['quarantine'] = max(1, self._page_state['quarantine'] + delta)
        self.reload_quarantine(reset_page=False)

    def reload_unrecognized(self, *, reset_page: bool = False) -> None:
        if reset_page:
            self._page_state['unrecognized'] = 1
        if not self.controller.status.is_connected:
            self._set_page_state('unrecognized', StateName.OFFLINE, title='Nerozpoznané nejsou dostupné', description='Připojte projekt pro práci s nerozpoznanými doklady.')
            self.unrecognized_table.set_records([], [('file_id', 'File ID')])
            self.unrecognized_detail.set_mapping({}, title='Detail nerozpoznaného')
            self.unrecognized_preview.load_document(None)
            self.unrecognized_page_info.update_state(page=1, page_size=self.PAGE_SIZE, total_count=0)
            return
        payload = self.controller.list_unrecognized_documents_page(search=self.unrecognized_search.text().strip(), page=self._page_state['unrecognized'], page_size=self.PAGE_SIZE)
        self._set_page_state('unrecognized', StateName.DEFAULT if payload['rows'] or payload['total_count'] else StateName.EMPTY, title='Fronta nerozpoznaných je prázdná', description='Aktuální filtr nevrátil žádný nerozpoznaný doklad.')
        self.unrecognized_table.set_records([dict(row) for row in payload['rows']], [
            ('file_id', 'File ID'), ('document_number', 'Doklad'), ('original_name', 'Soubor'), ('supplier_name', 'Dodavatel'), ('ico', 'IČO'), ('quarantine_reason', 'Chyba')
        ], key_field='file_id')
        self.unrecognized_page_info.update_state(page=payload['page'], page_size=payload['page_size'], total_count=payload['total_count'])
        if payload['rows']:
            self.on_unrecognized_selected()
        else:
            self.unrecognized_detail.set_mapping({}, title='Detail nerozpoznaného')
            self.unrecognized_preview.load_document(None)

    def on_unrecognized_selected(self) -> None:
        record = self.unrecognized_table.current_record()
        if not record:
            return
        self._current_unrecognized_record = record
        detail = dict(self.controller.get_processing_document_detail(int(record['file_id']), document_id=record.get('document_id')) or {})
        self.unrecognized_detail.set_mapping(detail, title='Detail nerozpoznaného')
        self.unrecognized_preview.load_document(detail.get('internal_path') or detail.get('original_file_path'))

    def change_unrecognized_page(self, delta: int) -> None:
        self._page_state['unrecognized'] = max(1, self._page_state['unrecognized'] + delta)
        self.reload_unrecognized(reset_page=False)

    def _refresh_settings(self) -> None:
        status = self.controller.status
        self._set_page_state('settings', StateName.DEFAULT if status.is_connected else StateName.OFFLINE, title='Nastavení čeká na projekt', description='Připojte projekt pro servisní a integrační nastavení.')
        self.current_project_field.setText(str(status.path) if status.path else '')
        input_dir = self.controller.project_input_dir()
        self.input_dir_field.setText(str(input_dir) if input_dir else '')
        self.output_dir_field.setText(self.controller.settings.output_directory)
        self.import_ready_count.setText(f'Připravených podporovaných souborů: {self.controller.count_importable_input_files() if status.is_connected else 0}')
        self.output_dir_status.setText('Output adresář připraven' if self.controller.settings.output_directory else 'Output adresář není vybraný.')
        self.output_dir_status.setProperty('statusType', 'ok' if self.controller.settings.output_directory else 'warning')
        self.output_dir_status.style().unpolish(self.output_dir_status); self.output_dir_status.style().polish(self.output_dir_status)
        self.openai_enabled.setChecked(self.controller.settings.openai_enabled)
        self.openai_primary.setChecked(self.controller.settings.openai_usage_policy == 'openai_only' and self.controller.settings.openai_enabled)
        self.openai_key.setText(self.controller.get_openai_key())
        if self.controller.settings.openai_model and self.openai_model.findText(self.controller.settings.openai_model) < 0:
            self.openai_model.addItem(self.controller.settings.openai_model)
        self.openai_model.setCurrentText(self.controller.settings.openai_model)
        self.openai_usage_policy.setCurrentText(self.controller.settings.openai_usage_policy)
        self.openai_key_status.setText('API key je uložen.' if self.controller.has_openai_key() else 'API key není uložen.')
        self.openai_key_status.setProperty('statusType', 'ok' if self.controller.has_openai_key() else 'warning')
        self.openai_key_status.style().unpolish(self.openai_key_status); self.openai_key_status.style().polish(self.openai_key_status)
        self.automatic_retry_limit.setValue(self.controller.settings.automatic_retry_limit)
        self.manual_retry_limit.setValue(self.controller.settings.manual_retry_limit)
        self.openai_retry_limit.setValue(self.controller.settings.openai_retry_limit)
        self.block_without_ares.setChecked(self.controller.settings.block_without_ares)
        self.require_valid_input_directory.setChecked(self.controller.settings.require_valid_input_directory)
        self.reduced_motion.setChecked(self.controller.settings.reduced_motion)
        self.confirm_destructive_actions.setChecked(self.controller.settings.confirm_destructive_actions)
        self.reduced_motion_accessibility.setChecked(self.controller.settings.reduced_motion)
        self.confirm_destructive_actions_accessibility.setChecked(self.controller.settings.confirm_destructive_actions)
        self.pattern_match_fields.setText(', '.join(self.controller.settings.pattern_match_fields))
        self.q_duplicate.setChecked(self.controller.settings.quarantine_duplicate)
        self.q_missing.setChecked(self.controller.settings.quarantine_missing_identification)
        self.allow_manual_openai.setChecked(self.controller.settings.allow_manual_openai_retry)
        report = self.controller.project_integrity_report() if status.is_connected else {'ok': False, 'message': 'Projekt není připojen.'}
        self.integrity_status.setText(report.get('message', 'Kontrola integrity nebyla spuštěna.'))
        self.integrity_status.setProperty('statusType', 'ok' if report.get('ok') else 'warning')
        self.integrity_status.style().unpolish(self.integrity_status); self.integrity_status.style().polish(self.integrity_status)
        self.admin_status.set_mapping(report, title='Administrace projektu')

    def refresh_patterns(self) -> None:
        self.patterns_list.clear()
        if not self.controller.status.is_connected:
            return
        payload = self.controller.list_visual_patterns_page(page=1, page_size=self.PATTERN_PAGE_SIZE)
        for row in payload['rows']:
            record = dict(row)
            item = QListWidgetItem(f"{record.get('name')} · {'aktivní' if record.get('is_active') else 'neaktivní'}")
            item.setData(Qt.ItemDataRole.UserRole, record)
            self.patterns_list.addItem(item)

    def refresh_admin_lists(self) -> None:
        self.group_list.clear(); self.catalog_list.clear()
        if not self.controller.status.is_connected:
            return
        groups = self.controller.list_item_groups_page(page=1, page_size=self.PATTERN_PAGE_SIZE)
        for row in groups['rows']:
            record = dict(row)
            item = QListWidgetItem(f"{record.get('name')} · {'aktivní' if record.get('is_active') else 'neaktivní'}")
            item.setData(Qt.ItemDataRole.UserRole, record)
            self.group_list.addItem(item)
        entries = self.controller.list_item_catalog_page(page=1, page_size=self.PATTERN_PAGE_SIZE)
        for row in entries['rows']:
            record = dict(row)
            item = QListWidgetItem(f"{record.get('name')} · DPH {record.get('vat_rate') or '—'} · {record.get('group_name') or 'bez skupiny'}")
            item.setData(Qt.ItemDataRole.UserRole, record)
            self.catalog_list.addItem(item)

    def on_connect_project(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, 'Připojit projekt')
        if not directory:
            return
        self._invoke(lambda: self.controller.connect_project(directory), success_message='Projekt byl připojen.', refresh=True)

    def on_create_project(self) -> None:
        base_dir = QFileDialog.getExistingDirectory(self, 'Vyberte nadřazený adresář pro nový projekt')
        if not base_dir:
            return
        dialog = ProjectSetupDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = dialog.values()
        target = Path(base_dir) / values['project_name']
        self._invoke(lambda: self.controller.create_project(str(target), values['project_name']), success_message=f'Projekt byl založen v {target}.', refresh=True)

    def on_disconnect_project(self) -> None:
        if not self._confirm_action('Odpojit projekt', 'Opravdu chcete odpojit projekt od aktuální relace?'):
            return
        self._invoke(self.controller.disconnect_project, success_message='Projekt byl odpojen.', refresh=True)

    def on_check_integrity(self) -> None:
        if not self.controller.status.is_connected:
            self._show_info('Integrita projektu', 'Projekt není připojen.')
            return
        report = self.controller.project_integrity_report()
        self.integrity_status.setText(report.get('message', 'Kontrola integrity dokončena.'))
        self.integrity_status.setProperty('statusType', 'ok' if report.get('ok') else 'error')
        self.integrity_status.style().unpolish(self.integrity_status); self.integrity_status.style().polish(self.integrity_status)
        self.admin_status.set_mapping(report, title='Administrace projektu')

    def on_select_input_dir(self) -> None:
        if not self.controller.status.is_connected or not self.controller.status.path:
            self._show_warning('Vstupní adresář', 'Nejprve připojte projekt.')
            return
        directory = QFileDialog.getExistingDirectory(self, 'Vyberte vstupní adresář', str(self.controller.status.path))
        if not directory:
            return
        self._invoke(lambda: self.controller.set_input_dir(directory), success_message='Vstupní adresář byl uložen.', refresh=True)

    def on_select_output_dir(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, 'Vyberte output adresář', self.controller.settings.output_directory or str(Path.home()))
        if not directory:
            return
        self._invoke(lambda: self.controller.set_output_dir(directory), success_message='Output adresář byl uložen.', refresh=True)

    def on_show_openai_key(self) -> None:
        self.openai_key.setEchoMode(QLineEdit.EchoMode.Normal)
        self.openai_key.setFocus()

    def on_save_openai_key(self) -> None:
        self._invoke(lambda: self.controller.save_openai_key(self.openai_key.text().strip()), success_message='API key byl uložen.', refresh=True)

    def on_delete_openai_key(self) -> None:
        if not self._confirm_action('Smazat API key', 'Opravdu chcete smazat uložený API key?'):
            return
        self._invoke(self.controller.delete_openai_key, success_message='API key byl smazán.', refresh=True)

    def on_load_openai_models(self) -> None:
        self._run_background_task(
            title='Načtení modelů OpenAI',
            fn=self.controller.fetch_openai_models,
            success_message='OpenAI modely byly načteny.',
            result_handler=self._apply_openai_models,
        )

    def _apply_openai_models(self, models: list[str]) -> None:
        current = self.openai_model.currentText()
        self.openai_model.clear()
        self.openai_model.addItems(models)
        if current and current in models:
            self.openai_model.setCurrentText(current)
        self._show_info('OpenAI modely', f'Načteno {len(models)} modelů.')

    def on_save_openai(self) -> None:
        enabled = self.openai_enabled.isChecked() or self.openai_primary.isChecked()
        usage_policy = 'openai_only' if self.openai_primary.isChecked() else self.openai_usage_policy.currentText().strip()
        self._invoke(lambda: self.controller.update_openai_settings(enabled, self.openai_model.currentText().strip(), usage_policy), success_message='OpenAI nastavení bylo uloženo.', refresh=True)

    def on_save_workflow(self) -> None:
        values = {
            'automatic_retry_limit': self.automatic_retry_limit.value(),
            'manual_retry_limit': self.manual_retry_limit.value(),
            'openai_retry_limit': self.openai_retry_limit.value(),
            'block_without_ares': self.block_without_ares.isChecked(),
            'require_valid_input_directory': self.require_valid_input_directory.isChecked(),
            'reduced_motion': self.reduced_motion.isChecked(),
            'confirm_destructive_actions': self.confirm_destructive_actions.isChecked(),
            'pattern_match_fields': [item.strip() for item in self.pattern_match_fields.text().split(',') if item.strip()],
            'quarantine_duplicate': self.q_duplicate.isChecked(),
            'quarantine_missing_identification': self.q_missing.isChecked(),
            'allow_manual_openai_retry': self.allow_manual_openai.isChecked(),
        }
        self._invoke(lambda: self.controller.update_workflow_settings(**values), success_message='Workflow pravidla byla uložena.', refresh=True)
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(stylesheet(self.controller.settings))

    def on_add_pattern(self) -> None:
        if not self.controller.status.path:
            self._show_warning('Vzory', 'Nejprve připojte projekt.')
            return
        dialog = PatternEditorDialog(self, project_path=self.controller.status.path)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = dialog.values()
        self._invoke(lambda: self.controller.create_visual_pattern(**values), success_message='Vzor byl vytvořen.', refresh=True)

    def on_edit_pattern(self) -> None:
        item = self.patterns_list.currentItem()
        if item is None or not self.controller.status.path:
            return
        pattern = item.data(Qt.ItemDataRole.UserRole)
        dialog = PatternEditorDialog(self, pattern=pattern, project_path=self.controller.status.path)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = dialog.values()
        self._invoke(lambda: self.controller.update_visual_pattern(int(pattern['id']), **values), success_message='Vzor byl upraven.', refresh=True)

    def on_toggle_pattern(self) -> None:
        item = self.patterns_list.currentItem()
        if item is None or not self.controller.status.path:
            return
        pattern = dict(item.data(Qt.ItemDataRole.UserRole))
        pattern['is_active'] = not bool(pattern.get('is_active'))
        self._invoke(
            lambda: self.controller.update_visual_pattern(
                int(pattern['id']),
                name=pattern.get('name', ''),
                document_path=pattern.get('document_path', ''),
                page_no=int(pattern.get('page_no', 1) or 1),
                recognition_rules=self._json_to_dict(pattern.get('recognition_rules')),
                field_map=self._json_to_dict(pattern.get('field_map')),
                is_active=bool(pattern.get('is_active')),
                preview_state=self._json_to_dict(pattern.get('preview_state')),
            ),
            success_message='Stav vzoru byl přepnut.',
            refresh=True,
        )

    def on_delete_pattern(self) -> None:
        item = self.patterns_list.currentItem()
        if item is None:
            return
        pattern = item.data(Qt.ItemDataRole.UserRole)
        if not self._confirm_action('Odstranit vzor', f'Opravdu chcete odstranit vzor {pattern.get("name")}?'):
            return
        self._invoke(lambda: self.controller.delete_visual_pattern(int(pattern['id'])), success_message='Vzor byl odstraněn.', refresh=True)

    def on_create_backup(self) -> None:
        self._invoke(self.controller.create_backup, success_message='Záloha byla vytvořena.', refresh=True)

    def on_restore_backup(self) -> None:
        backup_path, _ = QFileDialog.getOpenFileName(self, 'Vyberte zálohu', str(self.controller.status.path / 'backups') if self.controller.status.path else str(Path.home()), 'ZIP (*.zip)')
        if not backup_path:
            return
        if not self._confirm_action('Obnovit zálohu', 'Obnovení přepíše aktuální working a production DB. Pokračovat?'):
            return
        self._invoke(lambda: self.controller.restore_backup(backup_path), success_message='Záloha byla obnovena.', refresh=True)

    def on_export_diagnostics(self) -> None:
        destination, _ = QFileDialog.getSaveFileName(self, 'Uložit diagnostiku', str(Path.home() / 'kajovospend_diagnostics.zip'), 'ZIP (*.zip)')
        if not destination:
            return
        self._invoke(lambda: self.controller.export_diagnostics_bundle(destination), success_message='Diagnostika byla exportována.', refresh=False)

    def on_add_group(self) -> None:
        dialog = GroupEditorDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = dialog.values()
        self._invoke(lambda: self.controller.create_item_group(**values), success_message='Skupina byla vytvořena.', refresh=True)

    def on_edit_group(self) -> None:
        item = self.group_list.currentItem()
        if item is None:
            return
        group = item.data(Qt.ItemDataRole.UserRole)
        dialog = GroupEditorDialog(self, group=group)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = dialog.values()
        self._invoke(lambda: self.controller.update_item_group(int(group['id']), **values), success_message='Skupina byla upravena.', refresh=True)

    def on_delete_group(self) -> None:
        item = self.group_list.currentItem()
        if item is None:
            return
        group = item.data(Qt.ItemDataRole.UserRole)
        if not self._confirm_action('Smazat skupinu', f'Opravdu chcete smazat skupinu {group.get("name")}?'):
            return
        self._invoke(lambda: self.controller.delete_item_group(int(group['id'])), success_message='Skupina byla smazána.', refresh=True)

    def on_add_catalog_entry(self) -> None:
        groups = [item.data(Qt.ItemDataRole.UserRole) for item_index in range(self.group_list.count()) if (item := self.group_list.item(item_index))]
        dialog = CatalogEntryDialog(self, groups=groups)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = dialog.values()
        self._invoke(lambda: self.controller.create_item_catalog_entry(**values), success_message='Položka číselníku byla vytvořena.', refresh=True)

    def on_edit_catalog_entry(self) -> None:
        item = self.catalog_list.currentItem()
        if item is None:
            return
        entry = item.data(Qt.ItemDataRole.UserRole)
        groups = [itm.data(Qt.ItemDataRole.UserRole) for index in range(self.group_list.count()) if (itm := self.group_list.item(index))]
        dialog = CatalogEntryDialog(self, entry=entry, groups=groups)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = dialog.values()
        self._invoke(lambda: self.controller.update_item_catalog_entry(int(entry['id']), **values), success_message='Položka číselníku byla upravena.', refresh=True)

    def on_delete_catalog_entry(self) -> None:
        item = self.catalog_list.currentItem()
        if item is None:
            return
        entry = item.data(Qt.ItemDataRole.UserRole)
        if not self._confirm_action('Smazat položku', f'Opravdu chcete smazat položku {entry.get("name")}?'):
            return
        self._invoke(lambda: self.controller.delete_item_catalog_entry(int(entry['id'])), success_message='Položka číselníku byla smazána.', refresh=True)

    def on_reset_area(self, area: str) -> None:
        labels = {'processing': '1. vrstvu', 'production': '2. vrstvu', 'patterns': 'vzory'}
        if not self._confirm_action('Reset datové oblasti', f'Opravdu chcete resetovat {labels.get(area, area)}?'):
            return
        self._invoke(lambda: self.controller.reset_data_area(area), success_message='Reset byl dokončen.', refresh=True)

    def on_add_supplier(self) -> None:
        dialog = SupplierEditorDialog(self, controller=self.controller)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._invoke(lambda: self.controller.create_supplier(**dialog.values()), success_message='Dodavatel byl vytvořen.', refresh=True)

    def on_edit_supplier(self) -> None:
        record = self.suppliers_table.current_record()
        if not record:
            return
        dialog = SupplierEditorDialog(self, supplier=record, controller=self.controller)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._invoke(lambda: self.controller.update_supplier(int(record['id']), **dialog.values()), success_message='Dodavatel byl upraven.', refresh=True)

    def on_delete_supplier(self) -> None:
        record = self.suppliers_table.current_record()
        if not record:
            return
        if not self._confirm_action('Smazat dodavatele', f'Opravdu chcete smazat dodavatele {record.get("name")}?'):
            return
        self._invoke(lambda: self.controller.delete_supplier(int(record['id'])), success_message='Dodavatel byl smazán.', refresh=True)

    def on_merge_suppliers(self) -> None:
        source = self.suppliers_table.current_record()
        if not source:
            return
        targets = [self.suppliers_table.item(row, 2).text() for row in range(self.suppliers_table.rowCount()) if self.suppliers_table.item(row, 0) and int(self.suppliers_table.item(row, 0).text()) != int(source['id'])]
        if not targets:
            self._show_warning('Sloučení dodavatelů', 'Není k dispozici žádný cílový dodavatel.')
            return
        choice, ok = QInputDialog.getItem(self, 'Sloučit duplicity', 'Vyberte cílového dodavatele', targets, 0, False)
        if not ok:
            return
        target_id = None
        for row in range(self.suppliers_table.rowCount()):
            if self.suppliers_table.item(row, 2) and self.suppliers_table.item(row, 2).text() == choice:
                target_id = int(self.suppliers_table.item(row, 0).text())
                break
        if target_id is None:
            return
        if not self._confirm_action('Sloučit dodavatele', f'Přesunout doklady z {source.get("name")} do {choice}?'):
            return
        self._invoke(lambda: self.controller.merge_suppliers(int(source['id']), int(target_id)), success_message='Dodavatelé byli sloučeni.', refresh=True)

    def on_supplier_ares_refresh(self) -> None:
        record = self.suppliers_table.current_record()
        if not record:
            return
        self._invoke(lambda: self.controller.refresh_supplier_from_ares(int(record['id'])), success_message='Dodavatel byl aktualizován podle ARES.', refresh=True)

    def on_edit_document(self) -> None:
        if not self._current_document_id:
            return
        detail = dict(self.controller.get_final_document_detail(self._current_document_id) or {})
        dialog = DocumentEditorDialog(self, document=detail)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._invoke(lambda: self.controller.update_final_document(self._current_document_id or 0, **dialog.values()), success_message='Finální doklad byl upraven.', refresh=True)

    def on_export(self, dataset: str, format: str) -> None:
        suffix = 'csv' if format == 'csv' else 'xlsx'
        destination, _ = QFileDialog.getSaveFileName(self, 'Export datasetu', str(Path.home() / f'kajovospend_{dataset}.{suffix}'), f'{suffix.upper()} (*.{suffix})')
        if not destination:
            return
        filters: dict[str, Any] = {}
        if dataset == 'documents':
            filters = self._document_filters()
        elif dataset == 'items':
            filters = self._item_filters()
        elif dataset == 'suppliers':
            filters = {'search': self.supplier_search.text().strip()}
        self._run_background_task(
            title=f'Export {dataset}',
            fn=self.controller.export_dataset,
            kwargs={'dataset': dataset, 'destination': destination, 'format': format, **filters},
            success_message='Export byl dokončen.',
            refresh=False,
        )

    def on_print_document(self) -> None:
        if not self._current_document_id:
            return
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        dialog = QPrintDialog(printer, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        document = QTextDocument(self.doc_detail.toPlainText())
        document.print(printer)

    def on_import(self) -> None:
        self._run_background_task(title='IMPORT', fn=self.controller.start_import, success_message='Import byl dokončen.', refresh=True, cancellable=True)

    def on_stop(self) -> None:
        self.controller.stop_processing()
        self.refresh_ui()

    def on_process_pending(self) -> None:
        self._run_background_task(title='Zpracovat čekající', fn=self.controller.process_pending_attempts, success_message='Čekající fronta byla zpracována.', refresh=True, cancellable=True)

    def _selected_records(self, table: DataTable) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        seen_rows: set[int] = set()
        for item in table.selectedItems():
            row = item.row()
            if row in seen_rows:
                continue
            seen_rows.add(row)
            record = table.item(row, 0).data(Qt.ItemDataRole.UserRole) if table.item(row, 0) else None
            if record:
                records.append(dict(record))
        return records or ([table.current_record()] if table.current_record() else [])

    def on_bulk_retry(self) -> None:
        records = self._selected_records(self.attempts_table) or self._selected_records(self.document_table)
        file_ids = [int(record['file_id']) for record in records if record and record.get('file_id')]
        if not file_ids:
            self._show_warning('Hromadný retry', 'Vyberte alespoň jeden soubor nebo pokus.')
            return
        self._invoke(lambda: self.controller.queue_manual_retry(file_ids), success_message='Retry byl zařazen.', refresh=True)

    def on_bulk_quarantine(self) -> None:
        records = self._selected_records(self.quarantine_table) or self._selected_records(self.attempts_table) or self._selected_records(self.document_table)
        file_ids = [int(record.get('file_id') or 0) for record in records if record and record.get('file_id')]
        if not file_ids:
            self._show_warning('Hromadná karanténa', 'Vyberte alespoň jeden soubor.')
            return
        self._invoke(lambda: self.controller.bulk_mark(file_ids, 'quarantine', 'Manuální přesun do karantény z UI'), success_message='Doklady byly přesunuty do karantény.', refresh=True)

    def on_bulk_unrecognized(self) -> None:
        records = self._selected_records(self.quarantine_table) or self._selected_records(self.attempts_table) or self._selected_records(self.document_table)
        file_ids = [int(record.get('file_id') or 0) for record in records if record and record.get('file_id')]
        if not file_ids:
            self._show_warning('Hromadné vyřazení', 'Vyberte alespoň jeden soubor.')
            return
        self._invoke(lambda: self.controller.bulk_mark(file_ids, 'unrecognized', 'Manuální přesun do NEROZPOZNANÉ z UI'), success_message='Doklady byly přesunuty do NEROZPOZNANÉ.', refresh=True)

    def on_open_ocr_inspector(self) -> None:
        file_id, document_id = self._selected_processing_ids_forensics()
        if not file_id:
            self._show_warning('OCR inspektor', 'Vyberte doklad nebo pokus s navázaným file ID.')
            return
        detail = dict(self.controller.get_processing_document_detail(file_id, document_id=document_id) or {})
        pages = [dict(row) for row in self.controller.list_document_pages(file_id)]
        blocks = [dict(row) for row in self.controller.list_page_text_blocks(file_id)]
        fields = [dict(row) for row in self.controller.list_field_candidates(int(detail.get('document_id') or document_id or 0))] if detail.get('document_id') or document_id else []
        lines = [dict(row) for row in self.controller.list_line_item_candidates(int(detail.get('document_id') or document_id or 0))] if detail.get('document_id') or document_id else []
        dialog = OcrInspectorDialog(self, detail=detail, pages=pages, blocks=blocks, fields=fields, lines=lines)
        dialog.exec()

    def _selected_processing_ids_forensics(self) -> tuple[int | None, int | None]:
        if self._current_quarantine_record:
            return int(self._current_quarantine_record.get('file_id') or 0), int(self._current_quarantine_record.get('document_id') or 0)
        if self._current_unrecognized_record:
            return int(self._current_unrecognized_record.get('file_id') or 0), int(self._current_unrecognized_record.get('document_id') or 0)
        if self._current_attempt_record:
            return int(self._current_attempt_record.get('file_id') or 0), int(self._current_attempt_record.get('document_id') or 0)
        record = self.document_table.current_record()
        if record:
            return int(record.get('file_id') or 0), int(record.get('document_id') or 0)
        return None, None

    def on_open_quarantine_detail(self) -> None:
        if not self._current_quarantine_record:
            return
        self._show_info('Detail karantény', self.quarantine_detail.toPlainText())

    def on_quarantine_manual(self) -> None:
        if not self._current_quarantine_record:
            return
        file_id = int(self._current_quarantine_record['file_id'])
        document_id = int(self._current_quarantine_record.get('document_id') or 0)
        detail = dict(self.controller.get_processing_document_detail(file_id, document_id=document_id) or {})
        items = [dict(row) for row in self.controller.list_processing_items(document_id)] if document_id else []
        dialog = ManualCompletionDialog(self, detail=detail, items=items)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = dialog.values()
        self._invoke(lambda: self.controller.save_manual_processing_data(file_id, document_id=document_id or None, **values), success_message='Ruční doplnění bylo uloženo.', refresh=True)

    def on_quarantine_retry(self) -> None:
        if not self._current_quarantine_record:
            return
        file_id = int(self._current_quarantine_record['file_id'])
        document_id = int(self._current_quarantine_record.get('document_id') or 0)
        self._invoke(lambda: self.controller.retry_single_file(file_id, document_id=document_id or None, force_openai=False), success_message='Lokální větev byla znovu spuštěna.', refresh=True)

    def on_quarantine_openai(self) -> None:
        if not self._current_quarantine_record:
            return
        file_id = int(self._current_quarantine_record['file_id'])
        document_id = int(self._current_quarantine_record.get('document_id') or 0)
        self._invoke(lambda: self.controller.retry_single_file(file_id, document_id=document_id or None, force_openai=True), success_message='OpenAI pokus byl spuštěn.', refresh=True)

    def on_unrecognized_manual(self) -> None:
        if not self._current_unrecognized_record:
            return
        file_id = int(self._current_unrecognized_record['file_id'])
        document_id = int(self._current_unrecognized_record.get('document_id') or 0)
        detail = dict(self.controller.get_processing_document_detail(file_id, document_id=document_id) or {})
        items = [dict(row) for row in self.controller.list_processing_items(document_id)] if document_id else []
        dialog = ManualCompletionDialog(self, detail=detail, items=items)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = dialog.values()
        self._invoke(lambda: self.controller.save_manual_processing_data(file_id, document_id=document_id or None, **values), success_message='Ruční doplnění bylo uloženo.', refresh=True)

    def on_unrecognized_openai(self) -> None:
        if not self._current_unrecognized_record:
            return
        file_id = int(self._current_unrecognized_record['file_id'])
        document_id = int(self._current_unrecognized_record.get('document_id') or 0)
        self._invoke(lambda: self.controller.retry_single_file(file_id, document_id=document_id or None, force_openai=True), success_message='OpenAI pokus byl spuštěn.', refresh=True)

    def on_unrecognized_retry(self) -> None:
        if not self._current_unrecognized_record:
            return
        file_id = int(self._current_unrecognized_record['file_id'])
        document_id = int(self._current_unrecognized_record.get('document_id') or 0)
        self._invoke(lambda: self.controller.retry_single_file(file_id, document_id=document_id or None, force_openai=False), success_message='Lokální větev byla znovu spuštěna.', refresh=True)

    def on_supplier_document_double_clicked(self) -> None:
        record = self.supplier_documents.current_record()
        if not record:
            return
        self._switch_main_view('accounts')
        self._switch_accounts_section('documents')
        self._page_state['documents'] = self.controller.final_document_page_for_id(int(record['id']), page_size=self.PAGE_SIZE)
        self.reload_final_documents(reset_page=False)

    def on_supplier_item_double_clicked(self) -> None:
        record = self.supplier_items.current_record()
        if not record:
            return
        self._switch_main_view('accounts')
        self._switch_accounts_section('items')
        self._page_state['items'] = self.controller.final_item_page_for_id(int(record['id']), page_size=self.ITEM_PAGE_SIZE)
        self.reload_items(reset_page=False)

    def on_open_runtime_log(self) -> None:
        logs = self.controller.operational_panel_data().get('logs', {}) if self.controller.status.is_connected else {}
        self._open_local_path(logs.get('runtime_log'))

    def on_open_decisions_log(self) -> None:
        logs = self.controller.operational_panel_data().get('logs', {}) if self.controller.status.is_connected else {}
        self._open_local_path(logs.get('decisions_log'))

    def on_open_logs_folder(self) -> None:
        if not self.controller.status.path:
            return
        self._open_local_path(self.controller.status.path / 'logs')

    def _open_local_path(self, value: str | Path | None) -> None:
        if not value:
            self._show_warning('Otevřít cestu', 'Cesta není dostupná.')
            return
        QDesktopServices.openUrl(Path(str(value)).resolve().as_uri())

    def _run_background_task(
        self,
        *,
        title: str,
        fn: Callable[..., Any],
        kwargs: dict[str, Any] | None = None,
        success_message: str = '',
        refresh: bool = False,
        cancellable: bool = False,
    ) -> None:
        dialog = TaskProgressDialog(self, title=title)
        thread = QThread(self)
        worker = BackgroundWorker(fn, kwargs)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(dialog.update_progress)
        worker.error.connect(lambda message: self._show_error(title, message))
        if result_handler is not None:
            worker.result.connect(result_handler)
        elif success_message:
            worker.result.connect(lambda _: self._show_info(title, success_message))
        if cancellable:
            dialog.cancelled.connect(self.controller.stop_processing)
        def finish() -> None:
            dialog.close()
            thread.quit()
            thread.wait(100)
            if thread in self._threads:
                self._threads.remove(thread)
            if refresh:
                self.refresh_ui()
        worker.finished.connect(finish)
        thread.start()
        dialog.show()
        self._threads.append(thread)

    def _invoke(self, fn: Callable[[], Any], *, success_message: str = '', refresh: bool = False) -> None:
        try:
            fn()
        except Exception as exc:
            self._show_error('Operace selhala', str(exc))
            return
        if success_message:
            self._show_info('Operace dokončena', success_message)
        if refresh:
            self.refresh_ui()

    def _confirm_action(self, title: str, text: str) -> bool:
        if not self.controller.settings.confirm_destructive_actions:
            return True
        return ConfirmDialog(title, text, self).exec() == QDialog.DialogCode.Accepted

    def _show_info(self, title: str, text: str) -> None:
        InfoDialog(title, text, self).exec()

    def _show_warning(self, title: str, text: str) -> None:
        WarningDialog(title, text, self).exec()

    def _show_error(self, title: str, text: str) -> None:
        ErrorDialog(title, text, self).exec()

    def _format_money(self, value: Any) -> str:
        try:
            number = float(value or 0)
        except (TypeError, ValueError):
            return '—'
        return f'{number:,.2f} Kč'.replace(',', ' ').replace('.', ',')

    def _empty_table_message(self, total_count: int, filter_text: str, default: str) -> str:
        if total_count == 0 and filter_text:
            return 'Aktuální filtr nevrátil žádný výsledek.'
        return default

    def _json_to_dict(self, value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        try:
            return json.loads(value or '{}')
        except Exception:
            return {}
