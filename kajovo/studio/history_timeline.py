"""DAW-like časová stopa kreslená delegátem bez child widgetu pro každý segment."""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QStyle, QStyledItemDelegate, QTableView

from .components import COLORS
from .history_models import RunTableModel, RunView, StageView, format_duration


class TrackDelegate(QStyledItemDelegate):
    MINIMUM_SEGMENT = 92

    def __init__(self, parent=None):
        super().__init__(parent)
        self.zoom = 1.0
        self.selected_run = ""
        self.selected_step = ""

    def sizeHint(self, option, index):
        return QSize(960, 90)

    def _rectangles(self, run: RunView, rect) -> list[tuple[StageView, QRectF]]:
        if not run.stages:
            return []
        known = [stage.duration for stage in run.stages if stage.duration is not None]
        total = sum(known) if known else 0.0
        minimum = self.MINIMUM_SEGMENT * self.zoom
        available = max(rect.width() - 20, minimum * len(run.stages))
        flexible = max(0.0, available - minimum * len(run.stages))
        x = rect.left() + 10
        output = []
        for stage in run.stages:
            extra = flexible * (stage.duration / total) if total and stage.duration is not None else 0.0
            width = minimum + extra
            output.append((stage, QRectF(x, rect.top() + 15, width - 6, rect.height() - 30)))
            x += width
        return output

    def segment_at(self, run: RunView, rect, point) -> StageView | None:
        return next((stage for stage, area in self._rectangles(run, rect) if area.contains(point)), None)

    def paint(self, painter: QPainter, option, index):
        run = index.data(RunTableModel.RunRole)
        if not isinstance(run, RunView):
            return super().paint(painter, option, index)
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(option.rect, QColor("#0F1928" if index.row() % 2 == 0 else COLORS["surface"]))
        if not run.stages:
            painter.setPen(QColor(COLORS["muted"]))
            painter.drawText(option.rect.adjusted(14, 0, -8, 0), Qt.AlignVCenter, "Kroky nejsou evidovány · legacy/read-only")
            painter.restore()
            return
        for stage, rect in self._rectangles(run, option.rect):
            selected = run.run_id == self.selected_run and stage.step_id == self.selected_step
            fill = QColor(stage.status.color)
            fill.setAlpha(190 if selected else 125)
            painter.setBrush(fill)
            painter.setPen(QPen(QColor(COLORS["focus"] if selected else stage.status.color), 3 if selected else 1))
            painter.drawRoundedRect(rect, 6, 6)
            text_color = QColor("#08131F") if stage.status.key in {"completed", "partial", "failed", "ready_to_import"} else QColor(COLORS["text"])
            painter.setPen(text_color)
            font = QFont(option.font)
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(rect.adjusted(9, 7, -8, -30), Qt.AlignLeft | Qt.AlignTop, f"{stage.status.symbol} {stage.stage or '—'}")
            font.setBold(False)
            font.setPointSize(max(8, font.pointSize() - 1))
            painter.setFont(font)
            duration = "Není evidováno" if stage.duration is None else format_duration(stage.duration)
            painter.drawText(rect.adjusted(9, 30, -8, -7), Qt.AlignLeft | Qt.TextWordWrap, f"{stage.title}\n{duration}")
            badges = []
            if stage.artifact_count:
                badges.append(f"▤ {stage.artifact_count}")
            if stage.response_count:
                badges.append(f"↩ {stage.response_count}")
            if stage.error_count:
                badges.append("! chyba")
            if badges:
                painter.drawText(rect.adjusted(8, 0, -8, -6), Qt.AlignRight | Qt.AlignBottom, " · ".join(badges))
        if run.has_lineage:
            painter.setPen(QPen(QColor(COLORS["focus"]), 2))
            x = option.rect.right() - 14
            if run.run_id == self.selected_run:
                painter.drawLine(int(x - 48), int(option.rect.center().y()), int(x), int(option.rect.center().y()))
                if run.parent_run_id:
                    painter.drawLine(int(x - 48), int(option.rect.center().y()), int(x - 48), int(option.rect.top() + 10))
                for child in range(min(3, run.child_count)):
                    painter.drawEllipse(int(x - 39 + child * 11), int(option.rect.center().y() - 4), 8, 8)
            painter.drawEllipse(x - 5, option.rect.center().y() - 5, 10, 10)
            label = f"↳ {run.child_count} větví" if run.child_count else "↰ parent"
            painter.drawText(option.rect.adjusted(0, 2, -24, 0), Qt.AlignRight | Qt.AlignTop, label)
        painter.restore()


class MetaDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        painter.save()
        selected = bool(option.state & QStyle.State_Selected)
        painter.fillRect(option.rect, QColor("#285B85" if selected else "#0F1928" if index.row() % 2 == 0 else COLORS["surface"]))
        font = QFont(option.font)
        font.setPointSize(max(8, font.pointSize() - 2))
        painter.setFont(font)
        painter.setPen(QColor(COLORS["text"]))
        painter.drawText(option.rect.adjusted(8, 3, -6, -3), Qt.AlignLeft | Qt.AlignVCenter | Qt.TextWordWrap,
                         str(index.data(Qt.DisplayRole) or ""))
        painter.restore()


class RunTrackView(QTableView):
    stage_selected = Signal(object, object)
    run_activated = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.delegate = TrackDelegate(self)
        self.meta_delegate = MetaDelegate(self)
        self.setItemDelegateForColumn(RunTableModel.MetaColumn, self.meta_delegate)
        self.setItemDelegateForColumn(RunTableModel.TrackColumn, self.delegate)
        self.setAlternatingRowColors(True)
        self.setSelectionBehavior(QTableView.SelectRows)
        self.setSelectionMode(QTableView.SingleSelection)
        self.setEditTriggers(QTableView.NoEditTriggers)
        self.setWordWrap(True)
        self.verticalHeader().hide()
        self.verticalHeader().setDefaultSectionSize(90)
        self.horizontalHeader().setStretchLastSection(False)
        self.setColumnWidth(RunTableModel.MetaColumn, 430)
        self.setColumnWidth(RunTableModel.TrackColumn, 1100)
        self.setAccessibleName("Historické běhy jako časové stopy")
        self.doubleClicked.connect(self._activate)

    def setModel(self, model):
        super().setModel(model)
        self.setColumnWidth(RunTableModel.MetaColumn, 390)
        self.setColumnWidth(RunTableModel.TrackColumn, int(1100 * self.delegate.zoom))

    def set_zoom(self, value: float):
        self.delegate.zoom = max(0.55, min(3.0, float(value)))
        self.setColumnWidth(RunTableModel.TrackColumn, int(1100 * self.delegate.zoom))
        self.viewport().update()

    def select_stage(self, run_id: str, step_id: str):
        self.delegate.selected_run = run_id
        self.delegate.selected_step = step_id
        self.viewport().update()

    def mousePressEvent(self, event):
        index = self.indexAt(event.position().toPoint())
        if index.isValid():
            run = index.data(RunTableModel.RunRole)
            if isinstance(run, RunView) and index.column() == RunTableModel.TrackColumn:
                segment = self.delegate.segment_at(run, self.visualRect(index), event.position())
                if segment:
                    self.select_stage(run.run_id, segment.step_id)
                    self.stage_selected.emit(run, segment)
        super().mousePressEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self._activate(self.currentIndex())
            return
        super().keyPressEvent(event)

    def _activate(self, index):
        if index.isValid():
            run = index.data(RunTableModel.RunRole)
            if isinstance(run, RunView):
                self.run_activated.emit(run)
