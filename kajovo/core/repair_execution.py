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


def prepare_repair(directory, remote=False):
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
    return RepairProposal(root, script, content, description, hashlib.sha256(content).hexdigest(), remote)


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
