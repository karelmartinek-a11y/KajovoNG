"""Odvozené katalogy a pomocné workflow nesmějí rozcházet SSOT."""
import csv
import io
from pathlib import Path

from kajovo.core.model_registry import model_spec, supports_batch_endpoint
from scripts.export_ui_validation import render
from utf8nobom.app import build_scan_plan, TargetSpec

ROOT = Path(__file__).resolve().parents[1]


def test_ui_inventory_is_derived_and_references_current_sources():
    value = (ROOT / "docs/UI_VALIDATION_MATRIX.csv").read_text(encoding="utf-8")
    assert value == render(ROOT)
    for row in csv.DictReader(io.StringIO(value)):
        assert (ROOT / row["zdroj"].split(":")[0]).is_file()
    ids = [row[0] for row in csv.reader((ROOT / "docs/ui/validation-plan.csv").read_text(encoding="utf-8").splitlines())]
    assert len(ids) == len(set(ids))


def test_chat_context_limits_do_not_invent_independent_input_cap():
    for name in ["gpt-5-chat-latest", "gpt-5.1-chat-latest", "gpt-5.2-chat-latest", "gpt-5.3-chat-latest"]:
        spec = model_spec(name)
        assert spec["context_window"] == 128000
        assert spec["max_input_tokens"] is None


def test_image_batch_is_selected_by_endpoint():
    from kajovo.core.comic_types import IMAGE_MODEL
    assert supports_batch_endpoint(IMAGE_MODEL, "/v1/images/edits")
    assert supports_batch_endpoint(IMAGE_MODEL, "/v1/images/generations")
    assert not supports_batch_endpoint(IMAGE_MODEL, "/v1/responses")


def test_regular_converter_plan_counts_backup_read_and_processing(tmp_path):
    path = tmp_path / "text.txt"
    path.write_bytes(b"abc")
    plan = build_scan_plan([TargetSpec(tmp_path)])
    assert plan.total_units == 9


def test_release_metadata_names_are_platform_specific():
    workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    assert '"windows-x64-"' in workflow
    assert '${platform}-$(basename' in workflow
