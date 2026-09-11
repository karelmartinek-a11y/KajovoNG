"""Pořídí skutečné Qt snímky s izolovanými ukázkovými daty, bez přístupu k síti."""

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

parser = argparse.ArgumentParser(description="Snímky nového desktopového UI bez síťových volání.")
parser.add_argument("--size", default="1366,900")
parser.add_argument("--scale", default="1")
parser.add_argument("--output", required=True)
parser.add_argument("--native", action="store_true")
args = parser.parse_args()
output = Path(args.output).resolve()
output.mkdir(parents=True, exist_ok=True)
os.environ.pop("OPENAI_API_KEY", None)
os.environ["QT_QPA_PLATFORM"] = "windows" if args.native else "offscreen"
os.environ["QT_SCALE_FACTOR"] = args.scale
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import Qt, QCoreApplication, QEventLoop
from PySide6.QtWidgets import QApplication, QTabWidget, QDialog, QFileDialog, QScrollArea
from kajovo.app.main import _load_fonts
from kajovo.core.config import AppSettings
from kajovo.core.progress import ProgressEvent
from kajovo.desktop.application import MainWindow
from kajovo.desktop.dialogs import (
    DetailDialog,
    ProgressDialog,
    TaskProgressDialog,
    UploadProgressDialog,
    FilePicker,
    TextInputDialog,
)
from kajovo.desktop.windows import ModelPicker, SplashScreen

QCoreApplication.setAttribute(Qt.AA_DontUseNativeDialogs, True)
app = QApplication([])
_load_fonts()
workspace = Path(tempfile.mkdtemp(prefix="kajovo-studio-render-"))
os.chdir(workspace)
settings = AppSettings(
    log_dir=str(workspace / "LOG"),
    cache_dir=str(workspace / "cache"),
)
patches = [
    patch("kajovo.desktop.application.load_api_key", return_value=""),
    patch("kajovo.desktop.application.get_secret", return_value=None),
    patch(
        "requests.sessions.Session.request",
        side_effect=AssertionError("Síť je při snímkování zakázána."),
    ),
]
for guard in patches:
    guard.start()
window = MainWindow(settings)
window.resize(*map(int, args.size.split(",")))
window.all_models = ["gpt-4.1", "gpt-5.2", "gpt-6-astra"]
window.cb_model.addItems(window.all_models)
window._set_active_model("gpt-5.2")
window._refresh_generate_model_overrides()
window._refresh_model_tab()
window.cascade_panel.refresh_models()
window.cascade_panel._select_model("gpt-5.2")
window.ed_project.setText("Ukázková data · Rezervační systém")
window.txt_prompt.setPlainText(
    "Vytvoř přehled rezervací s filtrem podle data a hosta.\n\nVýstup má obsahovat čitelný seznam, detail rezervace a kontrolu neplatných termínů. Zachovej stávající data a přidej návod ke spuštění."
)
window.ed_in.setText(str(workspace / "vstup"))
window.ed_out.setText(str(workspace / "vystup"))
window.files_panel._apply_files_list(
    [
        {"id": "file_demo_spec", "filename": "zadani.pdf"},
        {"id": "file_demo_data", "filename": "rezervace.csv"},
    ]
)
window.files_panel.set_attached(["file_demo_spec"])
window.vector_panel._apply_vector_store_list([{"id": "vs_demo", "name": "Dokumentace projektu"}])
window.vector_panel.set_attached(["vs_demo"])
window.batch_panel._on_refreshed(
    {
        "key": "",
        "batches": [
            {
                "id": "batch_ukazka",
                "status": "in_progress",
                "endpoint": "/v1/responses",
                "request_counts": {"completed": 8, "total": 12, "failed": 0},
            }
        ],
    }
)
manifest = []


def capture(widget, name):
    widget.show()
    for _ in range(3):
        app.processEvents(QEventLoop.AllEvents, 50)
    path = output / (name + ".png")
    if not widget.grab().save(str(path)):
        raise RuntimeError("Nelze uložit snímek " + str(path))
    manifest.append(
        {
            "name": name,
            "file": path.name,
            "width": widget.width(),
            "height": widget.height(),
            "scale": args.scale,
        }
    )


def capture_tabs(root, prefix):
    for number, tabs in enumerate(root.findChildren(QTabWidget)):
        parent = tabs.parentWidget()
        nested = False
        while parent is not None and parent is not root:
            if isinstance(parent, QTabWidget):
                nested = True
                break
            parent = parent.parentWidget()
        if nested:
            continue
        original = tabs.currentIndex()
        for index in range(tabs.count()):
            tabs.setCurrentIndex(index)
            name = f"{prefix}_section{number}_{index}"
            capture(window, name)
            capture_tabs(tabs.widget(index), name)
        tabs.setCurrentIndex(original)


window.show()
for key in window.pages:
    window.select_page(key)
    capture(window, key)
    page = window.pages[key][0]
    capture_tabs(page, key)
    areas = [page, *page.findChildren(QScrollArea)]
    positions = []
    for area in areas:
        if isinstance(area, QScrollArea) and area.isVisible():
            bar = area.verticalScrollBar()
            positions.append((bar, bar.value()))
            bar.setValue(bar.maximum())
    if any(bar.maximum() for bar, _ in positions):
        capture(window, key + "_lower")
    for bar, position in positions:
        bar.setValue(position)

for state in ("active", "waiting", "preflight_pending", "batch_pending", "completed", "failed"):
    dialog = ProgressDialog(window)
    dialog.set_status("Ukázkový běh · vytvářím soubory projektu")
    dialog.add_log("A3 · Soubor 8 z 12 byl uložen a ověřen.")
    dialog.on_progress_event(
        ProgressEvent(
            "A3" if state in ("active", "waiting") else "RUN",
            state,
            completed=8,
            total=12,
            unit="souborů",
        )
    )
    capture(dialog, "progress_" + state)
    dialog.hide()
    dialog.timer.stop()

for kind in ("task", "upload"):
    dialog = (
        TaskProgressDialog("Hromadná operace", window)
        if kind == "task"
        else UploadProgressDialog("Nahrávání souborů", window)
    )
    dialog.set_status("Zpracovávám soubor 3 z 5")
    dialog.set_current("dokumentace/pravidla-rezervaci.pdf")
    dialog.set_progress(60)
    dialog.add_log("Dokončeno: zadani.pdf\nDokončeno: rezervace.csv")
    capture(dialog, "dialog_" + kind)
    dialog.mark_done()
    dialog.close()

for name, dialog in (
    (
        "message",
        DetailDialog(
            "Výsledek kontroly",
            "Výstup byl uložen. Jedna položka vyžaduje kontrolu.",
            window,
            {"chyby": ["Ukázková podrobná zpráva"]},
            True,
        ),
    ),
    ("model_picker", ModelPicker(window.all_models, window.caps_cache, window)),
    ("splash", SplashScreen()),
):
    capture(dialog, "dialog_" + name)
    dialog.hide()

for key in ("settings", "batch"):
    window.open_section(key)
    dialog = window.findChildren(QDialog)[-1]
    capture(dialog, "window_" + key)
    dialog.accept()
file_dialog = FilePicker(window, "Vybrat vstupní soubor", str(workspace))
file_dialog.setOption(QFileDialog.DontUseNativeDialog, True)
file_dialog.resize(800, 550)
capture(file_dialog, "dialog_file")
file_dialog.hide()
input_dialog = TextInputDialog(window)
input_dialog.setWindowTitle("Výběr souborů")
input_dialog.setLabelText("JSON pole relativních cest; odešle placenou dávku.")
input_dialog.setOption(TextInputDialog.UsePlainTextEditForTextInput, True)
input_dialog.setTextValue('["src/rezervace.py"]')
capture(input_dialog, "dialog_input")
input_dialog.hide()
(output / "manifest.json").write_text(
    json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)
window.close()
for guard in reversed(patches):
    guard.stop()
print(json.dumps({"screenshots": len(manifest), "output": str(output)}, ensure_ascii=False))
