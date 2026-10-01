"""Ztráta manifestu uzavřeného běhu nesmí povolit obnovu či export."""
import json

import pytest

from kajovo.core.run_bundle import LegacyRunAdapter, RunBundle
from kajovo.studio.history_artifacts import export_run_bundle


@pytest.mark.parametrize("damage", ["missing", "not_object", "files_not_object"])
def test_sealed_bundle_manifest_loss_is_changed_and_export_preserves_evidence(tmp_path, damage):
    bundle = RunBundle(tmp_path / "RUN_MANIFEST", "RUN_MANIFEST", create=True)
    (bundle.root / "output.txt").write_bytes(b"preserved output")
    bundle.seal()
    assert bundle.verify_integrity()["valid"]
    if damage == "missing":
        bundle.checksums_path.unlink()
    else:
        value = [] if damage == "not_object" else {"files": []}
        bundle.checksums_path.write_text(json.dumps(value), encoding="utf-8")
    before = {str(path.relative_to(bundle.root)): path.read_bytes()
              for path in bundle.root.rglob("*") if path.is_file()}
    result = RunBundle(bundle.root).verify_integrity()
    assert result["status"] == "changed", "Sealed evidence downgraded to ordinary unsealed"
    assert not result["valid"] and result["errors"]
    destination = tmp_path / "export.zip"
    with pytest.raises(ValueError, match="integrity"):
        export_run_bundle(LegacyRunAdapter(bundle.root), destination)
    assert not destination.exists()
    assert before == {str(path.relative_to(bundle.root)): path.read_bytes()
                      for path in bundle.root.rglob("*") if path.is_file()}


def test_never_sealed_active_bundle_keeps_unsealed_status(tmp_path):
    bundle = RunBundle(tmp_path / "RUN_ACTIVE", "RUN_ACTIVE", create=True)
    assert bundle.verify_integrity()["status"] == "unsealed"


@pytest.mark.parametrize("control", ["bundle.json", "checksums.json", "execution.lock"])
def test_control_alias_does_not_hide_unregistered_file(tmp_path, control):
    bundle = RunBundle(tmp_path / "RUN_ALIAS", "RUN_ALIAS", create=True)
    (bundle.root / "execution.lock").write_bytes(b"operational lock")
    bundle.seal()
    assert bundle.verify_integrity()["valid"]
    alias = bundle.root / "unregistered-output.txt"
    alias.symlink_to(control)
    before = bundle.checksums_path.read_bytes()
    result = bundle.verify_integrity()
    assert not result["valid"], "Symlink to control file hid unregistered content"
    assert any("unregistered-output.txt" in error for error in result["errors"])
    assert alias.is_symlink() and bundle.checksums_path.read_bytes() == before


def test_history_new_process_rejects_lost_sealed_manifest_before_submit(tmp_path):
    from pathlib import Path
    from test_runtime_end_to_end import child

    child("from pathlib import Path; import sys; from test_delivery_http_graph import delivery_process; "
          "delivery_process(Path(sys.argv[1]), 'GENERATE', False, 'prepare')", tmp_path)
    run = Path(json.loads((tmp_path / "delivery-info.json").read_text())["run"])
    assert RunBundle(run).verify_integrity()["valid"]
    (run / "checksums.json").unlink()
    before = {str(path.relative_to(run)): path.read_bytes() for path in run.rglob("*") if path.is_file()}
    requests = (tmp_path / "http-calls.jsonl").read_bytes()
    child("from pathlib import Path; import sys; from test_additional_process_recovery import history_process; "
          "root=Path(sys.argv[1])\n"
          "try:\n history_process(root, 'continue')\n"
          "except ValueError as error:\n assert 'kontrolou integrity' in str(error), str(error)\n"
          "else:\n raise AssertionError('Lost sealed manifest allowed History submit')", tmp_path)
    assert (tmp_path / "http-calls.jsonl").read_bytes() == requests
    assert before == {str(path.relative_to(run)): path.read_bytes() for path in run.rglob("*") if path.is_file()}
