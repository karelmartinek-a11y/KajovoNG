import os, sys, tempfile, json, time, math
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

os.environ.pop("OPENAI_API_KEY", None)
import argparse

parser = argparse.ArgumentParser(description="Izolovane rendery desktopu bez sitovych volani.")
parser.add_argument("--size", default="1366,768")
parser.add_argument("--scale", default="1")
parser.add_argument("--native", action="store_true")
args = parser.parse_args()
os.environ["QT_QPA_PLATFORM"] = "windows" if args.native else "offscreen"
os.environ["QT_SCALE_FACTOR"] = args.scale
os.environ["AUDIT_SIZE"] = args.size
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtWidgets import (QApplication, QTableWidget, QTableWidgetItem, QListWidget,
                               QWidget, QLabel, QPushButton, QCheckBox, QRadioButton,
                               QScrollArea, QMessageBox, QFileDialog, QInputDialog, QDialog)
from PySide6.QtCore import Qt, QCoreApplication, qInstallMessageHandler
from PySide6.QtGui import QFont, QFontInfo

qt_messages = []
qInstallMessageHandler(lambda kind, context, message: qt_messages.append(message))
QCoreApplication.setAttribute(Qt.AA_DontUseNativeDialogs, True)
app = QApplication([])
from kajovo.app.main import _load_fonts

_load_fonts()
app.setFont(QFont("Montserrat", 10))
from kajovo.core.config import AppSettings
from kajovo.ui.mainwindow import MainWindow
from kajovo.ui.progress_dialog import ProgressDialog
from kajovo.core.progress import ProgressEvent
from kajovo.ui.task_progress_dialog import TaskProgressDialog
from kajovo.ui.upload_progress_dialog import UploadProgressDialog
from kajovo.ui.apikey_dialog import ApiKeyDialog
from kajovo.ui.settings_dialog import SettingsDialog
from kajovo.ui.model_finder import ModelFinderDialog
from kajovo.ui.vectorstores_panel import FilesSelectorDialog
from kajovo.ui.splash import SplashScreen
from kajovo.ui.batch_window import BatchMonitorWindow
from kajovo.ui.widgets import StyledMessageDialog, BusyPopup, style_file_dialog
from kajovo.ui.cost_dialog import EstimateDialog, show_final_receipt
from kajovo.core.cost_accounting import Rates, quote, CostLedger
from kajovo.core.receipt import Receipt

root = Path(tempfile.mkdtemp(prefix="kajovo-ui-review-"))
os.chdir(root)
settings = AppSettings(
    log_dir=str(root / "LOG"), cache_dir=str(root / "cache"), db_path=str(root / "fixture.sqlite")
)
settings.pricing.auto_refresh_on_start = False
patches = [
    patch.object(MainWindow, "_relocate_legacy_logs_and_milestones"),
    patch.object(MainWindow, "_start_pricing_audit_loop"),
    patch("kajovo.ui.mainwindow.get_secret", return_value=None),
    patch(
        "requests.sessions.Session.request",
        side_effect=RuntimeError("Network forbidden in render audit"),
    ),
]
for p in patches:
    p.start()
window = MainWindow(settings)
longpath = "src/customer_management/international_reservations/components/ReservationValidationAndPricingSummaryController.py"
window.ed_project.setText("Hotel - reservation management and financial reconciliation")
window.txt_prompt.setPlainText(
    (
        "Implement reservation validation, price calculation and real backend actions. "
        + longpath
        + "\n"
    )
    * 8
)
window.ed_in.setText(str(root / "source" / longpath))
window.ed_out.setText(str(root / "output"))
for table in window.findChildren(QTableWidget):
    if table.rowCount() == 0:
        table.setRowCount(3)
        for r in range(3):
            for c in range(table.columnCount()):
                header = table.horizontalHeaderItem(c)
                title = header.text() if header else str(c)
                table.setItem(
                    r, c, QTableWidgetItem((title + " fixture " + str(r)) if c else longpath)
                )
for lst in window.findChildren(QListWidget):
    if lst.count() == 0:
        lst.addItems(["file-fixture-0000001 | " + longpath, "file-fixture-0000002 | README.md"])
records = []


def pump():
    for _ in range(5):
        app.processEvents()


def capture(widget, name, cap=False):
    widget.show()
    pump()
    if cap:
        widget.resize(min(widget.width(), W), min(widget.height(), H))
        pump()
    file = root / (name + ".png")
    widget.grab().save(str(file))
    suspect = []
    for child in widget.findChildren(QWidget):
        if not child.isVisibleTo(widget):
            continue
        if isinstance(child, (QLabel, QPushButton, QCheckBox, QRadioButton)):
            text = child.text()
            if not text:
                continue
            if isinstance(child, QLabel) and child.wordWrap():
                needed = child.heightForWidth(child.width())
                if needed > child.height() + 2:
                    suspect.append(
                        {
                            "kind": "wrapped_height",
                            "text": text[:110],
                            "actual": child.height(),
                            "needed": needed,
                        }
                    )
            elif "\n" not in text and not (
                isinstance(child, QLabel) and child.pixmap() and not child.pixmap().isNull()
            ):
                needed = child.fontMetrics().horizontalAdvance(text.replace("&", ""))
                if needed > child.contentsRect().width() + 2:
                    suspect.append(
                        {
                            "kind": "text_width",
                            "text": text[:110],
                            "actual": child.contentsRect().width(),
                            "needed": needed,
                        }
                    )
    record = {
        "name": name,
        "file": str(file),
        "width": widget.width(),
        "height": widget.height(),
        "dpr": widget.devicePixelRatioF(),
        "suspects": suspect,
    }
    records.append(record)
    return record


scale = float(os.environ.get("QT_SCALE_FACTOR", "1"))
physical = tuple(map(int, os.environ.get("AUDIT_SIZE", "1366,768").split(",")))
W, H = round(physical[0] / scale), round((physical[1] - 48) / scale)
from PySide6.QtCore import QRect

patch("PySide6.QtGui.QScreen.availableGeometry", return_value=QRect(0, 0, W, H + 32)).start()
window.resize(W, H)
window.show()
pump()
for i in range(window.tabs.count()):
    window.tabs.setCurrentIndex(i)
    pump()
    scroll = window.tabs.widget(i)
    label = window.tabs.tabText(i).replace("/", "_")
    capture(window, f"page_{i:02d}_{label}_top")
    if isinstance(scroll, QScrollArea):
        rec = records[-1]
        rec["scroll_x"] = scroll.horizontalScrollBar().maximum()
        rec["scroll_y"] = scroll.verticalScrollBar().maximum()
        if scroll.verticalScrollBar().maximum() > 0 or scroll.horizontalScrollBar().maximum() > 0:
            scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
            scroll.horizontalScrollBar().setValue(scroll.horizontalScrollBar().maximum())
            capture(window, f"page_{i:02d}_{label}_end")
            scroll.verticalScrollBar().setValue(0)
            scroll.horizontalScrollBar().setValue(0)
window.tabs.setCurrentIndex(0)
for mode in ["GENERATE", "MODIFY", "QA", "QFILE"]:
    window.cb_mode.setCurrentText(mode)
    window.chk_send_as_c.setChecked(False)
    pump()
    capture(window, "run_" + mode)
    if mode in ("GENERATE", "MODIFY"):
        window.chk_send_as_c.setChecked(True)
        pump()
        capture(window, "run_" + mode + "_batch")

for idx in range(window.run_sections.count()):
    window.run_sections.setCurrentIndex(idx)
    pump()
    capture(window, "run_section_" + str(idx))
window.run_sections.setCurrentIndex(0)
window.tabs.setCurrentIndex(2)
for idx in range(window.cascade_panel.editor_sections.count()):
    window.cascade_panel.editor_sections.setCurrentIndex(idx)
    pump()
    capture(window, "cascade_section_" + str(idx))
window.tabs.setCurrentIndex(3)
from kajovo.ui.layouts import AdaptivePanels

for panels in window.vector_panel.findChildren(AdaptivePanels):
    for idx in range(panels.tabs.count()):
        panels.tabs.setCurrentIndex(idx)
        pump()
        capture(window, "vector_section_" + str(idx))
window.tabs.setCurrentIndex(9)
for idx in range(window.pricing_panel.data_tabs.count()):
    window.pricing_panel.data_tabs.setCurrentIndex(idx)
    pump()
    capture(window, "pricing_section_" + str(idx))
window.tabs.setCurrentIndex(0)
dialogs = []


def add(d, name):
    dialogs.append(d)
    capture(d, name, True)
    d.hide()


d = ProgressDialog(window)
d.set_status("A3: FILE " + longpath + " (12/36)")
d.on_progress_event(ProgressEvent("A3", completed=12, total=36, unit="souboru", detail=longpath))
d.add_log("A3 | Request sent; waiting for provider response; elapsed 00:48")
add(d, "dialog_run_progress")
d = TaskProgressDialog("Indexing and validating uploaded files", window)
d.set_status(longpath)
d.set_progress(67)
d.set_subprogress(0)
d.add_log("7/12 completed; waiting for index")
add(d, "dialog_task_progress")
d.mark_done()
d = UploadProgressDialog("Upload source files to vector store", window)
d.set_current(longpath)
d.set_status("Uploading file 8 of 32; provider response pending")
d.set_progress(25)
add(d, "dialog_upload_progress")
d.mark_done()
add(ApiKeyDialog(window), "dialog_api_key")
add(SettingsDialog(settings, window), "dialog_legacy_settings")
add(
    ModelFinderDialog(window.caps_cache, ["gpt-4.1-nano", "gpt-6-astra"], window),
    "dialog_model_finder",
)
fake = SimpleNamespace(list_files=lambda: [{"id": "file-fixture-1", "filename": longpath}])
add(FilesSelectorDialog(fake, settings.retry, window.breaker, window), "dialog_files_selector")
d = SplashScreen()
pump()
d._fade_in.stop()
d._opacity.setOpacity(1)
add(d, "window_splash")
with patch.object(BatchMonitorWindow, "load"):
    add(BatchMonitorWindow(settings, "", window), "window_legacy_batch_monitor")
for kind, buttons in [
    ("info", [("OK", QMessageBox.Ok)]),
    ("question", [("Ano", QMessageBox.Yes), ("Ne", QMessageBox.No)]),
]:
    add(
        StyledMessageDialog(
            window,
            "Operation needs attention",
            ("The operation cannot complete because a required input is missing. " + longpath + " ")
            * 3,
            buttons=buttons,
            details=("Validation details: " + longpath + "\n") * 8,
        ),
        "dialog_message_" + kind,
    )
busy = BusyPopup(window, ("Waiting for remote operation: " + longpath + " ") * 3)
add(busy.dialog, "dialog_busy_long")
for mode in ["open", "save", "directory"]:
    d = QFileDialog(window, "Select " + mode, str(root))
    style_file_dialog(d)
    if mode == "save":
        d.setAcceptMode(QFileDialog.AcceptSave)
    if mode == "directory":
        d.setFileMode(QFileDialog.Directory)
    add(d, "dialog_file_" + mode)
d = QInputDialog(window)
d.setLabelText("Select files as a JSON array of project-relative paths")
d.setTextValue(json.dumps([longpath]))
d.setStyleSheet(window.centralWidget().styleSheet())
d.resize(520, 220)
add(d, "dialog_text_input")
q = quote(
    {"model": "gpt-4.1-nano", "max_output_tokens": 32000},
    58000,
    Rates(
        "gpt-4.1-nano",
        ".1",
        ".4",
        ".025",
        source="official OpenAI pricing",
        verified_at="2026-09-09",
    ),
)
estimate = {
    "items": [q, q, q],
    "maximum_usd": ".0558",
    "spent_usd": ".0175",
    "fx": {"date": "2026-09-09", "czk_per_usd": "21.005"},
}
add(EstimateDialog(estimate, "1.00", 32000, window), "dialog_cost_estimate")
ledger = CostLedger(settings.db_path)
op = ledger.reserve("fixture-run", estimate)
ledger.settle(op, ".0145", "resp-fixture", {"input_tokens": 58000, "output_tokens": 21750}, {})
window.db.insert(
    Receipt(
        "fixture-run",
        time.time(),
        "fixture-project",
        "gpt-4.1-nano",
        "GENERATE",
        "A1",
        "resp-fixture",
        None,
        58000,
        21750,
        0,
        0,
        0.0145,
        True,
        "fixture",
        {},
        {},
    )
)


def intercept(d):
    capture(d, "dialog_dynamic_" + str(len(records)), True)
    d.hide()
    return QDialog.Rejected


with patch.object(QDialog, "exec", intercept):
    show_final_receipt(window, window.db, "fixture-run")
    window.pricing_panel.load_receipts()
    window.pricing_panel.show_detail(0, 0)
    window.pricing_panel.show_budgets()

for state in ("waiting", "batch_pending", "completed", "failed", "cancelled"):
    d = ProgressDialog(window)
    d.on_progress_event(ProgressEvent("RUN", state))
    add(d, "progress_" + state)

report = {
    "scale": scale,
    "physical": physical,
    "logical": [W, H],
    "font": QFontInfo(app.font()).family(),
    "point_size": app.font().pointSizeF(),
    "records": records,
    "qt_messages": qt_messages,
}
(root / "report.json").write_text(json.dumps(report, ensure_ascii=True, indent=2), encoding="utf-8")
from PIL import Image, ImageDraw

thumbs = []
for rec in records:
    im = Image.open(rec["file"]).convert("RGB")
    im.thumbnail((420, 290))
    thumb = Image.new("RGB", (440, 320), "#303030")
    thumb.paste(im, ((440 - im.width) // 2, 25))
    ImageDraw.Draw(thumb).text((8, 5), rec["name"], fill="white")
    thumbs.append(thumb)
for start in range(0, len(thumbs), 12):
    part = thumbs[start : start + 12]
    sheet = Image.new("RGB", (440 * 3, 320 * math.ceil(len(part) / 3)), "#555555")
    for j, t in enumerate(part):
        sheet.paste(t, ((j % 3) * 440, (j // 3) * 320))
    sheet.save(root / f"contact_{start // 12}.jpg")
print(
    json.dumps(
        {
            "root": str(root),
            "count": len(records),
            "font": report["font"],
            "overflow": [
                {
                    "name": r["name"],
                    "size": [r["width"], r["height"]],
                    "x": r.get("scroll_x"),
                    "y": r.get("scroll_y"),
                    "suspects": len(r["suspects"]),
                }
                for r in records
                if r.get("scroll_x") or r.get("scroll_y") or r["suspects"]
            ],
        },
        ensure_ascii=True,
    ),
    flush=True,
)
os._exit(0)
