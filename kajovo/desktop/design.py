"""Jednotné nativní prvky; každý formulář se sestavuje právě jednou."""

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

STYLE = """
QWidget { color: #243348; font-size: 13px; }
QMainWindow, QDialog { background: #f3f5f8; }
QFrame#Sidebar { background: #16283d; border: none; }
QFrame#Sidebar QLabel { color: #dfe8f5; }
QFrame#Sidebar QPushButton { background: transparent; color: #dfe8f5; text-align: left; border: none; padding: 12px; border-radius: 7px; }
QFrame#Sidebar QPushButton:checked { background: #305171; color: white; font-weight: 700; }
QFrame#Sidebar QPushButton:hover { background: #24425f; }
QScrollArea#SidebarScroll { background: #16283d; }
QScrollArea#SidebarScroll QScrollBar:vertical { background: #16283d; width: 10px; }
QScrollArea#SidebarScroll QScrollBar::handle:vertical { background: #47617c; min-height: 30px; border-radius: 4px; }
QLabel#Brand { font-size: 25px; font-weight: 700; padding: 16px 10px; }
QLabel#Heading { font-size: 25px; font-weight: 700; color: #172b45; }
QLabel#Subtitle, QLabel#Hint { color: #56677e; }
QLabel#Metric { color: #126856; font-size: 22px; font-weight: 700; }
QFrame#Card { background: #ffffff; border: 1px solid #dce3eb; border-radius: 10px; }
QFrame#Card QLabel { background: transparent; }
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QDateEdit { background: white; border: 1px solid #b5c2d2; border-radius: 5px; padding: 7px; min-height: 20px; selection-background-color: #255d9c; selection-color: white; }
QPlainTextEdit, QTextEdit, QTextBrowser, QListWidget, QTreeWidget, QTableWidget { background: white; border: 1px solid #c7d2df; border-radius: 5px; selection-background-color: #255d9c; selection-color: white; }
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus { border: 2px solid #3279bd; }
QPushButton { background: white; border: 1px solid #bdcad9; border-radius: 6px; padding: 8px 13px; min-height: 20px; }
QPushButton:hover { background: #e8f0f8; border-color: #6f94bd; }
QPushButton#Primary { background: #186d60; color: white; border-color: #186d60; font-weight: 700; }
QPushButton#Primary:hover { background: #105b50; }
QPushButton#Danger { color: #9b293a; border-color: #d8aab2; }
QWidget:disabled { color: #728198; }
QPushButton:disabled { background: #e7ebf0; border-color: #d4dce5; }
QHeaderView::section { background: #edf2f7; color: #304761; border: none; border-bottom: 1px solid #c7d2df; padding: 9px; font-weight: 600; }
QTabWidget::pane { border: 0; }
QTabBar::tab { padding: 10px 14px; background: #e7edf4; border-bottom: 3px solid transparent; }
QTabBar::tab:selected { background: white; border-bottom: 3px solid #186d60; }
QProgressBar { border: 1px solid #c2d0df; border-radius: 5px; min-height: 18px; text-align: center; background: #e8eef5; color: #1c304a; }
QProgressBar::chunk { background: #72b8a9; border-radius: 4px; }
QCheckBox { spacing: 8px; padding: 4px 0; }
QToolTip { background: #172b45; color: white; border: none; padding: 6px; }
"""


def install_ui_style():
    app = QApplication.instance()
    if not app:
        return
    app.setStyle("Fusion")
    palette = QPalette()
    for role, color in (
        (QPalette.Window, "#f3f5f8"),
        (QPalette.Base, "#ffffff"),
        (QPalette.Text, "#243348"),
        (QPalette.WindowText, "#243348"),
        (QPalette.ButtonText, "#243348"),
        (QPalette.Highlight, "#255d9c"),
        (QPalette.HighlightedText, "#ffffff"),
        (QPalette.PlaceholderText, "#56677e"),
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
        layout.addWidget(item)
    return widget


def card(title="", description=""):
    widget = QFrame()
    widget.setObjectName("Card")
    layout = column(widget, 18)
    if title:
        heading = label(title)
        heading.setStyleSheet("font-size: 16px; font-weight: 700;")
        layout.addWidget(heading)
    if description:
        layout.addWidget(label(description, "Hint"))
    return widget, layout


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
    layout.setSpacing(12)
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
