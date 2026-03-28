from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QBoxLayout, QSplitter, QWidget

from kajovospend.app.settings import AppSettings
from kajovospend.project.models import ProjectStatus
from kajovospend.ui.design_contract import BreakpointName, breakpoint_for_width
from kajovospend.ui.dialogs.forms import (
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
from kajovospend.ui.main_window import MainWindow


class _ReportingServiceStub:
    @staticmethod
    def list_document_items(project_path: Path | None, document_id: int) -> list[dict]:
        return []


class _ControllerStub:
    def __init__(self) -> None:
        self.settings = AppSettings()
        self.status = ProjectStatus(path=None, is_connected=False)
        self.reporting_service = _ReportingServiceStub()

    def refresh_status(self) -> None:
        return None

    def project_integrity_report(self) -> dict:
        return {'ok': False, 'working_db': '—', 'production_db': '—', 'message': 'Projekt není připojen.'}

    def dashboard_data(self) -> dict:
        return {'runtime': {'progress': {}}, 'metrics': {}, 'operations': {}, 'success_breakdown': {}, 'monthly_trend': []}

    def operational_panel_data(self) -> dict:
        return {'last_run': {}, 'queue_size': 0, 'quarantine': 0, 'unrecognized': 0, 'duplicates': 0, 'errors_period': {'days': 7, 'count': 0}, 'logs': {}}

    def expense_data(self) -> dict:
        return {'by_month': [], 'by_quarter': [], 'by_year': [], 'vat_breakdown': [], 'top_suppliers_by_amount': [], 'top_suppliers_by_count': [], 'requires_review_total': 0, 'unrecognized_count': 0}

    def list_final_documents_page(self, **filters) -> dict:
        return {'rows': [], 'total_count': 0, 'page': 1, 'page_size': filters.get('page_size', 1)}

    def get_final_document_detail(self, document_id: int) -> dict:
        return {}

    def list_final_items_page(self, **filters) -> dict:
        return {'rows': [], 'total_count': 0, 'page': 1, 'page_size': filters.get('page_size', 1)}

    def get_final_item_detail(self, item_id: int) -> dict:
        return {}

    def list_suppliers_page(self, **filters) -> dict:
        return {'rows': [], 'total_count': 0, 'page': 1, 'page_size': filters.get('page_size', 1)}

    def get_supplier_detail(self, supplier_id: int) -> dict:
        return {}

    def list_supplier_documents_page(self, supplier_id: int, **filters) -> dict:
        return {'rows': [], 'total_count': 0, 'page': 1, 'page_size': filters.get('page_size', 1)}

    def list_supplier_items_page(self, supplier_id: int, **filters) -> dict:
        return {'rows': [], 'total_count': 0, 'page': 1, 'page_size': filters.get('page_size', 1)}

    def list_attempts_page(self, **filters) -> dict:
        return {'rows': [], 'total_count': 0, 'page': 1, 'page_size': filters.get('page_size', 1)}

    def list_documents_page(self, **filters) -> dict:
        return {'rows': [], 'total_count': 0, 'page': 1, 'page_size': filters.get('page_size', 1)}

    def get_attempt_detail(self, attempt_id: int) -> dict:
        return {}

    def list_quarantine_documents_page(self, **filters) -> dict:
        return {'rows': [], 'total_count': 0, 'page': 1, 'page_size': filters.get('page_size', 1)}

    def get_processing_document_detail(self, file_id: int, document_id=None) -> dict:
        return {}

    def list_unrecognized_documents_page(self, **filters) -> dict:
        return {'rows': [], 'total_count': 0, 'page': 1, 'page_size': filters.get('page_size', 1)}

    def project_input_dir(self) -> Path | None:
        return None

    def count_importable_input_files(self) -> int:
        return 0

    def get_openai_key(self) -> str:
        return ''

    def has_openai_key(self) -> bool:
        return False

    def fetch_openai_models(self) -> list[str]:
        return ['gpt-4.1-mini', 'gpt-4.1']

    def list_visual_patterns_page(self, **filters) -> dict:
        return {'rows': [], 'total_count': 0, 'page': 1, 'page_size': filters.get('page_size', 1)}

    def list_item_groups_page(self, **filters) -> dict:
        return {'rows': [], 'total_count': 0, 'page': 1, 'page_size': filters.get('page_size', 1)}

    def list_item_catalog_page(self, **filters) -> dict:
        return {'rows': [], 'total_count': 0, 'page': 1, 'page_size': filters.get('page_size', 1)}

    def stop_processing(self) -> None:
        return None


def _app() -> QApplication:
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    app = QApplication.instance()
    return app or QApplication([])


def test_main_window_builds_in_offscreen_mode() -> None:
    _app()
    window = MainWindow(_ControllerStub())
    assert window.centralWidget() is not None
    assert 'dashboard' in window.main_page_indices
    assert 'settings' in window.main_page_indices
    assert 'dashboard' in window._view_hosts
    assert window._view_hosts['dashboard'].current_state.value in {'default', 'offline', 'empty'}


def test_main_window_breakpoint_smoke_and_geometry() -> None:
    app = _app()
    window = MainWindow(_ControllerStub())
    window.show()
    expected = {
        480: Qt.Orientation.Vertical,
        900: Qt.Orientation.Vertical,
        1280: Qt.Orientation.Horizontal,
        1520: Qt.Orientation.Horizontal,
    }
    for width in [480, 900, 1280, 1520]:
        window.resize(width, 900)
        app.processEvents()
        central = window.centralWidget()
        assert central is not None
        assert central.width() <= window.width()
        assert window.main_pages.width() <= central.width()
        assert window._view_hosts['dashboard'].width() <= window.main_pages.width()
        assert window.import_button.isVisible()
        assert window.stop_button.isVisible()
        assert window.header_summary.isVisible()
        for splitter, _layout_key in window._responsive_splitters:
            assert splitter.orientation() == expected[width]
            for child in splitter.findChildren(QSplitter):
                assert child.orientation() == expected[width]


def test_breakpoint_contract_is_used_in_runtime() -> None:
    assert breakpoint_for_width(480) == BreakpointName.SM
    assert breakpoint_for_width(900) == BreakpointName.MD
    assert breakpoint_for_width(1280) == BreakpointName.LG
    assert breakpoint_for_width(1520) == BreakpointName.XL


def test_visible_primary_shell_children_stay_inside_central_widget() -> None:
    app = _app()
    window = MainWindow(_ControllerStub())
    window.resize(480, 900)
    window.show()
    app.processEvents()
    central = window.centralWidget()
    assert central is not None
    central_rect = central.rect()
    for child in central.findChildren(QWidget):
        if not child.isVisible() or child is central:
            continue
        top_left = child.mapTo(central, child.rect().topLeft())
        bottom_right = child.mapTo(central, child.rect().bottomRight())
        assert central_rect.contains(top_left)
        assert central_rect.contains(bottom_right)


def test_compact_toolbars_do_not_escape_card_width() -> None:
    app = _app()
    window = MainWindow(_ControllerStub())
    window.resize(480, 900)
    window.show()
    app.processEvents()
    toolbar_names = [
        'documents_toolbar',
        'items_toolbar',
        'suppliers_toolbar',
        'operations_toolbar',
        'quarantine_toolbar',
        'unrecognized_toolbar',
    ]
    for name in toolbar_names:
        toolbar = window.findChild(QWidget, name)
        assert toolbar is not None, name
        assert toolbar.layout() is not None
        assert toolbar.layout().count() >= 2
        for index in range(toolbar.layout().count()):
            row = toolbar.layout().itemAt(index).widget()
            assert row is not None
            assert row.width() <= toolbar.width()


def test_header_and_service_rows_switch_to_compact_stack_direction() -> None:
    app = _app()
    window = MainWindow(_ControllerStub())
    window.resize(480, 900)
    window.show()
    app.processEvents()
    for widget_name in [
        'project_actions',
        'import_actions',
        'openai_actions',
        'patterns_actions',
        'management_actions',
        'admin_actions',
        'group_actions',
        'catalog_actions',
    ]:
        widget = window.findChild(QWidget, widget_name)
        assert widget is not None, widget_name
        layout = widget.layout()
        assert isinstance(layout, QBoxLayout)
        assert layout.direction() == QBoxLayout.Direction.TopToBottom


def test_summary_grids_collapse_to_single_column_in_compact_width() -> None:
    app = _app()
    window = MainWindow(_ControllerStub())
    window.resize(480, 900)
    window.show()
    app.processEvents()
    candidates = [widget for widget in window.findChildren(QWidget) if widget.__class__.__name__ == 'SummaryGrid']
    assert candidates
    for grid in candidates:
        positions: set[int] = set()
        layout = getattr(grid, 'layout')
        for index in range(layout.count()):
            row, col, row_span, col_span = layout.getItemPosition(index)
            positions.add(col)
        assert positions == {0}


def test_offline_state_is_rendered_for_primary_views() -> None:
    app = _app()
    window = MainWindow(_ControllerStub())
    window.show()
    app.processEvents()
    for key in ['dashboard', 'expenses', 'accounts', 'suppliers', 'operations', 'quarantine', 'unrecognized', 'settings']:
        assert key in window._view_hosts
        assert window._view_hosts[key].current_state.value == 'offline'


def test_key_dialogs_build_with_brand_shell_and_mobile_safe_minimums() -> None:
    app = _app()
    dialogs = [
        ConfirmDialog('Potvrzení', 'Opravdu pokračovat?'),
        InfoDialog('Informace', 'Smoke test overlaye.'),
        WarningDialog('Varování', 'Smoke test overlaye.'),
        ErrorDialog('Chyba', 'Smoke test overlaye.'),
        SupplierEditorDialog(controller=_ControllerStub()),
        DocumentEditorDialog(document={}),
        ManualCompletionDialog(detail={}, items=[]),
        PatternEditorDialog(pattern={}, project_path=None),
        GroupEditorDialog(group={}),
        CatalogEntryDialog(entry={}, groups=[]),
        ProjectSetupDialog(),
        OcrInspectorDialog(detail={}, pages=[], blocks=[], fields=[], lines=[]),
        TaskProgressDialog(),
    ]
    for dialog in dialogs:
        dialog.show()
        app.processEvents()
        assert dialog.property('brandHost') is True
        assert dialog.minimumWidth() <= 480
        assert dialog.minimumHeight() <= 640
        assert dialog.layout() is not None


def test_responsive_dialog_splitters_switch_to_vertical_in_compact_width() -> None:
    app = _app()
    pattern = PatternEditorDialog(pattern={}, project_path=None)
    pattern.resize(480, 760)
    pattern.show()
    app.processEvents()
    assert pattern.shell_splitter.orientation() == Qt.Orientation.Vertical

    inspector = OcrInspectorDialog(detail={}, pages=[], blocks=[], fields=[], lines=[])
    inspector.resize(480, 720)
    inspector.show()
    app.processEvents()
    assert inspector.pages_splitter.orientation() == Qt.Orientation.Vertical
    assert inspector.blocks_splitter.orientation() == Qt.Orientation.Vertical



def test_openai_model_loading_runs_in_background_task(monkeypatch) -> None:
    _app()
    window = MainWindow(_ControllerStub())
    captured: dict[str, object] = {}

    def fake_run_background_task(**kwargs):
        captured.update(kwargs)
        handler = kwargs.get('result_handler')
        if handler is not None:
            handler(['gpt-4.1-mini', 'gpt-4.1'])

    monkeypatch.setattr(window, '_run_background_task', fake_run_background_task)
    monkeypatch.setattr(window, '_show_info', lambda *args, **kwargs: None)
    window.on_load_openai_models()
    assert captured['title'] == 'Načtení modelů OpenAI'
    assert captured['fn'] == window.controller.fetch_openai_models
    assert window.openai_model.count() == 2
