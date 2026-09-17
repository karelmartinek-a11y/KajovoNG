from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from collections.abc import Callable

from ..utils import ensure_dir

LOGGER = logging.getLogger(__name__)


def collect_windows_diagnostics(
    output_base_dir: str,
    on_line: Callable[[str], None] | None = None,
) -> tuple[str, list[str]]:
    ensure_dir(output_base_dir)
    script = os.path.join(os.path.dirname(__file__), "windows_collect.ps1")
    ts = time.strftime("%Y%m%d-%H%M%S")
    out_dir = os.path.join(output_base_dir, f"Diag_{ts}")
    ensure_dir(out_dir)
    cmd = [
        "powershell",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        script,
        "-OutDir",
        out_dir,
    ]
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    stdout_lines: list[str] = []
    stderr_lines: list[str] = []

    def _reader(stream, sink: list[str], prefix: str) -> None:
        if not stream:
            return
        for line in iter(stream.readline, ""):
            sink.append(line)
            if on_line:
                try:
                    on_line(f"{prefix}{line.rstrip()}")
                except Exception as exc:
                    LOGGER.warning(
                        "Callback diagnostického logu selhal (%s): %s",
                        type(exc).__name__,
                        exc,
                    )

    stdout_thread = threading.Thread(
        target=_reader,
        args=(process.stdout, stdout_lines, "WIN: "),
        daemon=True,
    )
    stderr_thread = threading.Thread(
        target=_reader,
        args=(process.stderr, stderr_lines, "WIN ERR: "),
        daemon=True,
    )
    stdout_thread.start()
    stderr_thread.start()
    try:
        process.wait(timeout=120)
    except subprocess.TimeoutExpired as exc:
        process.kill()
        process.wait()
        raise RuntimeError("Windows diagnostika překročila limit 120 sekund.") from exc
    stdout_thread.join(timeout=1.0)
    stderr_thread.join(timeout=1.0)
    process_stdout = "".join(stdout_lines)
    process_stderr = "".join(stderr_lines)
    log_txt = os.path.join(out_dir, "_collector_stdout_stderr.txt")
    with open(log_txt, "w", encoding="utf-8") as stream:
        stream.write(
            "STDOUT:\n"
            + (process_stdout or "")
            + "\n\nSTDERR:\n"
            + (process_stderr or "")
        )
    if process.returncode != 0:
        raise RuntimeError(
            f"Windows diagnostika selhala ({process.returncode}), log: {log_txt}"
        )
    files: list[str] = []
    for root, _, names in os.walk(out_dir):
        for name in names:
            files.append(os.path.join(root, name))
    return out_dir, files
