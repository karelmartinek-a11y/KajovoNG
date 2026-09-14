"""Jednotná sestava produkčního studia, testů a snímkovacího nástroje."""

from __future__ import annotations


from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMainWindow,
    QPlainTextEdit, QStackedWidget, QWidget,
)

from kajovo.core.model_registry import model_spec, selectable
from kajovo.core.resources import resource_path
from .batches import BatchesPage
from .cascades import CascadesPage
from .components import BranchMark, DetailDialog, Form, action, actions, caption, install_theme, scroll, vertical
from .context import StudioContext
from .history import HistoryPage
from .operations import Operations
from .photos import PhotosPage
from .comics import ComicsPage
from .resources import ResourcesPage
from .settings import SettingsPage
from .versions import VersionsPage
from .workbench import Workbench


class ModelsPage(QWidget):
    def __init__(self, context, workbench, parent=None):
        super().__init__(parent)
        self.context = context
        self.workbench = workbench
        root = vertical(self)
        form = Form()
        self.search = form.text("models.search", "Vyhledat model")
        self.compatible = form.check("models.compatible", "Pouze modely vhodné pro práci s projektem", True)
        self.batch = form.check("models.batch", "Podpora dávkového zpracování")
        self.images = form.check("models.images", "Podpora obrazového vstupu")
        root.addWidget(form)
        self.listing = QListWidget()
        self.listing.setWordWrap(True)
        self.listing.setAccessibleName("Katalog modelů")
        root.addWidget(self.listing, 1)
        self.notice = caption("Katalog účtu se načte až po ověření přístupu.", "muted")
        root.addWidget(self.notice)
        root.addWidget(actions(action("models.refresh", "Obnovit katalog", self.refresh),
                               action("models.select", "Použít v zadání", self.select),
                               action("models.default", "Nastavit jako výchozí", self.set_default),
                               action("models.details", "Schopnosti a pravidla", self.details)))
        form.changed.connect(self.render)
        context.models_changed.connect(self.render)

    def refresh(self):
        try:
            self.context.refresh_models()
        except ValueError as error:
            self.notice.setText(str(error))

    def render(self):
        self.listing.clear()
        for model in self.context.models:
            if self.search.text().casefold() not in model.casefold():
                continue
            try:
                spec = model_spec(model)
            except ValueError:
                spec = {}
            if self.compatible.isChecked() and not selectable(model):
                continue
            if self.batch.isChecked() and not spec.get("batch"):
                continue
            if self.images.isChecked() and "image_input" not in spec.get("features", []):
                continue
            text = model + (" · výchozí" if model == self.context.settings.default_model else "")
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, model)
            self.listing.addItem(item)
        self.notice.setText(f"Modelů v účtovém katalogu: {len(self.context.models)}; zobrazeno: {self.listing.count()}.")

    def selected(self):
        item = self.listing.currentItem()
        return item.data(Qt.UserRole) if item else ""

    def select(self):
        model = self.selected()
        if model and selectable(model):
            widget = self.workbench.widgets["model"]
            widget.setCurrentIndex(widget.findData(model))
            self.notice.setText("Model je vybraný v zadání.")

    def set_default(self):
        model = self.selected()
        if not model or not selectable(model):
            self.notice.setText("Vyberte model kompatibilní s pracovními požadavky.")
            return
        from kajovo.core.config import save_settings
        import copy

        settings = copy.deepcopy(self.context.settings)
        settings.default_model = model
        try:
            save_settings(settings)
        except Exception as error:
            self.notice.setText(str(error))
            return
        self.context.settings.default_model = model
        self.context.settings_changed.emit()
        self.render()

    def details(self):
        try:
            spec = model_spec(self.selected())
        except ValueError as error:
            self.notice.setText(str(error))
            return
        DetailDialog("Pravidla vybraného modelu", self.selected(), self, spec).exec()


class StudioWindow(QMainWindow):
    def __init__(self, settings, *, api_key="", client_factory=None):
        super().__init__()
        self.setObjectName("studio.window")
        self.setWindowTitle("Kájovo NG · Řídicí studio")
        self.resize(1440, 960)
        self.setMinimumSize(640, 360)
        self.operations = Operations(self, settings.ui_reduced_motion)
        self.context = StudioContext(settings, self.operations, api_key=api_key, client_factory=client_factory, parent=self)
        self.pages = {}
        self.navigation = {}
        self.detached = {}
        self.converter = None
        root = QWidget()
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.sidebar = QFrame()
        side = vertical(self.sidebar, 20)
        self.logo = QLabel()
        icon_path = resource_path("studio-symbol.png")
        pixmap = QPixmap(str(icon_path))
        if not pixmap.isNull():
            self.logo.setPixmap(pixmap.scaled(58, 58, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            self.setWindowIcon(QIcon(str(icon_path)))
        self.logo.setAccessibleName("Logo Kájovo NG")
        side.addWidget(self.logo)
        side.addWidget(caption("Kájovo NG", "heading"))
        side.addWidget(caption("ŘÍDICÍ STUDIO", "muted"))
        self.navigation_area = scroll(self.sidebar)
        self.navigation_area.setFixedWidth(230)
        outer.addWidget(self.navigation_area)
        content = QWidget()
        body = vertical(content, 12)
        self.menu_button = action("navigation.toggle", "Sekce", self.toggle_navigation)
        self.heading = caption("Zadání", "heading")
        header = QWidget()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.addWidget(self.menu_button)
        header_layout.addWidget(self.heading, 1)
        header_layout.addWidget(action("page.detach", "Samostatné okno", self.detach_page))
        body.addWidget(header)
        self.stack = QStackedWidget()
        body.addWidget(self.stack, 1)
        self.activity = caption("Připraveno k práci", "muted")
        self.mark = BranchMark()
        footer = QWidget()
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(0, 0, 0, 0)
        footer_layout.addWidget(self.mark)
        footer_layout.addWidget(self.activity, 1)
        footer_layout.addWidget(action("operations.open", "Přehled operací", self.operations.show_all))
        body.addWidget(footer)
        outer.addWidget(content, 1)
        self.setCentralWidget(root)
        self.workbench = Workbench(self.context)
        self.cascades = CascadesPage(self.context)
        self.resources = ResourcesPage(self.context)
        self.photos = PhotosPage(self.context)
        self.comics = ComicsPage(self.context)
        self.batches = BatchesPage(self.context)
        self.history = HistoryPage(self.context, self.workbench)
        self.versions = VersionsPage(self.context)
        self.settings_page = SettingsPage(self.context)
        self.models = ModelsPage(self.context, self.workbench)
        help_page = QPlainTextEdit()
        help_page.setReadOnly(True)
        help_page.setAccessibleName("Nápověda")
        help_page.setPlainText(
            "ZAČÍNÁME\n\nV Nastavení uložte přístupový klíč. V Modelech obnovte katalog a vyberte model. "
            "V Zadání připravte projekt, požadovaný výsledek a případné adresáře. Spustit práci odešle placenou pracovní operaci.\n\n"
            "ZDROJE\n\nNahraný soubor připojte k zadání. Odpojení nemění vzdálený soubor; odstranění ze služby je samostatná potvrzovaná akce.\n\n"
            "DÁVKY\n\nDávkové zpracování souborů navazuje na živou přípravu projektu. Po dokončení služby je nutné převzít a ověřit výsledky. "
            "Vypršení místního sledování neruší vzdálenou dávku.\n\n"
            "HISTORIE\n\nZadání lze klonovat do nového běhu. Pokračování vyžaduje ověřený bezpečný checkpoint. "
            "Zdrojová evidence se nepřepisuje a starším záznamům se nedoplňují neznámá fakta.\n\n"
            "PRŮBĚH\n\nDialog lze skrýt a znovu otevřít v Přehledu operací. Animace značí čekající práci, ne potvrzení aktivity serveru. "
            "Čísla postupu pocházejí z dokončených jednotek. Zastavení čeká na bezpečné ukončení pracovníka.\n\n"
            "OVLÁDÁNÍ\n\nKlávesou Tab procházejte ovladače, mezerníkem přepínejte volby. Přetažení souborů je dostupné ve Fotografiích; "
            "adresáře lze přetahovat do příslušných polí. Pořadí kroků kaskády lze změnit tažením i tlačítky. "
            "Animace lze omezit v Nastavení."
        )
        for key, title, page in (
            ("run", "Zadání", self.workbench), ("photos", "Fotografie", self.photos),
            ("comics", "Komiks", self.comics),
            ("cascade", "Kaskády", self.cascades), ("resources", "Zdroje", self.resources),
            ("batch", "Dávky", self.batches), ("history", "Historie", self.history),
            ("versions", "Verze projektu", self.versions), ("models", "Modely", self.models),
            ("settings", "Nastavení", self.settings_page), ("help", "Nápověda", help_page),
        ):
            self.pages[key] = page
            if key == "run":
                self.stack.addWidget(page)
            else:
                page.setMinimumHeight(620)
                self.stack.addWidget(scroll(page))
            button = action("navigation." + key, title, lambda checked=False, target=key: self.select_page(target))
            button.setCheckable(True)
            side.addWidget(button)
            self.navigation[key] = button
        side.addStretch()
        side.addWidget(action("converter.open", "Převod textů", self.open_converter))
        self.operations.changed.connect(self.update_activity)
        self.history.activate_workbench.connect(lambda: self.select_page("run"))
        self.history.activate_comic.connect(self.open_comic_operation)
        self.shortcut = QShortcut(QKeySequence("Ctrl+Return"), self)
        self.shortcut.activated.connect(self.start_current)
        self.select_page("run")

    def open_comic_operation(self, identifier):
        try:
            operation = self.comics.service.store.get("operations", identifier)
            self.comics.project_id = operation["project_id"]
            self.comics.refresh_projects()
            self.comics.tabs.setCurrentIndex(4)
            self.select_page("comics")
        except ValueError as error:
            self.history.notice.setText(str(error))

    def select_page(self, key):
        self.stack.setCurrentIndex(list(self.pages).index(key))
        self.heading.setText(self.navigation[key].text())
        for name, button in self.navigation.items():
            button.setChecked(name == key)
        if self.width() < 1000:
            self.navigation_area.hide()
        if key in self.detached:
            self.detached[key].show()
            self.detached[key].raise_()

    def detach_page(self):
        index = self.stack.currentIndex()
        key = list(self.pages)[index]
        if key in self.detached:
            self.detached[key].raise_()
            return
        page = self.stack.widget(index)
        self.stack.removeWidget(page)
        placeholder = caption("Sekce je otevřená v samostatném okně; jeho zavřením ji vrátíte do studia.")
        self.stack.insertWidget(index, placeholder)
        self.stack.setCurrentIndex(index)
        dialog = QDialog(self)
        dialog.setWindowTitle("Kájovo NG · " + self.navigation[key].text())
        dialog.resize(1000, 800)
        vertical(dialog).addWidget(page)
        self.detached[key] = dialog

        def restore():
            active = self.stack.currentIndex()
            dialog.layout().removeWidget(page)
            self.stack.removeWidget(placeholder)
            self.stack.insertWidget(index, page)
            self.stack.setCurrentIndex(active)
            placeholder.deleteLater()
            self.detached.pop(key, None)
            dialog.deleteLater()

        dialog.finished.connect(restore)
        page.show()
        dialog.show()

    def toggle_navigation(self):
        self.navigation_area.setVisible(not self.navigation_area.isVisible())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "navigation_area"):
            self.navigation_area.setVisible(event.size().width() >= 1000)
            self.menu_button.setVisible(event.size().width() < 1000)

    def update_activity(self):
        count = len(self.operations.active)
        self.activity.setText(f"Pracující operace: {count}" if count else "Všechny místní operace skončily")
        self.mark.set_running(bool(count), self.context.settings.ui_reduced_motion)

    def open_converter(self):
        from .converter import ConverterWindow

        if self.converter is None:
            self.converter = ConverterWindow()
        self.converter.show()
        self.converter.raise_()

    def start_current(self):
        page = list(self.pages.values())[self.stack.currentIndex()]
        if page in (self.workbench, self.photos, self.cascades):
            page.start()

    def closeEvent(self, event):
        if self.operations.active or (self.converter and self.converter.operations.active):
            self.activity.setText("Ještě probíhají operace; dokončete nebo bezpečně zastavte práci před zavřením.")
            event.ignore()
            self.operations.show_all()
        else:
            if not self.comics.save_pending():
                self.select_page("comics")
                event.ignore()
                return
            for dialog in list(self.detached.values()):
                dialog.reject()
            for record in self.operations.records.values():
                record.dialog.close()
            if self.operations.overview:
                self.operations.overview.hide()
            if self.converter:
                self.converter.close()
            super().closeEvent(event)


def create_window(settings, *, api_key="", client_factory=None):
    from PySide6.QtWidgets import QApplication

    install_theme(QApplication.instance())
    return StudioWindow(settings, api_key=api_key, client_factory=client_factory)
