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
    from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, QModelIndex, QTimer, Qt
    from PySide6.QtWidgets import QApplication, QAbstractButton, QAbstractItemView, QComboBox, QDialog, QFileDialog, QInputDialog, QLabel, QLineEdit, QListView, QMenu, QMessageBox, QPlainTextEdit, QScrollArea, QTabWidget, QTextEdit, QWidget
    from kajovo.core.config import AppSettings
    from kajovo.core import secret_store
    from kajovo.core.runlog import RunLogger
    from kajovo.core.run_bundle import LegacyRunAdapter
    from kajovo.core.batch_completion import read_state
    from kajovo.core.cascade_types import CascadeDefinition, CascadeStep
    from kajovo.core.progress import ProgressEvent
    from kajovo.studio.application import create_window
    from kajovo.studio.components import DetailDialog
    from kajovo.studio.converter import ConverterWindow
    from kajovo.studio.operations import OperationDialog
    from kajovo.studio.resources import ValueDialog, fill_records
    from kajovo.core.cascade_types import CascadeInput, CascadeOutput
    from kajovo.studio.cascade_items import CascadeItemDialog
    from kajovo.studio.history import _payload
    from kajovo.studio.history_composer import BranchComposer
    from kajovo.studio.history_details import CascadeStepsView, ModifyMap, RunDetailDialog
    from kajovo.studio.history_models import build_run
    from kajovo.studio.file_dialogs import FileDialog
    from kajovo.core.progress_model import STATES
    from kajovo.studio.components import CzechTranslator

    qt_texts = {}
    translate = CzechTranslator.translate

    def record_translation(translator, context, source_text, disambiguation=None, n=-1):
        result = translate(translator, context, source_text, disambiguation, n)
        qt_texts[(context, source_text, disambiguation or "")] = {
            "context": context, "source": source_text, "translation": result or source_text,
            "disambiguation": disambiguation or "", "plural": n,
        }
        return result

    CzechTranslator.translate = record_translation

    def create_run_fixtures(workspace, settings):
        log_dir = settings.log_dir
        fixtures = {}

        def base(run_id, project, mode, status="completed"):
            logger = RunLogger(log_dir, run_id, project)
            ui = {
                "project": project, "prompt": f"Produkční zadání pro {project}", "mode": mode,
                "model": "gpt-4.1", "model_a1": "gpt-4.1", "model_a2": "gpt-4.1",
                "model_a3": "gpt-4.1", "response_id": "", "attached_file_ids": [],
                "input_file_ids": [], "attached_vector_store_ids": [], "in_dir": "",
                "out_dir": str(workspace / "OUT" / project), "in_equals_out": False,
                "versing": False, "temperature": 0.2, "use_file_search": True,
                "send_as_c": False, "maximum_quality": False,
            }
            logger.update_state({"ui_state": ui})
            logger.bundle.update_run({"created_at": "2026-09-15T10:00:00+00:00"})
            return logger, ui, status

        def stage(logger, token, title, sequence, status="completed"):
            row = logger.bundle.ensure_step(token, title=title, kind="api", model="gpt-4.1")
            if logger.bundle.run_record().get("mode") in {"GENERATE", "MODIFY", "KASKADA"}:
                logger.save_json("requests", token + "_request", {"payload": {
                    "model": "gpt-4.1", "input": "Zpracuj uložené zadání: " + title}}, step_id=row["step_id"])
                if status == "completed":
                    logger.save_json("responses", token + "_response", {
                        "id": "resp_fixture_" + token, "status": "completed",
                        "output_text": title + "\n\nZpracované podklady: autentizace, ukládání dat a přístupnost.\n"
                                       "Výstup obsahuje rozhraní, konkrétní soubory a postup ověření.",
                        "usage": {"input_tokens": 1200 + sequence * 340, "output_tokens": 560 + sequence * 210}},
                        step_id=row["step_id"])
            logger.bundle.update_step(
                row["step_id"], status=status, progress=100 if status == "completed" else 62,
                started_at=f"2026-09-15T10:{sequence:02d}:00+00:00",
                finished_at=f"2026-09-15T10:{sequence:02d}:52+00:00" if status != "batch_pending" else "",
                technical_summary="Timeout při validaci" if status == "failed" else "",
            )
            return row["step_id"]

        logger, ui, _ = base("RUN_150920261001_GENERATE", "Rezervační portál", "GENERATE")
        for seq, token, title in ((0, "A0R", "Upřesnění požadavků"), (1, "A1", "Plán řešení"),
                                  (2, "A2", "Struktura projektu"), (3, "A3", "Tvorba souborů v dávce")):
            stage(logger, token, title, seq, "batch_pending" if token == "A3" else "completed")
        logger.update_state({"batch_id": "batch_generate_demo", "status": "batch_pending",
                             "batch_records": {"batch_generate_demo": {"id": "batch_generate_demo", "status": "completed",
                                 "request_counts": {"completed": 14, "failed": 0, "total": 14}}}})
        logger.bundle.update_run({"created_at": "2026-09-15T10:00:00+00:00", "started_at": "2026-09-15T10:00:00+00:00", "finished_at": ""})
        fixtures["generate"] = logger.paths.run_dir

        logger, ui, _ = base("RUN_150920261002_MODIFY", "Úprava fakturace", "MODIFY")
        ui["in_dir"] = str(workspace / "IN")
        logger.update_state({"ui_state": ui, "preparation_snapshot": {"canonical_stage": "B2", "structure": {
            "touched_files": [{"path": "billing.py", "action": "modify"}, {"path": "tests/test_billing.py", "action": "add"},
                              {"path": "README.md", "action": "modify"}],
            "preserved_files": [{"path": "config.py"}]}}})
        for seq, token, title, status in ((0, "B0R", "Upřesnění změn", "completed"), (1, "B1", "Plán změn", "completed"),
                                          (2, "B2", "Struktura změn", "completed"), (3, "B3", "Zápis souborů", "failed")):
            step_id = stage(logger, token, title, seq, status)
        old = workspace / "billing-old.py"
        old.write_text("def total():\n    return 10\n", encoding="utf-8")
        new = workspace / "billing-new.py"
        new.write_text("def total():\n    return 12\n", encoding="utf-8")
        logger.bundle.archive_artifact(old, role="in_project_file", step_id=step_id, reconstruction_role="billing.py",
                                       metadata={"relative_path": "billing.py"})
        logger.bundle.archive_artifact(new, role="modified_file", step_id=step_id, reconstruction_role="billing.py",
                                       metadata={"before_sha256": "old"})
        logger.update_state({"status": "partial", "missing_deliverables": [{"path": "README.md"}], "error": "B3: README.md nebyl uložen."})
        logger.bundle.update_run({"created_at": "2026-09-15T10:00:00+00:00", "finished_at": "2026-09-15T10:03:04+00:00"})
        logger.bundle.seal()
        fixtures["modify"] = logger.paths.run_dir

        logger, _ui, _ = base("RUN_150920261003_QA", "Audit přístupnosti", "QA")
        step_id = stage(logger, "QA", "Odpověď na dotaz", 0)
        logger.save_json("requests", "QA_request_demo", {"payload": {"model": "gpt-4.1", "input": "audit"}}, step_id=step_id)
        logger.save_json("responses", "QA_response_demo", {"id": "resp_qa_demo", "status": "completed",
                         "output_text": "Formulář potřebuje explicitní popisky a viditelný focus.",
                         "usage": {"input_tokens": 820, "output_tokens": 96}}, step_id=step_id)
        logger.update_state({"status": "completed", "completed_at": 1789467000})
        logger.bundle.update_run({"created_at": "2026-09-15T10:00:00+00:00", "finished_at": "2026-09-15T10:00:01+00:00"})
        logger.bundle.seal()
        fixtures["qa"] = logger.paths.run_dir

        logger, _ui, _ = base("RUN_150920261004_QFILE", "Export reportu", "QFILE")
        step_id = stage(logger, "QFILE", "Výsledný PDF soubor", 0)
        result = workspace / "vysledek.pdf"
        from PySide6.QtGui import QPainter, QPdfWriter
        writer = QPdfWriter(str(result))
        writer.setTitle("Deterministický QFILE výsledek")
        painter = QPainter(writer)
        font = painter.font()
        font.setPointSize(14)
        painter.setFont(font)
        from PySide6.QtCore import QRect, Qt
        painter.drawText(QRect(300, 300, writer.width() - 600, writer.height() - 600), Qt.TextWordWrap,
                         "Souhrn projektu\n\nVýsledný report připravený jako PDF.\n\n"
                         "Souborový kontrakt prošel automatickou kontrolou. "
                         "Obsah tohoto dokumentu dosud nebyl schválen člověkem.")
        painter.end()
        artifact = logger.bundle.archive_artifact(result, role="generated_file", kind="output_file", step_id=step_id,
                                                  reconstruction_role="report.pdf")
        logger.record_validation(step_id=step_id, target_type="file_contract", target_id=artifact["artifact_id"],
                                 validator="QFILE.A3_FILE", status="passed", evidence={"contract": "A3_FILE"})
        logger.update_state({"status": "completed", "completed_at": 1789467100,
                             "file_contract_valid": True, "human_verified": False})
        logger.bundle.update_run({"created_at": "2026-09-15T10:00:00+00:00", "finished_at": "2026-09-15T10:00:01+00:00"})
        logger.bundle.seal()
        fixtures["qfile"] = logger.paths.run_dir

        analysis_output = CascadeOutput(id="analysis_result", name="Analýza", kind="text")
        implementation_output = CascadeOutput(id="implementation_result", name="Implementace", kind="text")
        cascade = CascadeDefinition("Release pipeline", steps=[
            CascadeStep(id="analysis", title="Analýza", model="gpt-4.1", input_text="Analyzuj",
                        outputs=[analysis_output]),
            CascadeStep(id="implementation", title="Implementace", model="gpt-4.1", input_text="Implementuj",
                        inputs=[CascadeInput(name="Analýza", source="output", source_step_id="analysis",
                                             source_output_id=analysis_output.id)],
                        outputs=[implementation_output]),
            CascadeStep(id="validation", title="Validace", model="gpt-4.1", input_text="Validuj",
                        inputs=[CascadeInput(name="Implementace", source="output", source_step_id="implementation",
                                             source_output_id=implementation_output.id)]),
        ])
        logger, _ui, _ = base("RUN_150920261005_KASKADA", "Release 2.4", "KASKADA")
        first = stage(logger, "analysis", "Analýza", 0)
        stage(logger, "implementation", "Implementace", 1)
        stage(logger, "validation", "Validace", 2, "failed")
        state = {"mode": "KASKADA", "project": "Release 2.4", "cascade_definition": cascade.to_dict(),
                 "cascade_runtime": {"legacy_context": {}, "context_response_ids": {}, "values": {},
                                     "executed_step_ids": ["analysis", "implementation"], "step_signatures": {}},
                 "next_step_id": "validation", "failed_step_id": "validation", "failed_step_number": 3,
                 "human_error": "Validace nenašla očekávaný výstup.", "technical_error": "missing artifact"}
        logger.update_state(state)
        logger.checkpoint("cascade_step_completed", state_snapshot=state, safe_to_continue=True,
                          reason="Dva kroky jsou bezpečně dokončeny.", step_id=first)
        logger.update_state({"status": "failed", "error": "missing artifact", "human_error": state["human_error"]})
        logger.bundle.update_run({"created_at": "2026-09-15T10:00:00+00:00", "finished_at": "2026-09-15T10:02:03+00:00"})
        logger.bundle.seal()
        fixtures["cascade"] = logger.paths.run_dir
        source_cascade = LegacyRunAdapter(logger.paths.run_dir)
        child, _ui, _ = base("RUN_150920261005_REPAIR", "Release 2.4 · oprava", "KASKADA")
        child.record_lineage(source_cascade.run_id, "repair",
                             source_checkpoint_id=source_cascade.checkpoints()[-1]["checkpoint_id"],
                             notes="Doplň chybějící validovaný výstup.")
        stage(child, "validation", "Opravená validace", 0)
        child.update_state({"status": "completed", "cascade_definition": cascade.to_dict()})
        fixtures["cascade_repair"] = child.paths.run_dir

        logger, _ui, _ = base("RUN_150920261006_COMIC", "Komiks", "COMIC")
        stage(logger, "COMIC", "Komiksová operace", 0)
        logger.update_state({"status": "completed", "completed_at": 1789467200, "comic_operation_id": "comic_demo"})
        logger.bundle.update_run({"created_at": "2026-09-15T10:00:00+00:00", "finished_at": "2026-09-15T10:00:01+00:00"})
        logger.bundle.seal()
        fixtures["comic"] = logger.paths.run_dir
        # Veškeré záznamy vznikají reálným loggerem; čas fixture je následně
        # normalizován, aby snímek nezávisel na čase běhu rendereru.
        for day, directory in enumerate(fixtures.values()):
            bundle = LegacyRunAdapter(directory).bundle
            date = f"2026-09-{15 - day:02d}"
            bundle.update_run({"created_at": f"{date}T10:00:00+00:00"})
            rows = bundle.steps()
            for sequence, row in enumerate(rows):
                bundle.update_step(row["step_id"], started_at=f"{date}T10:{sequence:02d}:00+00:00",
                    finished_at=f"{date}T10:{sequence:02d}:52+00:00" if row["status"] != "batch_pending" else "")
            if bundle.run_record().get("status") != "batch_pending":
                bundle.update_run({"finished_at": f"{date}T10:{max(0, len(rows) - 1):02d}:52+00:00"})
            bundle.seal()
        return fixtures

    QCoreApplication.setAttribute(Qt.AA_DontUseNativeDialogs, True)
    app = QApplication([])
    from kajovo.app.main import _load_fonts
    _load_fonts()
    snapshots = []
    old_cwd = Path.cwd()
    with tempfile.TemporaryDirectory(prefix="kajovo-studio-", ignore_cleanup_errors=True) as temporary:
        workspace = Path(temporary)
        os.chdir(workspace)
        try:
            with patch("socket.create_connection", side_effect=AssertionError("Síť je při snímkování zakázaná.")), \
                 patch("socket.socket.connect", side_effect=AssertionError("Síť je při snímkování zakázaná.")), \
                 patch("requests.sessions.Session.request", side_effect=AssertionError("Síť je při snímkování zakázaná.")), \
                 patch("kajovo.core.secret_store._read_persisted_api_key", return_value=None), \
                 patch("kajovo.core.secret_store._read_keyring_api_key_record", return_value=secret_store._MISSING), \
                 patch("kajovo.core.secret_store.get_secret", return_value=None):
                settings = AppSettings(log_dir=str(workspace / "LOG"), cache_dir=str(workspace / "cache"))
                fixtures = create_run_fixtures(workspace, settings)
                window = create_window(settings)
                window.resize(*map(int, args.size.split(",")))
                window.context.models = ["gpt-4.1", "gpt-5.2", "gpt-image-1.5"]
                window.context.models_changed.emit()
                window.workbench.widgets["project"].setText("Ukázkový projekt · Rezervace")
                window.workbench.prompt.setPlainText("Vytvoř přehled rezervací s filtrem podle data a hosta. Zachovej současná data a ověř neplatné termíny.")
                fill_records(window.resources.lists["files"], [{"id": "file_ukazka", "filename": "Specifikace rezervací.pdf"}])
                fill_records(window.resources.lists["stores"], [{"id": "vs_ukazka", "name": "Dokumentace projektu"}])
                window.cascades.add_step()
                window.cascades.title.setText("Připravit návrh řešení")
                window.cascades.model.setCurrentText("gpt-4.1")
                window.cascades.commit_step()
                window.batches.records = [{"id": "batch_ukazka", "remote": {"status": "in_progress", "request_counts": {"completed": 8, "failed": 1, "total": 12}}, "state": {"project": "Rezervace", "status": "batch_pending"}, "run_dir": str(workspace / "LOG" / "RUN_ukazka"), "kind": "GENERATE", "photo": None, "started_at": "2026-09-15T10:00:00+00:00"}]
                window.batches.render()
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
                window.photos.jobs = [job]
                window.photos._render_jobs()

                comic = window.comics
                comic.timer.stop()
                project = comic.service.store.project("Ukázka rozhraní – Večer v kanceláři")
                karel = comic.service.store.entity(project, "character", "Karel", "Ukázková postava bez vzorového obrázku")
                room = comic.service.store.entity(project, "environment", "Kancelář", "Ukázkové prostředí bez vzorového obrázku")
                panel = comic.service.store.panel(project, "Příchod do kanceláře")
                record = comic.service.store.get("panels", panel)
                comic.service.store.save_panel(panel, record["revision"], record["name"], {"version": 1, "nodes": [
                    {"type": "character_ref", "entity_id": karel}, {"type": "text", "text": " otevírá dveře do "},
                    {"type": "environment_ref", "entity_id": room}, {"type": "text", "text": ". Večerní světlo, klidná atmosféra."},
                ]}, record["format"], [])
                comic.project_id = project
                comic.refresh_projects()
                comic.entity_lists["character"].setCurrentRow(0)
                comic.overlays.kind.setCurrentIndex(comic.overlays.kind.findData("dialog"))
                comic.overlays.add_layer()
                comic.overlays.text.setPlainText("Ahoj! Začneme pracovat.")

                def capture(widget, name, include_scroll=True):
                    print("Snímek:", name, flush=True)
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
                        if not isinstance(child, (QAbstractButton, QComboBox, QLabel, QLineEdit, QPlainTextEdit, QTextEdit, QAbstractItemView)):
                            continue
                        if not child.isVisibleTo(widget):
                            continue
                        if isinstance(child, (QPlainTextEdit, QTextEdit)):
                            text = child.toPlainText()
                        elif isinstance(child, QAbstractItemView):
                            model = child.model()
                            columns = 1 if isinstance(child, QListView) else model.columnCount(QModelIndex()) if model is not None else 0
                            text = "\n".join(" · ".join(str(model.index(row, column).data() or "")
                                              for column in range(columns))
                                             for row in range(model.rowCount(QModelIndex()))) if model is not None else ""
                        else:
                            text = child.text() if hasattr(child, "text") else child.currentText()
                        if isinstance(child, QLineEdit) and child.echoMode() != QLineEdit.Normal:
                            text = "[skrytý přístupový údaj]" if text else ""
                        origin = child.mapTo(widget, child.rect().topLeft())
                        controls.append({"id": child.objectName(), "type": type(child).__name__, "text": text,
                                         "x": origin.x(), "y": origin.y(), "width": child.width(), "height": child.height(),
                                         "enabled": child.isEnabled(), "accessible_name": child.accessibleName(),
                                         "tooltip": child.toolTip(), "placeholder": child.placeholderText() if hasattr(child, "placeholderText") else "",
                                         "choices": [child.itemText(index) for index in range(child.count())] if isinstance(child, QComboBox) else []})
                        if isinstance(child, QLabel) and text and child.wordWrap():
                            required = child.heightForWidth(child.width())
                            if required > child.height() + 2:
                                defects.append({"text": text, "required_height": required, "actual_height": child.height()})
                    snapshots.append({"name": name, "file": image_path.name, "width": widget.width(), "height": widget.height(), "scale": args.scale,
                                      "class": type(widget).__name__, "title": widget.windowTitle(), "controls": controls, "label_clipping": defects,
                                      "visible_classes": sorted({type(child).__name__ for child in widget.findChildren(QWidget) if child.isVisibleTo(widget)}),
                                      "tabs": [tabs.tabText(index) for tabs in widget.findChildren(QTabWidget) if tabs.isVisibleTo(widget) for index in range(tabs.count())],
                                      "current_tabs": [tabs.tabText(tabs.currentIndex()) for tabs in widget.findChildren(QTabWidget) if tabs.isVisibleTo(widget)],
                                      "menu_actions": [item.text() for item in widget.actions()] if isinstance(widget, QMenu) else []})
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

                def capture_evidence_dialog(view, name):
                    capture(view, name)
                    for number, tabs in enumerate(view.findChildren(QTabWidget)):
                        for index in range(tabs.count()):
                            tabs.setCurrentIndex(index)
                            capture(view, f"{name}_tabs{number}_{index}")
                            page = tabs.widget(index)
                            from kajovo.studio.evidence import EvidenceView
                            if isinstance(page, EvidenceView):
                                page.technical.setChecked(True)
                                capture(view, f"{name}_tabs{number}_{index}_expanded")
                                page.technical.setChecked(False)
                    view.hide()
                    return QDialog.Rejected

                def capture_popup(view, name):
                    capture(view, name)
                    view.hide()
                    return QDialog.Rejected

                for name, callback in (
                    ("resource_new_library", window.resources.create_store),
                    ("resource_file_codes", window.resources.add_store_files),
                    ("photo_save_template", window.photos.save_template),
                    ("cascade_contract", window.cascades.edit_contract),
                    ("cascade_options", window.cascades.definition_options),
                ):
                    with patch.object(QDialog, "exec", lambda view, n=name: capture_popup(view, n)):
                        callback()
                entity_record = comic.service.store.get("entities", karel)
                original_get = comic.service.store.get
                def reference_record(table, identifier):
                    if table == "entities" and identifier == karel:
                        return {**entity_record, "active_revision": "render-reference"}
                    if table == "entity_revisions" and identifier == "render-reference":
                        return {"asset_id": "render-asset", "descriptor": "Karel, tmavé vlasy, modrá košile. Zachovejte vzhled postavy i v dalších obrázcích."}
                    return original_get(table, identifier)
                with patch.object(comic.service.store, "get", reference_record), \
                     patch.object(comic.service.store, "asset_path", return_value=sample_path), \
                     patch.object(QDialog, "exec", lambda view: capture_popup(view, "comic_reference")):
                    comic.show_entity("character")

                window.show()
                window.select_page("history")
                from PySide6.QtTest import QTest
                for _ in range(200):
                    app.processEvents(QEventLoop.AllEvents, 25)
                    if window.history.model.rowCount() >= len(fixtures):
                        break
                    QTest.qWait(10)
                for row in range(window.history.model.rowCount()):
                    candidate = window.history.model.run_at(row)
                    if candidate and candidate.mode == "GENERATE":
                        window.history.tracks.selectRow(row)
                        break
                for _ in range(100):
                    app.processEvents(QEventLoop.AllEvents, 25)
                    if window.history.adapter:
                        break
                    QTest.qWait(10)
                if window.history.run and window.history.run.stages:
                    window.history._stage_selected(window.history.run, window.history.run.stages[-1])
                window.history.tracks.scrollToTop()
                capture(window, "run_studio_main")

                def run_detail(key, tab_name=None):
                    adapter = LegacyRunAdapter(fixtures[key])
                    payload = _payload(adapter)
                    state = read_state(adapter.root)
                    run = build_run(payload["summary"], steps=payload["steps"], state=state, events=payload["events"])
                    dialog = RunDetailDialog(window.context, adapter, payload, state, run, window)
                    if tab_name:
                        tabs = dialog.findChild(QTabWidget)
                        for index in range(tabs.count()):
                            if tabs.tabText(index) == tab_name:
                                tabs.setCurrentIndex(index)
                                break
                    dialog.show()
                    for _ in range(4):
                        app.processEvents(QEventLoop.AllEvents, 40)
                    for _ in range(100):
                        app.processEvents(QEventLoop.AllEvents, 25)
                        if not window.context.operations.active:
                            break
                        QTest.qWait(10)
                    if key == "modify":
                        view = dialog.findChild(ModifyMap)
                        view.table.setCurrentCell(view.table.rowCount() - 1, 0)
                        view.load_diff()
                        for _ in range(100):
                            app.processEvents(QEventLoop.AllEvents, 25)
                            if not window.context.operations.active:
                                break
                            QTest.qWait(10)
                    elif key == "cascade":
                        view = dialog.findChild(CascadeStepsView)
                        view.table.setCurrentCell(view.table.rowCount() - 1, 0)
                    capture(dialog, "run_studio_" + key, include_scroll=False)
                    for number, tabs in enumerate(dialog.findChildren(QTabWidget)):
                        for index in range(tabs.count()):
                            tabs.setCurrentIndex(index)
                            capture(dialog, f"run_studio_{key}_tabs{number}_{index}")
                    with patch.object(QDialog, "exec", lambda view: capture_evidence_dialog(view, "run_studio_" + key + "_technical")):
                        dialog.view.open_evidence()
                    dialog.hide()

                run_detail("generate", "Přehled")
                run_detail("modify", "Mapa změn a porovnání")
                run_detail("qa", "Přehled")
                run_detail("qfile", "Přehled")
                run_detail("cascade", "Přehled")
                run_detail("comic", "Přehled")
                def capture_menu():
                    menu = app.activePopupWidget()
                    if isinstance(menu, QMenu):
                        capture(menu, "history_actions")
                        menu.close()
                QTimer.singleShot(0, capture_menu)
                window.history.show_more()
                capture(window.history.filter_dialog, "history_filters")
                window.history.filter_dialog.hide()
                cascade_adapter = LegacyRunAdapter(fixtures["cascade"])
                repair = BranchComposer(window.history.launcher, cascade_adapter, cascade_adapter.checkpoints(),
                                        "repair", "validation", window)
                repair.show()
                for _ in range(200):
                    app.processEvents(QEventLoop.AllEvents, 25)
                    if not window.context.operations.active:
                        break
                    QTest.qWait(10)
                capture(repair, "run_studio_repair", include_scroll=False)
                repair.hide()
                for relation, edit in (("continue", False), ("rerun", False), ("rerun", True)):
                    composer = BranchComposer(window.history.launcher, cascade_adapter, cascade_adapter.checkpoints(), relation, "validation", window, edit_input=edit)
                    capture(composer, "history_branch_" + relation + ("_edit" if edit else ""))
                    composer.hide()
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
                    for number, combo in enumerate(page.findChildren(QComboBox)):
                        if combo.isVisibleTo(window) and combo.count():
                            combo.showPopup()
                            capture(combo.view().window(), f"{key}_choice{number}", include_scroll=False)
                            combo.hidePopup()
                    if key != "settings":
                        window.detach_page()
                        detached = window.detached[key]
                        capture(detached, "detached_" + key)
                        detached.accept()
                window.select_page("run")
                for mode in ("GENERATE", "MODIFY", "QA", "QFILE"):
                    window.workbench.widgets["mode"].setCurrentIndex(window.workbench.widgets["mode"].findData(mode))
                    capture(window, "run_mode_" + mode)
                    capture_scrolls("run_mode_" + mode)
                window.context.api_key = "render-only"
                window.workbench.widgets["model"].setCurrentIndex(window.workbench.widgets["model"].findData("gpt-4.1"))
                window.workbench.widgets["out_dir"].setText(str(workspace / "output"))
                window.workbench.validate()
                capture(window, "run_ready")
                window.select_page("settings")
                window.detach_page()
                detached = window.detached["settings"]
                capture(detached, "detached_settings")
                detached.accept()
                for key, record in (("input", CascadeInput(name="Zdrojový soubor")), ("output", CascadeOutput(name="Výsledný soubor"))):
                    dialog = CascadeItemDialog(record, window.cascades.definition.steps, window)
                    capture(dialog, "cascade_" + key + "_editor")
                    selector = dialog.form.fields["source" if key == "input" else "kind"]
                    for index in range(selector.count()):
                        selector.setCurrentIndex(index)
                        capture(dialog, "cascade_" + key + "_" + str(selector.currentData()))
                    dialog.hide()
                for key, mode, accept in (("files", QFileDialog.ExistingFiles, QFileDialog.AcceptOpen),
                                          ("file", QFileDialog.ExistingFile, QFileDialog.AcceptOpen),
                                          ("directory", QFileDialog.Directory, QFileDialog.AcceptOpen),
                                          ("save", QFileDialog.AnyFile, QFileDialog.AcceptSave)):
                    dialog = FileDialog(window, "Uložit soubor" if key == "save" else "Vybrat složku" if key == "directory" else "Vybrat soubory", str(workspace))
                    dialog.setFileMode(mode)
                    dialog.setAcceptMode(accept)
                    capture(dialog, "file_picker_" + key)
                    dialog.hide()
                for state in STATES:
                    dialog = OperationDialog("Připravuji soubory projektu", window)
                    dialog.notification.show()
                    dialog.on_event(ProgressEvent("PLAN", planned_steps=("QA_INPUT", "QA_RESPONSE", "QA_VALIDATION")))
                    dialog.on_event(ProgressEvent("QA_INPUT", "completed"))
                    dialog.on_event(ProgressEvent("Ověřování souborů", state, 8 if state == "completed" else 3, 8, "souborů", "Kontroluji obsah souborů před uložením."))
                    if state not in ("active", "waiting"):
                        dialog.finish(state)
                    capture(dialog, "operation_" + state)
                    dialog.timer.stop()
                    dialog.hide()
                dialog = OperationDialog("Čekání na bezpečné zastavení", window)
                dialog.stop_callback = lambda: None
                dialog.stop.setEnabled(True)
                dialog.request_stop()
                capture(dialog, "operation_stopping")
                dialog.inspector.tech_toggle.setChecked(True)
                capture(dialog, "operation_technical")
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
                dialog = ValueDialog("Vstupy a výstupy kroku", "Technický zápis vstupů a výstupů kroku (JSON)", '{"name": "Ukázka"}', window, structured=True)
                capture(dialog, "value_editor")
                dialog.hide()
                for kind in ("text", "multiline", "item"):
                    dialog = QInputDialog(window)
                    dialog.setWindowTitle("Pojmenovat práci" if kind != "item" else "Vybrat uložený soubor")
                    dialog.setLabelText("Název" if kind != "item" else "Soubor")
                    if kind == "multiline":
                        dialog.setOption(QInputDialog.UsePlainTextEditForTextInput)
                    if kind == "item":
                        dialog.setComboBoxItems(["1. navrh.txt", "2. obrazek.png"])
                        dialog.setComboBoxEditable(False)
                    capture(dialog, "input_dialog_" + kind)
                    dialog.hide()
                dialog = QMessageBox(QMessageBox.Warning, "Upozornění", "Vyberte uložený soubor.", QMessageBox.Ok, window)
                capture(dialog, "message_box")
                dialog.hide()
                for kind, editor in (("single", window.workbench.widgets["project"]), ("multiline", window.workbench.prompt)):
                    menu = editor.createStandardContextMenu()
                    capture(menu, "edit_menu_" + kind)
                    menu.hide()
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
    (output / "qt-texts.json").write_text(json.dumps(list(qt_texts.values()), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"screenshots": len(snapshots), "output": str(output), "label_clipping": sum(len(row["label_clipping"]) for row in snapshots)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
