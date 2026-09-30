"""Nabídka opravy je jednorázová napříč procesy a patří konkrétnímu hashi."""
import hashlib
import json
import subprocess
from pathlib import Path
import sys

import pytest

from kajovo.core.repair_execution import claim_published_repair_offer, published_repair_artifact


@pytest.mark.parametrize("remote", [False, True])
def test_repair_offer_claim_survives_restart_and_does_not_claim_other_hash(tmp_path, remote):
    name = "run_this_script_repairme_kajovo.sh" if remote else "run_this_script_repairme_kajovo_windows.bat"
    digest = hashlib.sha256(b"approved script").hexdigest()
    state = {"publication_state": "published_unverified",
             "published_files": [{"path": name, "sha256": digest}]}
    expected = {"path": name, "sha256": digest, "remote": remote}
    assert claim_published_repair_offer(tmp_path, state, remote) == expected
    claims = tmp_path / "manifests" / "repair_offer_claims"
    paths = list(claims.iterdir())
    assert len(paths) == 1 and paths[0].read_text() == digest + "\n"
    root = Path(__file__).resolve().parents[1]
    code = """import json,sys
from kajovo.core.repair_execution import claim_published_repair_offer
state=json.loads(sys.argv[2])
print(json.dumps(claim_published_repair_offer(sys.argv[1],state,sys.argv[3]=='True')))
"""
    result = subprocess.run([sys.executable, "-c", code, str(tmp_path), json.dumps(state), str(remote)],
                            cwd=root, capture_output=True, text=True, timeout=20, check=True)
    assert json.loads(result.stdout) is None
    assert len(list(claims.iterdir())) == 1
    new_digest = hashlib.sha256(b"new approved script").hexdigest()
    state["published_files"][0]["sha256"] = new_digest
    assert claim_published_repair_offer(tmp_path, state, remote)["sha256"] == new_digest
    assert len(list(claims.iterdir())) == 2


@pytest.mark.parametrize("state", [
    None, {}, {"dry_run": True, "publication_state": "published_unverified"},
    {"publication_state": "staged", "published_files": []},
    {"publication_state": "published_unverified", "published_files": [
        {"path": "run_this_script_repairme_kajovo_windows.bat", "sha256": ""}]},
    {"publication_state": "published_unverified", "published_files": [
        {"path": "run_this_script_repairme_kajovo_windows.bat", "sha256": "a"},
        {"path": "run_this_script_repairme_kajovo_windows.bat", "sha256": "b"}]},
])
def test_unpublished_or_ambiguous_repair_never_creates_claim(tmp_path, state):
    assert published_repair_artifact(state) is None
    assert claim_published_repair_offer(tmp_path, state) is None
    assert not (tmp_path / "manifests").exists()
