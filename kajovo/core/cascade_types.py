from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
import time
import math


@dataclass
class CascadeStep:
    title: str = ""
    model: str = ""
    temperature: Optional[float] = None
    instructions: str = ""
    input_text: str = ""
    input_content_json: Optional[Any] = None
    files_existing_ids: List[str] = field(default_factory=list)
    files_local_paths: List[str] = field(default_factory=list)
    previous_response_id_expr: Optional[str] = None
    output_type: str = "text"  # text|json
    output_schema_kind: Optional[str] = None  # manifest|prompts|custom|None
    output_schema_custom: Optional[Dict[str, Any]] = None
    expected_out_files: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "model": self.model,
            "temperature": self.temperature,
            "instructions": self.instructions,
            "input_text": self.input_text,
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
        for key in ("title", "model", "instructions", "input_text", "output_type"):
            if key in data and not isinstance(data[key], str):
                raise ValueError(f"{key} musí být text.")
        if data.get("previous_response_id_expr") is not None and not isinstance(data["previous_response_id_expr"], str):
            raise ValueError("Výraz návaznosti musí být text nebo null.")
        for key in ("files_existing_ids", "files_local_paths", "expected_out_files"):
            value = data.get(key, [])
            if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
                raise ValueError(f"{key} musí být seznam neprázdných textů.")
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
        if isinstance(temp_val, bool) or temperature is not None and (not math.isfinite(temperature) or not 0 <= temperature <= 2):
            raise ValueError("Teplota kaskády musí být konečné číslo od 0 do 2.")
        return cls(
            title=str(data.get("title") or ""),
            model=str(data.get("model") or ""),
            temperature=temperature,
            instructions=str(data.get("instructions") or ""),
            input_text=str(data.get("input_text") or ""),
            input_content_json=input_content,
            files_existing_ids=[str(x) for x in (data.get("files_existing_ids") or []) if str(x).strip()],
            files_local_paths=[str(x) for x in (data.get("files_local_paths") or []) if str(x).strip()],
            previous_response_id_expr=(str(data.get("previous_response_id_expr")).strip() if data.get("previous_response_id_expr") else None),
            output_type=output_type,
            output_schema_kind=output_schema_kind,
            output_schema_custom=custom_schema,
            expected_out_files=[str(x) for x in (data.get("expected_out_files") or []) if str(x).strip()],
        )


@dataclass
class CascadeDefinition:
    name: str
    steps: List[CascadeStep] = field(default_factory=list)
    default_out_dir: str = ""
    created_at: float = field(default_factory=lambda: float(time.time()))
    updated_at: float = field(default_factory=lambda: float(time.time()))
    version: int = 1

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "name": self.name,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "steps": [s.to_dict() for s in (self.steps or [])],
            "default_out_dir": self.default_out_dir,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CascadeDefinition":
        if not isinstance(data, dict):
            raise ValueError("Definice kaskády musí být objekt.")
        for key in ("name", "default_out_dir"):
            if key in data and not isinstance(data[key], str):
                raise ValueError(f"{key} musí být text.")
        steps_raw = data.get("steps", [])
        if not isinstance(steps_raw, list) or any(not isinstance(row, dict) for row in steps_raw):
            raise ValueError("Kroky kaskády musí být seznam objektů.")
        steps: List[CascadeStep] = []
        if isinstance(steps_raw, list):
            for row in steps_raw:
                if isinstance(row, dict):
                    steps.append(CascadeStep.from_dict(row))
        now = float(time.time())
        created_at = data.get("created_at", now)
        updated_at = data.get("updated_at", now)
        for timestamp in (created_at, updated_at):
            if type(timestamp) not in (int, float) or not math.isfinite(timestamp) or timestamp < 0:
                raise ValueError("Čas kaskády musí být konečné nezáporné číslo.")
        version = data.get("version", 1)
        if type(version) is not int or version <= 0:
            raise ValueError("Verze kaskády musí být kladné celé číslo.")
        return cls(
            name=str(data.get("name") or "Unnamed Cascade"),
            steps=steps,
            default_out_dir=str(data.get("default_out_dir") or "").strip(),
            created_at=created_at,
            updated_at=updated_at,
            version=version,
        )
