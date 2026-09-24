from __future__ import annotations

import copy
from dataclasses import dataclass, field, fields
from pathlib import PurePosixPath
from typing import Any, Dict, List, Optional
from uuid import uuid4
import math
import time


CASCADE_FILE_TYPES = (
    "txt",
    "md",
    "json",
    "csv",
    "xlsx",
    "docx",
    "pdf",
    "pptx",
    "png",
    "jpg",
    "jpeg",
    "zip",
)

CASCADE_OUTPUT_KINDS = ("text", "json", "file", "decision")
CASCADE_INPUT_SOURCES = ("text", "local_file", "file_id", "output")
CASCADE_FILE_MODES = ("create", "modify")


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def _closed_fields(cls, data):
    unknown = set(data) - {item.name for item in fields(cls)}
    if unknown:
        raise ValueError(f"{cls.__name__}: neznámá pole {sorted(unknown)}.")


@dataclass
class CascadeDecisionOption:
    value: str = ""
    target_step_id: str = ""
    target_step_number: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "value": self.value,
            "target_step_id": self.target_step_id,
            "target_step_number": self.target_step_number,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CascadeDecisionOption":
        if not isinstance(data, dict):
            raise ValueError("Volba rozhodnutí musí být objekt.")
        _closed_fields(cls, data)
        value = data.get("value", "")
        target_step_id = data.get("target_step_id", "")
        target_step_number = data.get("target_step_number", 0)
        if not isinstance(value, str) or not isinstance(target_step_id, str):
            raise ValueError("Hodnota rozhodnutí a ID cílového kroku musí být text.")
        if type(target_step_number) is not int or target_step_number < 0:
            raise ValueError("Číslo cílového kroku musí být nezáporné celé číslo.")
        return cls(
            value=value.strip(),
            target_step_id=target_step_id.strip(),
            target_step_number=target_step_number,
        )


@dataclass
class CascadeInput:
    id: str = field(default_factory=lambda: _new_id("in"))
    name: str = ""
    source: str = "text"  # text|local_file|file_id|output
    value: str = ""
    source_step_id: str = ""
    source_output_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "source": self.source,
            "value": self.value,
            "source_step_id": self.source_step_id,
            "source_output_id": self.source_output_id,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CascadeInput":
        if not isinstance(data, dict):
            raise ValueError("Vstup kroku musí být objekt.")
        _closed_fields(cls, data)
        for key in ("id", "name", "source", "value", "source_step_id", "source_output_id"):
            if key in data and not isinstance(data[key], str):
                raise ValueError(f"{key} vstupu musí být text.")
        source = str(data.get("source") or "text").strip().lower()
        if source not in CASCADE_INPUT_SOURCES:
            raise ValueError("Neznámý typ zdroje vstupu.")
        return cls(
            id=str(data.get("id") or _new_id("in")).strip(),
            name=str(data.get("name") or "").strip(),
            source=source,
            value=str(data.get("value") or ""),
            source_step_id=str(data.get("source_step_id") or "").strip(),
            source_output_id=str(data.get("source_output_id") or "").strip(),
        )


@dataclass
class CascadeOutput:
    id: str = field(default_factory=lambda: _new_id("out"))
    name: str = "Výstup"
    kind: str = "text"  # text|json|file|decision
    file_type: str = ""
    file_name: str = ""
    file_mode: str = "create"  # create|modify
    modify_input_id: str = ""
    json_schema: Optional[Dict[str, Any]] = None
    decision_options: List[CascadeDecisionOption] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "file_type": self.file_type,
            "file_name": self.file_name,
            "file_mode": self.file_mode,
            "modify_input_id": self.modify_input_id,
            "json_schema": copy.deepcopy(self.json_schema),
            "decision_options": [item.to_dict() for item in self.decision_options],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CascadeOutput":
        if not isinstance(data, dict):
            raise ValueError("Výstup kroku musí být objekt.")
        _closed_fields(cls, data)
        for key in ("id", "name", "kind", "file_type", "file_name", "file_mode", "modify_input_id"):
            if key in data and not isinstance(data[key], str):
                raise ValueError(f"{key} výstupu musí být text.")
        kind = str(data.get("kind") or "text").strip().lower()
        if kind not in CASCADE_OUTPUT_KINDS:
            raise ValueError("Neznámý typ výstupu.")
        file_type = str(data.get("file_type") or "").strip().lower().lstrip(".")
        if file_type and file_type not in CASCADE_FILE_TYPES:
            raise ValueError("Nepodporovaný typ souboru.")
        file_mode = str(data.get("file_mode") or "create").strip().lower()
        if file_mode not in CASCADE_FILE_MODES:
            raise ValueError("Neznámý režim souborového výstupu.")
        json_schema = data.get("json_schema")
        if json_schema is not None and not isinstance(json_schema, dict):
            raise ValueError("JSON maska výstupu musí být objekt nebo null.")
        options_raw = data.get("decision_options", [])
        if not isinstance(options_raw, list) or any(not isinstance(row, dict) for row in options_raw):
            raise ValueError("Volby rozhodnutí musí být seznam objektů.")
        return cls(
            id=str(data.get("id") or _new_id("out")).strip(),
            name=str(data.get("name") or "Výstup").strip(),
            kind=kind,
            file_type=file_type,
            file_name=str(data.get("file_name") or "").strip().replace("\\", "/"),
            file_mode=file_mode,
            modify_input_id=str(data.get("modify_input_id") or "").strip(),
            json_schema=copy.deepcopy(json_schema),
            decision_options=[CascadeDecisionOption.from_dict(row) for row in options_raw],
        )


@dataclass
class CascadeOutputRef:
    step_id: str = ""
    output_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"step_id": self.step_id, "output_id": self.output_id}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CascadeOutputRef":
        if not isinstance(data, dict):
            raise ValueError("Odkaz na finální výstup musí být objekt.")
        _closed_fields(cls, data)
        step_id = data.get("step_id", "")
        output_id = data.get("output_id", "")
        if not isinstance(step_id, str) or not isinstance(output_id, str):
            raise ValueError("Odkaz na finální výstup musí obsahovat textová ID.")
        return cls(step_id=step_id.strip(), output_id=output_id.strip())


@dataclass
class CascadeStep:
    id: str = field(default_factory=lambda: _new_id("step"))
    title: str = ""
    model: str = ""
    temperature: Optional[float] = None
    instructions: str = ""
    input_text: str = ""
    context_id: str = "Kontext 1"
    use_conversation_context: bool = False
    deterministic: bool = False
    inputs: List[CascadeInput] = field(default_factory=list)
    outputs: List[CascadeOutput] = field(default_factory=list)

    # Legacy/advanced wire fields. Kept so old saved cascades remain loadable.
    input_content_json: Optional[Any] = None
    files_existing_ids: List[str] = field(default_factory=list)
    files_local_paths: List[str] = field(default_factory=list)
    previous_response_id_expr: Optional[str] = None
    output_type: str = "text"  # text|json (legacy)
    output_schema_kind: Optional[str] = None  # manifest|prompts|custom|None (legacy)
    output_schema_custom: Optional[Dict[str, Any]] = None
    expected_out_files: List[str] = field(default_factory=list)

    def ensure_outputs(self) -> None:
        if self.outputs:
            return
        if self.expected_out_files:
            for path in self.expected_out_files:
                suffix = PurePosixPath(path).suffix.lower().lstrip(".")
                self.outputs.append(
                    CascadeOutput(
                        name=PurePosixPath(path).name or "Soubor",
                        kind="file",
                        file_type=suffix if suffix in CASCADE_FILE_TYPES else "txt",
                        file_name=path,
                    )
                )
            return
        if self.output_type == "json":
            self.outputs.append(
                CascadeOutput(
                    name="JSON",
                    kind="json",
                    json_schema=(
                        copy.deepcopy(self.output_schema_custom)
                        if self.output_schema_kind == "custom"
                        and isinstance(self.output_schema_custom, dict)
                        else None
                    ),
                )
            )
        else:
            self.outputs.append(CascadeOutput(name="Text", kind="text"))

    def ensure_inputs(self) -> None:
        if self.inputs:
            return
        for index, path in enumerate(self.files_local_paths, 1):
            self.inputs.append(
                CascadeInput(name=f"Lokální soubor {index}", source="local_file", value=path)
            )
        for index, file_id in enumerate(self.files_existing_ids, 1):
            self.inputs.append(
                CascadeInput(name=f"OpenAI soubor {index}", source="file_id", value=file_id)
            )

    def to_dict(self) -> Dict[str, Any]:
        self.ensure_outputs()
        return {
            "id": self.id,
            "title": self.title,
            "model": self.model,
            "temperature": self.temperature,
            "instructions": self.instructions,
            "input_text": self.input_text,
            "context_id": self.context_id,
            "use_conversation_context": self.use_conversation_context,
            "deterministic": self.deterministic,
            "inputs": [item.to_dict() for item in self.inputs],
            "outputs": [item.to_dict() for item in self.outputs],
            "input_content_json": self.input_content_json,
            "files_existing_ids": list(self.files_existing_ids or []),
            "files_local_paths": list(self.files_local_paths or []),
            "previous_response_id_expr": self.previous_response_id_expr,
            "output_type": self.output_type,
            "output_schema_kind": self.output_schema_kind,
            "output_schema_custom": self.output_schema_custom,
            "expected_out_files": list(self.expected_out_files or []),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CascadeStep":
        if not isinstance(data, dict):
            raise ValueError("Krok kaskády musí být objekt.")
        _closed_fields(cls, data)
        for key in (
            "id",
            "title",
            "model",
            "instructions",
            "input_text",
            "context_id",
            "output_type",
        ):
            if key in data and not isinstance(data[key], str):
                raise ValueError(f"{key} musí být text.")
        if data.get("previous_response_id_expr") is not None and not isinstance(
            data["previous_response_id_expr"], str
        ):
            raise ValueError("Výraz návaznosti musí být text nebo null.")
        for key in ("files_existing_ids", "files_local_paths", "expected_out_files"):
            value = data.get(key, [])
            if not isinstance(value, list) or any(
                not isinstance(item, str) or not item.strip() for item in value
            ):
                raise ValueError(f"{key} musí být seznam neprázdných textů.")
        for flag in ("deterministic", "use_conversation_context"):
            if flag in data and not isinstance(data[flag], bool):
                raise ValueError(f"{flag} musí být boolean.")
        inputs_raw = data.get("inputs", [])
        outputs_raw = data.get("outputs", [])
        if not isinstance(inputs_raw, list) or any(not isinstance(row, dict) for row in inputs_raw):
            raise ValueError("inputs musí být seznam objektů.")
        if not isinstance(outputs_raw, list) or any(not isinstance(row, dict) for row in outputs_raw):
            raise ValueError("outputs musí být seznam objektů.")
        output_type = str(data.get("output_type") or "text").lower()
        if output_type not in ("text", "json"):
            raise ValueError("Neznámý typ výstupu kaskády.")
        output_schema_kind = data.get("output_schema_kind")
        if output_schema_kind not in (None, "manifest", "prompts", "custom"):
            raise ValueError("Neznámý druh schématu kaskády.")
        input_content = data.get("input_content_json")
        if input_content is not None and not isinstance(input_content, (dict, list)):
            raise ValueError("Strukturovaný vstup musí být objekt nebo seznam.")
        custom_schema = data.get("output_schema_custom")
        if custom_schema is not None and not isinstance(custom_schema, dict):
            raise ValueError("Vlastní schéma musí být objekt.")
        temp_val = data.get("temperature")
        try:
            temperature = None if temp_val is None or temp_val == "" else float(temp_val)
        except (TypeError, ValueError) as exc:
            raise ValueError("Teplota kaskády musí být číslo.") from exc
        if (
            isinstance(temp_val, bool)
            or temperature is not None
            and (not math.isfinite(temperature) or not 0 <= temperature <= 2)
        ):
            raise ValueError("Teplota kaskády musí být konečné číslo od 0 do 2.")

        step = cls(
            id=str(data.get("id") or _new_id("step")).strip(),
            title=str(data.get("title") or ""),
            model=str(data.get("model") or ""),
            temperature=temperature,
            instructions=str(data.get("instructions") or ""),
            input_text=str(data.get("input_text") or ""),
            context_id=str(data.get("context_id") or "Kontext 1").strip(),
            use_conversation_context=bool(data.get("use_conversation_context", False)),
            deterministic=bool(data.get("deterministic", bool("outputs" in data or "inputs" in data))),
            inputs=[CascadeInput.from_dict(row) for row in inputs_raw],
            outputs=[CascadeOutput.from_dict(row) for row in outputs_raw],
            input_content_json=input_content,
            files_existing_ids=[
                str(x) for x in (data.get("files_existing_ids") or []) if str(x).strip()
            ],
            files_local_paths=[
                str(x) for x in (data.get("files_local_paths") or []) if str(x).strip()
            ],
            previous_response_id_expr=(
                str(data.get("previous_response_id_expr")).strip()
                if data.get("previous_response_id_expr")
                else None
            ),
            output_type=output_type,
            output_schema_kind=output_schema_kind,
            output_schema_custom=custom_schema,
            expected_out_files=[
                str(x) for x in (data.get("expected_out_files") or []) if str(x).strip()
            ],
        )
        if step.deterministic:
            step.ensure_inputs()
        step.ensure_outputs()
        return step


@dataclass
class CascadeDefinition:
    name: str
    steps: List[CascadeStep] = field(default_factory=list)
    default_out_dir: str = ""
    final_outputs: List[CascadeOutputRef] = field(default_factory=list)
    run_from_step_id: str = ""
    created_at: float = field(default_factory=lambda: float(time.time()))
    updated_at: float = field(default_factory=lambda: float(time.time()))
    version: int = 2

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "name": self.name,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "steps": [s.to_dict() for s in (self.steps or [])],
            "default_out_dir": self.default_out_dir,
            "final_outputs": [item.to_dict() for item in self.final_outputs],
            "run_from_step_id": self.run_from_step_id,
        }

    def step_index(self, step_id: str) -> int:
        for index, step in enumerate(self.steps):
            if step.id == step_id:
                return index
        return -1

    def step_by_id(self, step_id: str) -> Optional[CascadeStep]:
        index = self.step_index(step_id)
        return self.steps[index] if index >= 0 else None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CascadeDefinition":
        if not isinstance(data, dict):
            raise ValueError("Definice kaskády musí být objekt.")
        _closed_fields(cls, data)
        for key in ("name", "default_out_dir", "run_from_step_id"):
            if key in data and not isinstance(data[key], str):
                raise ValueError(f"{key} musí být text.")
        steps_raw = data.get("steps", [])
        if not isinstance(steps_raw, list) or any(not isinstance(row, dict) for row in steps_raw):
            raise ValueError("Kroky kaskády musí být seznam objektů.")
        final_raw = data.get("final_outputs", [])
        if not isinstance(final_raw, list) or any(not isinstance(row, dict) for row in final_raw):
            raise ValueError("Finální výstupy musí být seznam objektů.")
        steps = [CascadeStep.from_dict(row) for row in steps_raw]
        now = float(time.time())
        created_at = data.get("created_at", now)
        updated_at = data.get("updated_at", now)
        for timestamp in (created_at, updated_at):
            if type(timestamp) not in (int, float) or not math.isfinite(timestamp) or timestamp < 0:
                raise ValueError("Čas kaskády musí být konečné nezáporné číslo.")
        version = data.get("version", 1)
        if type(version) is not int or version not in {1, 2}:
            raise ValueError("Podporované verze kaskády jsou pouze 1 a 2.")
        return cls(
            name=str(data.get("name") or "Unnamed Cascade"),
            steps=steps,
            default_out_dir=str(data.get("default_out_dir") or "").strip(),
            final_outputs=[CascadeOutputRef.from_dict(row) for row in final_raw],
            run_from_step_id=str(data.get("run_from_step_id") or "").strip(),
            created_at=created_at,
            updated_at=updated_at,
            version=version,
        )
