"""Jednorázová deterministická migrace odstraněného placeného preflight workflow.

Skript je určen jen pro tento migrační commit a po úspěšné aplikaci se odstraní.
Neprovádí síťové požadavky ani nemění pracovní kontrakty mimo uvedené symboly.
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def remove_function(path: Path, name: str) -> bool:
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    matches = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
    ]
    if not matches:
        return False
    if len(matches) != 1:
        raise RuntimeError(f"{path}: očekávána právě jedna funkce {name}, nalezeno {len(matches)}")
    node = matches[0]
    lines = text.splitlines(keepends=True)
    start = node.lineno - 1
    if node.decorator_list:
        start = min(dec.lineno for dec in node.decorator_list) - 1
    end = node.end_lineno
    # Pohltit nejvýše jeden následující prázdný řádek, aby po smazání nezůstávaly bloky mezer.
    if end < len(lines) and not lines[end].strip():
        end += 1
    del lines[start:end]
    path.write_text("".join(lines), encoding="utf-8", newline="\n")
    return True


def remove_test_functions_containing(path: Path, marker: str) -> int:
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    nodes = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test_")
        and marker in node.name
    ]
    if not nodes:
        return 0
    lines = text.splitlines(keepends=True)
    for node in sorted(nodes, key=lambda item: item.lineno, reverse=True):
        start = node.lineno - 1
        if node.decorator_list:
            start = min(dec.lineno for dec in node.decorator_list) - 1
        end = node.end_lineno
        if end < len(lines) and not lines[end].strip():
            end += 1
        del lines[start:end]
    path.write_text("".join(lines), encoding="utf-8", newline="\n")
    return len(nodes)


def remove_lines_containing(path: Path, markers: tuple[str, ...]) -> int:
    if not path.exists():
        return 0
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    kept = []
    removed = 0
    for line in lines:
        if any(marker in line for marker in markers):
            removed += 1
        else:
            kept.append(line)
    candidate = "".join(kept)
    ast.parse(candidate)
    path.write_text(candidate, encoding="utf-8", newline="\n")
    return removed


def replace_all(path: Path, old: str, new: str) -> int:
    if not path.exists():
        return 0
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count:
        path.write_text(text.replace(old, new), encoding="utf-8", newline="\n")
    return count


def main() -> None:
    changed = []

    targets = [
        (ROOT / "kajovo/core/runlog.py", "record_preflight_batch"),
        (ROOT / "kajovo/core/batch_completion.py", "can_continue_preflight"),
    ]
    for path, name in targets:
        if remove_function(path, name):
            changed.append(f"removed {path.relative_to(ROOT)}::{name}")

    # preflight_pending byl aktivní stav odstraněného workflow. Historické LOGy
    # se čtou podle raw hodnoty; aktivní progress/UI mapování už jej nesmí mít.
    for rel in ("kajovo/core/progress.py", "kajovo/desktop/dialogs.py"):
        path = ROOT / rel
        count = remove_lines_containing(path, ('"preflight_pending"', "'preflight_pending'"))
        if count:
            changed.append(f"removed {count} preflight_pending lines from {rel}")

    # Staré testy placeného workflow se neudržují. Nahrazuje je explicitní
    # repo-wide zákaz v test_no_paid_preflight*.py a pracovní BATCH testy.
    for path in sorted((ROOT / "tests").glob("test_*.py")):
        if path.name.startswith("test_no_paid_preflight"):
            continue
        removed = remove_test_functions_containing(path, "preflight")
        if removed:
            changed.append(f"removed {removed} obsolete preflight tests from {path.name}")
        for old, new in (
            (".preflight_run(", ".prepare_run_validation("),
            (".preflight_response(", ".validate_prepared_payload("),
        ):
            count = replace_all(path, old, new)
            if count:
                changed.append(f"replaced {count} {old} in {path.name}")
        replace_all(path, "assert client._policy.proofs == {}", 'assert not hasattr(client._policy, "proofs")')
        replace_all(path, "assert not client._policy.proofs", 'assert not hasattr(client._policy, "proofs")')
        # Import odstraněné výjimky býval vždy samostatný řádek.
        remove_lines_containing(path, ("import PreflightPending",))

    # Po změnách musí všechny moduly/testy zůstat syntakticky validní.
    for root in (ROOT / "kajovo", ROOT / "tests"):
        for path in root.rglob("*.py"):
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    if not changed:
        raise RuntimeError("Migrace nic nezměnila; očekávané legacy symboly už nebyly přítomné.")
    print("\n".join(changed))


if __name__ == "__main__":
    main()
