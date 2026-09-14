"""Snímky produkční sestavy studia s izolovanými daty a zakázanou sítí."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--size", default="1366,900")
    parser.add_argument("--scale", default="1")
    parser.add_argument("--native", action="store_true")
    args = parser.parse_args()
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    os.environ["QT_QPA_PLATFORM"] = "windows" if args.native else "offscreen"
    os.environ["QT_SCALE_FACTOR"] = args.scale
    os.environ.pop("OPENAI_API_KEY", None)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, Qt
    from PySide6.QtWidgets import QApplication, QAbstractButton, QComboBox, QDialog, QFileDialog, QLabel, QLineEdit, QListWidgetItem, QScrollArea, QTabWidget, QWidget
    from kajovo.core.config import AppSettings
    from kajovo.core.progress import ProgressEvent
    from kajovo.studio.application import create_window
    from kajovo.studio.components import DetailDialog
    from kajovo.studio.converter import ConverterWindow
    from kajovo.studio.operations import OperationDialog
    from kajovo.studio.resources import ValueDialog, fill_records
    from kajovo.core.cascade_types import CascadeInput, CascadeOutput
    from kajovo.studio.cascade_items import CascadeItemDialog

    QCoreApplication.setAttribute(Qt.AA_DontUseNativeDialogs, True)
    app = QApplication([])
    from kajovo.app.main import _load_fonts
    _load_fonts()
    snapshots = []
    old_cwd = Path.cwd()
    with tempfile.TemporaryDirectory(prefix="kajovo-studio-") as temporary:
        workspace = Path(temporary)
        os.chdir(workspace)
        try:
            with patch("socket.create_connection", side_effect=AssertionError("Síť je při snímkování zakázaná.")), \
                 patch("requests.sessions.Session.request", side_effect=AssertionError("Síť je při snímkování zakázaná.")), \
                 patch("kajovo.core.secret_store._read_persisted_api_key", return_value=None), \
                 patch("kajovo.core.secret_store.get_secret", return_value=None):
                window = create_window(AppSettings(log_dir=str(workspace / "LOG"), cache_dir=str(workspace / "cache")))
                window.resize(*map(int, args.size.split(",")))
                window.context.models = ["gpt-4.1", "gpt-5.2", "gpt-image-1.5"]
                window.context.models_changed.emit()
                window.workbench.widgets["project"].setText("Ukázkový projekt · Rezervace")
                window.workbench.prompt.setPlainText("Vytvoř přehled rezervací s filtrem podle data a hosta. Zachovej současná data a ověř neplatné termíny.")
                fill_records(window.resources.lists["files"], [{"id": "file_ukazka", "filename": "Specifikace rezervací.pdf"}])
                fill_records(window.resources.lists["stores"], [{"id": "vs_ukazka", "name": "Dokumentace projektu"}])
                window.cascades.add_step()
                window.cascades.title.setText("Připravit návrh řešení")
                window.cascades.model.setText("gpt-4.1")
                window.cascades.commit_step()
                window.batches.records = [{"id": "batch_ukazka", "remote": {"status": "in_progress", "request_counts": {"completed": 8, "failed": 1, "total": 12}}, "state": {"project": "Rezervace", "status": "batch_pending"}, "run_dir": str(workspace / "LOG" / "RUN_ukazka")}]
                window.batches.render()
                examples = {
                    "summary": {"project": "Rezervace", "status": "partial", "mode": "GENERATE", "input_summary": "Přehled rezervací", "output_summary": "Osm souborů uloženo; jeden vyžaduje opravu."},
                    "steps": [{"step_id": "step_priprava", "title": "Příprava návrhu", "status": "completed", "human_summary": "Požadavky byly ověřeny."}],
                    "responses": [{"response_id": "resp_ukazka", "output_text": "Návrh obsahuje seznam rezervací, detail hosta a kontrolu termínů."}],
                    "artifacts": [{"artifact_id": "artifact_ukazka", "display_name": "rezervace.py", "size_bytes": 4096, "path_in_bundle": "artifacts/rezervace.py"}],
                    "events": [{"event_type": "validation", "human_message": "Před uložením byla ověřena cesta souboru.", "severity": "info"}],
                    "lineage": [{"source_run_id": "RUN_zdroj", "relation_type": "continue", "notes": "Navázáno na ověřený stav."}],
                    "integrity": {"human_summary": "Archivované soubory odpovídají uloženým otiskům."},
                }
                for key, value in examples.items():
                    window.history.views[key].set_value(value)
                window.workbench.result.set_value({"status": "completed", "text": "Projekt je připravený k místnímu ověření.", "saved": ["rezervace.py", "README.md"]})
                from PySide6.QtGui import QColor, QImage
                sample = QImage(640, 400, QImage.Format_RGB32)
                sample.fill(QColor("#2E766F"))
                sample_path = workspace / "ukazkova_fotografie.png"
                sample.save(str(sample_path))
                window.photos.add_paths([str(sample_path)])
                window.photos.photos.setCurrentRow(0)
                from kajovo.core import photo_batch
                edited_path = workspace / "ukazkovy_vysledek.png"
                sample.fill(QColor("#B08B59"))
                sample.save(str(edited_path))
                job = photo_batch.new_job(source_paths=[str(sample_path)], human_prompt="Upravit barevné podání",
                                          professional_prompt="", final_prompt="Upravit barevné podání", prompt_source="human",
                                          template_id="", prompt_model="", prompt_response_id="", image_model="gpt-image-1.5",
                                          quality="high", size="1024x1024", output_format="png", output_dir=str(workspace / "output"))
                job.items[0].output_path = str(edited_path)
                job.status = "completed"
                job.request_completed = 1
                item = QListWidgetItem("Ukázková úloha · dokončeno")
                item.setData(Qt.UserRole, job)
                window.photos.job_list.addItem(item)
                window.photos.job_list.setCurrentItem(item)

                def capture(widget, name, include_scroll=True):
                    if isinstance(widget, QDialog):
                        width, height = map(int, args.size.split(","))
                        widget.resize(min(widget.width(), width), min(widget.height(), height))
                    widget.show()
                    for _ in range(4):
                        app.processEvents(QEventLoop.AllEvents, 40)
                    image_path = output / (name + ".png")
                    if not widget.grab().save(str(image_path)):
                        raise RuntimeError("Snímek se nepodařilo uložit.")
                    controls = []
                    defects = []
                    for child in widget.findChildren(QWidget):
                        if not isinstance(child, (QAbstractButton, QComboBox, QLabel, QLineEdit)):
                            continue
                        if not child.isVisibleTo(widget):
                            continue
                        text = child.text() if hasattr(child, "text") else child.currentText()
                        origin = child.mapTo(widget, child.rect().topLeft())
                        controls.append({"id": child.objectName(), "type": type(child).__name__, "text": text,
                                         "x": origin.x(), "y": origin.y(), "width": child.width(), "height": child.height(),
                                         "enabled": child.isEnabled(), "accessible_name": child.accessibleName()})
                        if isinstance(child, QLabel) and text and child.wordWrap():
                            required = child.heightForWidth(child.width())
                            if required > child.height() + 2:
                                defects.append({"text": text, "required_height": required, "actual_height": child.height()})
                    snapshots.append({"name": name, "file": image_path.name, "width": widget.width(), "height": widget.height(), "scale": args.scale, "controls": controls, "label_clipping": defects})
                    if include_scroll and isinstance(widget, QDialog):
                        for index, area in enumerate(widget.findChildren(QScrollArea)):
                            if area.isVisible():
                                bar = area.verticalScrollBar()
                                previous = bar.value()
                                if bar.maximum():
                                    bar.setValue(bar.maximum())
                                    capture(widget, name + f"_lower{index}", False)
                                bar.setValue(previous)

                def capture_scrolls(name):
                    areas = window.stack.currentWidget().findChildren(QScrollArea)
                    if isinstance(window.stack.currentWidget(), QScrollArea):
                        areas.insert(0, window.stack.currentWidget())
                    for number, area in enumerate(areas):
                        if not area.isVisible():
                            continue
                        bar = area.verticalScrollBar()
                        original = bar.value()
                        for position in sorted({bar.maximum() // 2, bar.maximum()} - {0}):
                            bar.setValue(position)
                            capture(window, f"{name}_scroll{number}_{position}")
                        bar.setValue(original)
                        horizontal = area.horizontalScrollBar()
                        if horizontal.maximum():
                            previous = horizontal.value()
                            horizontal.setValue(horizontal.maximum())
                            capture(window, f"{name}_horizontal{number}")
                            horizontal.setValue(previous)

                window.show()
                for key, page in window.pages.items():
                    window.select_page(key)
                    capture(window, key)
                    capture_scrolls(key)
                    for number, tabs in enumerate(page.findChildren(QTabWidget)):
                        original = tabs.currentIndex()
                        for index in range(tabs.count()):
                            tabs.setCurrentIndex(index)
                            capture(window, f"{key}_tabs{number}_{index}")
                            capture_scrolls(f"{key}_tabs{number}_{index}")
                        tabs.setCurrentIndex(original)
                window.select_page("run")
                window.context.api_key = "render-only"
                window.workbench.widgets["model"].setCurrentIndex(window.workbench.widgets["model"].findData("gpt-4.1"))
                window.workbench.widgets["out_dir"].setText(str(workspace / "output"))
                window.workbench.validate()
                capture(window, "run_ready")
                def capture_comparison(dialog):
                    capture(dialog, "photos_comparison")
                    dialog.hide()
                    return QDialog.Rejected
                with patch.object(QDialog, "exec", capture_comparison):
                    window.photos.compare()
                window.select_page("settings")
                window.detach_page()
                detached = window.detached["settings"]
                capture(detached, "detached_settings")
                detached.accept()
                for key, record in (("input", CascadeInput(name="Zdrojový soubor")), ("output", CascadeOutput(name="Výsledný soubor"))):
                    dialog = CascadeItemDialog(record, window.cascades.definition.steps, window)
                    capture(dialog, "cascade_" + key + "_editor")
                    dialog.hide()
                dialog = QFileDialog(window, "Vybrat vstupní soubory", str(workspace))
                dialog.setFileMode(QFileDialog.ExistingFiles)
                capture(dialog, "file_picker")
                dialog.hide()
                for state in ("active", "waiting", "completed", "failed", "partial", "cancelled", "batch_pending", "response_pending", "submission_unknown", "dry_run"):
                    dialog = OperationDialog("Připravuji soubory projektu", window)
                    dialog.notification.show()
                    dialog.on_event(ProgressEvent("Ověřování souborů", state, 8 if state == "completed" else 3, 8, "souborů", "Kontroluji obsah souborů před uložením."))
                    if state not in ("active", "waiting"):
                        dialog.finish(state)
                    capture(dialog, "operation_" + state)
                    dialog.timer.stop()
                    dialog.hide()
                dialog = DetailDialog("Kontrola vstupu", "Vybraný soubor se nepodařilo otevřít.", window, {"soubor": "ukazka.txt"})
                capture(dialog, "detail")
                dialog.findChild(QAbstractButton, "dialog.details").click()
                capture(dialog, "detail_expanded")
                dialog.hide()
                dialog = DetailDialog("Odstranit vybrané soubory", "Operace trvale odstraní vybrané soubory ze služby.", window, confirm=True)
                capture(dialog, "confirmation")
                dialog.hide()
                dialog = ValueDialog("Vstupy a výstupy kroku", "Úplný kontrakt kroku", '{"name": "Ukázka"}', window, structured=True)
                capture(dialog, "value_editor")
                dialog.hide()
                converter = ConverterWindow()
                converter.resize(*map(int, args.size.split(",")))
                capture(converter, "converter")
                converter.close()
                window.operations.show_all()
                capture(window.operations.overview, "operations_overview")
                window.operations.overview.hide()
                window.close()
                window.deleteLater()
                converter.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
                app.processEvents()
        finally:
            os.chdir(old_cwd)
    (output / "manifest.json").write_text(json.dumps(snapshots, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"screenshots": len(snapshots), "output": str(output), "label_clipping": sum(len(row["label_clipping"]) for row in snapshots)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
