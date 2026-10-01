"""České popisky zachovávají hodnoty backendu a pravdivost výsledků."""

from types import SimpleNamespace

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QWidget

from kajovo.core.cascade_types import CascadeInput, CascadeOutput
from kajovo.core.config import AppSettings
from kajovo.core.progress import ProgressEvent
from kajovo.studio.application import create_window
from kajovo.studio.cascade_items import CascadeItemDialog
from kajovo.studio.evidence import EvidenceView
from kajovo.studio.history_models import RunTableModel, build_run
from kajovo.studio.presentation import comic_readable, git_status_readable, human_readable, integrity_summary
from kajovo.studio.progress_dialog import MultiProgressDialog


def window_fixture(qtbot, tmp_path):
    window = create_window(AppSettings(
        log_dir=str(tmp_path / "LOG"), cache_dir=str(tmp_path / "cache"),
        comic_library_dir=str(tmp_path / "COMICS"),
    ))
    qtbot.addWidget(window)
    return window


def test_readable_choices_keep_canonical_run_and_photo_parameters(qtbot, tmp_path):
    window = window_fixture(qtbot, tmp_path)
    page = window.workbench
    page.widgets["mode"].setCurrentIndex(page.widgets["mode"].findData("QFILE"))
    page.widgets["qfile_output_format"].setCurrentIndex(page.widgets["qfile_output_format"].findData("csv"))
    assert page.config().mode == "QFILE"
    assert page.config().qfile_output_format == "csv"
    assert "Tabulka" in page.widgets["qfile_output_format"].currentText()
    window.context.models = ["gpt-image-1.5"]
    window.context.models_changed.emit()
    photos = window.photos
    assert photos.size.itemText(photos.size.findData("auto")) == "Automaticky"
    photos.size.setCurrentIndex(photos.size.findData("1024x1024"))
    assert photos.size.currentData() == "1024x1024"
    photos.refresh_image_options()
    assert photos.size.currentData() == "1024x1024"
    assert "obrazových bodů" in photos.size.currentText()
    assert photos.format.currentData() == "png"
    comics = window.comics
    comics.project_id = comics.service.store.project("Velikost obrázku")
    comics.refresh_projects()
    comics.add_panel()
    assert "vzorovými obrázky" in comics.notice.text()
    assert "generativní" not in comics.notice.text()
    comics.preset.setCurrentIndex(comics.preset.findData("DL"))
    assert comics.preset.currentText() == "Úzký papír DL (110 × 220 mm)"
    assert comics.landscape.isEnabled()
    from kajovo.core.comic_types import PanelFormat

    expected = PanelFormat.paper("DL", dpi=comics.dpi.value())
    assert (comics.width_px.value(), comics.height_px.value()) == (expected.width, expected.height)
    comics.preset.setCurrentIndex(comics.preset.findData("1:1"))
    assert comics.preset.currentText() == "Čtverec (1:1)"
    assert not comics.landscape.isEnabled()
    assert comics.width_px.value() == comics.height_px.value()


def test_qt_file_type_and_action_names_have_czech_fallback():
    from kajovo.studio.components import CzechTranslator

    translator = CzechTranslator()
    assert translator.translate("QMimeType", "Portable Document Format") == "Dokument PDF"
    assert translator.translate("QAbstractFileIconProvider", "Folder") == "Složka"
    assert translator.translate("QPlatformTheme", "OK") == "Potvrdit"


def test_batch_context_display_keeps_confirmed_event_identity():
    from kajovo.core.progress_model import ProgressModel, step_name

    model = ProgressModel()
    model.update(ProgressEvent("Kontext BATCH", "completed"))
    assert "Kontext BATCH" in model.steps
    assert step_name("Kontext BATCH") == "Příprava podkladů dávkového zpracování"
    assert step_name("COMIC_REFERENCE") == "Vytvoření vzorového obrázku"


def test_history_localized_filter_still_selects_canonical_mode(qtbot, tmp_path):
    window = window_fixture(qtbot, tmp_path)
    combo = window.history.mode_filter
    combo.setCurrentIndex(combo.findData("QFILE"))
    assert "QFILE" not in combo.currentText()
    source = [build_run({"run_id": "one", "mode": "QFILE"}), build_run({"run_id": "two", "mode": "QA"})]
    assert [run.run_id for run in RunTableModel.filter_runs(source, {"mode": combo.currentData()})] == ["one"]
    model = RunTableModel()
    model.set_runs(source)
    assert "Vytvoření jednoho souboru" in model.data(model.index(0, 0), Qt.DisplayRole)
    assert source[0].mode == "QFILE"


def test_history_detail_avoids_raw_error_and_overlap_in_small_window(qtbot, tmp_path):
    from PySide6.QtWidgets import QLabel, QScrollArea
    from kajovo.core.run_bundle import LegacyRunAdapter
    from kajovo.core.runlog import RunLogger
    from kajovo.studio.history_details import RunDetailDialog

    window = window_fixture(qtbot, tmp_path)
    logger = RunLogger(str(tmp_path / "LOG"), "run", "Projekt")
    adapter = LegacyRunAdapter(logger.paths.run_dir)
    state = {"error": "missing artifact: PRIVATE_RAW", "ui_state": {"prompt": "Zadání"}}
    run = build_run({"run_id": "run", "project": "Projekt", "mode": "KASKADA", "status": "failed"})
    dialog = RunDetailDialog(window.context, adapter, {}, state, run)
    qtbot.addWidget(dialog)
    dialog.resize(640, 360)
    dialog.show()
    qtbot.wait(50)
    view = dialog.view
    labels = "\n".join(label.text() for label in view.findChildren(QLabel))
    assert "PRIVATE_RAW" not in labels and "missing artifact" not in labels
    assert "Číslo záznamu: run" in labels
    assert state["error"] == "missing artifact: PRIVATE_RAW"
    assert dialog.size().width() == 640 and dialog.size().height() == 360
    assert view.timeline.geometry().bottom() < view.phase_label.geometry().top()
    assert view.phase_label.geometry().bottom() < view.tabs.geometry().top()
    outer = next(area for area in dialog.findChildren(QScrollArea) if area.widget() is view)
    assert outer.verticalScrollBar().maximum() > 0


def test_czech_balloon_name_roundtrips_through_real_comic_store(qtbot, tmp_path):
    window = window_fixture(qtbot, tmp_path)
    page = window.comics
    page.project_id = page.service.store.project("Příběh")
    page.refresh_projects()
    balloon = page.style_fields["balloon"]
    balloon.setCurrentIndex(balloon.findData("narativní"))
    assert balloon.currentText() == "Vyprávění"
    assert page.save_style()
    assert page.service.store.get("projects", page.project_id)["style"]["balloon"] == "narativní"
    page.refresh_project()
    assert balloon.currentData() == "narativní"
    assert page.overlays.kind.currentData() == "caption"


def test_cascade_form_only_enables_fields_used_by_selected_kind(qtbot):
    parent = QWidget()
    qtbot.addWidget(parent)
    dialog = CascadeItemDialog(CascadeOutput(name="Výsledek"), [], parent)
    qtbot.addWidget(dialog)
    fields = dialog.form.fields
    fields["kind"].setCurrentIndex(fields["kind"].findData("file"))
    assert fields["file_name"].isEnabled()
    assert not fields["decision_options"].isEnabled()
    fields["kind"].setCurrentIndex(fields["kind"].findData("decision"))
    assert fields["decision_options"].isEnabled()
    assert not fields["file_name"].isEnabled()
    assert not fields["json_schema"].isEnabled()
    source = CascadeItemDialog(CascadeInput(name="Podklad", source="output"), [], parent)
    qtbot.addWidget(source)
    assert not source.form.fields["value"].isEnabled()
    assert source.form.fields["source_step_id"].isEnabled()


def test_comic_reference_dialog_keeps_close_button_reachable_in_small_window(qtbot, tmp_path, monkeypatch):
    from PySide6.QtGui import QImage
    from PySide6.QtWidgets import QPushButton, QScrollArea

    window = window_fixture(qtbot, tmp_path)
    page = window.comics
    image = QImage(1200, 900, QImage.Format_RGB32)
    image.fill(Qt.white)
    path = tmp_path / "reference.png"
    image.save(str(path))
    monkeypatch.setattr(page, "selected_entity", lambda kind: "entity")
    monkeypatch.setattr(page.service.store, "get", lambda table, identifier:
                        {"name": "Postava", "active_revision": "revision"} if table == "entities"
                        else {"asset_id": "asset", "descriptor": "Popis vzhledu. " * 30})
    monkeypatch.setattr(page.service.store, "asset_path", lambda identifier: path)

    def inspect(dialog):
        qtbot.addWidget(dialog)
        dialog.resize(480, 360)
        dialog.show()
        qtbot.wait(30)
        assert dialog.width() == 480 and dialog.height() == 360
        close = dialog.findChild(QPushButton, "comic.reference.close")
        assert close.isVisibleTo(dialog)
        assert close.mapTo(dialog, close.rect().bottomRight()).y() < dialog.height()
        assert dialog.findChild(QScrollArea).verticalScrollBar().maximum() > 0
        dialog.hide()
        return QDialog.Rejected

    monkeypatch.setattr(QDialog, "exec", inspect)
    page.show_entity("character")


def test_human_result_does_not_expose_json_or_claim_functional_success(qtbot):
    value = {"status": "completed_unverified", "published_files": [{"path": "projekt.py"}],
             "questions": [{"question": "Kterou verzi?"}], "private_code": "RAW_CODE"}
    view = EvidenceView("Výsledek")
    qtbot.addWidget(view)
    view.set_value(value)
    text = view.content.toPlainText()
    assert "čeká na ověření" in text
    assert "projekt.py" in text and "Kterou verzi?" in text
    assert "RAW_CODE" not in text and "published_files" not in text
    assert view.value is value
    view.technical.setChecked(True)
    assert "RAW_CODE" in view.content.toPlainText()


def test_dialog_result_and_error_show_actionable_summary(qtbot, monkeypatch):
    captured = []

    class Capture:
        def __init__(self, title, message, parent, details=None):
            captured.append((title, message, details))

        def exec(self):
            return QDialog.Rejected

    monkeypatch.setattr("kajovo.studio.progress_dialog.DetailDialog", Capture)
    dialog = MultiProgressDialog("Výsledek")
    qtbot.addWidget(dialog)
    dialog.result = {"status": "files_complete_unverified", "saved": ["vysledek.txt"]}
    dialog.show_result()
    assert "vysledek.txt" in captured[-1][1]
    assert "ověření" in captured[-1][1]
    dialog.error = SimpleNamespace(message="Chybí soubor.", next_step="Vyberte jiný soubor.", code="missing", detail="raw")
    dialog.show_details()
    assert "Vyberte jiný soubor." in captured[-1][1]
    dialog.timer.stop()


def test_progress_localizes_next_stage_without_changing_ring_truth(qtbot):
    dialog = MultiProgressDialog("Průběh")
    qtbot.addWidget(dialog)
    dialog.on_event(ProgressEvent("PLAN", planned_steps=("QA_INPUT", "QA_RESPONSE", "QA_VALIDATION")))
    dialog.on_event(ProgressEvent("QA_INPUT", "completed", next_step="QA_RESPONSE"))
    assert "Získání odpovědi" in dialog.inspector.next_label.text()
    assert "QA_RESPONSE" not in dialog.inspector.next_label.text()
    assert dialog.mark.done == 1 and dialog.mark.total == 3
    dialog.finish("partial")
    assert dialog.mark.done == 1 and dialog.mark.total == 3


def test_comic_summary_preserves_user_text_and_translates_only_system_values():
    text = comic_readable({"status": "pass", "dialogue": [{"text": "PASS", "speaker": "Karel"}]})
    assert "Kontrola prošla" in text
    assert "PASS" in text and "Karel" in text
    assert '"dialogue"' not in text
    assert human_readable({"text": "completed", "status": "partial"}).startswith("Výsledný text\ncompleted")


def test_git_summary_explains_changes_without_altering_paths():
    text = git_status_readable("## main...origin/main [ahead 1]\n M README.md\n?? novy.py\nUU konflikt.txt\n")
    assert "Větev projektu: main" in text
    assert "Změněný soubor: README.md" in text
    assert "Nový soubor: novy.py" in text
    assert "nevyřešeným konfliktem: konflikt.txt" in text
    assert "ahead" not in text


def test_artifact_label_confirms_integrity_only_after_actual_hash_check(qtbot, tmp_path):
    import hashlib
    from kajovo.studio.history_artifacts import ArtifactBrowser

    (tmp_path / "dobry.txt").write_text("Uložený výsledek", encoding="utf-8")
    (tmp_path / "zmeneny.txt").write_text("Změna po uložení", encoding="utf-8")
    digest = hashlib.sha256((tmp_path / "dobry.txt").read_bytes()).hexdigest()
    records = [{"artifact_id": str(index), "display_name": name, "path_in_bundle": name,
                "sha256": digest, "mime_type": "text/plain"}
               for index, name in enumerate(("dobry.txt", "zmeneny.txt"))]
    browser = ArtifactBrowser()
    qtbot.addWidget(browser)
    browser.set_artifacts(tmp_path, records)
    assert browser.table.item(0, 4).text() == "Neporušený soubor"
    assert browser.table.item(1, 4).text() == "Kontrola čeká"
    browser.table.setCurrentCell(1, 0)
    assert browser.table.item(1, 4).text() == "Kontrola neprošla"
    assert not browser.buttons["save"].isEnabled()
    assert "funkčnost" in integrity_summary({"status": "verified", "valid": True})
    assert "nebyla potvrzena" in integrity_summary({"status": "verified", "valid": False})


def test_history_file_picker_uses_names_but_keeps_artifact_identity(qtbot, tmp_path, monkeypatch):
    window = window_fixture(qtbot, tmp_path)
    page = window.history
    files = [{"artifact_id": "first", "display_name": "navrh.txt", "reusable": True},
             {"artifact_id": "second", "display_name": "navrh.txt", "reusable": True}]
    page.payload = {"artifacts": files}
    selected = []

    def choose(_parent, _title, _label, labels, _current, editable):
        assert not editable
        assert labels == ["1. navrh.txt", "2. navrh.txt"]
        return labels[1], True

    monkeypatch.setattr("kajovo.studio.history.QInputDialog.getItem", choose)
    monkeypatch.setattr(page, "clone_with_artifact", selected.append)
    page.choose_reusable_clone()
    assert selected == [files[1]]


def test_history_filters_fit_small_window_and_keep_close_action_reachable(qtbot, tmp_path):
    from PySide6.QtWidgets import QPushButton, QScrollArea

    dialog = window_fixture(qtbot, tmp_path).history.filter_dialog
    dialog.resize(480, 360)
    dialog.show()
    qtbot.wait(20)
    assert dialog.height() <= 360
    button = dialog.findChild(QPushButton, "history.filters.close")
    assert button.isVisibleTo(dialog)
    assert dialog.rect().contains(button.mapTo(dialog, button.rect().bottomRight()))
    area = dialog.findChild(QScrollArea)
    assert area.verticalScrollBar().maximum() > 0
    button.click()
    assert not dialog.isVisible()


def test_comic_error_explains_missing_prerequisite_without_exposing_raw_message():
    from kajovo.core.comic_types import ComicError
    from kajovo.core.user_errors import describe_error

    report = describe_error(ComicError("bible_missing", "Nejprve sestavte bible; Bearer sk-proj-secretvalue"))
    assert "pravidla komiksu" in report.message
    assert "Sestavit pravidla komiksu" in report.next_step
    assert "bible" not in report.message
    assert "sk-proj-secretvalue" not in report.detail
    selected = describe_error(ComicError("missing_entity", "Vyberte entitu"))
    assert "postava nebo prostředí" in selected.message
    assert "Vyberte" in selected.next_step
