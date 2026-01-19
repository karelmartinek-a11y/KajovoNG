from __future__ import annotations

import os, re, json, time, hashlib, random, string, datetime
from typing import Any, Optional

RUN_ID_RE = re.compile(r"^RUN_\d{12}_\w{4}$")

def now_local() -> datetime.datetime:
    return datetime.datetime.now()

def ts_code(dt: Optional[datetime.datetime]=None) -> str:
    dt = dt or now_local()
    return dt.strftime("%d%m%Y%H%M")

def new_run_id() -> str:
    rnd = "".join(random.choice(string.ascii_uppercase + string.digits) for _ in range(4))
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

def is_versing_snapshot_dir(dir_name: str, root_name: str) -> bool:
    if not dir_name.startswith(root_name):
        return False
    tail = dir_name[len(root_name):]
    return bool(re.fullmatch(r"\d{12}", tail))
