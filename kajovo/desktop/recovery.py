"""Obnova podkladů pokračování z dostupné evidence běhů."""

import copy
import json
import os
from pathlib import Path
from ..core.contracts import parse_json_strict, extract_text_from_response
from ..core.runlog import load_output_evidence
from ..core.delivery_preparation import validate_preparation_snapshot
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
    from ..core.recoverable_artifacts import load_run_state, artifact_path
    if (directory / "run_state.json").exists():
        state = load_run_state(directory)
    elif (directory / "artifacts" / "index.json").exists():
        raise ValueError("Chybí stav nového běhu; přesné artefakty nelze nahradit provozním logem.")
    else:
        state = {}
    if state.get("status") == "submission_unknown" or (
        not state.get("response_transport") and "timed out" in str(state.get("error", "")).lower()
    ):
        raise ValueError("Předchozí odeslání nemá potvrzený výsledek. Automatické opakování není bezpečné; případné nové generování spusťte jako nový běh.")
    ui = state.get("ui_state")
    if artifact_path(directory, "state/ui_state") is None:
        for path in newest(directory / "requests"):
            candidate = read_record(path).get("ui_state")
            if isinstance(candidate, dict) and candidate:
                ui = candidate
                break
    if not isinstance(ui, dict) or not ui:
        raise ValueError("Uložené zadání nebylo nalezeno.")
    ui = copy.deepcopy(ui)
    # Nová příprava patří výhradně vybranému běhu, včetně nedokončené přípravy.
    if ui.get("mode", "GENERATE") in ("GENERATE", "MODIFY") and (
        "preparation_snapshot" in state or "maximum_quality" in state
        or "maximum_quality" in ui or "preparation_snapshot" in ui
    ):
        snapshot = state.get("preparation_snapshot")
        ui["preparation_snapshot"] = copy.deepcopy(snapshot)
        ui["resume_files"], ui["resume_prev_id"] = [], None
        if snapshot is None:
            ui["response_id"] = ""
            return ui, None, []
        snapshot = validate_preparation_snapshot(
            snapshot, ui.get("mode", "GENERATE"),
            state.get("maximum_quality", ui.get("maximum_quality", False)),
        )
        structure = snapshot["structure"]
        ui["maximum_quality"] = snapshot["maximum_quality"]
        files_key = "touched_files" if ui.get("mode") == "MODIFY" else "files"
        return ui, snapshot["response_id"], copy.deepcopy(structure[files_key] if structure else [])
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
        # Strukturální odpověď je vhodnější záloha struktury než nezávislý soubor.
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
            source_state = read_record(source / "run_state.json")
            source_out = source_state.get("out_dir") or output
            for entry in load_output_evidence(source):
                try:
                    if source_out:
                        safe_join_under_root(source_out, entry["path"])
                except ValueError:
                    continue
                structure.append({"path": entry["path"], "purpose": entry.get("purpose", "")})
        if structure:
            return (
                ui,
                previous or structure_id,
                structure,
            )
    return ui, previous, []
