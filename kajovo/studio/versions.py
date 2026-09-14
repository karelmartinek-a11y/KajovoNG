"""Pohled na lokální Git a editor nad samostatným backendem projektu."""

from pathlib import Path

from PySide6.QtWidgets import QDialog, QFileDialog, QListWidget, QPlainTextEdit, QTabWidget, QWidget

from kajovo.core.project_git import ProjectGit
from .components import Form, PathInput, action, actions, caption, confirm, vertical
from .resources import ValueDialog


class VersionsPage(QWidget):
    def __init__(self, context, parent=None):
        super().__init__(parent)
        self.context = context
        self.busy = False
        self.loaded = None
        root = vertical(self)
        form = Form()
        self.path = form.add("git.root", "Adresář projektu", PathInput(directories=True))
        self.path.setText(str(Path.cwd()))
        self.path.textChanged.connect(self.invalidate)
        root.addWidget(form)
        root.addWidget(actions(action("git.browse", "Vybrat projekt", self.browse), action("git.refresh", "Načíst stav", self.refresh)))
        self.tabs = QTabWidget()
        page = QWidget()
        body = vertical(page)
        form = Form()
        self.remote = form.text("git.remote", "Vzdálený repozitář")
        body.addWidget(form)
        body.addWidget(actions(action("git.init", "Založit repozitář", lambda: self.execute("Založení repozitáře", lambda service: service.init())),
                               action("git.remote.save", "Uložit vzdálenou adresu", self.set_remote),
                               action("git.pull", "Stáhnout změny", lambda: self.synchronize(False)),
                               action("git.push", "Odeslat změny", lambda: self.synchronize(True))))
        self.status = QPlainTextEdit()
        self.status.setReadOnly(True)
        self.status.setAccessibleName("Stav repozitáře")
        body.addWidget(self.status)
        self.tags = QListWidget()
        self.tags.setAccessibleName("Milníky projektu")
        body.addWidget(self.tags)
        body.addWidget(actions(action("git.tag.create", "Nový milník", self.create_tag),
                               action("git.tag.restore", "Obnovit milník", self.restore_tag),
                               action("git.tag.delete", "Odstranit milník", self.delete_tag, "danger")))
        body.addWidget(action("git.delete", "Odstranit místní historii Git", self.remove_repository, "danger"))
        self.tabs.addTab(page, "Verze a synchronizace")
        page = QWidget()
        body = vertical(page)
        self.files = QListWidget()
        self.files.setAccessibleName("Soubory projektu")
        self.files.setWordWrap(True)
        self.files.itemDoubleClicked.connect(lambda item: self.open_file())
        body.addWidget(self.files, 1)
        body.addWidget(actions(action("git.file.open", "Otevřít vybraný soubor", self.open_file),
                               action("git.file.diff", "Porovnat s milníkem", lambda: self.open_file(compare=True))))
        self.editor = QPlainTextEdit()
        self.editor.setReadOnly(True)
        self.editor.setAccessibleName("Obsah souboru nebo porovnání")
        body.addWidget(self.editor, 2)
        body.addWidget(action("git.file.save", "Uložit soubor", self.save_file, "primary"))
        self.tabs.addTab(page, "Soubory a porovnání")
        root.addWidget(self.tabs, 1)
        self.notice = caption("Vyberte kořen projektu a načtěte jeho stav.", "muted")
        root.addWidget(self.notice)

    def invalidate(self):
        self.loaded = None
        if hasattr(self, "editor"):
            self.editor.setReadOnly(True)

    def browse(self):
        path = QFileDialog.getExistingDirectory(self, "Adresář projektu", self.path.text())
        if path:
            self.path.setText(path)
            self.refresh()

    def execute(self, title, call, receive=None):
        if self.busy:
            return
        try:
            service = ProjectGit(self.path.text())
        except ValueError as error:
            self.notice.setText(str(error))
            return
        self.busy = True
        root = self.path.text()
        def deliver(value):
            if self.path.text() == root:
                (receive or self.render)(value)
        record = self.context.operations.start(title, lambda task: call(service), deliver)
        record.worker.finished.connect(lambda: setattr(self, "busy", False))
        return record

    def render(self, result):
        self.path.setText(result["root"])
        self.status.setPlainText(result["status"])
        self.remote.setText(result["remote"])
        self.tags.clear()
        self.tags.addItems(result["tags"])
        self.files.clear()
        self.files.addItems(result["files"])
        self.notice.setText("Stav projektu byl načten.")

    def refresh(self):
        self.execute("Načtení projektu", lambda service: service.snapshot())

    def set_remote(self):
        remote = self.remote.text().strip()
        self.execute("Uložení vzdálené adresy", lambda service: service.set_remote(remote))

    def synchronize(self, push):
        self.execute("Odeslání změn" if push else "Stažení změn", lambda service: service.synchronize(push))

    def tag(self):
        return self.tags.currentItem().text() if self.tags.currentItem() else ""

    def create_tag(self):
        dialog = ValueDialog("Nový milník", "Název milníku", parent=self)
        if dialog.exec() == QDialog.Accepted:
            self.execute("Vytvoření milníku", lambda service: service.milestone(dialog.value))

    def restore_tag(self):
        name = self.tag()
        if name and confirm(self, "Obnovit milník", "Obnovit sledované soubory ze zvoleného milníku? Pracovní strom musí být čistý."):
            self.execute("Obnova milníku", lambda service: service.restore(name))

    def delete_tag(self):
        name = self.tag()
        if name and confirm(self, "Odstranit milník", f"Odstranit označení milníku {name}?"):
            self.execute("Odstranění milníku", lambda service: service.remove_milestone(name))

    def remove_repository(self):
        if confirm(self, "Odstranit historii Git", f"Trvale odstranit vlastní adresář .git projektu {self.path.text()}? Pracovní soubory zůstanou zachované."):
            self.execute("Odstranění historie Git", lambda service: service.remove_repository())

    def open_file(self, checked=False, compare=False):
        item = self.files.currentItem()
        if not item:
            return
        relative = item.text()
        root = self.path.text()
        self.invalidate()
        tag = self.tag() if compare else ""
        if compare and not tag:
            self.notice.setText("Vyberte milník pro porovnání.")
            return

        def receive(result):
            if self.path.text() != root:
                return
            text, digest = result
            self.editor.setPlainText(text)
            self.editor.setReadOnly(compare)
            self.loaded = (relative, digest, root) if not compare else None

        self.execute("Otevření souboru", lambda service: service.read_file(relative, tag), receive)

    def save_file(self):
        if not self.loaded or self.editor.isReadOnly():
            self.notice.setText("Nejprve načtěte soubor k úpravě.")
            return
        relative, digest, root = self.loaded
        if root != self.path.text():
            return
        text = self.editor.toPlainText()

        def receive(result):
            self.loaded = (relative, result[1], root)
            self.notice.setText("Soubor byl uložen.")

        self.execute("Uložení souboru", lambda service: service.write_file(relative, text, digest), receive)
