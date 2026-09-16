"""Jednorázový deterministický hardening release workflow o provenance attestation."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"
SELF = ROOT / "scripts" / "_refactor_release_attestation.py"
RUNNER = ROOT / ".github" / "workflows" / "refactor-release-attestation.yml"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"Release refaktor odmítnut: {label}, nalezeno {count} shod.")
    return text.replace(old, new, 1)


def main() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "    permissions:\n      actions: read\n      contents: write\n",
        "    permissions:\n      actions: read\n      contents: write\n      id-token: write\n      attestations: write\n",
        "publish permissions",
    )
    text = replace_once(
        text,
        "      - name: Create or safely update GitHub Release\n",
        "      - name: Attest release archive provenance\n"
        "        uses: actions/attest@v4\n"
        "        with:\n"
        "          subject-path: 'release-assets/*.zip'\n"
        "      - name: Create or safely update GitHub Release\n",
        "attestation insertion",
    )
    WORKFLOW.write_text(text, encoding="utf-8", newline="\n")
    SELF.unlink()
    RUNNER.unlink()


if __name__ == "__main__":
    main()
