from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QPixmap


def build_app_icon(size: int = 256) -> QIcon:
    repo_root = Path(__file__).resolve().parents[2]
    for candidate in [
        repo_root / 'brand' / 'logo' / 'exports' / 'mark' / 'png' / 'kajovo-spend_mark_256.png',
        repo_root / 'brand' / 'logo' / 'exports' / 'mark' / 'svg' / 'kajovo-spend_mark.svg',
        repo_root / 'kajovospend' / 'branding' / 'app_icon.svg',
        repo_root / 'brand' / 'signace' / 'signace.png',
        repo_root / 'brand' / 'signace' / 'signace.svg',
        repo_root / 'signace' / 'signace.svg',
    ]:
        if candidate.exists():
            icon = QIcon(str(candidate))
            if not icon.isNull():
                return icon
            pixmap = QPixmap(str(candidate))
            if not pixmap.isNull():
                return QIcon(pixmap.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
    fallback = QPixmap(size, size)
    fallback.fill(Qt.GlobalColor.white)
    return QIcon(fallback)
