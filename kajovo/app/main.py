from __future__ import annotations

import sys, os
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFontDatabase, QFont
from PySide6.QtCore import Qt, QCoreApplication

from ..core.config import load_settings
from ..ui.mainwindow import MainWindow

def _load_fonts():
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    res = os.path.join(root, "resources")
    reg = os.path.join(res, "montserrat_regular.ttf")
    bold = os.path.join(res, "montserrat_bold.ttf")
    for p in (reg, bold):
        if os.path.exists(p):
            QFontDatabase.addApplicationFont(p)

def main():
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    QCoreApplication.setAttribute(Qt.AA_DontUseNativeDialogs, True)
    app = QApplication(sys.argv)
    app.setApplicationName("Kája")
    app.setOrganizationName("Kájovo")

    _load_fonts()

    # default font (falls back if Montserrat missing)
    f = QFont("Montserrat", 10)
    app.setFont(f)

    settings = load_settings()
    w = MainWindow(settings)
    w.showMaximized()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
