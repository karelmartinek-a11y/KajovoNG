from __future__ import annotations

import hashlib
import json
from pathlib import PurePosixPath
from typing import Any, Dict, List, Set, Tuple

from .cascade_types import (
    CASCADE_FILE_TYPES,
    CascadeDefinition,
    CascadeInput,
    CascadeOutput,
    CascadeStep,
)
from .structured_output import validate_schema


class CascadeValidationError(ValueError):
    pass


def _machine_key(prefix: str, raw_id: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in raw_id)
    cleaned = cleaned.strip("_") or "value"
    return f"{prefix}_{cleaned}"[:64]


def output_machine_key(output: CascadeOutput) -> str:
    return _machine_key("out", output.id)


def _step_position_map(definition: CascadeDefinition) -> Dict[str, int]:
    return {step.id: index for index, step in enumerate(definition.steps)}


def resolve_decision_targets(
    definition: CascadeDefinition,
    *,
    strict: bool,
) -> None:
    """Bind visible step numbers to stable IDs without breaking forward references.

    In draft mode, a future step number may point beyond the currently created steps.
    In strict mode (pre-run), every target must exist.
    """
    positions = _step_position_map(definition)
    for index, step in enumerate(definition.steps):
        for output in step.outputs:
            if output.kind != "decision":
                continue
            for option in output.decision_options:
                if option.target_step_id:
                    target_index = positions.get(option.target_step_id, -1)
                    if target_index < 0:
                        if strict:
                            raise CascadeValidationError(
                                f"Krok {index + 1}: volba „{option.value}“ odkazuje na neexistující cílový krok."
                            )
                        option.target_step_id = ""
                    elif target_index <= index:
                        raise CascadeValidationError(
                            f"Krok {index + 1}: rozhodnutí smí pokračovat pouze do některého z následujících kroků."
                        )
                    else:
                        option.target_step_number = target_index + 1
                        continue

                if option.target_step_number:
                    target_index = option.target_step_number - 1
                    if target_index <= index:
                        raise CascadeValidationError(
                            f"Krok {index + 1}: rozhodnutí smí pokračovat pouze do některého z následujících kroků."
                        )
                    if target_index < len(definition.steps):
                        option.target_step_id = definition.steps[target_index].id
                    elif strict:
                        raise CascadeValidationError(
                            f"Krok {index + 1}: cílový krok {option.target_step_number} ještě neexistuje."
                        )
                elif strict:
                    raise CascadeValidationError(
                        f"Krok {index + 1}: volba „{option.value}“ nemá určený cílový krok."
                    )


def _output_lookup(definition: CascadeDefinition) -> Dict[Tuple[str, str], CascadeOutput]:
    result: Dict[Tuple[str, str], CascadeOutput] = {}
    for step in definition.steps:
        for output in step.outputs:
            result[(step.id, output.id)] = output
    return result


def _validate_output(step: CascadeStep, output: CascadeOutput, step_number: int) -> None:
    if not output.id:
        raise CascadeValidationError(f"Krok {step_number}: výstup nemá interní ID.")
    if not output.name.strip():
        raise CascadeValidationError(f"Krok {step_number}: každý výstup musí mít název.")
    if output.kind == "file":
        if output.file_type not in CASCADE_FILE_TYPES:
            raise CascadeValidationError(
                f"Krok {step_number}: u souborového výstupu „{output.name}“ vyberte typ souboru."
            )
        if not output.file_name:
            raise CascadeValidationError(
                f"Krok {step_number}: u souborového výstupu „{output.name}“ zadejte název souboru."
            )
        path = PurePosixPath(output.file_name.replace("\\", "/"))
        if path.is_absolute() or ".." in path.parts:
            raise CascadeValidationError(
                f"Krok {step_number}: název výstupního souboru musí být relativní a nesmí opouštět OUT."
            )
        suffix = path.suffix.lower().lstrip(".")
        if suffix and suffix != output.file_type:
            raise CascadeValidationError(
                f"Krok {step_number}: soubor „{output.file_name}“ neodpovídá vybranému typu {output.file_type.upper()}."
            )
        if output.file_mode == "modify":
            candidate = next((item for item in step.inputs if item.id == output.modify_input_id), None)
            if candidate is None:
                raise CascadeValidationError(
                    f"Krok {step_number}: upravovaný soubor „{output.name}“ nemá vybraný vstupní soubor."
                )
            if candidate.source not in ("local_file", "file_id", "output"):
                raise CascadeValidationError(
                    f"Krok {step_number}: režim upravit lze použít jen nad souborovým vstupem."
                )
    elif output.kind == "json":
        if not isinstance(output.json_schema, dict):
            raise CascadeValidationError(
                f"Krok {step_number}: strukturovaný výstup „{output.name}“ nemá explicitní JSON Schema masku."
            )
        try:
            validate_schema(output.json_schema)
        except (ValueError, TypeError) as exc:
            raise CascadeValidationError(
                f"Krok {step_number}: JSON maska výstupu „{output.name}“ není strict schema: {exc}"
            ) from exc

        def contains_local_ref(node: Any) -> bool:
            if isinstance(node, dict):
                return "$ref" in node or "$defs" in node or any(
                    contains_local_ref(value) for value in node.values()
                )
            if isinstance(node, list):
                return any(contains_local_ref(value) for value in node)
            return False

        if contains_local_ref(output.json_schema):
            raise CascadeValidationError(
                f"Krok {step_number}: JSON maska výstupu „{output.name}“ nesmí obsahovat $ref/$defs; "
                "vnořená maska musí být úplná a samostatná."
            )
    elif output.kind == "decision":
        if len(output.decision_options) < 2:
            raise CascadeValidationError(
                f"Krok {step_number}: rozhodnutí „{output.name}“ musí mít alespoň dvě možné odpovědi."
            )
        values = [item.value.strip() for item in output.decision_options]
        if any(not value for value in values):
            raise CascadeValidationError(
                f"Krok {step_number}: každá možná odpověď rozhodnutí musí mít text."
            )
        if len(values) != len(set(values)):
            raise CascadeValidationError(
                f"Krok {step_number}: možné odpovědi rozhodnutí musí být jedinečné."
            )


def _validate_input(
    definition: CascadeDefinition,
    step: CascadeStep,
    item: CascadeInput,
    step_number: int,
    positions: Dict[str, int],
    outputs: Dict[Tuple[str, str], CascadeOutput],
) -> None:
    if not item.id:
        raise CascadeValidationError(f"Krok {step_number}: vstup nemá interní ID.")
    if not item.name.strip():
        raise CascadeValidationError(f"Krok {step_number}: každý vstup musí mít název.")
    if item.source in ("text", "local_file", "file_id"):
        if not item.value.strip():
            raise CascadeValidationError(
                f"Krok {step_number}: vstup „{item.name}“ nemá vyplněnou hodnotu."
            )
        return
    if item.source != "output":
        raise CascadeValidationError(f"Krok {step_number}: neznámý typ vstupu „{item.source}“.")
    source_index = positions.get(item.source_step_id, -1)
    if source_index < 0:
        raise CascadeValidationError(
            f"Krok {step_number}: vstup „{item.name}“ odkazuje na neexistující krok."
        )
    if source_index >= step_number - 1:
        raise CascadeValidationError(
            f"Krok {step_number}: vstup „{item.name}“ smí čerpat jen z předchozích kroků."
        )
    source_output = outputs.get((item.source_step_id, item.source_output_id))
    if source_output is None:
        raise CascadeValidationError(
            f"Krok {step_number}: vstup „{item.name}“ odkazuje na neexistující výstup."
        )


def _graph(definition: CascadeDefinition) -> Dict[int, Set[int]]:
    edges: Dict[int, Set[int]] = {index: set() for index in range(len(definition.steps))}
    positions = _step_position_map(definition)
    for index, step in enumerate(definition.steps):
        decisions = [output for output in step.outputs if output.kind == "decision"]
        if decisions:
            decision = decisions[0]
            for option in decision.decision_options:
                target = positions.get(option.target_step_id, -1)
                if target >= 0:
                    edges[index].add(target)
        elif index + 1 < len(definition.steps):
            edges[index].add(index + 1)
    return edges


def _dominators(definition: CascadeDefinition) -> Dict[int, Set[int]]:
    count = len(definition.steps)
    if not count:
        return {}
    edges = _graph(definition)
    predecessors: Dict[int, Set[int]] = {index: set() for index in range(count)}
    for src, targets in edges.items():
        for target in targets:
            predecessors[target].add(src)

    reachable: Set[int] = {0}
    changed = True
    while changed:
        changed = False
        for src in list(reachable):
            for target in edges[src]:
                if target not in reachable:
                    reachable.add(target)
                    changed = True

    dom: Dict[int, Set[int]] = {}
    all_reachable = set(reachable)
    for node in range(count):
        if node == 0:
            dom[node] = {0}
        elif node in reachable:
            dom[node] = set(all_reachable)
        else:
            dom[node] = set()

    changed = True
    while changed:
        changed = False
        for node in sorted(reachable):
            if node == 0:
                continue
            preds = [p for p in predecessors[node] if p in reachable]
            if not preds:
                new_value = {node}
            else:
                common = set(dom[preds[0]])
                for pred in preds[1:]:
                    common &= dom[pred]
                new_value = {node} | common
            if new_value != dom[node]:
                dom[node] = new_value
                changed = True
    return dom


def validate_cascade_definition(
    definition: CascadeDefinition,
    *,
    strict: bool = True,
) -> List[str]:
    if not isinstance(definition, CascadeDefinition):
        raise CascadeValidationError("Neplatná definice kaskády.")
    if not definition.name.strip():
        raise CascadeValidationError("Kaskáda musí mít název.")
    if not definition.steps:
        raise CascadeValidationError("Kaskáda musí obsahovat alespoň jeden krok.")

    step_ids = [step.id for step in definition.steps]
    if any(not value for value in step_ids) or len(step_ids) != len(set(step_ids)):
        raise CascadeValidationError("Každý krok musí mít jedinečné stabilní ID.")

    for step in definition.steps:
        step.ensure_outputs()
    resolve_decision_targets(definition, strict=strict)
    positions = _step_position_map(definition)
    outputs = _output_lookup(definition)
    warnings: List[str] = []

    for index, step in enumerate(definition.steps, 1):
        if not step.model.strip():
            raise CascadeValidationError(f"Krok {index}: vyberte model.")
        if not step.deterministic:
            # Staré kaskády zůstávají spustitelné bez vynucení nového UI kontraktu.
            continue
        if not step.title.strip():
            raise CascadeValidationError(f"Krok {index}: vyplňte název.")
        if not step.input_text.strip() and not step.inputs:
            raise CascadeValidationError(f"Krok {index}: vyplňte zadání nebo přidejte vstup.")
        if not step.context_id.strip():
            raise CascadeValidationError(f"Krok {index}: vyberte nebo vytvořte kontext.")

        input_ids = [item.id for item in step.inputs]
        if len(input_ids) != len(set(input_ids)):
            raise CascadeValidationError(f"Krok {index}: vstupy musí mít jedinečná ID.")
        output_ids = [item.id for item in step.outputs]
        if len(output_ids) != len(set(output_ids)):
            raise CascadeValidationError(f"Krok {index}: výstupy musí mít jedinečná ID.")
        decision_outputs = [item for item in step.outputs if item.kind == "decision"]
        if len(decision_outputs) > 1:
            raise CascadeValidationError(
                f"Krok {index}: krok může obsahovat nejvýše jedno rozhodnutí."
            )

        for item in step.inputs:
            _validate_input(definition, step, item, index, positions, outputs)
        for output in step.outputs:
            _validate_output(step, output, index)
            if output.kind == "file" and output.file_mode == "modify":
                candidate = next(
                    (item for item in step.inputs if item.id == output.modify_input_id),
                    None,
                )
                if candidate is not None and candidate.source == "output":
                    source_output = outputs.get(
                        (candidate.source_step_id, candidate.source_output_id)
                    )
                    if source_output is None or source_output.kind != "file":
                        raise CascadeValidationError(
                            f"Krok {index}: výstup „{output.name}“ lze upravovat jen z předchozího souborového výstupu."
                        )

    if strict:
        edges = _graph(definition)
        reachable: Set[int] = {0}
        stack = [0]
        while stack:
            source = stack.pop()
            for target in edges[source]:
                if target not in reachable:
                    reachable.add(target)
                    stack.append(target)
        unreachable = [str(index + 1) for index in range(len(definition.steps)) if index not in reachable]
        if unreachable:
            raise CascadeValidationError(
                "Některé kroky nejsou z žádné předchozí cesty dosažitelné: " + ", ".join(unreachable) + "."
            )

        dom = _dominators(definition)
        for target_index, step in enumerate(definition.steps):
            for item in step.inputs:
                if item.source != "output":
                    continue
                source_index = positions[item.source_step_id]
                if source_index not in dom.get(target_index, set()):
                    raise CascadeValidationError(
                        f"Krok {target_index + 1}: vstup „{item.name}“ není dostupný na všech možných cestách do tohoto kroku."
                    )

        final_seen = set()
        for ref in definition.final_outputs:
            if not ref.step_id or not ref.output_id:
                raise CascadeValidationError("Finální výstup není úplně určen.")
            if (ref.step_id, ref.output_id) not in outputs:
                raise CascadeValidationError("Finální výstup odkazuje na neexistující výstup kroku.")
            key = (ref.step_id, ref.output_id)
            if key in final_seen:
                raise CascadeValidationError("Stejný finální výstup je vybrán vícekrát.")
            final_seen.add(key)

        if definition.run_from_step_id and definition.run_from_step_id not in positions:
            raise CascadeValidationError("Vybraný počáteční krok už v kaskádě neexistuje.")
    else:
        for index, step in enumerate(definition.steps, 1):
            for output in step.outputs:
                if output.kind == "decision":
                    for option in output.decision_options:
                        if option.target_step_number > len(definition.steps):
                            warnings.append(
                                f"Krok {index}: volba „{option.value}“ čeká na vytvoření kroku {option.target_step_number}."
                            )
    return warnings


def step_signature(step: CascadeStep) -> str:
    payload = step.to_dict()
    payload.pop("previous_response_id_expr", None)
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def runtime_schema_for_step(step: CascadeStep) -> Dict[str, Any]:
    """Create a strict deterministic schema for named outputs."""
    step.ensure_outputs()
    properties: Dict[str, Any] = {}
    required: List[str] = []
    for output in step.outputs:
        key = output_machine_key(output)
        required.append(key)
        if output.kind == "text":
            properties[key] = {
                "type": "string",
                "description": f"Výstup „{output.name}“.",
            }
        elif output.kind == "json":
            if not isinstance(output.json_schema, dict):
                raise CascadeValidationError(
                    f"Strukturovaný výstup „{output.name}“ nemá explicitní JSON Schema masku."
                )
            properties[key] = json.loads(
                json.dumps(output.json_schema, ensure_ascii=False)
            )
        elif output.kind == "decision":
            properties[key] = {
                "type": "string",
                "enum": [item.value for item in output.decision_options],
                "description": f"Vyber právě jednu předdefinovanou odpověď pro „{output.name}“.",
            }
        elif output.kind == "file":
            properties[key] = {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "enum": [output.file_name]},
                    "encoding": {"type": "string", "enum": ["utf-8", "base64"]},
                    "content": {
                        "type": "string",
                        "description": (
                            "Obsah souboru. Pro textové formáty použij utf-8; "
                            "pro binární formáty použij base64."
                        ),
                    },
                },
                "required": ["path", "encoding", "content"],
                "additionalProperties": False,
            }
        else:
            raise CascadeValidationError(f"Neznámý typ výstupu: {output.kind}")
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def describe_output(output: CascadeOutput) -> str:
    if output.kind == "file":
        mode = "upravit existující" if output.file_mode == "modify" else "vytvořit nový"
        return f"{output.name} · {output.file_type.upper()} · {mode}"
    if output.kind == "decision":
        return f"{output.name} · rozhodnutí ({' / '.join(item.value for item in output.decision_options)})"
    return f"{output.name} · {output.kind.upper()}"


def humanize_cascade_error(exc: BaseException) -> str:
    raw = str(exc or "").strip()
    low = raw.lower()
    if raw in ("STOPPED", "STOP_REQUESTED"):
        return "Běh kaskády byl zastaven."
    if "insufficient_quota" in low or "quota" in low or "billing" in low:
        return "OpenAI účet nemá dostatek kreditu nebo má omezené účtování."
    if "rate limit" in low or "429" in low:
        return "OpenAI je právě přetížené nebo byl dosažen rychlostní limit; krok se po třech pokusech nepodařilo dokončit."
    if "timeout" in low or "timed out" in low:
        return "OpenAI neodpovědělo včas ani po třech pokusech."
    if "api key" in low or "authentication" in low or "401" in low:
        return "OpenAI API klíč není platný nebo nemá potřebné oprávnění."
    if "context" in low and ("length" in low or "window" in low):
        return "Požadavek je příliš velký pro kontext zvoleného modelu."
    if "output" in low and ("contract" in low or "schéma" in low or "schema" in low):
        return "Model ani po třech pokusech nevrátil výstup v požadovaném formátu."
    if "soubor" in low or "file" in low:
        return "Krok se zastavil, protože požadovaný soubor není dostupný nebo neodpovídá definici."
    if "rozhod" in low or "decision" in low:
        return "Rozhodovací krok nevrátil jednu z předem povolených odpovědí."
    if isinstance(exc, CascadeValidationError):
        return raw.rstrip(".") + "."
    return "Krok se nepodařilo dokončit ani po třech pokusech; technický důvod je uložen v logu."
