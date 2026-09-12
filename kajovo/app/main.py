from __future__ import annotations

import sys, os
from pathlib import Path

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFontDatabase, QFont, QIcon, QPixmap
from PySide6.QtCore import Qt, QCoreApplication

from kajovo.core.config import load_settings
from kajovo.core.resources import resource_path
from kajovo.desktop.application import MainWindow

def _project_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return Path(__file__).resolve().parents[2]


def _resource_path(*parts: str) -> Path:
    return resource_path(str(Path(*parts)))


def _load_fonts() -> None:
    for path in (_resource_path("montserrat_regular.ttf"), _resource_path("montserrat_bold.ttf")):
        if path.exists():
            QFontDatabase.addApplicationFont(str(path))


def _embedded_icon() -> QIcon:
    xpm = [
        "16 16 4 1",
        "  c #0A1226",
        ". c #57BCFF",
        "+ c #172448",
        "@ c #FFFFFF",
        "                ",
        "   ..........   ",
        "  ..++++++++..  ",
        " ..++..++++++.. ",
        " ..++@.++++@@.. ",
        " ..++@@+++@@+.. ",
        " ..++@@@+@@++.. ",
        " ..++@@@@@+++.. ",
        " ..++@@@@@+++.. ",
        " ..++@@@+@@++.. ",
        " ..++@@+++@@+.. ",
        " ..++@.++++@@.. ",
        " ..++..++++++.. ",
        "  ..++++++++..  ",
        "   ..........   ",
        "                ",
    ]
    return QIcon(QPixmap(xpm))


def _load_app_icon() -> QIcon:
    icon_path = _resource_path("app_icon.png")
    if icon_path.exists():
        return QIcon(str(icon_path))
    return _embedded_icon()


def main():
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    QCoreApplication.setAttribute(Qt.AA_DontUseNativeDialogs, True)
    app = QApplication(sys.argv)
    app.setApplicationName("Kája")
    app.setOrganizationName("Kájovo")

    _load_fonts()

    # Výchozí písmo; při nedostupném Montserratu zůstává systémové.
    f = QFont("Montserrat", 10)
    app.setFont(f)
    from ..desktop.design import install_ui_style
    install_ui_style()

    app_icon = _load_app_icon()
    app.setWindowIcon(app_icon)

    from kajovo.desktop.windows import SplashScreen
    splash = SplashScreen()
    splash.show()
    app.processEvents()
    settings = load_settings()
    app.processEvents()
    w = MainWindow(settings)
    w.setWindowIcon(app_icon)
    w.showMaximized()
    splash.finish()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
