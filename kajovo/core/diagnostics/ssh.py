from __future__ import annotations

import os
import subprocess
from typing import Callable, List, Optional, Tuple

import paramiko


def execute_ssh_repair(script: bytes, cfg, timeout_s: int = 120) -> subprocess.CompletedProcess:
    """Spustí schválený skript přes stdin vzdáleného shellu s ověřením hostitele."""
    import base64
    import hashlib

    if not cfg.ssh_host or not cfg.ssh_user:
        raise ValueError("SSH oprava vyžaduje hostitele a uživatele.")
    pin = (cfg.ssh_pin or "").strip()
    if cfg.ssh_pin_required and not pin:
        raise ValueError("SSH oprava vyžaduje nastavený otisk hostitele.")
    client = paramiko.SSHClient()
    try:
        client.load_system_host_keys()
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
        kwargs = {"hostname": cfg.ssh_host, "username": cfg.ssh_user,
                  "timeout": 30, "banner_timeout": 30, "auth_timeout": 30,
                  "allow_agent": True, "look_for_keys": bool(cfg.ssh_key)}
        if cfg.ssh_key:
            kwargs["key_filename"] = cfg.ssh_key
        if cfg.ssh_password:
            kwargs["password"] = cfg.ssh_password
        client.connect(**kwargs)
        if pin:
            remote = client.get_transport().get_remote_server_key()
            actual = base64.b64encode(hashlib.sha256(remote.asbytes()).digest()).decode("ascii").rstrip("=")
            if actual != pin.removeprefix("SHA256:").rstrip("="):
                raise RuntimeError("Otisk SHA256 SSH hostitele neodpovídá nastavenému pinu.")
        stdin, stdout, stderr = client.exec_command("sh -s 2>&1", timeout=timeout_s)
        stdin.write(script)
        stdin.flush()
        stdin.channel.shutdown_write()
        output = stdout.read().decode("utf-8", errors="replace")
        return subprocess.CompletedProcess("sh -s", stdout.channel.recv_exit_status(), output, "")
    finally:
        client.close()


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
