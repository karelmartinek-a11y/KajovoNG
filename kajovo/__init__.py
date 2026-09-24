__all__ = []
"""Verze vychází ze zdrojového projektu nebo metadat instalované distribuce."""

from importlib.metadata import version
from pathlib import Path
import tomllib


def _project_version():
    project = Path(__file__).resolve().parent.parent / "pyproject.toml"
    if project.is_file():
        with project.open("rb") as stream:
            metadata = tomllib.load(stream)["project"]
        if metadata["name"] == "kajovong":
            return metadata["version"]
    return version("kajovong")


__version__ = _project_version()
