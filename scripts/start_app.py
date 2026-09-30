"""Ověření provozních závislostí a spuštění z projektového prostředí."""

import argparse
import importlib
from importlib import metadata
from pathlib import Path, PureWindowsPath
import sqlite3
import struct
import subprocess
import sys
import tempfile
import tomllib


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def runtime_sanity() -> None:
    """Prověří importy, prostředky, konfiguraci a skutečný zápis bez sítě a klíčů."""
    for name in (
        "PySide6.QtCore", "PySide6.QtGui", "PySide6.QtWidgets", "openai", "requests",
        "jsonschema", "PIL.Image", "paramiko", "keyring", "kajovo.studio.application",
    ):
        importlib.import_module(name)
    from kajovo.core.config import default_settings_path, load_settings
    from kajovo.core.resources import resource_path

    from PIL import Image
    from jsonschema import Draft202012Validator
    from kajovo.core.orchestration.contracts import parse_json_strict
    from kajovo.core.orchestration.resource_contracts import physical_contract_schemas
    from kajovo.core.orchestration.image_slots import normalization_policy_hash

    normalization_policy_hash()

    for name in ("studio-symbol.png", "Kajovo_new.png"):
        path = resource_path(name)
        try:
            with Image.open(path) as image:
                if image.format != "PNG":
                    raise ValueError("Požadovaný prostředek není PNG.")
                image.verify()
            with Image.open(path) as image:
                image.load()
        except (OSError, ValueError) as exc:
            raise ValueError(f"Neplatný runtime prostředek: {name}.") from exc
    for name in ("montserrat_regular.ttf", "montserrat_bold.ttf"):
        _validate_font(resource_path(name))
    for name, expected in physical_contract_schemas().items():
        path = resource_path(f"orchestration/contracts/{name}.schema.json")
        try:
            actual = parse_json_strict(path.read_text(encoding="utf-8"))
            Draft202012Validator.check_schema(actual)
            # Deklarace dialektu nemění masku runtime, která je již 2020-12.
            if actual.get("$schema", "https://json-schema.org/draft/2020-12/schema") != "https://json-schema.org/draft/2020-12/schema":
                raise ValueError("Nepodporovaný dialekt masky.")
            if {key: value for key, value in actual.items() if key != "$schema"} != {key: value for key, value in expected.items() if key != "$schema"}:
                raise ValueError("Maska neodpovídá runtime kontraktu.")
        except (OSError, ValueError) as exc:
            raise ValueError(f"Neplatný nebo chybějící runtime kontrakt: {name}.") from exc
    settings = load_settings(str(ROOT / default_settings_path()), resolve_secrets=False)
    for field in ("log_dir", "cache_dir", "comic_library_dir"):
        value = getattr(settings, field)
        if not value.strip() or (sys.platform != "win32" and PureWindowsPath(value).drive):
            raise ValueError(f"Nastavení {field} nemá platnou cestu pro tento systém.")
        path = Path(value)
        if field == "comic_library_dir":
            # Stejná interpretace jako ComicStore; LOG a cache vlnovku
            # nerozbalují a musí se kontrolovat jejich skutečná cesta.
            path = path.expanduser()
        if not path.is_absolute():
            path = ROOT / path
        path.mkdir(parents=True, exist_ok=True)
        # Dočasný podadresář se dotýká jen nově vytvořených kontrolních souborů.
        with tempfile.TemporaryDirectory(prefix=".kajovo-check-", dir=path) as probe:
            target = Path(probe) / "write-check"
            target.write_bytes(b"kajovo")
            if target.read_bytes() != b"kajovo":
                raise OSError(f"Nelze ověřit zápis do {field}.")
            target.rename(target.with_suffix(".renamed"))
            with sqlite3.connect(str(Path(probe) / "check.sqlite")) as connection:
                connection.execute("CREATE TABLE sanity (value TEXT)")
                connection.execute("INSERT INTO sanity VALUES ('ok')")
                connection.commit()
                if connection.execute("SELECT value FROM sanity").fetchone() != ("ok",):
                    raise OSError(f"SQLite není použitelné v {field}.")


def _validate_font(path: Path) -> None:
    """Ověří sfnt hlavičku a hranice tabulek bez načítání GUI či dalších balíčků."""
    raw = path.read_bytes()
    if len(raw) < 12 or raw[:4] not in (b"\x00\x01\x00\x00", b"OTTO"):
        raise ValueError(f"Neplatný runtime font: {path.name}.")
    count = struct.unpack_from(">H", raw, 4)[0]
    if not count or len(raw) < 12 + 16 * count:
        raise ValueError(f"Neúplný runtime font: {path.name}.")
    tags = set()
    for index in range(count):
        tag, _checksum, offset, length = struct.unpack_from(">4sIII", raw, 12 + 16 * index)
        if tag in tags or offset < 12 + 16 * count or offset + length > len(raw):
            raise ValueError(f"Neplatná tabulka runtime fontu: {path.name}.")
        tags.add(tag)
    if not {b"cmap", b"head", b"hhea", b"hmtx", b"maxp", b"name"}.issubset(tags):
        raise ValueError(f"Chybí tabulka runtime fontu: {path.name}.")


def run(*arguments: str) -> int:
    return subprocess.run([sys.executable, *arguments], cwd=ROOT, check=False).returncode


def missing_requirements(requirements: list[str]) -> list[str]:
    # Pip poskytuje parser verzí i v čerstvém virtuálním prostředí.
    from pip._vendor.packaging.requirements import Requirement

    missing = []
    for value in requirements:
        requirement = Requirement(value)
        if requirement.marker and not requirement.marker.evaluate():
            continue
        try:
            installed = metadata.version(requirement.name)
        except metadata.PackageNotFoundError:
            missing.append(value)
            continue
        if not requirement.specifier.contains(installed):
            missing.append(value)
    return missing


def prepare() -> int:
    if sys.version_info < (3, 12) or sys.prefix == sys.base_prefix:
        print("Spouštěč vyžaduje projektové .venv s Pythonem 3.12+.", flush=True)
        return 1
    print("Kontroluji provozní závislosti...", flush=True)
    if run("-m", "pip", "--version") != 0:
        if run("-m", "ensurepip", "--upgrade") != 0:
            return 1
    with (ROOT / "pyproject.toml").open("rb") as source:
        requirements = tomllib.load(source)["project"]["dependencies"]
    missing = missing_requirements(requirements)
    consistent = run("-m", "pip", "check") == 0
    if missing or not consistent:
        print("Doplňuji závislosti podle pyproject.toml; může být potřeba internet.", flush=True)
        if run("-m", "pip", "install", *requirements) != 0:
            return 1
        if missing_requirements(requirements) or run("-m", "pip", "check") != 0:
            print("Závislosti nejsou konzistentní. Aplikace se nespustí.", flush=True)
            return 1
    try:
        runtime_sanity()
    except Exception as error:
        # Nastavení může obsahovat citlivé údaje; vypisujeme jen typ závady.
        print(f"Runtime kontrola selhala ({type(error).__name__}). Prověřte konfiguraci, "
              "datové adresáře a instalované prostředky.", flush=True)
        return 1
    print("Prostředí je připraveno.", flush=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    result = prepare()
    if result or args.check_only:
        return result
    return run("-m", "kajovo.app.main")


if __name__ == "__main__":
    raise SystemExit(main())
