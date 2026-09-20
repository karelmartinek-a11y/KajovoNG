"""Ověřený návrh opravného skriptu oddělený od souhlasu v rozhraní."""

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile

from .utils import atomic_write_text, safe_join_under_root


@dataclass(frozen=True)
class RepairProposal:
    root: Path
    script: Path
    content: bytes
    description: str
    digest: str
    remote: bool


def prepare_repair(directory, remote=False, expected_digest=""):
    root = Path(directory).resolve()
    expected = "run_this_script_repairme_kajovo.sh" if remote else "run_this_script_repairme_kajovo_windows.bat"
    paths = [path for path in root.iterdir() if path.name.casefold() == expected]
    if len(paths) != 1:
        raise ValueError("Výstup neobsahuje právě jeden odpovídající opravný skript.")
    script = Path(safe_join_under_root(root, paths[0].name))
    description = Path(safe_join_under_root(root, "readmerepair.txt")).read_text(encoding="utf-8")
    content = script.read_bytes()
    if not content:
        raise ValueError("Opravný skript je prázdný.")
    digest = hashlib.sha256(content).hexdigest()
    if expected_digest and digest != expected_digest:
        raise ValueError(
            "Publikovaný opravný skript neodpovídá evidovanému SHA-256."
        )
    return RepairProposal(root, script, content, description, digest, remote)


def execute_repair(proposal, cfg):
    if hashlib.sha256(proposal.script.read_bytes()).hexdigest() != proposal.digest:
        raise ValueError("Opravný skript se po potvrzení změnil; je nutné jej znovu zkontrolovat.")
    if proposal.remote:
        from .diagnostics.ssh import execute_ssh_repair
        result = execute_ssh_repair(proposal.content, cfg)
    else:
        if os.name != "nt":
            raise ValueError("Tato oprava vyžaduje systém Windows.")
        with tempfile.NamedTemporaryFile(dir=proposal.root, suffix=".bat", delete=False) as stream:
            stream.write(proposal.content)
            script = Path(stream.name)
        try:
            result = subprocess.run(["cmd.exe", "/d", "/s", "/c", '"' + str(script) + '"'],
                                    cwd=proposal.root, capture_output=True, text=True, errors="replace",
                                    timeout=120, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        finally:
            script.unlink(missing_ok=True)
    log = Path(safe_join_under_root(proposal.root, "_repair_ssh_exec_log.txt" if proposal.remote else "_repair_exec_log.txt"))
    atomic_write_text(log, proposal.description + f"\nNávratový kód: {result.returncode}\n{result.stdout}\n{result.stderr}")
    if result.returncode:
        raise subprocess.CalledProcessError(result.returncode, result.args, result.stdout, result.stderr)
    return {"status": "completed", "text": "Opravný skript skončil bez chyby procesu; účinek ověřte diagnostikou.", "saved": [str(log)]}



def published_repair_artifact(state, remote=False):
    """Return a repair-script artifact only after an explicit successful publish."""
    if not isinstance(state, dict) or state.get("dry_run"):
        return None
    if state.get("publication_state") != "published_unverified":
        return None
    name = (
        "run_this_script_repairme_kajovo.sh"
        if remote
        else "run_this_script_repairme_kajovo_windows.bat"
    )
    matches = [
        row for row in (state.get("published_files") or [])
        if isinstance(row, dict)
        and str(row.get("path") or "").casefold() == name.casefold()
        and row.get("sha256")
    ]
    if len(matches) != 1:
        return None
    return {
        "path": str(matches[0]["path"]),
        "sha256": str(matches[0]["sha256"]),
        "remote": bool(remote),
    }


def claim_published_repair_offer(run_dir, state, remote=False):
    """Cross-process one-shot claim keyed by the exact published script hash."""
    artifact = published_repair_artifact(state, remote)
    if artifact is None:
        return None
    root = Path(run_dir).resolve()
    claims = root / "manifests" / "repair_offer_claims"
    claims.mkdir(parents=True, exist_ok=True)
    token = hashlib.sha256(
        (
            ("remote:" if remote else "local:")
            + artifact["path"]
            + ":"
            + artifact["sha256"]
        ).encode("utf-8")
    ).hexdigest()
    claim = claims / token
    try:
        fd = os.open(str(claim), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return None
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(artifact["sha256"] + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    return artifact
