# ui_main.py
from __future__ import annotations

import os
import logging
from typing import Any, List, Optional, Tuple

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFileDialog, QTextEdit, QComboBox,
    QFrame, QMessageBox, QPushButton, QProgressBar, QInputDialog, QLineEdit, QDialog
)
from PySide6.QtGui import QFontDatabase, QPainter, QPixmap
from PySide6.QtCore import Qt, QTimer, QObject, Signal, Slot, QThread

from style import load_stylesheet
from registry import save_api_key, load_api_key

from api_logic import (
    set_api_key,
    list_available_models,
    upload_project_per_file,
    load_manifest,
    build_manifest_text,
    ask_or_modify_with_file_ids,
    apply_patches_and_reupload,
    combine_costs,
)

from pricing import estimate_cost

logger = logging.getLogger(__name__)


def resource_path(*parts: str) -> str:
    """Vrátí absolutní cestu k souboru v ./resources vedle tohoto .py."""
    return os.path.join(os.path.dirname(__file__), "resources", *parts)


class GlassStatusDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("PROBÍHÁ OPERACE…")
        self.setModal(True)
        self.setWindowFlags(Qt.Dialog | Qt.CustomizeWindowHint | Qt.WindowTitleHint)
        self.resize(520, 320)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(10)

        self.status_label = QLabel("Start…")
        layout.addWidget(self.status_label)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        layout.addWidget(self.progress)

        self.cost_label = QLabel("Cena: neznámá")
        layout.addWidget(self.cost_label)

        self.log_label = QLabel("")
        self.log_label.setWordWrap(True)
        self.log_label.setAlignment(Qt.AlignTop)
        layout.addWidget(self.log_label)

        self.setStyleSheet("""
        QDialog {
            background-color: rgba(0, 0, 0, 220);
            color: white;
            border: 2px solid white;
            border-radius: 14px;
        }
        QLabel { color: white; }
        QProgressBar {
            border: 2px solid white;
            border-radius: 8px;
            background-color: black;
            color: white;
            text-align: center;
        }
        QProgressBar::chunk { background-color: white; border-radius: 8px; }
        """)

    def reset(self, title: str) -> None:
        self.setWindowTitle(title)
        self.status_label.setText("Start…")
        self.progress.setValue(0)
        self.cost_label.setText("Cena: neznámá")
        self.log_label.setText("")

    def append(self, text: str) -> None:
        old = self.log_label.text()
        self.log_label.setText((old + "\n" + text).strip() if old else text)
        self.status_label.setText(text)
        self.status_label.repaint()

    def set_progress(self, value: int) -> None:
        self.progress.setValue(value)

    def set_cost(self, text: str) -> None:
        self.cost_label.setText(text)


class Worker(QObject):
    progress = Signal(int)
    status = Signal(str)
    done = Signal(bool, object, tuple, str)  # ok, payload, usage(in,out), error

    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs

    @Slot()
    def run(self):
        try:
            def cb(msg: str):
                self.status.emit(msg)
            self.kwargs["progress_cb"] = cb

            payload, usage = self.fn(*self.args, **self.kwargs)
            self.done.emit(True, payload, usage, "")
        except Exception as exc:
            logger.exception("Worker chyba")
            self.done.emit(False, None, (0, 0), str(exc))


class KajaMainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("KÁJA – PER-FILE EDITION")
        self.showMaximized()

        self._load_fonts()
        self.setStyleSheet(load_stylesheet())

        self._watermark = QPixmap(resource_path("logo_hotel.png"))
        self._wm_op = 0.15
        self._wm_delta = 0.003
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._wm_tick)
        self._timer.start(30)

        self.api_key = load_api_key()
        if self.api_key:
            set_api_key(self.api_key)

        self.target_dir: str = ""
        self.manifest: Optional[dict] = None
        self.file_ids: List[str] = []

        self.thread: Optional[QThread] = None
        self.worker: Optional[Worker] = None

        self.dialog = GlassStatusDialog(self)

        self._build_ui()
        self._connect()
        self._reload_models(True)

    def _load_fonts(self) -> None:
        regular = resource_path("montserrat_regular.ttf")
        bold = resource_path("montserrat_bold.ttf")
        for p in (regular, bold):
            if os.path.exists(p):
                QFontDatabase.addApplicationFont(p)
            else:
                logger.warning("Nelze najít font: %s", p)

    def _wm_tick(self):
        self._wm_op += self._wm_delta
        if self._wm_op >= 0.25 or self._wm_op <= 0.10:
            self._wm_delta *= -1
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if self._watermark.isNull():
            return
        p = QPainter(self)
        p.setOpacity(self._wm_op)
        size = min(self.width(), self.height()) * 0.6
        scaled = self._watermark.scaled(int(size), int(size), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        x = (self.width() - scaled.width()) // 2
        y = (self.height() - scaled.height()) // 2
        p.drawPixmap(x, y, scaled)

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(30, 30, 30, 30)
        root.setSpacing(15)

        title = QLabel("KÁJA – PER-FILE EDITION")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("font-weight: bold; font-size: 28px;")
        root.addWidget(title)

        panel = QFrame()
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(20, 20, 20, 20)
        panel_layout.setSpacing(12)
        root.addWidget(panel)

        self.prompt = QTextEdit()
        self.prompt.setPlaceholderText("Zadání programu…")
        panel_layout.addWidget(self.prompt)

        row = QHBoxLayout()
        self.model_box = QComboBox()
        row.addWidget(self.model_box)

        self.dir_btn = QPushButton("Vybrat cílový adresář")
        row.addWidget(self.dir_btn)
        panel_layout.addLayout(row)

        self.estimate_lbl = QLabel("Odhad ceny: neznámý")
        panel_layout.addWidget(self.estimate_lbl)

        self.final_lbl = QLabel("Skutečná cena: zatím žádná")
        panel_layout.addWidget(self.final_lbl)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        panel_layout.addWidget(self.progress)

        self.status = QLabel("Připraveno.")
        panel_layout.addWidget(self.status)

        row1 = QHBoxLayout()
        self.api_btn = QPushButton("API-KEY")
        self.gen_btn = QPushButton("GENEROVAT")
        self.upload_btn = QPushButton("UPLOAD FILES")
        row1.addWidget(self.api_btn)
        row1.addWidget(self.gen_btn)
        row1.addWidget(self.upload_btn)
        panel_layout.addLayout(row1)

        row2 = QHBoxLayout()
        self.qa_btn = QPushButton("Q&A")
        self.mod_btn = QPushButton("MODIFY")
        self.new_btn = QPushButton("NEW")
        self.exit_btn = QPushButton("EXIT")
        self.exit_btn.setObjectName("ExitButton")
        row2.addWidget(self.qa_btn)
        row2.addWidget(self.mod_btn)
        row2.addWidget(self.new_btn)
        row2.addWidget(self.exit_btn)
        panel_layout.addLayout(row2)

    def _connect(self):
        self.exit_btn.clicked.connect(self.close)
        self.dir_btn.clicked.connect(self._pick_dir)
        self.api_btn.clicked.connect(self._set_key)
        self.prompt.textChanged.connect(self._update_estimate)
        self.model_box.currentIndexChanged.connect(self._update_estimate)

        self.upload_btn.clicked.connect(self._upload_files)
        self.qa_btn.clicked.connect(self._qa)
        self.mod_btn.clicked.connect(self._modify)
        self.new_btn.clicked.connect(self._new)

        # generování nechávám jak máš (tvoje generace je mimo scope per-file revizí)
        self.gen_btn.clicked.connect(lambda: QMessageBox.information(self, "Poznámka", "Generátor projektu necháváme beze změny. Per-file se týká upload/revize."))

    def _pick_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Vybrat cílový adresář")
        if d:
            self.target_dir = d
            self.status.setText(f"Cílový adresář: {d}")
            # zkus načíst existující manifest
            self.manifest = load_manifest(d)
            if self.manifest:
                self.file_ids = [self.manifest["files"][p]["file_id"] for p in self.manifest.get("files", {}).keys()]
                self.status.setText(f"Načten existující manifest (.kaja). Souborů: {len(self.file_ids)}")

    def _set_key(self):
        key, ok = QInputDialog.getText(self, "API-KEY", "Zadej OpenAI API klíč:", QLineEdit.Normal)
        if ok and key:
            save_api_key(key)
            set_api_key(key)
            self.api_key = key
            QMessageBox.information(self, "OK", "API klíč uložen.")
            self._reload_models(False)

    def _reload_models(self, initial: bool):
        self.model_box.clear()
        models = list_available_models()
        if not models:
            models = ["gpt-4.1", "gpt-4.1-mini", "gpt-5.1", "gpt-5.0", "gpt-4o"]
        self.model_box.addItems(models)
        if not initial:
            self._update_estimate()

    def _update_estimate(self):
        prompt = self.prompt.toPlainText().strip()
        model = self.model_box.currentText()
        if not prompt or not model:
            self.estimate_lbl.setText("Odhad ceny: neznámý")
            return
        in_tok = max(1, len(prompt) // 4)
        out_tok = in_tok * 3
        cost = estimate_cost(model, in_tok, out_tok)
        self.estimate_lbl.setText(f"Odhad ceny: ~{cost} USD (IN≈{in_tok}, OUT≈{out_tok})")

    # -------- PER-FILE upload --------

    def _upload_files(self):
        if not self.target_dir:
            QMessageBox.warning(self, "Chybí adresář", "Vyber cílový adresář.")
            return

        self._start_worker("UPLOAD FILES", self._upload_files_job)

    def _upload_files_job(self, progress_cb=None):
        def p(msg):
            if progress_cb:
                progress_cb(msg)
        p("Zahajuji per-file upload…")
        manifest, file_ids = upload_project_per_file(self.target_dir, progress_cb=p)
        return {"manifest": manifest, "file_ids": file_ids}, (0, 0)

    # -------- Q&A / MODIFY --------

    def _qa(self):
        if not self._ensure_manifest():
            return
        q, ok = QInputDialog.getMultiLineText(self, "Q&A", "Zadej dotaz:", "")
        if not ok or not q.strip():
            return
        self._start_worker("Q&A", lambda progress_cb=None: self._ask_modify_job("answer", q.strip(), progress_cb))

    def _modify(self):
        if not self._ensure_manifest():
            return
        q, ok = QInputDialog.getMultiLineText(self, "MODIFY", "Zadej požadované změny:", "")
        if not ok or not q.strip():
            return
        self._start_worker("MODIFY", lambda progress_cb=None: self._ask_modify_job("modify", q.strip(), progress_cb))

    def _ask_modify_job(self, mode: str, text: str, progress_cb=None):
        model = self.model_box.currentText()
        out_text, usage = ask_or_modify_with_file_ids(
            model=model,
            file_ids=self.file_ids,
            manifest=self.manifest,
            user_prompt=text,
            mode=mode,
            progress_cb=progress_cb,
        )
        return {"mode": mode, "output": out_text}, usage

    def _ensure_manifest(self) -> bool:
        if not self.target_dir:
            QMessageBox.warning(self, "Chybí adresář", "Vyber cílový adresář.")
            return False
        self.manifest = load_manifest(self.target_dir)
        if not self.manifest:
            QMessageBox.warning(self, "Chybí manifest", "Nejdřív udělej UPLOAD FILES (per-file).")
            return False
        self.file_ids = [self.manifest["files"][p]["file_id"] for p in self.manifest.get("files", {}).keys()]
        return True

    # -------- worker infra --------

    def _start_worker(self, title: str, job_fn):
        if self.thread is not None:
            QMessageBox.information(self, "Probíhá operace", "Už běží jiná operace.")
            return

        self.dialog.reset(title)
        self.dialog.show()

        self.progress.setValue(0)
        self.status.setText(f"{title}…")

        self.thread = QThread(self)
        self.worker = Worker(job_fn)
        self.worker.moveToThread(self.thread)

        self.thread.started.connect(self.worker.run)
        self.worker.status.connect(self._on_worker_status)
        self.worker.done.connect(self._on_worker_done)
        self.worker.done.connect(self._cleanup_worker)
        self.thread.start()

    @Slot(str)
    def _on_worker_status(self, text: str):
        self.status.setText(text)
        self.dialog.append(text)

    @Slot(bool, object, tuple, str)
    def _on_worker_done(self, ok: bool, payload: Any, usage: tuple, err: str):
        self.dialog.hide()

        if not ok:
            QMessageBox.critical(self, "Chyba", err)
            return

        # cost vyčíslit z usage (pro Q&A/MODIFY)
        tin, tout = usage
        model = self.model_box.currentText()
        cost, tin2, tout2 = combine_costs([(tin, tout)], model)

        if isinstance(payload, dict) and "manifest" in payload and "file_ids" in payload:
            self.manifest = payload["manifest"]
            self.file_ids = payload["file_ids"]
            self.final_lbl.setText(f"UPLOAD hotov. Souborů: {len(self.file_ids)}")
            QMessageBox.information(self, "UPLOAD FILES", f"Nahráno souborů: {len(self.file_ids)}\nManifest uložen do .kaja/manifest.json")
            return

        if isinstance(payload, dict) and payload.get("mode") == "answer":
            self.final_lbl.setText(f"Skutečná cena Q&A: {cost} USD (IN={tin2}, OUT={tout2})")
            QMessageBox.information(self, "Q&A", payload.get("output", ""))
            return

        if isinstance(payload, dict) and payload.get("mode") == "modify":
            # aplikuj patche + reupload změněných
            out = payload.get("output", "")
            self.dialog.reset("APPLY PATCHES + REUPLOAD")
            self.dialog.show()

            def p(msg):
                self.dialog.append(msg)

            changed, new_manifest, _new_ids = apply_patches_and_reupload(self.target_dir, self.manifest, out, progress_cb=p)
            self.manifest = new_manifest
            self.file_ids = [self.manifest["files"][p]["file_id"] for p in self.manifest.get("files", {}).keys()]

            self.dialog.hide()
            self.final_lbl.setText(f"Skutečná cena MODIFY: {cost} USD (IN={tin2}, OUT={tout2})")
            QMessageBox.information(self, "MODIFY hotovo", "Změněné soubory:\n" + "\n".join(changed))
            return

        QMessageBox.information(self, "Hotovo", "Operace dokončena.")

    @Slot(bool, object, tuple, str)
    def _cleanup_worker(self, *_):
        if self.thread is not None:
            self.thread.quit()
            self.thread.wait()
            self.thread.deleteLater()
            self.thread = None
        if self.worker is not None:
            self.worker.deleteLater()
            self.worker = None

    def _new(self):
        self.prompt.clear()
        self.progress.setValue(0)
        self.status.setText("Nová relace.")
        self.final_lbl.setText("Skutečná cena: zatím žádná")
