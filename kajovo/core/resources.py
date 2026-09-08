"""Umístění prostředků ve zdrojovém stromu, instalaci a PyInstaller balíčku."""
from pathlib import Path
import sys


def resource_path(name: str) -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "resources" / name
    source = Path(__file__).resolve().parents[2] / "resources" / name
    if source.is_file():
        return source
    return Path(sys.prefix) / "resources" / name
