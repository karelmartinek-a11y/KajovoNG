"""Přesné obsahově adresované artefakty pro obnovu a forenzní evidenci."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

from .contracts import parse_json_strict, parse_json_value_strict
from .orchestration.contracts import canonical_bytes
from .orchestration.errors import OrchestrationError
from .utils import atomic_write_text


def save_artifact(run_dir, name, value):
    root = Path(run_dir) / "artifacts"
    root.mkdir(exist_ok=True, mode=0o700)
    raw = canonical_bytes(value)
    digest = hashlib.sha256(raw).hexdigest()
    target = root / (digest + ".json")
    try:
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        if target.read_bytes() != raw:
            raise OrchestrationError(
                "RECOVERY_ARTIFACT_CORRUPT",
                "Obnovitelný artefakt byl poškozen; nelze jej přepsat.",
            ) from None
    else:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    index_path = root / "index.json"
    index = (
        parse_json_strict(index_path.read_text("utf-8"))
        if index_path.exists()
        else {"version": 1, "entries": {}}
    )
    if index.get("version") != 1:
        raise OrchestrationError(
            "RECOVERY_INDEX_VERSION",
            "Nepodporovaná verze indexu artefaktů.",
        )
    index["entries"][name] = digest
    atomic_write_text(
        str(index_path),
        canonical_bytes(index).decode("utf-8"),
    )
    return str(target)


def artifact_path(run_dir, name):
    root = Path(run_dir) / "artifacts"
    index_path = root / "index.json"
    if not index_path.exists():
        return None
    index = parse_json_strict(index_path.read_text("utf-8"))
    if index.get("version") != 1:
        raise OrchestrationError(
            "RECOVERY_INDEX_VERSION",
            "Nepodporovaná verze indexu artefaktů.",
        )
    digest = index["entries"].get(name)
    if digest is None:
        return None
    if (
        not isinstance(digest, str)
        or len(digest) != 64
        or set(digest) - set("0123456789abcdef")
    ):
        raise OrchestrationError(
            "RECOVERY_ARTIFACT_REFERENCE",
            "Neplatná reference artefaktu.",
        )
    target = root / (digest + ".json")
    if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
        raise OrchestrationError(
            "RECOVERY_ARTIFACT_HASH",
            "Obnovitelný artefakt má neplatný hash; jiná kopie evidence není náhradou.",
        )
    return str(target)


STATE_ARTIFACTS = {
    "preparation_snapshot",
    "generate_batch",
    "generate_batches",
    "ui_state",
    "pending_batch_submission",
}


def load_run_state(run_dir):
    state = parse_json_strict((Path(run_dir) / "run_state.json").read_text("utf-8"))
    for name in STATE_ARTIFACTS:
        path = artifact_path(run_dir, "state/" + name)
        # Vymazaný stavový klíč se nesmí obnovit ze staršího artefaktu.
        if path and name in state:
            state[name] = parse_json_value_strict(Path(path).read_text("utf-8"))
    return state
