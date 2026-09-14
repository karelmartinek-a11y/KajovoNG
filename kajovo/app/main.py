from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtGui import QFont, QFontDatabase, QIcon, QPixmap
from PySide6.QtWidgets import QApplication

from kajovo.core.config import load_settings
from kajovo.core.resources import resource_path
from kajovo.core.secret_store import load_api_key
from kajovo.studio.application import create_window


def _project_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return Path(__file__).resolve().parents[2]


def _resource_path(*parts: str) -> Path:
    return resource_path(str(Path(*parts)))


def _load_fonts() -> None:
    for path in (
        _resource_path("montserrat_regular.ttf"),
        _resource_path("montserrat_bold.ttf"),
    ):
        if path.exists():
            QFontDatabase.addApplicationFont(str(path))


def _embedded_icon() -> QIcon:
    from PySide6.QtGui import QColor, QPainter, QPen

    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    color = QColor("#5EEAD4")
    painter.setPen(QPen(color, 5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    painter.drawLine(16, 12, 16, 52)
    painter.drawLine(16, 32, 48, 12)
    painter.drawLine(16, 32, 48, 52)
    painter.setBrush(color)
    painter.drawEllipse(11, 27, 10, 10)
    painter.end()
    return QIcon(pixmap)


def _load_app_icon() -> QIcon:
    icon_path = _resource_path("studio-symbol.png")
    if icon_path.exists():
        return QIcon(str(icon_path))
    return _embedded_icon()


def main():
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    QCoreApplication.setAttribute(Qt.AA_DontUseNativeDialogs, True)
    app = QApplication(sys.argv)
    app.setApplicationName("Kájovo NG")
    app.setOrganizationName("Kájovo")

    _load_fonts()

    # Výchozí písmo; při nedostupném Montserratu zůstává systémové.
    font = QFont("Montserrat", 10)
    app.setFont(font)
    app_icon = _load_app_icon()
    app.setWindowIcon(app_icon)

    settings = load_settings()
    key_error = None
    try:
        api_key = load_api_key()
    except Exception as error:
        api_key = ""
        key_error = error
    window = create_window(settings, api_key=api_key)
    window.setWindowIcon(app_icon)
    window.showMaximized()
    if key_error:
        from kajovo.studio.components import show_error
        show_error(window, key_error)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
