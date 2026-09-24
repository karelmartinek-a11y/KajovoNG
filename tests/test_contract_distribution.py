"""Distribuční seznamy a runtime vazby fyzických masek."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib
import zipfile

from kajovo.core import resources
from tools.verify_contract_links import schema_inventory


ROOT = Path(__file__).resolve().parents[1]


def test_all_physical_contracts_have_distribution_and_runtime_bindings():
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    files = metadata["tool"]["setuptools"]["data-files"]
    included = {path.resolve() for patterns in files.values() for pattern in patterns for path in ROOT.glob(pattern)}
    actual = set((ROOT / "resources" / "orchestration").rglob("*.schema.json"))
    assert actual and actual <= included
    inventory = schema_inventory()
    assert not inventory["errors"], inventory["errors"]
    for script, separator in (("Build/build_windows.ps1", ";"), ("Build/build_macos.sh", ":")):
        assert f"resources/orchestration{separator}resources/orchestration" in (ROOT / script).read_text(encoding="utf-8")


def test_wheel_contains_loadable_runtime_contracts(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    for name in ("kajovo", "kajovong", "utf8nobom", "resources"):
        shutil.copytree(ROOT / name, source / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for name in ("pyproject.toml", "README.md"):
        shutil.copyfile(ROOT / name, source / name)
    wheel_dir = tmp_path / "wheels"
    wheel_dir.mkdir()
    subprocess.run(
        [sys.executable, "-c", "from setuptools.build_meta import build_wheel; import sys; build_wheel(sys.argv[1])", str(wheel_dir)],
        cwd=source, capture_output=True, check=True, timeout=120,
    )
    installed = tmp_path / "installed"
    with zipfile.ZipFile(next(wheel_dir.glob("*.whl"))) as wheel:
        for name in wheel.namelist():
            marker = ".data/data/"
            if marker not in name:
                continue
            target = installed / name.split(marker, 1)[1]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(wheel.read(name))
    monkeypatch.setattr(resources, "__file__", str(installed / "lib" / "kajovo" / "core" / "resources.py"))
    monkeypatch.setattr(sys, "prefix", str(installed))
    monkeypatch.delattr(sys, "frozen", raising=False)
    for schema in (ROOT / "resources" / "orchestration").rglob("*.schema.json"):
        name = schema.relative_to(ROOT / "resources").as_posix()
        assert json.loads(resources.resource_path(name).read_text(encoding="utf-8")) == json.loads(schema.read_text(encoding="utf-8"))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(installed), raising=False)
    assert resources.resource_path("orchestration/contracts/local/MANUAL_RESOURCE_BINDINGS_V1.schema.json").is_file()
