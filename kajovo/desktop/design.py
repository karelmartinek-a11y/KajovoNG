"""Kanonický design systém desktopového UI KájovoNG.

Barvy, rozestupy, role tlačítek a opakované komponenty jsou definovány zde,
aby jednotlivé obrazovky neměly vlastní neslučitelné vizuální kontrakty.
"""

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QFont, QPalette, QColor
from PySide6.QtWidgets import (
    QApplication,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QFormLayout,
    QFrame,
    QLineEdit,
    QComboBox,
    QSpinBox,
    QDoubleSpinBox,
    QPlainTextEdit,
    QTableWidget,
    QHeaderView,
    QStackedWidget,
    QDialog,
)


DESIGN_TOKENS = {
    "color": {
        "canvas": "#F4F7FA",
        "surface": "#FFFFFF",
        "surface_alt": "#EEF3F7",
        "surface_hover": "#E8F1F5",
        "border": "#D7E0E8",
        "border_strong": "#B8C6D3",
        "text": "#1A2B40",
        "text_muted": "#607087",
        "text_soft": "#8190A2",
        "sidebar": "#10263B",
        "sidebar_hover": "#1A3A56",
        "sidebar_active": "#28506F",
        "sidebar_text": "#E8F0F7",
        "primary": "#0F766E",
        "primary_hover": "#0A625B",
        "accent": "#2F6FA7",
        "accent_hover": "#285F91",
        "success": "#147D64",
        "success_bg": "#E6F4EF",
        "warning": "#A96516",
        "warning_bg": "#FFF4DE",
        "danger": "#B4233A",
        "danger_bg": "#FCEBED",
        "info": "#2F6FA7",
        "info_bg": "#EAF2F9",
        "focus": "#3A82C2",
        "selection": "#285F91",
        "disabled": "#7C8998",
    },
    "radius": {"control": 7, "card": 12, "pill": 999},
    "spacing": {"xs": 4, "sm": 8, "md": 12, "lg": 18, "xl": 24},
    "control_height": 34,
}

COMPONENT_CATALOG = {
    "button": ("Primary", "Secondary", "Quiet", "Success", "Warning", "Danger"),
    "label": (
        "Brand",
        "Heading",
        "Subtitle",
        "SectionTitle",
        "CardTitle",
        "Hint",
        "Metric",
        "NavSection",
        "BadgeNeutral",
        "BadgeInfo",
        "BadgeSuccess",
        "BadgeWarning",
        "BadgeDanger",
    ),
    "surface": ("Card", "NoticeInfo", "NoticeSuccess", "NoticeWarning", "NoticeDanger", "EmptyState"),
    "data": ("CopyTable", "QListWidget", "QTreeWidget", "TechnicalViewer"),
    "dialog": ("FitDialog", "DetailDialog", "ProgressDialog", "TaskProgressDialog", "FilePicker"),
}


def _style() -> str:
    c = DESIGN_TOKENS["color"]
    return f"""
QWidget {{ color: {c['text']}; font-size: 13px; }}
QMainWindow, QDialog {{ background: {c['canvas']}; }}
QFrame#Sidebar {{ background: {c['sidebar']}; border: none; }}
QFrame#Sidebar QLabel {{ color: {c['sidebar_text']}; background: transparent; }}
QFrame#Sidebar QLabel#NavSection {{ color: #9DB0C3; font-size: 10px; font-weight: 700; padding: 12px 10px 3px 10px; }}
QFrame#Sidebar QPushButton {{ background: transparent; color: {c['sidebar_text']}; text-align: left; border: none; padding: 11px 12px; border-radius: 7px; }}
QFrame#Sidebar QPushButton:checked {{ background: {c['sidebar_active']}; color: white; font-weight: 700; }}
QFrame#Sidebar QPushButton:hover {{ background: {c['sidebar_hover']}; }}
QScrollArea#SidebarScroll {{ background: {c['sidebar']}; border: none; }}
QScrollArea#SidebarScroll > QWidget > QWidget {{ background: {c['sidebar']}; }}
QScrollArea#SidebarScroll QScrollBar:vertical {{ background: {c['sidebar']}; width: 9px; margin: 0; }}
QScrollArea#SidebarScroll QScrollBar::handle:vertical {{ background: #47647E; min-height: 30px; border-radius: 4px; }}
QScrollArea#SidebarScroll QScrollBar::add-line:vertical, QScrollArea#SidebarScroll QScrollBar::sub-line:vertical {{ height: 0; }}
QLabel#Brand {{ font-size: 24px; font-weight: 800; padding: 14px 10px 2px 10px; }}
QLabel#Heading {{ font-size: 26px; font-weight: 800; color: #14283F; }}
QLabel#Subtitle {{ color: {c['text_muted']}; font-size: 14px; }}
QLabel#SectionTitle {{ font-size: 18px; font-weight: 750; color: #18324D; }}
QLabel#CardTitle {{ font-size: 15px; font-weight: 750; color: #18324D; }}
QLabel#Hint {{ color: {c['text_muted']}; }}
QLabel#Metric {{ color: {c['primary']}; font-size: 22px; font-weight: 800; }}
QLabel#BadgeNeutral, QLabel#BadgeInfo, QLabel#BadgeSuccess, QLabel#BadgeWarning, QLabel#BadgeDanger {{ padding: 3px 8px; border-radius: 9px; font-weight: 700; }}
QLabel#BadgeNeutral {{ background: {c['surface_alt']}; color: {c['text_muted']}; }}
QLabel#BadgeInfo {{ background: {c['info_bg']}; color: {c['info']}; }}
QLabel#BadgeSuccess {{ background: {c['success_bg']}; color: {c['success']}; }}
QLabel#BadgeWarning {{ background: {c['warning_bg']}; color: {c['warning']}; }}
QLabel#BadgeDanger {{ background: {c['danger_bg']}; color: {c['danger']}; }}
QFrame#Card {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 12px; }}
QFrame#Card QLabel {{ background: transparent; }}
QFrame#NoticeInfo, QFrame#NoticeSuccess, QFrame#NoticeWarning, QFrame#NoticeDanger, QFrame#EmptyState {{ border-radius: 9px; padding: 2px; }}
QFrame#NoticeInfo {{ background: {c['info_bg']}; border: 1px solid #C6DBED; }}
QFrame#NoticeSuccess {{ background: {c['success_bg']}; border: 1px solid #B8DDD0; }}
QFrame#NoticeWarning {{ background: {c['warning_bg']}; border: 1px solid #EACB91; }}
QFrame#NoticeDanger {{ background: {c['danger_bg']}; border: 1px solid #E7BCC4; }}
QFrame#EmptyState {{ background: {c['surface_alt']}; border: 1px dashed {c['border_strong']}; }}
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QDateEdit {{ background: {c['surface']}; border: 1px solid {c['border_strong']}; border-radius: 7px; padding: 7px 9px; min-height: 20px; selection-background-color: {c['selection']}; selection-color: white; }}
QPlainTextEdit, QTextEdit, QTextBrowser, QListWidget, QTreeWidget, QTableWidget {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 8px; selection-background-color: {c['selection']}; selection-color: white; alternate-background-color: #F8FAFC; }}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QPlainTextEdit:focus, QTextEdit:focus, QListWidget:focus, QTreeWidget:focus, QTableWidget:focus, QComboBox:focus {{ border: 2px solid {c['focus']}; }}
QLineEdit[readOnly="true"], QPlainTextEdit[readOnly="true"], QTextEdit[readOnly="true"] {{ background: #F8FAFC; }}
QPushButton {{ background: {c['surface']}; color: {c['text']}; border: 1px solid {c['border_strong']}; border-radius: 7px; padding: 8px 13px; min-height: 20px; }}
QPushButton:hover {{ background: {c['surface_hover']}; border-color: #7895AF; }}
QPushButton:pressed {{ padding-top: 9px; padding-bottom: 7px; }}
QPushButton#Primary {{ background: {c['primary']}; color: white; border-color: {c['primary']}; font-weight: 700; }}
QPushButton#Primary:hover {{ background: {c['primary_hover']}; }}
QPushButton#Secondary {{ color: {c['accent']}; border-color: #9AB7CF; font-weight: 700; }}
QPushButton#Secondary:hover {{ background: {c['info_bg']}; border-color: {c['accent']}; }}
QPushButton#Quiet {{ background: transparent; border-color: transparent; color: {c['text_muted']}; }}
QPushButton#Quiet:hover {{ background: {c['surface_alt']}; color: {c['text']}; }}
QPushButton#Success {{ color: {c['success']}; border-color: #9CCDBD; font-weight: 700; }}
QPushButton#Warning {{ color: {c['warning']}; border-color: #E3BE7B; font-weight: 700; }}
QPushButton#Danger {{ color: {c['danger']}; border-color: #DEA6B0; font-weight: 700; }}
QPushButton#Danger:hover {{ background: {c['danger_bg']}; border-color: {c['danger']}; }}
QWidget:disabled {{ color: {c['disabled']}; }}
QPushButton:disabled {{ background: #E9EDF1; border-color: #D8DFE6; color: #8995A3; }}
QHeaderView::section {{ background: #EDF2F6; color: #30475F; border: none; border-bottom: 1px solid {c['border']}; padding: 9px; font-weight: 700; }}
QTableWidget {{ gridline-color: #E7ECF1; }}
QTabWidget::pane {{ border: 0; top: -1px; }}
QTabBar::tab {{ padding: 10px 14px; margin-right: 3px; color: {c['text_muted']}; background: transparent; border: none; border-bottom: 3px solid transparent; }}
QTabBar::tab:hover {{ color: {c['text']}; background: #EEF3F7; }}
QTabBar::tab:selected {{ color: {c['text']}; background: {c['surface']}; border-bottom: 3px solid {c['primary']}; font-weight: 700; }}
QProgressBar {{ border: 1px solid #C5D0DA; border-radius: 6px; min-height: 18px; text-align: center; background: #E7EDF2; color: #1C304A; }}
QProgressBar::chunk {{ background: #66B3A2; border-radius: 5px; }}
QCheckBox, QRadioButton {{ spacing: 8px; padding: 4px 0; }}
QSplitter::handle {{ background: #E3E9EF; }}
QSplitter::handle:horizontal {{ width: 4px; }}
QSplitter::handle:vertical {{ height: 4px; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 1px; }}
QScrollBar::handle:vertical {{ background: #BCC8D3; min-height: 28px; border-radius: 4px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 1px; }}
QScrollBar::handle:horizontal {{ background: #BCC8D3; min-width: 28px; border-radius: 4px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QToolTip {{ background: #172B45; color: white; border: none; padding: 7px; }}
QMenu {{ background: {c['surface']}; border: 1px solid {c['border']}; padding: 5px; }}
QMenu::item {{ padding: 7px 24px 7px 10px; border-radius: 5px; }}
QMenu::item:selected {{ background: {c['surface_alt']}; }}
"""


STYLE = _style()


def install_ui_style():
    app = QApplication.instance()
    if not app:
        return
    app.setStyle("Fusion")
    c = DESIGN_TOKENS["color"]
    palette = QPalette()
    for role, color in (
        (QPalette.Window, c["canvas"]),
        (QPalette.Base, c["surface"]),
        (QPalette.AlternateBase, "#F8FAFC"),
        (QPalette.Text, c["text"]),
        (QPalette.WindowText, c["text"]),
        (QPalette.ButtonText, c["text"]),
        (QPalette.Highlight, c["selection"]),
        (QPalette.HighlightedText, "#FFFFFF"),
        (QPalette.PlaceholderText, c["text_muted"]),
    ):
        palette.setColor(role, QColor(color))
    app.setPalette(palette)
    app.setFont(QFont("Montserrat", 10))
    app.setStyleSheet(STYLE)


def label(text="", role="", wrap=True):
    item = QLabel(text)
    item.setWordWrap(wrap)
    item.setTextFormat(Qt.PlainText)
    item.setTextInteractionFlags(Qt.TextSelectableByMouse)
    item.setObjectName(role)
    return item


def button(text, action=None, role=""):
    item = QPushButton(text)
    item.setObjectName(role)
    if action:
        item.clicked.connect(action)
    return item


def set_button_role(item, role=""):
    item.setObjectName(role)
    item.style().unpolish(item)
    item.style().polish(item)
    return item


def column(parent=None, margins=0):
    layout = QVBoxLayout(parent)
    layout.setContentsMargins(margins, margins, margins, margins)
    layout.setSpacing(12)
    return layout


def row(*items):
    widget = QWidget()
    layout = QHBoxLayout(widget)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(8)
    for item in items:
        if item is not None:
            layout.addWidget(item)
    return widget


def toolbar(*items):
    widget = row(*items)
    widget.setObjectName("Toolbar")
    return widget


def page_header(title, subtitle=""):
    widget = QWidget()
    layout = column(widget)
    layout.setSpacing(3)
    layout.addWidget(label(title, "Heading"))
    if subtitle:
        layout.addWidget(label(subtitle, "Subtitle"))
    return widget


def section_title(title, description=""):
    widget = QWidget()
    layout = column(widget)
    layout.setSpacing(2)
    layout.addWidget(label(title, "SectionTitle"))
    if description:
        layout.addWidget(label(description, "Hint"))
    return widget


def card(title="", description=""):
    widget = QFrame()
    widget.setObjectName("Card")
    layout = column(widget, 18)
    if title:
        layout.addWidget(label(title, "CardTitle"))
    if description:
        layout.addWidget(label(description, "Hint"))
    return widget, layout


def notice(message, kind="info", title=""):
    role = {
        "info": "NoticeInfo",
        "success": "NoticeSuccess",
        "warning": "NoticeWarning",
        "danger": "NoticeDanger",
    }.get(kind, "NoticeInfo")
    widget = QFrame()
    widget.setObjectName(role)
    layout = column(widget, 12)
    if title:
        layout.addWidget(label(title, "CardTitle"))
    layout.addWidget(label(message))
    return widget


def badge(text, kind="neutral"):
    role = {
        "info": "BadgeInfo",
        "success": "BadgeSuccess",
        "warning": "BadgeWarning",
        "danger": "BadgeDanger",
    }.get(kind, "BadgeNeutral")
    item = label(text, role, False)
    item.setTextInteractionFlags(Qt.NoTextInteraction)
    return item


def empty_state(title, description=""):
    widget = QFrame()
    widget.setObjectName("EmptyState")
    layout = column(widget, 18)
    layout.addWidget(label(title, "SectionTitle"))
    if description:
        layout.addWidget(label(description, "Hint"))
    return widget


def metric_card(title, value, hint=""):
    widget, layout = card(title)
    metric = label(str(value), "Metric")
    layout.addWidget(metric)
    if hint:
        layout.addWidget(label(hint, "Hint"))
    return widget, metric


def scroll(widget):
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.NoFrame)
    area.setWidget(widget)
    return area


def form(parent):
    layout = QFormLayout()
    layout.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
    layout.setRowWrapPolicy(QFormLayout.WrapLongRows)
    layout.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    layout.setHorizontalSpacing(16)
    layout.setVerticalSpacing(12)
    parent.addLayout(layout)
    return layout


def text(value="", placeholder=""):
    field = QLineEdit(str(value))
    field.setPlaceholderText(placeholder)
    field.setMinimumWidth(0)
    return field


def combo(values=()):
    field = QComboBox()
    field.addItems(list(values))
    field.setMinimumContentsLength(10)
    field.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
    return field


def number(value=0, minimum=0, maximum=2_000_000, decimal=False):
    field = QDoubleSpinBox() if decimal else QSpinBox()
    field.setRange(minimum, maximum)
    field.setValue(value)
    return field


def editor(value="", readonly=False, height=140):
    field = QPlainTextEdit(value)
    field.setReadOnly(readonly)
    field.setMinimumHeight(height)
    return field


def table(headers):
    widget = CopyTable(0, len(headers))
    widget.setHorizontalHeaderLabels(headers)
    widget.setEditTriggers(QTableWidget.NoEditTriggers)
    widget.setSelectionBehavior(QTableWidget.SelectRows)
    widget.setAlternatingRowColors(True)
    widget.verticalHeader().hide()
    widget.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
    widget.horizontalHeader().setStretchLastSection(True)
    widget.setHorizontalScrollMode(QTableWidget.ScrollPerPixel)
    widget.setVerticalScrollMode(QTableWidget.ScrollPerPixel)
    return widget


class CopyTable(QTableWidget):
    def keyPressEvent(self, event):
        from PySide6.QtGui import QKeySequence

        if event.matches(QKeySequence.Copy):
            copy_selection(self)
            event.accept()
        else:
            super().keyPressEvent(event)


class PageStack(QStackedWidget):
    def minimumSizeHint(self):
        return QSize(100, 100)


def copy_selection(view):
    selected = sorted(
        view.selectionModel().selectedIndexes(), key=lambda index: (index.row(), index.column())
    )
    rows = {}
    for index in selected:
        value = index.data(Qt.DisplayRole)
        rows.setdefault(index.row(), []).append("" if value is None else str(value))
    QApplication.clipboard().setText("\n".join("\t".join(values) for values in rows.values()))


class FitDialog(QDialog):
    def showEvent(self, event):
        parent = self.parentWidget()
        available = parent.window().size() if parent else self.screen().availableGeometry().size()
        self.resize(
            min(self.width(), max(360, available.width() - 16)),
            min(self.height(), max(300, available.height() - 16)),
        )
        super().showEvent(event)
