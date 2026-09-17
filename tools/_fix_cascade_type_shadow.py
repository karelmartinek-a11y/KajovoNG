from __future__ import annotations

from pathlib import Path

PATH = Path(__file__).resolve().parents[1] / "kajovo" / "core" / "cascade_pipeline.py"


def replace_exact(text: str, old: str, new: str, expected: int = 1) -> str:
    old_count = text.count(old)
    new_count = text.count(new)
    if old_count == expected:
        return text.replace(old, new, expected)
    if old_count == 0 and new_count == expected:
        return text
    raise RuntimeError(
        f"Neočekávaná kardinalita cascade type migrace: old={old_count}, new={new_count}, expected={expected}."
    )


def main() -> None:
    text = PATH.read_text(encoding="utf-8")
    text = replace_exact(text, "        file_ids: List[str] = []\n", "        legacy_file_ids: List[str] = []\n")
    text = replace_exact(text, "                file_ids.append(resolved)\n", "                legacy_file_ids.append(resolved)\n")
    text = replace_exact(text, "        for file_id in file_ids:\n            if file_id and file_id not in preflight_existing:\n", "        for file_id in legacy_file_ids:\n            if file_id and file_id not in preflight_existing:\n")
    text = replace_exact(text, "            file_ids.append(file_id)\n\n        existing = self._extract_input_file_ids(content_parts)\n        for file_id in file_ids:\n", "            legacy_file_ids.append(file_id)\n\n        existing = self._extract_input_file_ids(content_parts)\n        for file_id in legacy_file_ids:\n")
    text = replace_exact(text, "        return payload, schema or {}, file_ids\n\n    def _process_deterministic_output", "        return payload, schema or {}, legacy_file_ids\n\n    def _process_deterministic_output")
    PATH.write_text(text, encoding="utf-8")
    print("Cascade legacy file-id list now has an explicit non-shadowing type identity.")


if __name__ == "__main__":
    main()
