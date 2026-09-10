"""Adaptivní řádky a omezení dialogů dostupnou plochou monitoru."""

from PySide6.QtCore import QEvent, QObject, QRect, QSize, Qt
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
    QTabWidget,
    QSplitter,
    QTableWidget,
    QListWidget,
    QTreeWidget,
)


class FlowLayout(QLayout):
    """Řádek ovládacích prvků, který se při nedostatku místa zalomí."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._items = []
        self.setContentsMargins(0, 0, 0, 0)
        self.setSpacing(6)

    def addItem(self, item):
        self._items.append(item)

    def addWidget(self, widget, stretch=0, alignment=None):
        super().addWidget(widget)

    def addLayout(self, layout, stretch=0):
        self.addChildLayout(layout)
        self.addItem(layout)

    def addStretch(self, stretch=0):
        pass

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < self.count() else None

    def takeAt(self, index):
        return self._items.pop(index) if 0 <= index < self.count() else None

    def expandingDirections(self):
        return Qt.Orientation.Horizontal

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._arrange(QRect(0, 0, width, 0), False)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._arrange(rect, True)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return size + QSize(margins.left() + margins.right(), margins.top() + margins.bottom())

    def _arrange(self, rect, apply):
        m = self.contentsMargins()
        area = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x, y, height = area.x(), area.y(), 0
        for item in self._items:
            if item.isEmpty():
                continue
            size = item.sizeHint().expandedTo(item.minimumSize())
            width = min(size.width(), max(0, area.width()))
            if x > area.x() and x + width > area.right() + 1:
                x, y, height = area.x(), y + height + self.spacing(), 0
            h = item.heightForWidth(width) if item.hasHeightForWidth() else size.height()
            if apply:
                item.setGeometry(QRect(x, y, width, h))
            x += width + self.spacing()
            height = max(height, h)
        return y + height - rect.y() + m.bottom()


class ContentTabs(QTabWidget):
    """Obsah záložky se roztahuje v přidělené ploše, nikoli podle editoru."""

    def heightForWidth(self, width):
        return self.minimumSizeHint().height()

    def sizeHint(self):
        return self.minimumSizeHint()


class ContentSplitter(QSplitter):
    def sizeHint(self):
        return self.minimumSizeHint()


class AdaptivePanels(QWidget):
    """Na široké ploše panely vedle sebe, na úzké přepínatelné sekce."""

    def __init__(self, titles):
        super().__init__()
        self.titles, self.panels = titles, []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.split = QSplitter()
        self.tabs = ContentTabs()
        self.tabs.hide()
        layout.addWidget(self.split)
        layout.addWidget(self.tabs)
        self.compact = False

    def addWidget(self, widget):
        self.panels.append(widget)
        self.split.addWidget(widget)

    def setStretchFactor(self, index, stretch):
        self.split.setStretchFactor(index, stretch)

    def setSizes(self, sizes):
        self.split.setSizes(sizes)

    def setChildrenCollapsible(self, value):
        self.split.setChildrenCollapsible(value)

    def heightForWidth(self, width):
        return self.minimumSizeHint().height()

    def sizeHint(self):
        return self.minimumSizeHint()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        compact = self.width() < 1200
        if compact == self.compact:
            return
        self.compact = compact
        for index, panel in enumerate(self.panels):
            if compact:
                self.tabs.addTab(panel, self.titles[index])
            else:
                self.split.addWidget(panel)
                panel.show()
        self.tabs.setVisible(compact)
        self.split.setVisible(not compact)


def scroll_content(widget):
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QScrollArea.NoFrame)
    scroll.setWidget(widget)
    scroll.setMinimumSize(0, 0)
    return scroll


def fit_dialog(dialog):
    """Ponechá patičku na obrazovce; dlouhý obsah posouvá samostatně."""
    screen = dialog.screen() or QApplication.primaryScreen()
    if screen is None:
        return
    area = screen.availableGeometry().adjusted(12, 12, -12, -48)
    layout = dialog.layout()
    preferred_height = dialog.height()
    if layout:
        preferred_height = max(
            preferred_height, layout.totalHeightForWidth(min(dialog.width(), area.width()))
        )
    if isinstance(layout, QVBoxLayout) and not getattr(dialog, "_fit_prepared", False):
        dialog._fit_prepared = True
        for label in dialog.findChildren(QLabel):
            if not label.pixmap() or label.pixmap().isNull():
                label.setWordWrap(True)
                label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        if layout.count() > 1:
            body = QWidget()
            body_layout = QVBoxLayout(body)
            body_layout.setContentsMargins(0, 0, 0, 0)
            while layout.count() > 1:
                item = layout.takeAt(0)
                if item.widget():
                    body_layout.addWidget(item.widget())
                elif item.layout():
                    child_layout = item.layout()
                    child_layout.setParent(None)
                    body_layout.addLayout(child_layout)
                else:
                    body_layout.addItem(item)
            scroll = scroll_content(body)
            layout.insertWidget(0, scroll, 1)
    if layout:
        layout.setSizeConstraint(QLayout.SetNoConstraint)
    dialog.setMinimumSize(0, 0)
    dialog.setMaximumSize(area.size())
    dialog.resize(min(dialog.width(), area.width()), min(preferred_height, area.height()))
    pos = dialog.pos()
    dialog.move(
        max(area.left(), min(pos.x(), area.right() - dialog.width() + 1)),
        max(area.top(), min(pos.y(), area.bottom() - dialog.height() + 1)),
    )


class _DialogFilter(QObject):
    def eventFilter(self, obj, event):
        if isinstance(obj, (QTableWidget, QListWidget, QTreeWidget)) and event.type() == QEvent.Show and not obj.property("copy_ready"):
            from PySide6.QtGui import QAction, QKeySequence
            obj.setProperty("copy_ready", True)
            obj.setMouseTracking(True)
            obj.entered.connect(lambda index, view=obj: view.setToolTip(display_value(index)))
            action = QAction("Kopírovat výběr", obj)
            action.setShortcut(QKeySequence.Copy)
            action.setShortcutContext(Qt.WidgetWithChildrenShortcut)
            action.triggered.connect(lambda checked=False, view=obj: copy_selection(view))
            obj.addAction(action)
            if obj.contextMenuPolicy() == Qt.DefaultContextMenu:
                obj.setContextMenuPolicy(Qt.ActionsContextMenu)
        if (
            isinstance(obj, QLabel)
            and event.type() == QEvent.Resize
            and obj.wordWrap()
            and isinstance(obj.window(), QDialog)
        ):
            needed = obj.heightForWidth(event.size().width())
            if needed > 0 and obj.minimumHeight() != needed:
                obj.setMinimumHeight(needed)
        if isinstance(obj, QDialog) and event.type() == QEvent.Show:
            fit_dialog(obj)
        if isinstance(obj, QDialog) and event.type() == QEvent.ScreenChangeInternal:
            fit_dialog(obj)
        return False


def copy_selection(view):
    rows = {}
    for index in sorted(view.selectionModel().selectedIndexes(), key=lambda item: (item.row(), item.column())):
        rows.setdefault((index.parent(), index.row()), []).append(display_value(index))
    if rows:
        QApplication.clipboard().setText("\n".join("\t".join(values) for values in rows.values()))


def display_value(index):
    value = index.data()
    return "" if value is None else str(value)


def install_ui_style():
    from .theme import DARK_STYLESHEET
    from PySide6.QtGui import QColor, QPalette

    app = QApplication.instance()
    if app is None or getattr(app, "_kajovo_ui_filter", None):
        return
    app.setStyleSheet(DARK_STYLESHEET)
    palette = app.palette()
    for role, color in ((QPalette.Window, "#111111"), (QPalette.Base, "#1b1b1b"),
                        (QPalette.Text, "#EAEAEA"), (QPalette.WindowText, "#EAEAEA"),
                        (QPalette.Button, "#1b1b1b"), (QPalette.ButtonText, "#EAEAEA"),
                        (QPalette.PlaceholderText, "#AFAFAF"), (QPalette.Highlight, "#2699E8"),
                        (QPalette.HighlightedText, "#0b0b0b")):
        palette.setColor(role, QColor(color))
    app.setPalette(palette)
    app._kajovo_ui_filter = _DialogFilter(app)
    app.installEventFilter(app._kajovo_ui_filter)
