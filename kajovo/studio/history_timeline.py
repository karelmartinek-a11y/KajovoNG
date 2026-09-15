"""DAW-like časová stopa kreslená delegátem bez child widgetu pro každý segment."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QHeaderView, QStyle, QStyledItemDelegate, QTableView, QToolTip

from .components import COLORS
from .history_models import RunTableModel, RunView, StageView, format_duration


class TrackDelegate(QStyledItemDelegate):
    MINIMUM_SEGMENT = 160

    def __init__(self, parent=None):
        super().__init__(parent)
        self.zoom = 1.0
        self.selected_run = ""
        self.selected_step = ""
        self.span = 600.0

    def sizeHint(self, option, index):
        return QSize(960, 112)

    def _rectangles(self, run: RunView, rect) -> list[tuple[StageView, QRectF]]:
        if not run.stages:
            return []
        starts = [stage.started for stage in run.stages if stage.started is not None]
        origin = min(starts) if starts else None
        minimum = self.MINIMUM_SEGMENT
        available = max(1, rect.width() - 20)
        x = rect.left() + 10
        output = []
        for stage in run.stages:
            if origin is not None and stage.started is not None:
                x = rect.left() + 10 + (stage.started - origin) / self.span * available
                width = max(8, (stage.duration or 0) / self.span * available)
                if stage.duration is None:
                    width = minimum
            else:
                width = minimum
            output.append((stage, QRectF(x, rect.top() + 12, max(5, width - 6), rect.height() - 24)))
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
        for tick in range(6):
            x = option.rect.left() + 10 + (option.rect.width() - 20) * tick / 5
            painter.setPen(QPen(QColor("#253449"), 1))
            painter.drawLine(int(x), option.rect.top(), int(x), option.rect.bottom())
        if not run.stages:
            painter.setPen(QColor(COLORS["muted"]))
            painter.drawText(option.rect.adjusted(14, 0, -8, 0), Qt.AlignVCenter,
                             "Starší záznam · podrobný průběh nebyl uložen" if run.legacy else "Průběh fází nebyl uložen")
            painter.restore()
            return
        for stage, rect in self._rectangles(run, option.rect):
            selected = run.run_id == self.selected_run and stage.step_id == self.selected_step
            fill = QColor(stage.status.color)
            fill.setAlpha(55 if selected else 28)
            painter.setBrush(fill)
            painter.setPen(QPen(QColor(COLORS["focus"] if selected else stage.status.color), 3 if selected else 1))
            painter.drawRoundedRect(rect, 6, 6)
            painter.setClipRect(rect.adjusted(4, 4, -4, -4))
            painter.setPen(QColor(COLORS["text"]))
            font = QFont(option.font)
            font.setBold(True)
            painter.setFont(font)
            metrics = QFontMetrics(font)
            painter.drawText(rect.adjusted(9, 7, -8, -30), Qt.AlignLeft | Qt.AlignTop,
                             metrics.elidedText(f"{stage.status.symbol} {stage.title}", Qt.ElideRight, int(rect.width() - 18)))
            font.setBold(False)
            font.setPointSize(max(10, font.pointSize() - 1))
            painter.setFont(font)
            painter.setPen(QColor(COLORS["muted"]))
            duration = "čas nezapsán" if stage.duration is None else format_duration(stage.duration)
            painter.drawText(rect.adjusted(9, 31, -8, -7), Qt.AlignLeft | Qt.AlignTop, f"{stage.stage} · {duration}")
            label = stage.status.label
            if stage.status.key in {"running", "created", "preparing"} and run.status.terminal:
                label = "Konec fáze nezapsán"
            painter.drawText(rect.adjusted(9, 0, -8, -8), Qt.AlignLeft | Qt.AlignBottom,
                             QFontMetrics(font).elidedText(label, Qt.ElideRight, int(rect.width() - 18)))
            painter.setClipping(False)
        if run.has_lineage:
            painter.setPen(QPen(QColor(COLORS["focus"]), 2))
            x = option.rect.right() - 14
            painter.drawEllipse(x - 5, option.rect.center().y() - 5, 10, 10)
            label = f"↳ {run.child_count} větví" if run.child_count else "↰ navazující běh"
            painter.drawText(option.rect.adjusted(0, 2, -24, 0), Qt.AlignRight | Qt.AlignTop, label)
        painter.restore()


class MetaDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        painter.save()
        selected = bool(option.state & QStyle.State_Selected)
        painter.fillRect(option.rect, QColor("#285B85" if selected else "#0F1928" if index.row() % 2 == 0 else COLORS["surface"]))
        font = QFont(option.font)
        font.setPointSize(max(10, font.pointSize()))
        painter.setFont(font)
        painter.setPen(QColor(COLORS["text"]))
        metrics = QFontMetrics(font)
        lines = str(index.data(Qt.DisplayRole) or "").splitlines()
        y = option.rect.top() + 12
        for line in lines:
            painter.drawText(option.rect.left() + 12, y + metrics.ascent(),
                             metrics.elidedText(line, Qt.ElideRight, option.rect.width() - 24))
            y += metrics.height() + 2
        painter.restore()


class TimeHeader(QHeaderView):
    def __init__(self, view):
        super().__init__(Qt.Horizontal, view)
        self.view = view
        self.setMinimumHeight(48)

    def paintSection(self, painter, rect, logical_index):
        if logical_index == 0:
            super().paintSection(painter, rect, logical_index)
            return
        painter.save()
        painter.setClipRect(rect)
        painter.fillRect(rect, QColor(COLORS["raised"]))
        painter.setPen(QColor(COLORS["muted"]))
        for tick in range(6):
            x = rect.left() + 10 + (rect.width() - 20) * tick / 5
            label = format_duration(self.view.delegate.span * tick / 5)
            if tick == 4 and rect.width() < 1000:
                continue
            label_x = x - QFontMetrics(painter.font()).horizontalAdvance(label) - 3 if tick == 5 else x + 3
            painter.drawText(int(label_x), rect.top() + 23, label)
            painter.drawLine(int(x), rect.bottom() - 10, int(x), rect.bottom())
        painter.restore()


class RunTrackView(QTableView):
    stage_selected = Signal(object, object)
    run_activated = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.delegate = TrackDelegate(self)
        self.setHorizontalHeader(TimeHeader(self))
        self.meta_delegate = MetaDelegate(self)
        self.setItemDelegateForColumn(RunTableModel.MetaColumn, self.meta_delegate)
        self.setItemDelegateForColumn(RunTableModel.TrackColumn, self.delegate)
        self.setAlternatingRowColors(True)
        self.setSelectionBehavior(QTableView.SelectRows)
        self.setSelectionMode(QTableView.SingleSelection)
        self.setEditTriggers(QTableView.NoEditTriggers)
        self.setHorizontalScrollMode(QTableView.ScrollPerPixel)
        self.setWordWrap(True)
        self.verticalHeader().hide()
        self.verticalHeader().setDefaultSectionSize(112)
        self.horizontalHeader().setStretchLastSection(False)
        self.setColumnWidth(RunTableModel.MetaColumn, 430)
        self.setColumnWidth(RunTableModel.TrackColumn, 1100)
        self.setAccessibleName("Historické běhy jako časové stopy")
        self.doubleClicked.connect(self._activate)

    def setModel(self, model):
        super().setModel(model)
        self.setColumnWidth(RunTableModel.MetaColumn, 390)
        self.setColumnWidth(RunTableModel.TrackColumn, int(1100 * self.delegate.zoom))
        model.modelReset.connect(self.update_scale)
        self.update_scale()

    def update_scale(self):
        spans = []
        for run in self.model()._rows:
            starts = [stage.started for stage in run.stages if stage.started is not None]
            ends = [stage.finished for stage in run.stages if stage.finished is not None]
            if starts:
                spans.append(max(ends + starts) - min(starts))
        self.delegate.span = max([60.0, *spans]) * 1.25
        self.horizontalHeader().viewport().update()
        self.viewport().update()

    def set_zoom(self, value: float):
        self.delegate.zoom = max(0.55, min(3.0, float(value)))
        self.setColumnWidth(RunTableModel.TrackColumn, int(1100 * self.delegate.zoom))
        self.viewport().update()

    def select_stage(self, run_id: str, step_id: str):
        self.delegate.selected_run = run_id
        self.delegate.selected_step = step_id
        self.viewport().update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self.model() or self.isColumnHidden(0):
            return
        rows = {run.run_id: (row, run) for row, run in enumerate(self.model()._rows)}
        selected = self.delegate.selected_run
        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor(COLORS["focus"]), 2))
        for target_id, (target_row, target) in rows.items():
            parent = rows.get(target.parent_run_id)
            if not parent or selected not in {target_id, target.parent_run_id}:
                continue
            parent_row, source = parent
            lineage = next((row for row in target.raw.get("lineage_records") or []
                            if row.get("source_run_id") == source.run_id), None)
            if not lineage:
                continue
            checkpoint = next((row for row in source.raw.get("checkpoint_markers") or []
                               if row.get("checkpoint_id") == lineage.get("source_checkpoint_id")), None)
            if not checkpoint:
                continue
            source_rect = self.visualRect(self.model().index(parent_row, 1))
            target_rect = self.visualRect(self.model().index(target_row, 1))
            boundary = next((rect.right() for stage, rect in self.delegate._rectangles(source, source_rect)
                             if stage.step_id == checkpoint.get("step_id") or stage.stage == checkpoint.get("checkpoint_type")), None)
            if checkpoint.get("checkpoint_type") in {"input_ready", "cascade_input_ready"}:
                boundary = source_rect.left() + 10
            if boundary is None:
                continue
            end_x, end_y = target_rect.left() + 10, target_rect.top() + 8
            start_y = source_rect.bottom() - 5
            path = QPainterPath()
            path.moveTo(boundary, start_y)
            path.lineTo(boundary, end_y)
            path.lineTo(end_x, end_y)
            painter.drawPath(path)
            painter.drawEllipse(QRectF(boundary - 4, start_y - 4, 8, 8))
        painter.end()

    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        index = self.indexAt(event.position().toPoint())
        if index.isValid():
            run = index.data(RunTableModel.RunRole)
            if isinstance(run, RunView) and index.column() == RunTableModel.TrackColumn:
                segment = self.delegate.segment_at(run, self.visualRect(index), event.position())
                if segment:
                    self.select_stage(run.run_id, segment.step_id)
                    self.stage_selected.emit(run, segment)

    def viewportEvent(self, event):
        if event.type() == QEvent.ToolTip:
            index = self.indexAt(event.pos())
            run = index.data(RunTableModel.RunRole) if index.isValid() else None
            if run and index.column() == RunTableModel.TrackColumn:
                stage = self.delegate.segment_at(run, self.visualRect(index), event.pos())
                if stage:
                    duration = format_duration(stage.duration) if stage.duration is not None else "Trvání nebylo uloženo"
                    QToolTip.showText(event.globalPos(), f"{stage.title} · {stage.stage}\n{stage.status.label} · {duration}\n"
                                      f"{stage.model}\n{stage.artifact_count} souborů · {stage.response_count} záznamů odpovědi", self)
                    return True
        return super().viewportEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Left, Qt.Key_Right):
            run = self.currentIndex().data(RunTableModel.RunRole)
            if run and run.stages:
                current = next((i for i, stage in enumerate(run.stages)
                                if stage.step_id == self.delegate.selected_step), -1)
                selected = max(0, min(len(run.stages) - 1, current + (1 if event.key() == Qt.Key_Right else -1)))
                stage = run.stages[selected]
                self.select_stage(run.run_id, stage.step_id)
                self.stage_selected.emit(run, stage)
                return
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self._activate(self.currentIndex())
            return
        super().keyPressEvent(event)

    def _activate(self, index):
        if index.isValid():
            run = index.data(RunTableModel.RunRole)
            if isinstance(run, RunView):
                self.run_activated.emit(run)
