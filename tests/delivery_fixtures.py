"""Společné podklady offline testů přípravy a dodání souborů."""

from copy import deepcopy


def requirements_payload(mode="GENERATE", **overrides):
    """Vrátí explicitní i implicitní požadavek se stabilními identifikátory."""
    explicit = [{"id": "REQ-1", "description": "Dodat požadovaný textový výstup."}]
    implicit = [{"id": "REQ-2", "description": "Zachovat konzistentní obsah souborů."}]
    if mode == "GENERATE":
        payload = {
            "contract": "A0R_REQUIREMENTS", "product_intent": "Vytvořit textový výstup.",
            "explicit_requirements": explicit, "implicit_requirements": implicit,
            **{key: [] for key in (
                "invariants", "assumptions", "user_flows", "system_flows",
                "states_and_lifecycle", "validation_and_error_behaviour",
                "quality_attributes", "security_and_privacy", "persistence_and_consistency",
                "dependencies_and_integrations",
            )},
        }
    elif mode == "MODIFY":
        payload = {
            "contract": "B0R_REQUIREMENTS", "requested_change": "Upravit textový výstup.",
            "explicit_change_requirements": explicit, "implicit_change_requirements": implicit,
            "current_behaviour_to_preserve": ["Nedotčené soubory zůstanou zachované."],
            **{key: [] for key in (
                "impacted_flows", "impacted_states", "impacted_contracts",
                "validation_and_error_behaviour", "migration_or_compatibility_concerns",
            )},
        }
    else:
        raise ValueError(f"Nepodporovaný režim fixtures: {mode}")
    payload.update(
        acceptance_criteria=["Obsah všech určených souborů odpovídá zadání."],
        definition_of_done=["Úplné soubory byly ověřeny a uloženy."],
    )
    return {**payload, **deepcopy(overrides)}


def plan_payload(mode="GENERATE", files=None, **overrides):
    """Vrátí samostatný plán; explicitní testovací vady nepřepisuje."""
    if mode == "GENERATE":
        payload = {
            "contract": "A1_PLAN",
            "project": {key: "test" for key in
                        ("name", "one_liner", "target_os", "language", "runtime")},
            "assumptions": [],
            "requirements": {key: [] for key in
                             ("functional", "non_functional", "constraints")},
            "architecture": {key: [] for key in
                             ("modules", "data_flow", "error_handling", "security_notes")},
            "build_run": {key: [] for key in ("prerequisites", "commands", "verification")},
            "deliverable_policy": {"max_lines_per_chunk": 500},
        }
    elif mode == "MODIFY":
        if files is None:
            files = [{"path": "hello.txt", "action": "add"}]
        payload = {
            "contract": "B1_PLAN",
            "diagnosis": {"summary": "test", "evidence": [], "likely_root_causes": []},
            "change_plan": {key: [] for key in
                            ("goals", "files_to_modify", "files_to_add", "verification_steps")},
            "missing_inputs": [],
        }
        for action, key in (("add", "files_to_add"), ("modify", "files_to_modify")):
            payload["change_plan"][key] = [
                {"path": item["path"], "intent": item.get("intent", "Dodat úplný obsah.")}
                for item in files if item.get("action", "add") == action
            ]
    else:
        raise ValueError(f"Nepodporovaný režim fixtures: {mode}")
    payload["architecture_items"] = [{
        "id": "ARCH-1", "requirement_ids": ["REQ-1", "REQ-2"],
        "responsibility": "Dodat úplné a konzistentní textové soubory.",
    }]
    return {**payload, **deepcopy(overrides)}


def structure_payload(mode="GENERATE", files=None, **overrides):
    """Doplní výchozí metadata souborů bez změny dodaných vazeb a vad."""
    if files is None:
        files = [{"path": "hello.txt"}]
    if mode == "GENERATE":
        payload = {
            "contract": "A2_STRUCTURE", "version": 2,
            "rules": [], "packages": [], "interfaces": [],
            "files": [
                {"purpose": "test", "language": "text", "kind": "text",
                 "dependencies": [], "provides": [], "requires": [],
                 "behavior": "test", **deepcopy(item)} for item in files
            ],
        }
    elif mode == "MODIFY":
        payload = {
            "contract": "B2_STRUCTURE",
            "invariants": [], "interfaces": [], "preserved_files": [],
            "touched_files": [
                {"intent": "test", "action": "add", "purpose": "test",
                 "language": "text", "kind": "text", "dependencies": [],
                 "provides": [], "requires": [], "behavior": "test",
                 "preserved_behaviour": [], **deepcopy(item)} for item in files
            ],
        }
    else:
        raise ValueError(f"Nepodporovaný režim fixtures: {mode}")
    key = "files" if mode == "GENERATE" else "touched_files"
    payload[key] = [
        {"requirement_ids": ["REQ-1", "REQ-2"], "architecture_item_ids": ["ARCH-1"], **item}
        for item in payload[key]
    ]
    return {**payload, **deepcopy(overrides)}


def delivery_payloads(mode="GENERATE", files=None):
    """Vrátí samostatné, vzájemně dohledatelné requirements, plán a strukturu."""
    return requirements_payload(mode), plan_payload(mode, files), structure_payload(mode, files)
