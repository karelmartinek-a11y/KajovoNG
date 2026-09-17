from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXECUTOR = ROOT / "kajovo" / "core" / "runs" / "executor.py"
PROCESS_AUDIT = ROOT / "tests" / "test_process_audit_regressions.py"
RUN_STUDIO = ROOT / "tests" / "test_run_studio.py"


def replace_exact(path: Path, old: str, new: str, expected: int) -> None:
    text = path.read_text(encoding="utf-8")
    old_count = text.count(old)
    new_count = text.count(new)
    if old_count == expected:
        path.write_text(text.replace(old, new), encoding="utf-8")
        return
    if old_count == 0 and new_count == expected:
        return
    raise RuntimeError(
        f"Neočekávaná kardinalita migrace v {path}: old={old_count}, new={new_count}, expected={expected}."
    )


def fix_executor() -> None:
    text = EXECUTOR.read_text(encoding="utf-8")
    if "import logging\n" not in text:
        text = text.replace("import json\n", "import json\nimport logging\n", 1)

    text = text.replace(
        "[NOVÁ VĚTEV – explicitní pokyn platí pouze pro nově prováděnou část]",
        "[NOVÁ VĚTEV - explicitní pokyn platí pouze pro nově prováděnou část]",
        1,
    )

    pattern = re.compile(
        r"(?m)^(?P<indent>[ ]*)try:\n"
        r"(?P<body>(?:(?P=indent)    .*\n)+?)"
        r"(?P=indent)except Exception:\n"
        r"(?P=indent)    pass\n"
    )

    def replace(match: re.Match[str]) -> str:
        indent = match.group("indent")
        body = match.group("body")
        return (
            f"{indent}try:\n"
            f"{body}"
            f"{indent}except Exception as evidence_error:\n"
            f"{indent}    logging.getLogger(__name__).warning(\n"
            f"{indent}        \"Zápis pomocné evidence selhal: %s\", evidence_error\n"
            f"{indent}    )\n"
        )

    text, count = pattern.subn(replace, text)
    if count not in {0, 12}:
        raise RuntimeError(
            f"Po safe Ruff autofixu je očekáváno 12 transparentních evidence catchů, nalezeno {count}."
        )
    if count == 0 and "except Exception:\n" in text and "    pass\n" in text:
        raise RuntimeError("Evidence migrace je nejednoznačná: zůstaly kandidátní try/pass bloky.")

    EXECUTOR.write_text(text, encoding="utf-8")
    print(f"Upraveno {count} evidence catchů bez tichého pass.")


def fix_migrated_tests() -> None:
    replace_exact(
        PROCESS_AUDIT,
        'Path("kajovo/core/pipeline.py").read_text(encoding="utf-8")',
        'Path("kajovo/core/runs/executor.py").read_text(encoding="utf-8")',
        2,
    )
    replace_exact(
        RUN_STUDIO,
        "worker._create_response = Mock(",
        "worker._executor._create_response = Mock(",
        2,
    )
    replace_exact(
        RUN_STUDIO,
        "worker._run_qa(Mock(), [], None)",
        "worker._executor._run_qa(Mock(), [], None)",
        2,
    )


def main() -> None:
    fix_executor()
    fix_migrated_tests()


if __name__ == "__main__":
    main()
