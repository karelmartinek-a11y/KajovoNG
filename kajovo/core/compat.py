from __future__ import annotations

import os


ALLOWED_EXTS = {
    ".txt",
    ".md",
    ".json",
    ".csv",
    ".yaml",
    ".yml",
    ".py",
    ".java",
    ".js",
    ".ts",
    ".html",
    ".css",
    ".xml",
    ".sql",
    ".log",
}


def is_compatible_path(path: str) -> bool:
    """Vrací True, pokud je přípona souboru kompatibilní s Files API / Vector Store / Files-search."""
    ext = os.path.splitext(path)[1].lower()
    return ext in ALLOWED_EXTS
