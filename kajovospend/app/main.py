from __future__ import annotations

from kajovospend.app.bootstrap import AppBootstrapper
from kajovospend.application.controller import AppController
from kajovospend.ui.main_window import MainWindow


def main() -> int:
    context = AppBootstrapper().create_application()
    controller = AppController(context.container)
    controller.bootstrap()
    window = MainWindow(controller)
    window.place_on_primary_screen()
    return context.app.exec()
