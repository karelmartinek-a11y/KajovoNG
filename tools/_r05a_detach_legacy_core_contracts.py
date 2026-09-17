from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace(path: str, old: str, new: str, *, expected: int = 1) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count == 0 and new in text:
        return
    if count != expected:
        raise RuntimeError(f"{path}: expected {expected} occurrences of {old!r}, found {count}")
    target.write_text(text.replace(old, new), encoding="utf-8")


def main() -> None:
    replace(
        "tests/test_desktop_preparation_recovery.py",
        "from kajovo.desktop.recovery import recover_run",
        "from kajovo.core.recovery import recover_run",
    )
    replace(
        "tests/test_response_journal.py",
        "from kajovo.desktop.recovery import recover_run",
        "from kajovo.core.recovery import recover_run",
    )
    replace(
        "tests/test_output_chunks.py",
        "from kajovo.desktop.batches import import_bundle",
        "from kajovo.core.batch_completion import import_bundle",
    )
    replace(
        "tests/test_studio_contracts.py",
        "from kajovo.desktop.batches import import_bundle",
        "from kajovo.core.batch_completion import import_bundle",
    )
    replace(
        "tests/test_studio_contracts.py",
        "from kajovo.desktop.recovery import recover_run",
        "from kajovo.core.recovery import recover_run",
        expected=2,
    )
    replace(
        "tests/test_desktop.py",
        "from kajovo.desktop.batches import import_bundle",
        "from kajovo.core.batch_completion import import_bundle",
    )

    conftest = ROOT / "tests" / "conftest.py"
    text = conftest.read_text(encoding="utf-8")
    legacy = '        monkeypatch.setattr("kajovo.desktop.settings.persist_api_key", blocked)\n'
    if legacy in text:
        text = text.replace(legacy, "", 1)
    elif "kajovo.desktop.settings.persist_api_key" in text:
        raise RuntimeError("Unexpected legacy secret-store patch shape")
    text = text.replace(
        "# neimportujeme jen kvůli monkeypatchi; v plné desktopové regresi jsou oba\n"
        "    # aliasy dále explicitně blokované.\n",
        "# neimportujeme jen kvůli monkeypatchi; kanonický Studio alias blokujeme\n"
        "    # pouze v prostředí, kde je Qt skutečně dostupné.\n",
    )
    conftest.write_text(text, encoding="utf-8")

    print("R05-A core contract detachment prepared.")


if __name__ == "__main__":
    main()
