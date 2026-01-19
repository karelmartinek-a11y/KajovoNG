from __future__ import annotations

import os, fnmatch, re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from .utils import sha256_file, is_versing_snapshot_dir

SENSITIVE_NAMES = {".env",".env.local",".env.prod",".pypirc","id_rsa","id_ed25519"}
SECRET_PATTERNS = [
    re.compile(r"OPENAI[_-]?API[_-]?KEY\s*[:=]\s*['\"]?[A-Za-z0-9-_]{10,}"),
    re.compile(r"(?i)\b(secret|token|password|api[_-]?key)\b\s*[:=]"),
    re.compile(r"-----BEGIN (RSA|OPENSSH|EC) PRIVATE KEY-----"),
]

@dataclass
class ScanItem:
    rel_path: str
    abs_path: str
    size: int
    sha256: Optional[str]
    uploadable: bool
    reason: str
    sensitive: bool

def is_probably_binary(path: str) -> bool:
    try:
        with open(path, "rb") as f:
            data = f.read(4096)
        if b"\x00" in data:
            return True
        text = sum((32 <= b <= 126) or b in (9,10,13) for b in data)
        return (len(data) > 0 and (text / len(data)) < 0.75)
    except Exception:
        return True

def match_any_glob(rel_path: str, patterns: Optional[List[str]]) -> bool:
    if not patterns:
        return False
    p = rel_path.replace("\\","/")
    for pat in patterns:
        if fnmatch.fnmatch(p, pat):
            return True
    return False

def ext_of(path: str) -> str:
    _, ext = os.path.splitext(path)
    return ext.lower()

def scan_tree(root_dir: str, root_name: str, deny_dirs: List[str], deny_exts: Optional[List[str]],
              allow_exts: Optional[List[str]], deny_globs: Optional[List[str]], allow_globs: Optional[List[str]],
              max_size_bytes: int = 10*1024*1024) -> List[ScanItem]:
    items: List[ScanItem] = []
    for cur, dirs, files in os.walk(root_dir):
        kept = []
        for d in dirs:
            if d in deny_dirs:
                continue
            if is_versing_snapshot_dir(d, root_name):
                continue
            kept.append(d)
        dirs[:] = kept

        for fn in files:
            abs_path = os.path.join(cur, fn)
            rel_path = os.path.relpath(abs_path, root_dir).replace("\\","/")
            try:
                size = os.path.getsize(abs_path)
            except Exception:
                items.append(ScanItem(rel_path, abs_path, 0, None, False, "stat_failed", True))
                continue

            if allow_globs and not match_any_glob(rel_path, allow_globs):
                items.append(ScanItem(rel_path, abs_path, size, None, False, "not_in_allow_globs", False))
                continue
            if deny_globs and match_any_glob(rel_path, deny_globs):
                items.append(ScanItem(rel_path, abs_path, size, None, False, "deny_glob", False))
                continue

            ext = ext_of(rel_path)
            if allow_exts and ext not in [e.lower() for e in allow_exts]:
                items.append(ScanItem(rel_path, abs_path, size, None, False, "ext_not_allowed", False))
                continue
            if deny_exts and ext in [e.lower() for e in deny_exts]:
                items.append(ScanItem(rel_path, abs_path, size, None, False, "denied_extension", False))
                continue

            if size == 0:
                items.append(ScanItem(rel_path, abs_path, size, None, False, "empty_file", False))
                continue

            sensitive = (fn.lower() in SENSITIVE_NAMES) or rel_path.lower().endswith(".env")
            if size > max_size_bytes:
                items.append(ScanItem(rel_path, abs_path, size, None, False, "too_large", sensitive))
                continue
            if is_probably_binary(abs_path):
                items.append(ScanItem(rel_path, abs_path, size, None, False, "binary", sensitive))
                continue

            secret_hit = False
            try:
                with open(abs_path, "r", encoding="utf-8", errors="ignore") as f:
                    head = f.read(20000)
                for rx in SECRET_PATTERNS:
                    if rx.search(head):
                        secret_hit = True
                        break
            except Exception:
                secret_hit = True

            if sensitive or secret_hit:
                items.append(ScanItem(rel_path, abs_path, size, None, False, "sensitive_or_secret_detected", True))
                continue

            sha = None
            try:
                sha = sha256_file(abs_path, max_bytes=5*1024*1024)
            except Exception:
                sha = None

            items.append(ScanItem(rel_path, abs_path, size, sha, True, "ok", False))

    items.sort(key=lambda x: x.rel_path)
    return items

def build_manifest(root_dir: str, items: List[ScanItem], extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    m: Dict[str, Any] = {
        "root": os.path.abspath(root_dir),
        "generated_at": __import__("time").time(),
        "files": [
            {
                "path": it.rel_path,
                "size": it.size,
                "sha256": it.sha256,
                "uploadable": it.uploadable,
                "reason": it.reason,
                "sensitive": it.sensitive,
            } for it in items
        ],
    }
    if extra:
        m["extra"] = extra
    return m
