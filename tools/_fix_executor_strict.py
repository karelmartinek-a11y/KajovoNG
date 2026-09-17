from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXECUTOR = ROOT / "kajovo" / "core" / "runs" / "executor.py"
PROCESS_AUDIT = ROOT / "tests" / "test_process_audit_regressions.py"
RUN_STUDIO = ROOT / "tests" / "test_run_studio.py"

TARGET_MARKERS = (
    'self.log.exception("run", e)',
    'self.log.event("diagnostics.windows.collected"',
    'self.log.exception("diagnostics.windows.failed", e)',
    'self.log.event("diagnostics.ssh.collected"',
    'self.log.exception("diagnostics.ssh.failed", e)',
    'self.log.event("upload.in_dir"',
    'self.log.event("vector_store.in_dir"',
    'self.log.event("versing.snapshot.created"',
    'self.log.event("upload.mirror"',
    'self.log.event("contract.mismatch"',
)


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


def replace_executor_exact(text: str, old: str, new: str, expected: int = 1) -> str:
    old_count = text.count(old)
    new_count = text.count(new)
    if old_count == expected:
        return text.replace(old, new, expected)
    if old_count == 0 and new_count == expected:
        return text
    raise RuntimeError(
        f"Neočekávaná kardinalita executor migrace: old={old_count}, new={new_count}, expected={expected}: {old!r}"
    )


def fix_executor_types(text: str) -> str:
    text = replace_executor_exact(
        text,
        "        self._response_file_ids = {}\n",
        "        self._response_file_ids: dict[str, str] = {}\n"
        "        self.resume_generate_batch: dict[str, Any] | None = None\n"
        "        self._delivery_snapshot: dict[str, Any] = {}\n",
    )
    text = replace_executor_exact(
        text,
        "            for model in self.cfg.caps_by_model:\n",
        "            for model in (self.cfg.caps_by_model or {}):\n",
    )
    text = replace_executor_exact(
        text,
        "        plan = {}\n        a3_model = self._generate_model(\"A3\")\n",
        "        plan: dict[str, Any] = {}\n        struct: dict[str, Any]\n"
        "        a3_model = self._generate_model(\"A3\")\n",
    )
    text = replace_executor_exact(
        text,
        "        for idx, f in enumerate(files, start=1):\n"
        "            self._check_stop()\n"
        "            path = f.get(\"path\")\n"
        "            self._progress_stage = \"A3\"\n",
        "        for idx, f in enumerate(files, start=1):\n"
        "            self._check_stop()\n"
        "            path = f.get(\"path\")\n"
        "            if not isinstance(path, str) or not path:\n"
        "                continue\n"
        "            self._progress_stage = \"A3\"\n",
    )
    text = replace_executor_exact(
        text,
        "        context_path = self.log.find_json(\"manifests\", \"response_modify_context\") if self._response_journal else None\n"
        "        if context_path:\n",
        "        context_path = self.log.find_json(\"manifests\", \"response_modify_context\") if self._response_journal else None\n"
        "        tools: list[dict[str, Any]] | None\n"
        "        supports_fs: bool\n"
        "        vs_id: str | None\n"
        "        if context_path:\n",
    )
    text = replace_executor_exact(
        text,
        "            tools: list[dict[str, Any]] | None = None\n"
        "            vs_id: str | None = None\n",
        "            tools = None\n"
        "            vs_id = None\n",
    )
    text = replace_executor_exact(
        text,
        "        step = next((row for row in reversed(self.log.bundle.steps()) if row.get(\"stage\") == \"QFILE\"), {})\n",
        "        step: dict[str, Any] = next(\n"
        "            (row for row in reversed(self.log.bundle.steps()) if row.get(\"stage\") == \"QFILE\"), {}\n"
        "        )\n",
    )
    text = replace_executor_exact(
        text,
        "        gen_ref_files, gen_input_files, gen_input_images = [], [], []\n",
        "        gen_ref_files: list[str] = []\n"
        "        gen_input_files: list[str] = []\n"
        "        gen_input_images: list[str] = []\n",
    )
    text = replace_executor_exact(
        text,
        "            vs_ids = []\n",
        "            vs_ids: list[str] = []\n",
    )
    return text


def is_strict_ruff_target(body: str) -> bool:
    if any(marker in body for marker in TARGET_MARKERS):
        return True
    return 'self.log.save_json(' in body and 'resume_structure_' in body


def fix_executor() -> None:
    text = EXECUTOR.read_text(encoding="utf-8")
    text = fix_executor_types(text)
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
    replaced = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal replaced
        body = match.group("body")
        if not is_strict_ruff_target(body):
            return match.group(0)
        replaced += 1
        indent = match.group("indent")
        return (
            f"{indent}try:\n"
            f"{body}"
            f"{indent}except Exception as evidence_error:\n"
            f"{indent}    logging.getLogger(__name__).warning(\n"
            f"{indent}        \"Zápis pomocné evidence selhal: %s\", evidence_error\n"
            f"{indent}    )\n"
        )

    text = pattern.sub(replace, text)
    remaining_targets = sum(
        1 for match in pattern.finditer(text) if is_strict_ruff_target(match.group("body"))
    )
    if replaced not in {0, 12} or remaining_targets != 0:
        raise RuntimeError(
            "Evidence migrace neodpovídá explicitnímu strict-Ruff allow-listu: "
            f"replaced={replaced}, remaining={remaining_targets}."
        )

    EXECUTOR.write_text(text, encoding="utf-8")
    print(f"Upraveno {replaced} explicitních strict-Ruff evidence catchů a typový kontrakt executorů.")


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
