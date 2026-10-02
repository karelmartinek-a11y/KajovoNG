"""Přístupné rychlé otevření sekcí, nástrojů a již spuštěné práce."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import math
import unicodedata

from PySide6.QtCore import QEvent, QPointF, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QTextLayout, QTextOption
from PySide6.QtWidgets import (
    QAbstractItemView, QBoxLayout, QDialog, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QPushButton, QStyledItemDelegate, QVBoxLayout,
)


@dataclass(frozen=True)
class PaletteCommand:
    """Popis místní navigace; samotná paleta žádnou operaci nevykonává."""

    key: str
    title: str
    description: str
    keywords: tuple[str, ...] = ()
    category: str = "Sekce"


def _normalized(text: str) -> str:
    characters = unicodedata.normalize("NFKD", text.casefold())
    return " ".join("".join(character for character in characters
                            if not unicodedata.combining(character)).split())


class _CommandDelegate(QStyledItemDelegate):
    """Výška každého výsledku odpovídá skutečně zalomenému textu."""

    def __init__(self, listing: QListWidget):
        super().__init__(listing)
        self.listing = listing
        self.title_font = QFont("Montserrat")
        self.title_font.setPixelSize(16)
        self.title_font.setWeight(QFont.Bold)
        self.description_font = QFont("Montserrat")
        self.description_font.setPixelSize(14)

    @staticmethod
    def _layout(text: str, font: QFont, width: int) -> tuple[QTextLayout, int]:
        layout = QTextLayout(text, font)
        option = QTextOption()
        option.setWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        layout.setTextOption(option)
        layout.beginLayout()
        height = 0.0
        while True:
            line = layout.createLine()
            if not line.isValid():
                break
            line.setLineWidth(width)
            line.setPosition(QPointF(0, height))
            height += line.height()
        layout.endLayout()
        return layout, math.ceil(height)

    def _layouts(self, command: PaletteCommand, width: int):
        text_width = max(24, width - 100)
        return (self._layout(command.title, self.title_font, text_width),
                self._layout(command.description, self.description_font, text_width))

    def sizeHint(self, option, index):
        command = index.data(Qt.UserRole + 1)
        width = max(1, self.listing.viewport().width() - 2 * self.listing.spacing())
        (_, title_height), (_, description_height) = self._layouts(command, width - 4)
        return QSize(width, max(76, title_height + description_height + 34))

    def paint(self, painter: QPainter, option, index):
        command = index.data(Qt.UserRole + 1)
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        rect = option.rect.adjusted(2, 2, -2, -2)
        selected = self.listing.currentIndex() == index
        painter.setBrush(QColor("#193438" if selected else "#0F1928"))
        painter.setPen(QPen(QColor("#5EEAD4" if selected else "#31445A"), 1))
        painter.drawRoundedRect(rect, 9, 9)
        painter.setPen(QPen(QColor("#5EEAD4" if selected else "#B8C7D9"), 1.7))
        icon = QRect(rect.left() + 18, rect.center().y() - 11, 19, 23)
        painter.drawRoundedRect(icon, 2, 2)
        painter.drawLine(icon.left() + 5, icon.top() + 8, icon.right() - 4, icon.top() + 8)
        painter.drawLine(icon.left() + 5, icon.top() + 14, icon.right() - 4, icon.top() + 14)
        arrow_x = rect.right() - 20
        arrow_y = rect.center().y()
        painter.drawLine(arrow_x - 4, arrow_y - 5, arrow_x + 1, arrow_y)
        painter.drawLine(arrow_x + 1, arrow_y, arrow_x - 4, arrow_y + 5)
        (title_layout, title_height), (description_layout, _description_height) = self._layouts(command, rect.width())
        text_x = rect.left() + 60
        painter.setPen(QColor("#F3F7FC"))
        title_layout.draw(painter, QPointF(text_x, rect.top() + 13))
        painter.setPen(QColor("#B8C7D9"))
        description_layout.draw(painter, QPointF(text_x, rect.top() + 18 + title_height))
        painter.restore()


class CommandPalette(QDialog):
    """Filtruje popisy a na výslovné otevření emituje pouze identifikátor."""

    command_selected = Signal(str)

    def __init__(self, commands: Iterable[PaletteCommand], parent=None):
        super().__init__(parent)
        self.setObjectName("studio.command_palette")
        self.setWindowTitle("Kájovo NG · Rychlé otevření")
        self.setAccessibleName("Rychlé otevření sekce nebo nástroje")
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setWindowModality(Qt.WindowModal)
        self.setMinimumSize(300, 250)
        self.resize(680, 620)
        self._commands: tuple[PaletteCommand, ...] = ()
        self._last_query: str | None = None
        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(24, 24, 24, 24)
        self.root.setSpacing(12)
        self.header = QHBoxLayout()
        self.heading = QLabel("Kam chcete přejít?")
        self.heading.setObjectName("palette.heading")
        self.heading.setWordWrap(True)
        self.header.addWidget(self.heading, 1)
        self.status = QLabel()
        self.status.setObjectName("palette.status")
        self.status.setAccessibleName("Počet nalezených položek")
        self.header.addWidget(self.status)
        self.root.addLayout(self.header)
        self.subtitle = QLabel("Sekce, nástroje a probíhající práce na jednom místě.")
        self.subtitle.setObjectName("palette.subtitle")
        self.subtitle.setWordWrap(True)
        self.root.addWidget(self.subtitle)
        self.search = QLineEdit()
        self.search.setObjectName("palette.search")
        self.search.setPlaceholderText("Hledat sekci nebo nástroj…")
        self.search.setAccessibleName("Hledat sekci nebo nástroj")
        self.search.setAccessibleDescription("Pište i bez diakritiky. Šipkami vyberte výsledek a Enterem jej otevřete.")
        self.root.addWidget(self.search)
        self.results = QListWidget()
        self.results.setObjectName("palette.results")
        self.results.setAccessibleName("Nalezené sekce, nástroje a práce")
        self.results.setWordWrap(True)
        self.results.setTextElideMode(Qt.ElideNone)
        self.results.setSelectionMode(QAbstractItemView.SingleSelection)
        self.results.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.results.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.results.setSpacing(4)
        self.results.setItemDelegate(_CommandDelegate(self.results))
        self.root.addWidget(self.results, 1)
        self.empty = QLabel("Nic jsme nenašli. Zkuste kratší název sekce nebo nástroje.")
        self.empty.setObjectName("palette.empty")
        self.empty.setWordWrap(True)
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setAccessibleName("Žádné odpovídající položky")
        self.root.addWidget(self.empty, 1)
        footer = QHBoxLayout()
        self.hint = QLabel("↑ ↓  Vybrat")
        self.hint.setObjectName("palette.hint")
        footer.addWidget(self.hint, 1)
        self.open_button = QPushButton("Otevřít")
        self.open_button.setObjectName("palette.open")
        self.open_button.setAccessibleName("Otevřít vybranou položku")
        self.close_button = QPushButton("Zavřít")
        self.close_button.setObjectName("palette.close")
        self.close_button.setAccessibleName("Zavřít rychlé otevření")
        for button in (self.open_button, self.close_button):
            button.setAutoDefault(False)
            button.setDefault(False)
            footer.addWidget(button)
        self.root.addLayout(footer)
        self.setStyleSheet("""
            QDialog { background: #131F30; border: 1px solid #31445A; border-radius: 14px; }
            QLabel { background: transparent; color: #B8C7D9; font-family: Montserrat; font-size: 14px; border: none; }
            QLabel#palette\\.heading { color: #F3F7FC; font-size: 26px; font-weight: 700; }
            QLineEdit { background: #0B1220; color: #F3F7FC; font-family: Montserrat; font-size: 16px;
                        border: 1px solid #31445A; border-radius: 9px; padding: 12px; }
            QLineEdit:focus { border: 1px solid #5EEAD4; }
            QListWidget { background: #131F30; border: none; }
            QPushButton { background: #1B2C41; color: #F3F7FC; font-family: Montserrat; font-size: 14px;
                          border: 1px solid #31445A; border-radius: 7px; padding: 8px 12px; }
            QPushButton:focus, QPushButton:hover { border-color: #5EEAD4; }
            QPushButton:disabled { color: #91A2B8; }
            QScrollBar:vertical { background: #131F30; width: 10px; }
            QScrollBar::handle:vertical { background: #465B75; border-radius: 4px; min-height: 26px; }
            QScrollBar::add-line, QScrollBar::sub-line { height: 0; }
        """)
        self.search.textChanged.connect(self._refresh)
        self.search.installEventFilter(self)
        self.results.installEventFilter(self)
        self.results.currentRowChanged.connect(lambda row: self.open_button.setEnabled(row >= 0))
        self.results.itemDoubleClicked.connect(lambda _item: self._open_selected())
        self.open_button.clicked.connect(self._open_selected)
        self.close_button.clicked.connect(self.reject)
        self.set_commands(commands)

    def set_commands(self, commands: Iterable[PaletteCommand]) -> None:
        entries = tuple(commands)
        if any(not command.key for command in entries) or len({command.key for command in entries}) != len(entries):
            raise ValueError("Každá položka rychlého otevření musí mít vlastní neprázdný identifikátor.")
        self._commands = entries
        self._refresh()

    def _refresh(self, *_args) -> None:
        query = _normalized(self.search.text())
        selected = self.results.currentItem()
        selected_key = selected.data(Qt.UserRole) if selected and query == self._last_query else None
        self._last_query = query
        terms = query.split()
        matches = []
        for command in self._commands:
            title = _normalized(command.title)
            searchable = _normalized(" ".join((command.title, command.description, command.category, *command.keywords)))
            if all(term in searchable for term in terms):
                rank = 0 if title == query else 1 if title.startswith(query) else 2 if query in title else 3
                matches.append((rank, command))
        matches.sort(key=lambda match: match[0])
        self.results.clear()
        for _rank, command in matches:
            item = QListWidgetItem(command.title + "\n" + command.description)
            item.setData(Qt.UserRole, command.key)
            item.setData(Qt.UserRole + 1, command)
            item.setData(Qt.AccessibleTextRole, command.title + ". " + command.category + ". " + command.description)
            self.results.addItem(item)
            if command.key == selected_key:
                self.results.setCurrentItem(item)
        if matches and self.results.currentRow() < 0:
            self.results.setCurrentRow(0)
        self.status.setText(f"Výsledků: {len(matches)}")
        self.results.setVisible(bool(matches))
        self.empty.setVisible(not matches)
        self.open_button.setEnabled(bool(matches))

    def open(self) -> None:
        self.search.clear()
        self._refresh()
        if self.results.count():
            self.results.setCurrentRow(0)
        screen = self.parentWidget().screen() if self.parentWidget() else self.screen()
        if screen is not None:
            available = screen.availableGeometry()
            parent = self.parentWidget()
            bounds = available.intersected(parent.frameGeometry()) if parent and parent.isVisible() else available
            width = min(680, max(self.minimumWidth(), bounds.width() - 32))
            height = min(620, max(self.minimumHeight(), bounds.height() - 32))
            self.resize(min(width, available.width()), min(height, available.height()))
            center = bounds.center()
            x = min(max(available.left(), center.x() - self.width() // 2), available.right() - self.width() + 1)
            y = min(max(available.top(), center.y() - self.height() // 2), available.bottom() - self.height() + 1)
            self.move(x, y)
        self.show()
        self.raise_()
        self.activateWindow()
        self.search.setFocus(Qt.ShortcutFocusReason)

    def _open_selected(self) -> None:
        item = self.results.currentItem()
        if item is None or not self.isVisible():
            return
        key = item.data(Qt.UserRole)
        self.accept()
        self.command_selected.emit(key)

    def eventFilter(self, watched, event):
        if watched in (self.search, self.results) and event.type() == QEvent.KeyPress:
            key = event.key()
            if key in (Qt.Key_Up, Qt.Key_Down):
                if self.results.count():
                    step = 1 if key == Qt.Key_Down else -1
                    row = max(0, min(self.results.count() - 1, self.results.currentRow() + step))
                    self.results.setCurrentRow(row)
                    self.results.scrollToItem(self.results.currentItem())
                return True
            if key in (Qt.Key_Return, Qt.Key_Enter):
                self._open_selected()
                return True
            if key == Qt.Key_Escape:
                self.reject()
                return True
        return super().eventFilter(watched, event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not hasattr(self, "results"):
            return
        compact = event.size().width() < 540 or event.size().height() < 440
        self.root.setContentsMargins(*((12,) * 4 if compact else (24,) * 4))
        self.root.setSpacing(6 if compact else 12)
        self.subtitle.setVisible(not compact)
        self.header.setDirection(QBoxLayout.TopToBottom if compact else QBoxLayout.LeftToRight)
        self.results.doItemsLayout()
