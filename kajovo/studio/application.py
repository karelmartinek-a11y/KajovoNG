"""Jednotná sestava produkčního studia, testů a snímkovacího nástroje."""

from __future__ import annotations


from PySide6.QtCore import QSize, QTimer, Qt
from PySide6.QtGui import QIcon, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup, QDialog, QFrame, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMainWindow,
    QPlainTextEdit, QStackedWidget, QWidget,
)

from kajovo.core.model_registry import model_spec, selectable
from kajovo.core.resources import resource_path
from .batches import BatchesPage
from .cascades import CascadesPage
from .components import BranchMark, DetailDialog, Form, action, actions, caption, friendly_error, install_theme, scroll, vertical
from .context import StudioContext
from .command_palette import CommandPalette, PaletteCommand
from .history import HistoryPage
from .operations import Operations
from .navigation_icons import navigation_icon
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
        self.compatible = form.check("models.compatible", "Pouze modely pro text a soubory projektu", True)
        self.batch = form.check("models.batch", "Podpora dávkového zpracování")
        self.batch_endpoint = form.choice("models.batch_endpoint", "Druh dávkového zpracování", [
            ("Textové odpovědi", "/v1/responses"), ("Úprava obrázků", "/v1/images/edits"),
            ("Vytvoření obrázků", "/v1/images/generations"),
        ])
        self.images = form.check("models.images", "Model dokáže pracovat s obrázky")
        root.addWidget(form)
        self.listing = QListWidget()
        self.listing.setWordWrap(True)
        self.listing.setAccessibleName("Katalog modelů")
        root.addWidget(self.listing, 1)
        self.notice = caption("Seznam dostupných modelů se načte po uložení přístupového klíče.", "muted")
        root.addWidget(self.notice)
        root.addWidget(actions(action("models.refresh", "Obnovit katalog", self.refresh),
                               action("models.select", "Použít v zadání", self.select),
                               action("models.default", "Nastavit jako výchozí", self.set_default),
                               action("models.details", "Co vybraný model umí", self.details)))
        form.changed.connect(self.render)
        context.models_changed.connect(self.render)

    def refresh(self):
        try:
            self.context.refresh_models()
        except ValueError as error:
            self.notice.setText(friendly_error(error))

    def render(self):
        self.listing.clear()
        for model in self.context.models:
            if self.search.text().casefold() not in model.casefold():
                continue
            try:
                spec = model_spec(model)
            except ValueError:
                spec = {}
            image_batch = self.batch.isChecked() and self.batch_endpoint.currentData() != "/v1/responses"
            if self.compatible.isChecked() and not image_batch and not selectable(model):
                continue
            if self.batch.isChecked():
                from kajovo.core.model_registry import supports_batch_endpoint
                if not spec or not supports_batch_endpoint(model, self.batch_endpoint.currentData()):
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
            self.notice.setText("Vyberte model vhodný pro text a soubory projektu.")
            return
        from kajovo.core.config import save_settings
        import copy

        settings = copy.deepcopy(self.context.settings)
        settings.default_model = model
        try:
            save_settings(settings)
        except Exception as error:
            self.notice.setText(friendly_error(error))
            return
        self.context.settings.default_model = model
        self.context.settings_changed.emit()
        self.render()

    def details(self):
        model = self.selected()
        if not model:
            self.notice.setText("Vyberte model.")
            return
        try:
            spec = model_spec(model)
        except ValueError:
            spec = {}
        features = set(spec.get("features") or [])
        summary = ["Vybraný model: " + model]
        for title, enabled in (
            ("Odpovědi na textová zadání", spec.get("responses")),
            ("Dávkové zpracování", spec.get("batch")),
            ("Práce s obrázky ve vstupu", "image_input" in features),
            ("Hledání v připojených dokumentech", "file_search" in features),
        ):
            summary.append(title + ": " + ("Ano" if enabled else "Podpora není doložena"))
        summary.append("Jde o schopnosti uvedené v katalogu aplikace. Dostupnost konkrétní práce se kontroluje také podle vašeho účtu.")
        DetailDialog(
            "Pravidla vybraného modelu",
            "\n\n".join(summary),
            self,
            self.context.model_details(model),
        ).exec()


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
        side.setSpacing(6)
        brand = QWidget()
        brand_layout = QHBoxLayout(brand)
        brand_layout.setContentsMargins(0, 0, 0, 12)
        brand_layout.setSpacing(10)
        self.logo = QLabel()
        icon_path = resource_path("studio-symbol.png")
        pixmap = QPixmap(str(icon_path))
        if not pixmap.isNull():
            self.logo.setPixmap(pixmap.scaled(64, 64, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            self.setWindowIcon(QIcon(str(icon_path)))
        self.logo.setAccessibleName("Logo Kájovo NG")
        brand_layout.addWidget(self.logo)
        brand_text = QWidget()
        brand_text_layout = vertical(brand_text, 0)
        brand_text_layout.setSpacing(4)
        brand_text_layout.addWidget(caption("Kájovo NG", "brand"))
        brand_text_layout.addWidget(caption("ŘÍDICÍ STUDIO", "eyebrow"))
        brand_layout.addWidget(brand_text, 1)
        side.addWidget(brand)
        divider = QFrame()
        divider.setObjectName("studioDivider")
        divider.setFixedHeight(1)
        side.addWidget(divider)
        side.addSpacing(10)
        self.navigation_area = scroll(self.sidebar)
        self.navigation_area.setFixedWidth(260)
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
        self.quick_button = action("navigation.quick", "Rychlý přechod  Ctrl+K", self.open_command_palette)
        self.quick_button.setIcon(navigation_icon("search"))
        self.quick_button.setAccessibleName("Rychlý přechod do sekce nebo k probíhající práci")
        self.quick_button.setToolTip("Vyhledat sekci, nástroj nebo probíhající práci · Ctrl+K")
        header_layout.addWidget(self.quick_button)
        self.detach_button = action("page.detach", "Samostatné okno", self.detach_page)
        header_layout.addWidget(self.detach_button)
        body.addWidget(header)
        self.stack = QStackedWidget()
        body.addWidget(self.stack, 1)
        self.activity = caption("Připraveno k práci", "muted")
        self.mark = BranchMark()
        footer = QWidget()
        footer.setObjectName("studioFooter")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(0, 8, 0, 0)
        footer_layout.addWidget(self.mark)
        footer_layout.addWidget(self.activity, 1)
        footer_layout.addWidget(action("operations.open", "Přehled probíhající práce", self.operations.show_all))
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
            "ZAČÍNÁME\n\nV Nastavení uložte přístupový klíč ke službě OpenAI. V sekci Výběr modelu obnovte seznam "
            "a vyberte model, který zadání zpracuje. V Zadání pojmenujte práci, popište požadovaný výsledek a vyberte potřebné složky. "
            "Tlačítko Spustit práci odešle zadání službě; její použití je placené.\n\n"
            "DRUHY PRÁCE\n\nVytvoření projektu připraví soubory nového projektu. Úprava projektu mění existující soubory. "
            "Odpověď na dotaz vrátí textovou odpověď. Vytvoření jednoho souboru uloží jeden výsledek pod zadaným názvem. "
            "Příprava bez zápisu zobrazí návrh změn; samotné soubory projektu při ní neměníte.\n\n"
            "PODKLADY\n\nNahrajte soubor a připojte jej k zadání. Odpojením jej odeberete jen ze zadání. "
            "Odstranění ze služby je samostatná akce, kterou musíte potvrdit.\n\n"
            "DÁVKY\n\nSlužba může zpracovat více úloh najednou na pozadí. Po dokončení převezměte výsledky a ověřte je. "
            "Konec místního sledování nezastaví práci služby; nový stav zjistíte tlačítkem Obnovit.\n\n"
            "HISTORIE\n\nUložené zadání lze zkopírovat a znovu spustit. Pro pokračování od rozpracované části potřebujete "
            "ověřený bod obnovy: uložený stav, od kterého aplikace umí bezpečně navázat. Původní záznam se zachová. "
            "Převzetí souborů samo o sobě nepotvrzuje, že vytvořený program správně funguje.\n\n"
            "PRŮBĚH\n\nOkno průběhu lze skrýt a znovu otevřít v přehledu práce. Kruhový ukazatel počítá potvrzené kroky. "
            "Pohyb ukazatele při čekání neudává procento hotové práce. Po žádosti o zastavení aplikace čeká na bezpečné ukončení.\n\n"
            "OVLÁDÁNÍ\n\nKlávesou Tab procházejte ovladače, mezerníkem přepínejte volby. Přetažení souborů je dostupné ve Fotografiích; "
            "složky lze přetahovat do příslušných polí. Pořadí kroků v Posloupnostech úloh lze změnit tažením i tlačítky. "
            "Animace lze omezit v Nastavení."
        )
        self.navigation_group = QButtonGroup(self)
        self.navigation_group.setExclusive(True)
        for key, title, page in (
            ("run", "Zadání", self.workbench), ("photos", "Fotografie", self.photos),
            ("comics", "Komiks", self.comics),
            ("cascade", "Posloupnosti úloh", self.cascades), ("resources", "Podklady", self.resources),
            ("batch", "Dávky", self.batches), ("history", "Historie", self.history),
            ("versions", "Verze projektu", self.versions), ("models", "Výběr modelu", self.models),
            ("settings", "Nastavení", self.settings_page), ("help", "Nápověda", help_page),
        ):
            self.pages[key] = page
            if key == "run":
                self.stack.addWidget(page)
            else:
                page.setMinimumHeight(620)
                self.stack.addWidget(scroll(page))
            button = action("navigation." + key, title, lambda checked=False: None, "navigation")
            button.setIcon(navigation_icon(key))
            button.setIconSize(QSize(20, 20))
            button.setCheckable(True)
            self.navigation_group.addButton(button)
            # Přístupná akce Toggle mění výběr bez signálu clicked.
            button.toggled.connect(lambda checked, target=key: self.select_page(target) if checked else None)
            side.addWidget(button)
            self.navigation[key] = button
        side.addStretch()
        converter_button = action("converter.open", "Převod textů", self.open_converter)
        converter_button.setIcon(navigation_icon("converter"))
        side.addWidget(converter_button)
        self.command_palette = CommandPalette([], self)
        self.command_palette.command_selected.connect(self.execute_palette_command)
        self.command_shortcuts = []
        for sequence in ("Ctrl+K", "Meta+K"):
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.setContext(Qt.WindowShortcut)
            shortcut.activated.connect(self.open_command_palette)
            self.command_shortcuts.append(shortcut)
        self.operations.changed.connect(self.update_activity)
        self.context.settings_changed.connect(self.update_activity)
        self.history.activate_workbench.connect(lambda: self.select_page("run"))
        self.history.activate_comic.connect(self.open_comic_operation)
        self.history.activate_batch.connect(self.open_history_batch)
        self.shortcut = QShortcut(QKeySequence("Ctrl+Return"), self)
        self.shortcut.activated.connect(self.start_current)
        self.select_page("run")
        self.update_activity()
        if self.context.api_key:
            QTimer.singleShot(0, self.context.ensure_models)

    def open_history_batch(self, identifier):
        self.select_page("batch")
        self.batches.focus_batch(identifier)

    def open_comic_operation(self, identifier):
        try:
            operation = self.comics.service.store.get("operations", identifier)
            self.comics.project_id = operation["project_id"]
            self.comics.refresh_projects()
            self.comics.tabs.setCurrentWidget(self.comics.history_page)
            self.select_page("comics")
        except ValueError as error:
            self.history.notice.setText(friendly_error(error))

    def select_page(self, key):
        self.stack.setCurrentIndex(list(self.pages).index(key))
        self.heading.setText(self.navigation[key].text())
        for name, button in self.navigation.items():
            button.setChecked(name == key)
        if key == "history" and not self.history.records:
            self.history.refresh()
        if key == "photos":
            self.photos.page_activated()
        if key == "batch":
            self.batches.page_activated()
        if key in {"photos", "cascade", "models"}:
            self.context.ensure_models()
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

    def open_command_palette(self):
        descriptions = {
            "run": "Připravit novou práci", "photos": "Hromadné úpravy fotografií",
            "comics": "Postavy, prostředí a obrázky příběhu", "cascade": "Propojit kroky a jejich výsledky",
            "resources": "Soubory a knihovny dokumentů", "batch": "Vzdálené dávky a převzetí výsledků",
            "history": "Běhy, výsledky a další postup", "versions": "Soubory, změny a uložené verze",
            "models": "Vybrat model pro práci", "settings": "Přístup a nastavení aplikace",
            "help": "Jak pracovat se Studiem",
        }
        commands = [PaletteCommand("section:" + key, button.text(), descriptions[key])
                    for key, button in self.navigation.items()]
        commands.extend((
            PaletteCommand("tool:converter", "Převod textů", "Záloha a převod do UTF-8", category="Nástroje"),
            PaletteCommand("work:overview", "Přehled probíhající práce", "Otevřít již spuštěné úlohy", category="Práce"),
        ))
        commands.extend(PaletteCommand("operation:" + record.identifier, record.title,
                                       "Otevřít průběh této úlohy", category="Probíhá")
                        for record in self.operations.active)
        if self.converter is not None:
            commands.extend(PaletteCommand("converter-operation:" + record.identifier, record.title,
                                           "Otevřít průběh převodu textů", category="Probíhá")
                            for record in self.converter.operations.active)
        self.command_palette.set_commands(commands)
        self.command_palette.open()

    def execute_palette_command(self, key):
        if key.startswith("section:"):
            section = key.removeprefix("section:")
            if section in self.pages:
                self.select_page(section)
        elif key == "tool:converter":
            self.open_converter()
        elif key == "work:overview":
            self.show_work_overview()
        elif key.startswith("operation:"):
            self.operations.show_operation(key.removeprefix("operation:"))
        elif key.startswith("converter-operation:") and self.converter is not None:
            self.converter.operations.show_operation(key.removeprefix("converter-operation:"))

    def show_work_overview(self):
        converter_active = self.converter is not None and self.converter.operations.active
        if self.operations.active or not converter_active:
            self.operations.show_all()
        if converter_active:
            self.converter.show()
            self.converter.raise_()
            self.converter.operations.show_all()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "navigation_area"):
            self.navigation_area.setVisible(event.size().width() >= 1000)
            self.menu_button.setVisible(event.size().width() < 1000)
            if hasattr(self, "quick_button"):
                narrow = event.size().width() < 1000
                self.quick_button.setText("Přejít…" if narrow else "Rychlý přechod  Ctrl+K")
                self.detach_button.setText("Okno" if narrow else "Samostatné okno")

    def update_activity(self):
        count = len(self.operations.active)
        self.activity.setText(f"Právě spuštěné úlohy: {count}" if count else "V tomto okně nyní neprobíhá práce")
        self.mark.set_running(bool(count), self.context.settings.ui_reduced_motion)
        if self.converter is not None:
            self.converter.set_reduced_motion(self.context.settings.ui_reduced_motion)

    def open_converter(self):
        from .converter import ConverterWindow

        if self.converter is None:
            self.converter = ConverterWindow(self, reduced_motion=self.context.settings.ui_reduced_motion)
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
            self.show_work_overview()
        else:
            if not self.comics.save_pending():
                self.select_page("comics")
                event.ignore()
                return
            for dialog in list(self.detached.values()):
                dialog.reject()
            for record in self.operations.records.values():
                if record.dialog is not None:
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
