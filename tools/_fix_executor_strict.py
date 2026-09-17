from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXECUTOR = ROOT / "kajovo" / "core" / "runs" / "executor.py"


def main() -> None:
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
    if count != 12:
        raise RuntimeError(f"Očekáváno 12 transparentních evidence catchů, nalezeno {count}.")

    EXECUTOR.write_text(text, encoding="utf-8")
    print(f"Upraveno {count} evidence catchů bez tichého pass.")


if __name__ == "__main__":
    main()
