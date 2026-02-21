from __future__ import annotations

import os
from typing import Callable, List, Optional, Tuple

import paramiko


def collect_ssh_diagnostics(
    root: str,
    host: str,
    user: str,
    key_path: str,
    password: str,
    on_line: Optional[Callable[[str], None]] = None,
    timeout_s: int = 900,
) -> Tuple[str, List[str]]:
    os.makedirs(root, exist_ok=True)
    out_dir = os.path.join(root, "ssh")
    os.makedirs(out_dir, exist_ok=True)
    fp = os.path.join(out_dir, "ssh_diag.txt")

    def log(msg: str):
        if on_line:
            on_line(msg)

    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.RejectPolicy())
    cli.load_system_host_keys()

    connect_kwargs = {
        "hostname": host,
        "username": user,
        "timeout": 30,
        "banner_timeout": 30,
        "auth_timeout": 30,
        "look_for_keys": bool(key_path),
        "allow_agent": True,
    }
    if key_path:
        connect_kwargs["key_filename"] = key_path
    if password:
        connect_kwargs["password"] = password

    log(f"SSH connecting to {user}@{host} with strict host-key policy (RejectPolicy).")
    cli.connect(**connect_kwargs)
    pin = os.environ.get("KAJOVO_SSH_HOSTKEY_SHA256", "").strip()
    if pin:
        remote = cli.get_transport().get_remote_server_key()
        got = remote.get_fingerprint().hex()
        expected = pin.lower().replace(":", "")
        if got.lower() != expected:
            cli.close()
            raise RuntimeError("SSH host key fingerprint mismatch (explicit pin).")

    commands = ["uname -a", "whoami", "uptime"]
    lines: List[str] = []
    for cmd in commands:
        log(f"SSH exec: {cmd}")
        stdin, stdout, stderr = cli.exec_command(cmd, timeout=min(timeout_s, 120))
        out = stdout.read().decode("utf-8", errors="ignore")
        err = stderr.read().decode("utf-8", errors="ignore")
        lines.append(f"$ {cmd}\n{out}\n{err}\n")
    cli.close()

    with open(fp, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return out_dir, [fp]
