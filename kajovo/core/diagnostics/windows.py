from __future__ import annotations

import os, subprocess, time, threading
from typing import List, Tuple
from ..utils import ensure_dir

def collect_windows_diagnostics(output_base_dir: str, on_line=None) -> Tuple[str, List[str]]:
    ensure_dir(output_base_dir)
    script = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "diagnostics", "windows_collect.ps1"))
    ts = time.strftime("%Y%m%d-%H%M%S")
    out_dir = os.path.join(output_base_dir, f"Diag_{ts}")
    ensure_dir(out_dir)
    cmd = ["powershell", "-ExecutionPolicy", "Bypass", "-File", script, "-OutDir", out_dir]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    stdout_lines: List[str] = []
    stderr_lines: List[str] = []
    def _reader(stream, sink, prefix):
        if not stream:
            return
        for line in iter(stream.readline, ""):
            sink.append(line)
            if on_line:
                try:
                    on_line(f"{prefix}{line.rstrip()}")
                except Exception:
                    pass
    t_out = threading.Thread(target=_reader, args=(p.stdout, stdout_lines, "WIN: "), daemon=True)
    t_err = threading.Thread(target=_reader, args=(p.stderr, stderr_lines, "WIN ERR: "), daemon=True)
    t_out.start()
    t_err.start()
    p.wait()
    t_out.join(timeout=1.0)
    t_err.join(timeout=1.0)
    p_stdout = "".join(stdout_lines)
    p_stderr = "".join(stderr_lines)
    log_txt = os.path.join(out_dir, "_collector_stdout_stderr.txt")
    with open(log_txt, "w", encoding="utf-8") as f:
        f.write("STDOUT:\n" + (p_stdout or "") + "\n\nSTDERR:\n" + (p_stderr or ""))
    files: List[str] = []
    for root, _, fnames in os.walk(out_dir):
        for fn in fnames:
            files.append(os.path.join(root, fn))
    return out_dir, files
