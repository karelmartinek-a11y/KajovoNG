from __future__ import annotations

import os, re, json, hashlib, secrets, string, datetime
import ntpath
import tempfile
from typing import Any, Optional

RUN_ID_RE = re.compile(r"^RUN_\d{12}_\w{4}$")

def now_local() -> datetime.datetime:
    return datetime.datetime.now()

def ts_code(dt: Optional[datetime.datetime]=None) -> str:
    dt = dt or now_local()
    return dt.strftime("%d%m%Y%H%M")

def new_run_id() -> str:
    rnd = "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(4))
    return f"RUN_{ts_code()}_{rnd}"

def sha256_file(path: str, max_bytes: Optional[int]=None) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        if max_bytes is None:
            for chunk in iter(lambda: f.read(1024*1024), b""):
                h.update(chunk)
        else:
            remaining = max_bytes
            while remaining > 0:
                chunk = f.read(min(1024*1024, remaining))
                if not chunk:
                    break
                h.update(chunk)
                remaining -= len(chunk)
    return h.hexdigest()

def safe_json_dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2, default=str)

def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def atomic_write_text(path: str, content: str) -> None:
    directory = os.path.dirname(os.path.abspath(path))
    ensure_dir(directory)
    fd, temporary = tempfile.mkstemp(prefix=".kajovo_", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if os.path.isfile(path):
            import stat
            os.chmod(temporary, stat.S_IMODE(os.stat(path).st_mode))
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)

def is_versing_snapshot_dir(dir_name: str, root_name: str) -> bool:
    if not dir_name.startswith(root_name):
        return False
    tail = dir_name[len(root_name):]
    return bool(re.fullmatch(r"\d{12}(?:\d{2})?", tail))


def validate_relative_path(path: str) -> str:
    if not isinstance(path, str) or not path or ntpath.splitdrive(path)[0]:
        raise ValueError("Cesta musí být neprázdná relativní cesta.")
    path = path.replace("\\", "/")
    reserved = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
    reserved.update(f"{prefix}{i}" for prefix in ("COM", "LPT") for i in range(1, 10))
    reserved.update(f"{prefix}{i}" for prefix in ("COM", "LPT") for i in "¹²³")
    for part in path.split("/"):
        if (not part or part in (".", "..") or part.endswith((" ", "."))
                or any(ord(c) < 32 or c in '<>:"|?*' for c in part)
                or part.split(".")[0].upper() in reserved):
            raise ValueError(f"Neplatná souborová cesta: {path}")
    return path


def safe_join_under_root(root: str, unsafe_rel_path: str) -> str:
    """Join a potentially unsafe relative path under root and block traversal.

    Raises ValueError when the resulting path escapes the provided root.
    """
    rel_path = validate_relative_path(unsafe_rel_path)
    root_abs = os.path.realpath(root)
    candidate = os.path.realpath(os.path.join(root_abs, *rel_path.split("/")))
    if os.path.normcase(os.path.commonpath([root_abs, candidate])) != os.path.normcase(root_abs):
        raise ValueError(f"Path escapes target root: {unsafe_rel_path}")
    return candidate
