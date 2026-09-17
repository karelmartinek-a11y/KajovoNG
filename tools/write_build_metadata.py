from __future__ import annotations

import hashlib
import importlib.metadata
import json
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONSTRAINTS = ROOT / "requirements" / "constraints.txt"
OUT_DIR = ROOT / "build-metadata"


def command(*args: str) -> str:
    return subprocess.check_output(args, cwd=ROOT, text=True).strip()


def constraints_sha256() -> str:
    return hashlib.sha256(CONSTRAINTS.read_bytes()).hexdigest()


def dependency_manifest() -> list[dict[str, str]]:
    rows = [
        {"name": dist.metadata.get("Name", dist.metadata["Name"]), "version": dist.version}
        for dist in importlib.metadata.distributions()
        if dist.metadata.get("Name")
    ]
    rows.sort(key=lambda row: row["name"].lower().replace("_", "-").replace(".", "-"))
    return rows


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest = dependency_manifest()
    by_name = {row["name"].lower().replace("_", "-"): row["version"] for row in manifest}
    info = {
        "git_sha": command("git", "rev-parse", "HEAD"),
        "python_version": platform.python_version(),
        "platform": platform.system(),
        "architecture": platform.machine(),
        "pyinstaller_version": by_name.get("pyinstaller", "unknown"),
        "constraints_sha256": constraints_sha256(),
        "build_timestamp_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    (OUT_DIR / "build-info.json").write_text(
        json.dumps(info, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (OUT_DIR / "dependency-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    freeze = command(sys.executable, "-m", "pip", "freeze", "--all")
    (OUT_DIR / "pip-freeze.txt").write_text(freeze + "\n", encoding="utf-8")
    print(json.dumps(info, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
