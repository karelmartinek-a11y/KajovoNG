from __future__ import annotations

import logging
import sys
from dataclasses import dataclass

from PySide6.QtWidgets import QApplication

from kajovospend.app.container import ServiceContainer
from kajovospend.branding.app_icon import build_app_icon
from kajovospend.branding.theme import stylesheet
from kajovospend.diagnostics.logging_setup import configure_logging

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class BootstrapContext:
    app: QApplication
    container: ServiceContainer


class AppBootstrapper:
    def create_application(self, argv: list[str] | None = None) -> BootstrapContext:
        container = ServiceContainer.build()
        configure_logging(container.config.log_dir)
        LOGGER.info('bootstrap.start', extra={'event_name': 'bootstrap.start', 'app_slug': container.config.slug, 'app_version': container.config.version, 'environment': container.config.environment})
        app = QApplication.instance() or QApplication(argv or sys.argv)
        app.setApplicationName(container.config.app_name)
        app.setApplicationDisplayName(container.config.app_name)
        app.setApplicationVersion(container.config.version)
        app.setOrganizationName(container.config.organization)
        app.setDesktopFileName(container.config.slug)
        app.setWindowIcon(build_app_icon())
        app.setStyleSheet(stylesheet(container.settings))
        return BootstrapContext(app=app, container=container)
