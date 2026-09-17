from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "kajovo" / "core" / "cascade_pipeline.py"


def main() -> None:
    text = CORE.read_text(encoding="utf-8")
    start_marker = "        # Legacy compatibility path.\n"
    end_marker = "\n    def _process_deterministic_output"
    start = text.find(start_marker)
    end = text.find(end_marker, start)
    if start < 0 or end < 0:
        raise RuntimeError("Legacy _prepare_step block was not found.")

    segment = text[start:end]
    if "legacy_file_ids" not in segment:
        segment, count = re.subn(r"\bfile_ids\b", "legacy_file_ids", segment)
        if count != 6:
            raise RuntimeError(f"Expected 6 legacy file_ids references, found {count}.")
    elif re.search(r"\bfile_ids\b", segment):
        raise RuntimeError("Legacy block contains mixed file_ids naming after typing fix.")

    text = text[:start] + segment + text[end:]
    CORE.write_text(text, encoding="utf-8")
    print("Cascade executor mypy shadowing fix applied.")


if __name__ == "__main__":
    main()
