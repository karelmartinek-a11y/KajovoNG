from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXECUTOR = ROOT / "kajovo/core/runs/executor.py"

IO_METHODS = {
    "_attachments_snapshot",
    "_input_file_ids",
    "_generate_model",
    "_model_caps",
    "_preparation_cap",
    "_remember_file_name",
    "_classify_input_kind",
    "_log_request_attachments",
    "_prepare_response_runtime",
    "_input_parts",
    "_payload_base",
    "_build_diag_text",
    "_write_diagnostics_json",
    "_maybe_collect_diagnostics",
    "_zip_in_dir",
    "_prepare_in_dir_upload",
    "_files_with_in_dir",
    "_is_supported_input_file",
    "_input_files_with_in_dir",
    "_build_input_attachments",
    "_io_reference_note",
    "_append_io_reference",
    "_append_io_reference_instructions",
    "_should_inline_diag_text",
    "_attach_diagnostics_vector_store",
    "_with_diag_text",
    "_wait_vector_store_files",
    "_in_dir_fallback_note",
    "_ingest_prompt_if_needed",
}
DELIVERY_METHODS = {
    "_verify_completed_files",
    "_create_snapshot",
    "_save_out_files",
    "_finish_file_delivery",
    "_write_missing_files_report",
}
MODE_METHODS = {
    "_run_a_generate",
    "_submit_generate_batch",
    "_run_b_modify",
    "_run_qa",
    "_run_qfile",
    "_gen_file_chunks",
}


def replace_required(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if old not in text:
        raise RuntimeError(f"Expected text not found in {path.relative_to(ROOT)}: {old[:80]!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def _source_block(lines: list[str], node: ast.AST) -> str:
    start = min([d.lineno for d in getattr(node, "decorator_list", [])] or [node.lineno])
    end = node.end_lineno or node.lineno
    return "".join(lines[start - 1 : end]).rstrip() + "\n"


def _module_imports(source: str, tree: ast.Module) -> str:
    lines = source.splitlines(keepends=True)
    blocks: list[str] = []
    for node in tree.body:
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        if isinstance(node, ast.ImportFrom) and node.module == "__future__":
            continue
        blocks.append(_source_block(lines, node).rstrip())
    return "\n".join(blocks)


def _silent_exception_hardening(source: str) -> str:
    pattern = re.compile(r"(?m)^([ \t]*)except Exception:\n\1    pass(?=\n|$)")

    def repl(match: re.Match[str]) -> str:
        indent = match.group(1)
        return (
            f"{indent}except Exception as suppressed_error:\n"
            f"{indent}    logging.getLogger(__name__).warning(\n"
            f"{indent}        \"Pomocná operace selhala a byla degradována: %s\",\n"
            f"{indent}        type(suppressed_error).__name__,\n"
            f"{indent}    )"
        )

    hardened, count = pattern.subn(repl, source)
    if count < 5:
        raise RuntimeError(f"Expected several silent exception handlers in executor, found only {count}")
    return hardened


def split_executor() -> None:
    source = _silent_exception_hardening(EXECUTOR.read_text(encoding="utf-8"))
    tree = ast.parse(source, filename=str(EXECUTOR))
    lines = source.splitlines(keepends=True)
    split_node = next(
        node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "split_text"
    )
    cls = next(
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "RunExecutor"
    )
    methods = {
        node.name: node
        for node in cls.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    expected = IO_METHODS | DELIVERY_METHODS | MODE_METHODS
    missing = expected - methods.keys()
    if missing:
        raise RuntimeError(f"RunExecutor changed unexpectedly; missing methods: {sorted(missing)}")

    imports = _module_imports(source, tree)
    base = ROOT / "kajovo/core/runs/runtime_base.py"
    base.write_text(
        '"""Společný typový základ runtime mixinů orchestrace."""\n\n'
        "from __future__ import annotations\n\n"
        "from typing import Any\n\n\n"
        "class RuntimeMixinBase:\n"
        "    \"\"\"MRO základ: konkrétní stav a porty vlastní RunExecutor.\"\"\"\n\n"
        "    def __getattr__(self, name: str) -> Any:\n"
        "        raise AttributeError(name)\n",
        encoding="utf-8",
    )

    text_path = ROOT / "kajovo/core/runs/text.py"
    text_path.write_text(
        '"""Čisté textové utility run orchestrace."""\n\n'
        "from __future__ import annotations\n\n\n"
        + _source_block(lines, split_node),
        encoding="utf-8",
    )

    def write_mixin(filename: str, class_name: str, names: set[str], doc: str) -> None:
        body = "\n\n".join(_source_block(lines, methods[name]).rstrip() for name in sorted(names, key=lambda n: methods[n].lineno))
        path = ROOT / "kajovo/core/runs" / filename
        path.write_text(
            f'"""{doc}"""\n\n'
            "from __future__ import annotations\n\n"
            f"{imports}\n"
            "from .runtime_base import RuntimeMixinBase\n"
            "from .text import split_text\n\n\n"
            f"class {class_name}(RuntimeMixinBase):\n"
            f"{body}\n",
            encoding="utf-8",
        )

    write_mixin(
        "runtime_io.py",
        "RuntimeIOMixin",
        IO_METHODS,
        "Přílohy, diagnostika, vstupní balíčky a příprava runtime kontextu.",
    )
    write_mixin(
        "runtime_delivery.py",
        "RuntimeDeliveryMixin",
        DELIVERY_METHODS,
        "Durable ukládání a důkazy výstupní delivery.",
    )
    write_mixin(
        "runtime_modes.py",
        "RuntimeModesMixin",
        MODE_METHODS,
        "Implementace GENERATE, MODIFY, QA, QFILE a dávkové souborové delivery.",
    )

    nodes_to_remove = [split_node, *(methods[name] for name in expected)]
    spans: list[tuple[int, int]] = []
    for node in nodes_to_remove:
        start = min([d.lineno for d in getattr(node, "decorator_list", [])] or [node.lineno])
        spans.append((start, node.end_lineno or node.lineno))
    remaining = lines[:]
    for start, end in sorted(spans, reverse=True):
        del remaining[start - 1 : end]
    compact = "".join(remaining)
    if "class RunExecutor:" not in compact:
        raise RuntimeError("RunExecutor declaration changed unexpectedly")
    compact = compact.replace(
        "class RunExecutor:",
        "from .runtime_delivery import RuntimeDeliveryMixin\n"
        "from .runtime_io import RuntimeIOMixin\n"
        "from .runtime_modes import RuntimeModesMixin\n"
        "from .text import split_text\n\n\n"
        "class RunExecutor(RuntimeIOMixin, RuntimeDeliveryMixin, RuntimeModesMixin):",
        1,
    )
    EXECUTOR.write_text(compact, encoding="utf-8")

    pipeline = ROOT / "kajovo/core/pipeline.py"
    text = pipeline.read_text(encoding="utf-8")
    old = "from .runs.executor import RunExecutor, split_text"
    if old not in text:
        raise RuntimeError("Pipeline facade import changed unexpectedly")
    pipeline.write_text(
        text.replace(old, "from .runs.executor import RunExecutor\nfrom .runs.text import split_text", 1),
        encoding="utf-8",
    )


def write_hardening_contracts() -> None:
    path = ROOT / "tests/test_hardening_contracts.py"
    path.write_text(
        '''from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "kajovo" / "core" / "runs"


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module)
    return out


def test_run_executor_is_small_orchestrator():
    executor = RUNTIME / "executor.py"
    assert len(executor.read_text(encoding="utf-8").splitlines()) < 650
    source = executor.read_text(encoding="utf-8")
    for class_name in ("RuntimeIOMixin", "RuntimeDeliveryMixin", "RuntimeModesMixin"):
        assert class_name in source


def test_runtime_decomposition_is_qt_free():
    for name in ("executor.py", "runtime_io.py", "runtime_delivery.py", "runtime_modes.py", "runtime_base.py", "text.py"):
        path = RUNTIME / name
        assert path.is_file(), name
        imports = _imports(path)
        assert not any(value == "PySide6" or value.startswith("PySide6.") for value in imports), name


def test_runtime_has_no_silent_broad_exception_pass():
    failures = []
    for name in ("executor.py", "runtime_io.py", "runtime_delivery.py", "runtime_modes.py"):
        path = RUNTIME / name
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler) and len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
                caught = node.type
                if isinstance(caught, ast.Name) and caught.id in {"Exception", "BaseException"}:
                    failures.append(f"{name}:{node.lineno}")
        assert not failures, failures


def test_credential_persistence_uses_keyring_not_registry_write():
    source = (ROOT / "kajovo/core/secret_store.py").read_text(encoding="utf-8")
    assert "keyring.set_password" in source
    assert "SetValueEx" not in source
    assert "_delete_persisted_api_key" in source  # legacy záznam se pouze migruje/odstraňuje


def test_ci_covers_supported_pythons_static_types_and_coverage():
    source = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "python312-contract" in source
    assert "python313-full" in source
    assert "python-version: '3.12'" in source
    assert "python-version: '3.13'" in source
    assert "python -m mypy" in source
    assert "--cov-fail-under=70" in source


def test_legacy_ui_and_tracked_runtime_examples_are_absent():
    assert not (ROOT / "kajovo/desktop").exists()
    assert not (ROOT / "aa.txt").exists()
''',
        encoding="utf-8",
    )


def update_docs() -> None:
    agents = ROOT / "AGENTS.md"
    text = agents.read_text(encoding="utf-8")
    text = text.replace(
        "- `kajovo/desktop`: desktopové rozhraní PySide6.\n",
        "- `kajovo/studio`: jediné produkční desktopové rozhraní PySide6.\n",
    )
    text = text.replace(
        "- `tests`: automatické regresní a desktopové testy.\n",
        "- `tests`: automatické regresní, doménové a Studio UI testy.\n",
    )
    agents.write_text(text, encoding="utf-8")

    hardening = ROOT / "docs/refactor/ARCHITECTURE_HARDENING.md"
    text = hardening.read_text(encoding="utf-8")
    text = text.replace(
        "- `kajovo.desktop` zůstává pouze zdrojový regresní referenční materiál. Není součástí instalovaného ani distribuovaného balíku a produkční `app`/`studio` jej nesmí importovat.\n",
        "- Legacy balík `kajovo.desktop` je fyzicky odstraněn; jedinou produkční UI implementací i regresním referenčním povrchem je `kajovo.studio`.\n",
    )
    marker = "  - `delivery.py` – durable ukládání výstupů, hash guardy a delivery evidence;\n"
    addition = (
        marker
        + "  - `runtime_io.py` – přílohy, diagnostika, IN balíčky a runtime kontext;\n"
        + "  - `runtime_delivery.py` – orchestrace durable delivery;\n"
        + "  - `runtime_modes.py` – GENERATE/MODIFY/QA/QFILE režimy;\n"
        + "  - `executor.py` – pouze životní cyklus běhu, řízení portů a terminální stav;\n"
    )
    if marker in text and "`runtime_io.py`" not in text:
        text = text.replace(marker, addition, 1)
    hardening.write_text(text, encoding="utf-8")


def main() -> None:
    split_executor()
    write_hardening_contracts()
    update_docs()
    print("Final nine-point code hardening prepared")


if __name__ == "__main__":
    main()
