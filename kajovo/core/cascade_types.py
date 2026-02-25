from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
import time


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
        data = data or {}
        output_type = str(data.get("output_type") or "text").lower()
        if output_type not in ("text", "json"):
            output_type = "text"
        output_schema_kind = data.get("output_schema_kind")
        if output_schema_kind not in (None, "manifest", "prompts", "custom"):
            output_schema_kind = None
        input_content = data.get("input_content_json")
        if input_content is not None and not isinstance(input_content, (dict, list)):
            input_content = None
        custom_schema = data.get("output_schema_custom")
        if custom_schema is not None and not isinstance(custom_schema, dict):
            custom_schema = None
        temp_val = data.get("temperature")
        try:
            temperature = None if temp_val is None or temp_val == "" else float(temp_val)
        except Exception:
            temperature = None
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
            "version": int(self.version or 1),
            "name": self.name,
            "created_at": float(self.created_at or time.time()),
            "updated_at": float(self.updated_at or time.time()),
            "steps": [s.to_dict() for s in (self.steps or [])],
            "default_out_dir": self.default_out_dir,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CascadeDefinition":
        data = data or {}
        steps_raw = data.get("steps") or []
        steps: List[CascadeStep] = []
        if isinstance(steps_raw, list):
            for row in steps_raw:
                if isinstance(row, dict):
                    steps.append(CascadeStep.from_dict(row))
        now = float(time.time())
        created_at = data.get("created_at", now)
        updated_at = data.get("updated_at", now)
        try:
            created_at = float(created_at)
        except Exception:
            created_at = now
        try:
            updated_at = float(updated_at)
        except Exception:
            updated_at = now
        version = data.get("version", 1)
        try:
            version = int(version)
        except Exception:
            version = 1
        if version <= 0:
            version = 1
        return cls(
            name=str(data.get("name") or "Unnamed Cascade"),
            steps=steps,
            default_out_dir=str(data.get("default_out_dir") or "").strip(),
            created_at=created_at,
            updated_at=updated_at,
            version=version,
        )
