"""Sdílený vzhled kompaktních karet vzdálených dávek."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QVBoxLayout

from .components import action, caption


def format_started(value) -> str:
    try:
        if isinstance(value, (int, float)):
            stamp = datetime.fromtimestamp(value)
        else:
            stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if stamp.tzinfo is not None:
                stamp = stamp.astimezone()
        return stamp.strftime("%d.%m.%Y %H:%M")
    except (OSError, OverflowError, TypeError, ValueError):
        return "Datum spuštění není k dispozici"


def build_job_card(title, status, started, summary, button_specs=(), object_name=""):
    frame = QFrame()
    frame.setProperty("card", True)
    if object_name:
        frame.setObjectName(object_name)
    frame.setAccessibleName(f"{title}, {status}, spuštěno {started}")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(14, 12, 14, 12)
    layout.setSpacing(5)
    top = QHBoxLayout()
    title_label = caption(title, "section")
    status_label = caption(status, "muted")
    status_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    top.addWidget(title_label, 1)
    top.addWidget(status_label)
    layout.addLayout(top)
    layout.addWidget(caption(f"Spuštěno {started}", "muted"))
    layout.addWidget(caption(summary))
    buttons = []
    if button_specs:
        row = QHBoxLayout()
        row.addStretch()
        for identifier, label, callback, role, enabled in button_specs:
            button = action(identifier, label, callback, role)
            button.setEnabled(enabled)
            row.addWidget(button)
            buttons.append(button)
        layout.addLayout(row)
    return frame, buttons


def clear_cards(layout):
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.hide()
            widget.deleteLater()
        elif item.layout() is not None:
            child = item.layout()
            while child.count():
                nested = child.takeAt(0)
                if nested.widget() is not None:
                    nested.widget().hide()
                    nested.widget().deleteLater()


def photo_failure_summary(job):
    failed = [item for item in job.items if item.status == "failed"]
    if not failed:
        return ""
    from kajovo.core.user_errors import describe_recorded_error

    message = describe_recorded_error(
        failed[0].error_message,
        operation="Úprava fotografie",
    ).message
    remaining = len(failed) - 1
    if remaining:
        message += f" Další fotografie s chybou: {remaining}."
    return message
