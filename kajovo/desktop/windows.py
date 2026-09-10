"""Samostatná okna používají nové stránky a vracejí je do původního místa."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialogButtonBox,
    QListWidget,
    QListWidgetItem,
    QCheckBox,
    QProgressBar,
)
from ..core.model_registry import selectable
from .design import column, row, label, text, button, FitDialog, scroll
from .jobs import Jobs


class DetachedPageDialog(FitDialog):
    def __init__(self, title, owner, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(1000, 740)
        self.owner = owner
        self.page = owner.takeWidget()
        layout = column(self, 20)
        layout.addWidget(label(title, "Heading"))
        self.container = scroll(self.page)
        layout.addWidget(self.container, 1)
        layout.addWidget(button("Vrátit do hlavního okna", self.accept))
        self.finished.connect(self.restore)

    def restore(self, result):
        self.container.takeWidget()
        self.owner.setWidget(self.page)
        self.page.show()

    def done(self, result):
        if any(manager.active for manager in self.findChildren(Jobs)):
            return
        super().done(result)


class ModelPicker(FitDialog):
    def __init__(self, models, cache, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Vybrat model")
        self.resize(700, 570)
        self.models, self.cache = models, cache
        layout = column(self, 20)
        layout.addWidget(label("Model pro zadání", "Heading"))
        self.query = text(placeholder="Hledat model")
        layout.addWidget(self.query)
        self.filters = {}
        controls = []
        for key, title in (
            ("supports_previous_response_id", "Návaznost"),
            ("supports_temperature", "Teplota"),
            ("supports_file_search", "Hledání"),
        ):
            control = QCheckBox(title)
            control.toggled.connect(self.refresh)
            self.filters[key] = control
            controls.append(control)
        layout.addWidget(row(*controls))
        self.include_unknown = QCheckBox("Ukázat také nedostupné a nepodporované")
        self.include_unknown.toggled.connect(self.refresh)
        layout.addWidget(self.include_unknown)
        self.listing = QListWidget()
        layout.addWidget(self.listing, 1)
        self.query.textChanged.connect(self.refresh)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Použít model")
        buttons.button(QDialogButtonBox.Cancel).setText("Zrušit")
        buttons.accepted.connect(self.apply)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.selected_model = ""
        self.refresh()

    def refresh(self):
        self.listing.clear()
        for model in self.models:
            allowed = selectable(model)
            caps = self.cache.get(model)
            if (
                self.query.text().lower() not in model.lower()
                or not allowed
                and not self.include_unknown.isChecked()
            ):
                continue
            if any(
                control.isChecked() and not getattr(caps, key, False)
                for key, control in self.filters.items()
            ):
                continue
            item = QListWidgetItem(model + ("" if allowed else " · nepodporovaný"))
            item.setData(Qt.UserRole, model)
            if not allowed:
                item.setFlags(item.flags() & ~Qt.ItemIsEnabled)
            self.listing.addItem(item)

    def apply(self):
        item = self.listing.currentItem()
        if item and selectable(item.data(Qt.UserRole)):
            self.selected_model = item.data(Qt.UserRole)
            self.accept()


class SplashScreen(FitDialog):
    def __init__(self, title="Kájovo NG", subtitle="Pracovní studio", parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.SplashScreen)
        self.resize(460, 250)
        layout = column(self, 32)
        layout.addWidget(label(title, "Heading"))
        layout.addWidget(label(subtitle, "Subtitle"))
        self.status = label("Načítání pracovního prostředí…")
        layout.addWidget(self.status)
        progress = QProgressBar()
        progress.setRange(0, 0)
        layout.addWidget(progress)

    def set_status(self, value):
        self.status.setText(value)

    def finish(self):
        self.hide()
