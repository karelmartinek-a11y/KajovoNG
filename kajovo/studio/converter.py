"""Samostatné rozhraní převodu textů se společnými komponentami studia."""

from __future__ import annotations

from PySide6.QtWidgets import QFileDialog, QMainWindow, QPlainTextEdit, QWidget

from kajovo.core.progress import ProgressEvent
from .components import (
    BranchMark, Form, PathInput, action, actions, caption, panel, scroll, vertical,
)
from .operations import Operations


class ConversionEvents:
    """Převádí skutečné události převodníku na signály správce operací."""

    def __init__(self, task):
        self.task = task
        self.error = None
        self.result = None

    def put(self, event):
        kind = event.get("type")
        if kind == "progress":
            self.task.progress_event.emit(ProgressEvent(
                str(event.get("phase", "Převod")),
                completed=event.get("done_units"), total=event.get("total_units"),
                unit="jednotek", detail=str(event.get("detail", "")),
            ))
        elif kind == "status":
            self.task.status.emit(str(event.get("message", "")))
        elif kind == "log":
            self.task.logline.emit(str(event.get("message", "")))
        elif kind == "error":
            self.error = str(event.get("message", "Převod se nezdařil."))
        elif kind == "done":
            self.result = event


class ConverterWindow(QMainWindow):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("converter.window")
        self.setWindowTitle("Kájovo NG · Převod textů")
        self.resize(1060, 820)
        self.setMinimumSize(640, 360)
        self.operations = Operations(self)
        root = QWidget()
        body = vertical(root, 20)
        contents = QWidget()
        area = vertical(contents)
        area.addWidget(BranchMark())
        area.addWidget(caption("Čitelné texty. Bez ztráty originálu.", "heading"))
        area.addWidget(caption("Záloha původních dat předchází každému převodu souborů a archivů.", "muted"))
        box, fields = panel("Adresáře ke kontrole")
        self.form = Form()
        self.paths = []
        for index in range(5):
            path = self.form.add(f"converter.path.{index}", f"Adresář {index + 1}", PathInput(directories=True))
            self.paths.append(path)
            self.form.body.addRow("", action(f"converter.browse.{index}", "Vybrat adresář", lambda checked=False, target=path: self.browse(target)))
        fields.addWidget(self.form)
        area.addWidget(box)
        backup, fields = panel("Bezpečná záloha")
        self.backup = PathInput(directories=True)
        self.backup.setObjectName("converter.backup")
        self.backup.setAccessibleName("Adresář pro zálohu")
        fields.addWidget(self.backup)
        fields.addWidget(action("converter.backup.browse", "Vybrat umístění zálohy", lambda: self.browse(self.backup)))
        area.addWidget(backup)
        self.validation = caption("Vyplňte alespoň jeden vstupní adresář a umístění zálohy.", "muted")
        area.addWidget(self.validation)
        self.result = QPlainTextEdit()
        self.result.setReadOnly(True)
        self.result.setAccessibleName("Výsledek převodu")
        self.result.setMinimumHeight(160)
        area.addWidget(self.result)
        body.addWidget(scroll(contents), 1)
        self.start_button = action("converter.start", "Zálohovat a převést", self.start, "primary")
        self.start_button.setEnabled(False)
        body.addWidget(actions(self.start_button, action("converter.operations", "Průběh operace", self.operations.show_all)))
        self.setCentralWidget(root)
        for path in [*self.paths, self.backup]:
            path.textChanged.connect(self.validate)
        self.operations.changed.connect(self.validate)

    def browse(self, target):
        path = QFileDialog.getExistingDirectory(self, "Vybrat adresář", target.text())
        if path:
            target.setText(path)

    def validate(self):
        from utf8nobom.app import validate_input_paths

        try:
            result = validate_input_paths([field.text() for field in self.paths], self.backup.text())
            for path in (*(target.path for target in result[0]), result[1]):
                self.operations.assert_output_available(path)
        except ValueError as error:
            self.validation.setText(str(error))
            self.start_button.setEnabled(False)
            return None
        self.validation.setText("Adresáře jsou připravené; před úpravami vznikne záloha.")
        self.start_button.setEnabled(not self.operations.active)
        return result

    def start(self):
        from utf8nobom.app import run_job

        if self.operations.active:
            return
        validated = self.validate()
        if validated is None:
            return
        targets, backup = validated

        def execute(task):
            events = ConversionEvents(task)
            run_job(targets, backup, events)
            if events.error:
                raise RuntimeError(events.error)
            return events.result

        def completed(result):
            self.result.setPlainText(str((result or {}).get("message", "Převod byl dokončen.")))

        self.operations.start("Záloha a převod textů", execute, completed,
                              write_roots=(*(target.path for target in targets), backup))

    def closeEvent(self, event):
        if self.operations.active:
            self.validation.setText("Převod ještě pracuje; okno lze zavřít po dokončení bezpečného zápisu.")
            event.ignore()
        else:
            super().closeEvent(event)


def main():
    import sys
    from PySide6.QtWidgets import QApplication
    from .components import install_theme

    app = QApplication.instance() or QApplication(sys.argv)
    install_theme(app)
    window = ConverterWindow()
    window.show()
    return app.exec()
