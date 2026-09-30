"""České dialogy pro výběr souborů a složek se zřetelnou navigací."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog, QToolButton


class FileDialog(QFileDialog):
    """Ne-nativní Qt dialog se slovně označeným pohybem mezi složkami."""

    def __init__(self, parent=None, title="Vybrat soubor", directory="", file_filter=""):
        super().__init__(parent, title, directory, file_filter)
        self.setOption(QFileDialog.DontUseNativeDialog, True)
        for object_name, title, tip in (
            ("backButton", "Zpět", "Vrátit se do předchozí složky"),
            ("toParentButton", "O úroveň výš", "Přejít do nadřazené složky"),
            ("newFolderButton", "Nová složka", "Založit složku; její název zadáte v následujícím okně"),
        ):
            button = self.findChild(QToolButton, object_name)
            if button is not None:
                button.setText(title)
                button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
                button.setToolTip(tip)
                button.setAccessibleName(title)


def _dialog(parent, title, directory, file_filter):
    return FileDialog(parent, title, directory, file_filter)


def get_existing_directory(parent=None, caption="Vybrat složku", directory=""):
    dialog = _dialog(parent, caption, directory, "")
    dialog.setFileMode(QFileDialog.Directory)
    dialog.setOption(QFileDialog.ShowDirsOnly, True)
    if dialog.exec() != QFileDialog.Accepted:
        return ""
    paths = dialog.selectedFiles()
    return paths[0] if paths else dialog.directory().absolutePath()


def get_open_file_name(parent=None, caption="Otevřít soubor", directory="", file_filter=""):
    dialog = _dialog(parent, caption, directory, file_filter)
    dialog.setFileMode(QFileDialog.ExistingFile)
    dialog.setAcceptMode(QFileDialog.AcceptOpen)
    if dialog.exec() != QFileDialog.Accepted:
        return "", ""
    paths = dialog.selectedFiles()
    return (paths[0] if paths else ""), dialog.selectedNameFilter()


def get_open_file_names(parent=None, caption="Otevřít soubory", directory="", file_filter=""):
    dialog = _dialog(parent, caption, directory, file_filter)
    dialog.setFileMode(QFileDialog.ExistingFiles)
    dialog.setAcceptMode(QFileDialog.AcceptOpen)
    if dialog.exec() != QFileDialog.Accepted:
        return [], ""
    return dialog.selectedFiles(), dialog.selectedNameFilter()


def get_save_file_name(parent=None, caption="Uložit soubor", directory="", file_filter=""):
    dialog = _dialog(parent, caption, directory, file_filter)
    dialog.setFileMode(QFileDialog.AnyFile)
    dialog.setAcceptMode(QFileDialog.AcceptSave)
    if dialog.exec() != QFileDialog.Accepted:
        return "", ""
    paths = dialog.selectedFiles()
    return (paths[0] if paths else ""), dialog.selectedNameFilter()
