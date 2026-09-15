"""Časový přehled skutečných kroků kaskády, bez domýšlení dalších kroků."""

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPen
from PySide6.QtWidgets import QStyledItemDelegate, QTableView

from .history_models import format_duration


class CascadeModel(QAbstractTableModel):
    def __init__(self, stages, parent=None):
        super().__init__(parent)
        self.stages = stages

    def rowCount(self, parent=None):
        parent = parent or QModelIndex()
        return 0 if parent.isValid() else len(self.stages)

    def columnCount(self, parent=None):
        parent = parent or QModelIndex()
        return 0 if parent.isValid() else 2

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        stage = self.stages[index.row()]
        if role == Qt.UserRole:
            return stage
        if role in (Qt.DisplayRole, Qt.AccessibleTextRole):
            return f"{index.row() + 1}. {stage.title}\n{stage.model}" if index.column() == 0 else stage.status.label
        if role == Qt.ToolTipRole:
            return f"{stage.title} · {stage.status.label}\n{stage.started} → {stage.finished}"

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return ["Krok a model", "Čas od začátku kaskády"][section]


class CascadeDelegate(QStyledItemDelegate):
    def __init__(self, stages, parent):
        super().__init__(parent)
        starts = [stage.started for stage in stages if stage.started is not None]
        ends = [stage.finished for stage in stages if stage.finished is not None]
        self.origin = min(starts) if starts else None
        self.span = max(1, max(ends or starts) - self.origin) if starts else 1
        self.dependencies = set()

    def paint(self, painter, option, index):
        stage = index.data(Qt.UserRole)
        painter.save()
        painter.setClipRect(option.rect)
        painter.fillRect(option.rect, QColor("#213F5A" if stage.stage in self.dependencies else "#131F30"))
        available = option.rect.width() - 24
        for tick in range(5):
            x = option.rect.left() + 12 + available * tick / 4
            painter.setPen(QPen(QColor("#2C3D51"), 1))
            painter.drawLine(int(x), option.rect.top(), int(x), option.rect.bottom())
        if stage.started is None or self.origin is None:
            painter.setPen(QColor("#B8C7D9"))
            painter.drawText(option.rect.adjusted(12, 0, -12, 0), Qt.AlignVCenter, "Začátek nebyl uložen")
        else:
            offset = (stage.started - self.origin) / self.span * available
            width = (stage.duration or 0) / self.span * available
            rect = QRectF(option.rect.left() + 12 + offset, option.rect.top() + 22, max(5, width), 26)
            painter.setBrush(QColor(stage.status.color))
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(rect, 4, 4)
            painter.setPen(QColor("#F3F7FC"))
            text = format_duration(stage.started - self.origin) + " · " + stage.status.label
            if stage.duration is not None:
                text += " · " + format_duration(stage.duration)
            painter.drawText(option.rect.adjusted(12, 0, -12, -3), Qt.AlignBottom, text)
        painter.restore()


class CascadeTimeline(QTableView):
    step_selected = Signal(int)

    def __init__(self, stages, parent=None):
        super().__init__(parent)
        self.setModel(CascadeModel(stages, self))
        self.delegate = CascadeDelegate(stages, self)
        self.setItemDelegateForColumn(1, self.delegate)
        self.setSelectionBehavior(QTableView.SelectRows)
        self.setEditTriggers(QTableView.NoEditTriggers)
        self.setSelectionMode(QTableView.SingleSelection)
        self.setColumnWidth(0, 210)
        self.setColumnWidth(1, 380)
        self.horizontalHeader().setStretchLastSection(True)
        self.verticalHeader().hide()
        self.verticalHeader().setDefaultSectionSize(86)
        self.setAccessibleName("Časový průběh kroků kaskády")
        self.selectionModel().currentRowChanged.connect(lambda index, _old: self.step_selected.emit(index.row()))

    def setCurrentCell(self, row, column):
        self.setCurrentIndex(self.model().index(row, column))

    def rowCount(self):
        return self.model().rowCount()

    def highlight_dependencies(self, stages):
        self.delegate.dependencies = set(stages)
        self.viewport().update()
