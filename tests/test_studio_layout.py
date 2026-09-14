"""Čitelnost společných popisků a barev použitých ve studiu."""

from PySide6.QtWidgets import QApplication, QLabel

from kajovo.studio.components import COLORS, Form, install_theme


def test_file_dialog_labels_are_czech(qtbot):
    from PySide6.QtWidgets import QFileDialog

    install_theme(QApplication.instance())
    dialog = QFileDialog()
    dialog.setOption(QFileDialog.DontUseNativeDialog)
    qtbot.addWidget(dialog)
    assert dialog.labelText(QFileDialog.LookIn) == "Umístění:"
    assert dialog.labelText(QFileDialog.FileType) == "Typ souborů:"


def test_wrapped_checkbox_labels_get_required_height(qtbot):
    install_theme(QApplication.instance())
    form = Form()
    form.check("check", "Podpora dávkového zpracování a ověřování výsledků")
    qtbot.addWidget(form)
    form.resize(350, 100)
    form.show()
    qtbot.wait(50)
    label = form.findChild(QLabel)
    assert label.height() >= label.heightForWidth(label.width())


def test_text_palette_has_minimum_contrast():
    def luminance(color):
        values = [int(color[index:index + 2], 16) / 255 for index in (1, 3, 5)]
        values = [value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4 for value in values]
        return sum(value * weight for value, weight in zip(values, (0.2126, 0.7152, 0.0722), strict=True))
    for foreground in ("text", "muted", "success", "warning", "danger"):
        for background in ("canvas", "surface", "raised"):
            ratio = (luminance(COLORS[foreground]) + 0.05) / (luminance(COLORS[background]) + 0.05)
            assert ratio >= 4.5, (foreground, background, ratio)


def test_terminal_progress_preserves_measured_units(qtbot):
    from kajovo.core.progress import ProgressEvent
    from kajovo.studio.operations import OperationDialog
    dialog = OperationDialog("Převod")
    qtbot.addWidget(dialog)
    dialog.on_event(ProgressEvent("Soubory", completed=3, total=8, unit="souborů"))
    dialog.on_event(ProgressEvent("RUN", "partial"))
    dialog.finish("partial")
    assert dialog.progress.maximum() == 8
    assert dialog.progress.value() == 3
    assert "3 z 8" in dialog.counts.text()
