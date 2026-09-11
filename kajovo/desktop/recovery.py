"""Obnova podkladů pokračování z dostupné evidence běhů."""

import json
import os
from pathlib import Path
from ..core.contracts import parse_json_strict, extract_text_from_response
from ..core.utils import safe_join_under_root


def read_record(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def newest(directory, pattern="*.json"):
    return sorted(directory.glob(pattern), key=lambda path: path.stat().st_mtime, reverse=True)


def recover_run(log_dir, run_id):
    directory = Path(safe_join_under_root(log_dir, run_id))
    state = read_record(directory / "run_state.json")
    ui = state.get("ui_state")
    for path in newest(directory / "requests"):
        candidate = read_record(path).get("ui_state")
        if isinstance(candidate, dict) and candidate:
            ui = candidate
            break
    if not isinstance(ui, dict) or not ui:
        raise ValueError("Uložené zadání nebylo nalezeno.")
    previous = state.get("last_response_id") or state.get("last_structure_response_id")
    events = directory / "events.jsonl"
    if not previous and events.is_file():
        for line in reversed(events.read_text(encoding="utf-8", errors="replace").splitlines()):
            try:
                event = json.loads(line)
                data = event.get("data") or {}
                if (
                    event.get("type") == "api.trace"
                    and data.get("action") == "complete"
                    and data.get("response_id")
                ):
                    previous = data["response_id"]
                    break
            except (ValueError, AttributeError):
                continue
    candidates = [directory]
    output = state.get("out_dir") or ui.get("out_dir")
    if output:
        target = os.path.normcase(os.path.realpath(output))
        for path in newest(Path(log_dir), "*/run_state.json"):
            other = read_record(path).get("out_dir")
            if (
                other
                and path.parent != directory
                and os.path.normcase(os.path.realpath(other)) == target
            ):
                candidates.append(path.parent)
    for source in candidates:
        structure, structure_id = [], None
        responses = newest(source / "responses")
        # Strukturální odpověď je vhodnější záloha návaznosti než nezávislý soubor.
        responses.sort(
            key=lambda path: not any(part in path.name for part in ("A2_response", "B2_response"))
        )
        for path in responses:
            response = read_record(path)
            if source == directory:
                previous = previous or response.get("id")
            try:
                parsed = parse_json_strict(extract_text_from_response(response))
                if (
                    isinstance(parsed, dict)
                    and (parsed.get("contract") == "A2_STRUCTURE" or "A2_response" in path.name)
                    and isinstance(parsed.get("files"), list)
                ):
                    structure, structure_id = parsed["files"], response.get("id")
                    if structure:
                        break
            except (ValueError, KeyError, TypeError):
                continue
        manifests = newest(source / "manifests")
        if not structure:
            for path in manifests:
                if "resume_structure" in path.name:
                    record = read_record(path)
                    structure = record.get("resume_files") or []
                    structure_id = record.get("resume_prev_id")
                    if structure:
                        break
        if not structure:
            for path in manifests:
                if not path.name.endswith("_out_saved_map.json"):
                    continue
                record = read_record(path)
                saved = record.get(
                    "saved", {key: value for key, value in record.items() if key != "out_dir"}
                )
                entries = [{"path": key} for key in saved] if isinstance(saved, dict) else saved
                for entry in entries if isinstance(entries, list) else []:
                    if not isinstance(entry, dict):
                        continue
                    name = entry.get("path") or entry.get("dst_rel") or entry.get("dst")
                    try:
                        if name:
                            safe_join_under_root(output or "/validation", name)
                            structure.append({"path": name, "purpose": entry.get("purpose", "")})
                    except ValueError:
                        continue
                if structure:
                    break
        if structure:
            return (
                ui,
                previous or structure_id,
                structure,
            )
    return ui, previous, []
