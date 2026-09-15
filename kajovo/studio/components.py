"""Sdílené komponenty, přístupnost a vizuální pravidla tmavého studia."""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Property, QLibraryInfo, QLocale, QPropertyAnimation, Qt, QTranslator, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPalette, QPen
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QFormLayout, QFrame,
    QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton,
    QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)


COLORS = {
    "canvas": "#0B1220", "surface": "#131F30", "raised": "#1B2C41",
    "text": "#F3F7FC", "muted": "#B8C7D9", "primary": "#5EEAD4",
    "focus": "#7DBBFF", "border": "#465B75", "success": "#79E2B0",
    "warning": "#FFD080", "danger": "#FF9DAB",
}


class CzechTranslator(QTranslator):
    """Doplní chybějící české popisky v distribuovaném katalogu Qt."""

    def translate(self, context, source_text, disambiguation=None, n=-1):
        if context == "QFileDialog":
            override = {"Look in:": "Umístění:", "&Look in:": "Umístění:",
                        "Files of type:": "Typ souborů:", "Files of &type:": "Typ souborů:"}
            if source_text in override:
                return override[source_text]
        return super().translate(context, source_text, disambiguation, n) or None


def install_theme(app: QApplication) -> None:
    QLocale.setDefault(QLocale("cs_CZ"))
    if not hasattr(app, "_studio_translator"):
        translator = CzechTranslator(app)
        if translator.load("qtbase_cs", QLibraryInfo.path(QLibraryInfo.TranslationsPath)):
            app.installTranslator(translator)
        app._studio_translator = translator
    app.setStyle("Fusion")
    palette = app.palette()
    for role, color in ((QPalette.Window, "canvas"), (QPalette.WindowText, "text"),
                        (QPalette.Base, "surface"), (QPalette.AlternateBase, "raised"),
                        (QPalette.Text, "text"), (QPalette.Button, "raised"),
                        (QPalette.ButtonText, "text"), (QPalette.Highlight, "raised"),
                        (QPalette.HighlightedText, "text")):
        palette.setColor(role, QColor(COLORS[color]))
    app.setPalette(palette)
    app.setFont(QFont("Montserrat", 11))
    app.setStyleSheet("""
        QWidget { background: #0B1220; color: #F3F7FC; font-family: Montserrat; font-size: 11pt; }
        QLabel { background: transparent; }
        QToolTip { background: #1B2C41; color: #F3F7FC; border: 1px solid #7DBBFF; padding: 8px; }
        QFrame[card="true"] { background: #131F30; border: 1px solid #465B75; border-radius: 12px; }
        QFrame[card="true"] > QLabel { background: transparent; }
        QLabel[role="heading"] { font-size: 26px; font-weight: 700; }
        QLabel[role="section"] { font-size: 18px; font-weight: 700; }
        QLabel[role="muted"] { color: #B8C7D9; }
        QLabel[role="error"] { color: #FF9DAB; }
        QPushButton { background: #1B2C41; border: 1px solid #465B75; border-radius: 8px; padding: 9px 14px; min-height: 22px; }
        QPushButton:hover { background: #28405B; border-color: #7DBBFF; }
        QPushButton:checked { background: #213F5A; border-color: #7DBBFF; }
        QPushButton[role="primary"] { background: #5EEAD4; color: #0B1220; font-weight: 700; border-color: #5EEAD4; }
        QPushButton[role="danger"] { color: #FF9DAB; border-color: #FF9DAB; }
        QPushButton:disabled { color: #91A2B8; background: #182333; border-color: #34465D; }
        QPushButton:focus, QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus { border: 2px solid #7DBBFF; }
        QLineEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox, QDateEdit { background: #0F1928; border: 1px solid #465B75; border-radius: 6px; padding: 8px; selection-background-color: #285B85; }
        QLineEdit:disabled, QComboBox:disabled { color: #91A2B8; }
        QComboBox QAbstractItemView { background: #131F30; selection-background-color: #285B85; }
        QCheckBox { spacing: 10px; padding: 6px 0; }
        QCheckBox::indicator { width: 20px; height: 20px; border: 1px solid #7DBBFF; border-radius: 4px; background: #0F1928; }
        QCheckBox::indicator:checked { background: #5EEAD4; }
        QTabWidget::pane { border: 0; }
        QTabBar::tab { background: #131F30; padding: 12px 16px; border-bottom: 2px solid #131F30; }
        QTabBar::tab:selected { color: #5EEAD4; border-bottom-color: #5EEAD4; }
        QTabBar::tab:hover { background: #1B2C41; }
        QTableView, QListView, QTreeView { background: #0F1928; alternate-background-color: #131F30; border: 1px solid #465B75; border-radius: 6px; selection-background-color: #285B85; selection-color: #F3F7FC; }
        QAbstractItemView::item:selected { color: #F3F7FC; background: #285B85; }
        QHeaderView::section { background: #1B2C41; padding: 9px; border: 0; border-right: 1px solid #465B75; }
        QProgressBar { background: #0F1928; border: 1px solid #465B75; border-radius: 5px; min-height: 16px; text-align: center; }
        QProgressBar::chunk { background: #5EEAD4; border-radius: 4px; }
        QScrollArea { border: 0; }
        QScrollBar:vertical { background: #0B1220; width: 12px; margin: 0; }
        QScrollBar::handle:vertical { background: #465B75; min-height: 28px; border-radius: 5px; }
        QScrollBar:horizontal { background: #0B1220; height: 12px; margin: 0; }
        QScrollBar::handle:horizontal { background: #465B75; min-width: 28px; border-radius: 5px; }
        QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
        QSplitter::handle { background: #465B75; }
        QMenu { background: #131F30; border: 1px solid #465B75; }
        QMenu::item { padding: 10px 18px; }
        QMenu::item:selected { background: #285B85; }
    """)


class WrappedLabel(QLabel):
    """Výška popisku odpovídá zalomení i vedle nízkého zaškrtávacího pole."""

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.wordWrap():
            # QLabel při heightForWidth respektuje i dříve nastavené minimum.
            # Po rozšíření panelu nesmí zůstat výška úzkého zalomení uzamčena.
            self.setMinimumHeight(0)
            required = max(0, self.heightForWidth(self.width()))
            if self.minimumHeight() != required:
                self.setMinimumHeight(required)


def caption(value: str, role: str = "") -> QLabel:
    widget = WrappedLabel(value)
    widget.setWordWrap(True)
    widget.setTextFormat(Qt.PlainText)
    widget.setProperty("role", role)
    widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
    return widget


def action(identifier: str, title: str, callback, role: str = "") -> QPushButton:
    widget = QPushButton(title)
    widget.setObjectName(identifier)
    widget.setAccessibleName(title)
    widget.setProperty("role", role)
    widget.setCursor(Qt.PointingHandCursor)
    widget.clicked.connect(callback)
    return widget


def vertical(widget=None, margin=16) -> QVBoxLayout:
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(margin, margin, margin, margin)
    layout.setSpacing(12)
    return layout


def actions(*widgets) -> QWidget:
    box = QWidget()
    layout = QHBoxLayout(box)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(8)
    for widget in widgets:
        layout.addWidget(widget)
    layout.addStretch()
    scroller = QScrollArea()
    scroller.setWidgetResizable(True)
    scroller.setWidget(box)
    scroller.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    scroller.setFixedHeight(60)
    return scroller


def panel(title: str) -> tuple[QFrame, QVBoxLayout]:
    box = QFrame()
    box.setProperty("card", True)
    layout = vertical(box, 20)
    layout.addWidget(caption(title, "section"))
    return box, layout


def scroll(widget: QWidget) -> QScrollArea:
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setWidget(widget)
    return area


class Form(QWidget):
    """Formulář se při nedostatku místa zalomí pod popisky."""

    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.fields = {}
        self.body = QFormLayout(self)
        self.body.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self.body.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.body.setSpacing(14)

    def add(self, key, title, widget):
        widget.setObjectName(key)
        widget.setAccessibleName(title)
        self.fields[key] = widget
        label = caption(title)
        label.setBuddy(widget)
        self.body.addRow(label, widget)
        for signal in ("textChanged", "currentIndexChanged", "valueChanged", "toggled"):
            if hasattr(widget, signal):
                getattr(widget, signal).connect(self.changed)
                break
        return widget

    def text(self, key, title, value="", secret=False):
        widget = QLineEdit(str(value))
        if secret:
            widget.setEchoMode(QLineEdit.Password)
        return self.add(key, title, widget)

    def choice(self, key, title, values, current=None):
        widget = QComboBox()
        for item in values:
            label, data = item if isinstance(item, tuple) else (item, item)
            widget.addItem(label, data)
        if current is not None:
            index = widget.findData(current)
            if index < 0 and current:
                widget.addItem(str(current) + " · nedostupné", current)
                index = widget.count() - 1
            if index >= 0:
                widget.setCurrentIndex(index)
        return self.add(key, title, widget)

    def check(self, key, title, value=False):
        widget = QCheckBox()
        widget.setChecked(value)
        return self.add(key, title, widget)


class PathInput(QLineEdit):
    """Přijímá pouze existující místní cesty očekávaného druhu."""

    paths_dropped = Signal(list)

    def __init__(self, directories=False, parent=None):
        super().__init__(parent)
        self.directories = directories
        self.setAcceptDrops(True)

    def accepted_paths(self, mime):
        paths = [Path(url.toLocalFile()) for url in mime.urls() if url.isLocalFile()]
        return [str(p) for p in paths if p.is_dir() if self.directories] if self.directories else [str(p) for p in paths if p.is_file()]

    def dragEnterEvent(self, event):
        if self.accepted_paths(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        paths = self.accepted_paths(event.mimeData())
        if paths:
            self.setText(paths[0])
            self.paths_dropped.emit(paths)
            event.acceptProposedAction()


class BranchMark(QWidget):
    """Stavový motiv větví; animace nevyjadřuje procenta ani aktivitu serveru."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(42, 42)
        self.setMaximumSize(64, 64)
        self._intensity = 1.0
        self.running = False
        self.reduced_motion = False
        self.animation = QPropertyAnimation(self, b"intensity", self)
        self.animation.setDuration(1500)
        self.animation.setStartValue(0.45)
        self.animation.setKeyValueAt(0.5, 1.0)
        self.animation.setEndValue(0.45)
        self.animation.setLoopCount(-1)
        self.setAccessibleName("Stav práce")

    def get_intensity(self):
        return self._intensity

    def set_intensity(self, value):
        self._intensity = value
        self.update()

    intensity = Property(float, get_intensity, set_intensity)

    def set_running(self, active, reduced_motion=False):
        self.running = active
        self.reduced_motion = reduced_motion
        self.animation.stop()
        self.set_intensity(1.0)
        if active and not reduced_motion and self.isVisible():
            self.animation.start()

    def showEvent(self, event):
        super().showEvent(event)
        self.set_running(self.running, self.reduced_motion)

    def hideEvent(self, event):
        self.animation.stop()
        super().hideEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.scale(self.width() / 64, self.height() / 64)
        color = QColor(COLORS["primary"])
        color.setAlphaF(self._intensity)
        painter.setPen(QPen(color, 5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        painter.drawLine(16, 12, 16, 52)
        painter.drawLine(16, 32, 48, 12)
        painter.drawLine(16, 32, 48, 52)
        painter.setBrush(color)
        painter.drawEllipse(11, 27, 10, 10)


class DetailDialog(QDialog):
    def __init__(self, title, message, parent=None, details=None, confirm=False):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setObjectName("dialog.detail")
        self.resize(650, 420)
        content = QWidget()
        body = vertical(content)
        body.addWidget(caption(title, "section"))
        body.addWidget(caption(message))
        if details is not None:
            text = details if isinstance(details, str) else json.dumps(details, ensure_ascii=False, indent=2, default=str)
            view = QPlainTextEdit(text)
            view.setReadOnly(True)
            view.setAccessibleName("Technické podrobnosti")
            view.setMinimumHeight(180)
            view.hide()
            toggle = action("dialog.details", "Technické podrobnosti", lambda: view.setVisible(not view.isVisible()))
            body.addWidget(toggle)
            body.addWidget(view)
        body.addStretch()
        root = vertical(self)
        root.addWidget(scroll(content), 1)
        buttons = [action("dialog.close", "Zrušit" if confirm else "Zavřít", self.reject)]
        if confirm:
            buttons.append(action("dialog.confirm", "Potvrdit", self.accept, "primary"))
        root.addWidget(actions(*buttons))

    def showEvent(self, event):
        screen = self.screen().availableGeometry()
        self.resize(min(self.width(), screen.width()), min(self.height(), screen.height()))
        super().showEvent(event)


def show_error(parent, error):
    from kajovo.core.user_errors import describe_error

    report = describe_error(error)
    dialog = DetailDialog("Operace vyžaduje pozornost", report.message, parent,
                          report.detail + "\n\nDalší krok: " + report.next_step)
    dialog.exec()


def confirm(parent, title, message):
    return DetailDialog(title, message, parent, confirm=True).exec() == QDialog.Accepted
