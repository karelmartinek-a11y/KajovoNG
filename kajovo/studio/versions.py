"""Pohled na lokální Git a editor nad samostatným backendem projektu."""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QListWidget, QListWidgetItem, QPlainTextEdit, QTabWidget, QWidget

from kajovo.core.project_git import ProjectGit
from .components import Form, PathInput, action, actions, caption, confirm, friendly_error, vertical
from .file_dialogs import get_existing_directory
from .resources import ValueDialog
from .presentation import git_status_readable


class VersionsPage(QWidget):
    def __init__(self, context, parent=None):
        super().__init__(parent)
        self.context = context
        self.busy = False
        self.loaded = None
        self.generation = 0
        root = vertical(self)
        form = Form()
        self.path = form.add("git.root", "Složka projektu", PathInput(directories=True))
        self.path.setText(str(Path.cwd()))
        self.path.textChanged.connect(self.invalidate)
        root.addWidget(form)
        root.addWidget(actions(action("git.browse", "Vybrat projekt", self.browse), action("git.refresh", "Načíst stav", self.refresh)))
        self.tabs = QTabWidget()
        page = QWidget()
        body = vertical(page)
        form = Form()
        self.remote = form.text("git.remote", "Vzdálená adresa pro sdílení verzí projektu")
        body.addWidget(form)
        body.addWidget(actions(action("git.init", "Začít ukládat verze projektu", lambda: self.execute("Založení repozitáře", lambda service: service.init())),
                               action("git.remote.save", "Uložit vzdálenou adresu", self.set_remote),
                               action("git.pull", "Stáhnout změny", lambda: self.synchronize(False)),
                               action("git.push", "Odeslat změny", lambda: self.synchronize(True))))
        self.status = QPlainTextEdit()
        self.status.setReadOnly(True)
        self.status.setAccessibleName("Stav repozitáře")
        body.addWidget(self.status)
        self.technical_status = None
        body.addWidget(action("git.status.technical", "Technický záznam verzovacího systému", self.show_technical_status))
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
        self.generation += 1
        self.loaded = None
        if hasattr(self, "editor"):
            self.editor.setReadOnly(True)

    def browse(self):
        path = get_existing_directory(self, "Složka projektu", self.path.text())
        if path:
            self.path.setText(path)
            self.refresh()

    def execute(self, title, call, receive=None, *, reserve=True):
        if self.busy:
            return
        try:
            service = ProjectGit(self.path.text())
            if reserve:
                self.context.operations.assert_output_available(service.root)
        except ValueError as error:
            self.notice.setText(friendly_error(error))
            return
        self.busy = True
        root = self.path.text()
        generation = self.generation
        def deliver(value):
            if self.path.text() == root and generation == self.generation:
                (receive or self.render)(value["git_result"])
        record = self.context.operations.start(
            title, lambda task: {"status": "completed", "git_result": call(service)}, deliver,
            output_dir=service.root if reserve else None,
        )
        self.context.operations.on_finished(record, lambda: setattr(self, "busy", False))
        return record

    def render(self, result):
        self.path.setText(result["root"])
        self.technical_status = result["status"]
        self.status.setPlainText(git_status_readable(result["status"]))
        self.remote.setText(result["remote"])
        self.tags.clear()
        types = result.get("milestone_types") or {}
        for name in result["tags"]:
            legacy = types.get(name) == "legacy_commit_only"
            item = QListWidgetItem(
                name + (" · starší milník, pouze uložené změny" if legacy else " · úplná kopie souborů")
            )
            item.setData(Qt.UserRole, name)
            self.tags.addItem(item)
        self.files.clear()
        self.files.addItems(result["files"])
        self.notice.setText(
            result.get("milestone_notice") or "Stav projektu byl načten."
        )

    def refresh(self):
        self.execute("Načtení projektu", lambda service: service.snapshot(), reserve=False)

    def show_technical_status(self):
        from .components import DetailDialog

        DetailDialog("Technický záznam verzovacího systému", "Úplný výstup kontroly verzí projektu.", self, self.technical_status).exec()

    def set_remote(self):
        remote = self.remote.text().strip()
        self.execute("Uložení vzdálené adresy", lambda service: service.set_remote(remote))

    def synchronize(self, push):
        self.execute("Odeslání změn" if push else "Stažení změn", lambda service: service.synchronize(push))

    def tag(self):
        item = self.tags.currentItem()
        return str(item.data(Qt.UserRole) or "") if item else ""

    def create_tag(self):
        dialog = ValueDialog("Nový milník", "Název milníku", parent=self)
        if dialog.exec() == QDialog.Accepted:
            self.execute("Vytvoření milníku", lambda service: service.milestone(dialog.value))

    def restore_tag(self):
        name = self.tag()
        if name and confirm(self, "Obnovit milník", "Vrátit soubory spravované verzovacím systémem do zvoleného milníku? Nejprve uložte všechny současné změny do milníku."):
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

        self.execute("Otevření souboru", lambda service: service.read_file(relative, tag), receive, reserve=False)

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
