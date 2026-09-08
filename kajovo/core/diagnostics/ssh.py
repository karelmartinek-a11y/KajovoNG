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
    pin: Optional[str] = None,
    pin_required: Optional[bool] = None,
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

    pin = os.environ.get("KAJOVO_SSH_HOSTKEY_SHA256", "").strip() if pin is None else pin.strip()
    if pin_required is None:
        pin_required = os.environ.get("KAJOVO_SSH_PIN_REQUIRED", "").strip().lower() in ("1", "true", "yes", "on")
    if pin_required and not pin:
        raise RuntimeError("SSH host key pin is required (set KAJOVO_SSH_HOSTKEY_SHA256).")

    log(f"SSH connecting to {user}@{host} with strict host-key policy (RejectPolicy).")
    try:
        cli.connect(**connect_kwargs)
        if pin:
            import base64
            import hashlib
            remote = cli.get_transport().get_remote_server_key()
            got = base64.b64encode(hashlib.sha256(remote.asbytes()).digest()).decode("ascii").rstrip("=")
            expected = pin.removeprefix("SHA256:").rstrip("=")
            if got != expected:
                raise RuntimeError("Otisk SHA256 SSH hostitele neodpovídá nastavenému pinu.")
        commands = ["uname -a", "whoami", "uptime"]
        lines: List[str] = []
        for cmd in commands:
            log(f"SSH exec: {cmd}")
            stdin, stdout, stderr = cli.exec_command(cmd, timeout=min(timeout_s, 120))
            stdin.close()
            out = stdout.read().decode("utf-8", errors="replace")
            err = stderr.read().decode("utf-8", errors="replace")
            lines.append(f"$ {cmd}\n{out}\n{err}\n")
    finally:
        cli.close()

    with open(fp, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return out_dir, [fp]
