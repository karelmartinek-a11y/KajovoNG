from __future__ import annotations

import json
from typing import Any, Dict, List

from .openai_client import OpenAIClient
from .pricing import PriceRow


class PricingFetcher:
    DEFAULT_MODEL = "gpt-4.1"

    INSTRUCTIONS = (
        "Return ONLY valid JSON with field 'rows' (list). "
        "Each row: {\"model\":\"string\",\"input_per_1k\":float,\"output_per_1k\":float,"
        "\"batch_input_per_1k\":float|null,\"batch_output_per_1k\":float|null,"
        "\"file_search_per_1k\":float|null,\"storage_per_gb_day\":float|null}. "
        "Use USD prices for current OpenAI production models. No commentary."
    )

    @classmethod
    def payload(cls) -> Dict[str, Any]:
        return {
            "model": cls.DEFAULT_MODEL,
            "instructions": cls.INSTRUCTIONS,
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": "Give me the current OpenAI API pricing table."}
                    ],
                }
            ],
        }

    @classmethod
    def parse_response(cls, resp: Dict[str, Any]) -> Dict[str, PriceRow]:
        rows: Dict[str, PriceRow] = {}
        text_parts = cls._extract_text_parts(resp)
        for text in text_parts:
            if not text:
                continue
            try:
                parsed = json.loads(text)
            except Exception:
                continue
            if not isinstance(parsed, dict):
                continue
            for row in parsed.get("rows", []):
                try:
                    pr = PriceRow(
                        model=row.get("model", ""),
                        input_per_1k=float(row.get("input_per_1k", 0.0)),
                        output_per_1k=float(row.get("output_per_1k", 0.0)),
                        batch_input_per_1k=None
                        if row.get("batch_input_per_1k") in (None, "")
                        else float(row.get("batch_input_per_1k")),
                        batch_output_per_1k=None
                        if row.get("batch_output_per_1k") in (None, "")
                        else float(row.get("batch_output_per_1k")),
                        file_search_per_1k=None
                        if row.get("file_search_per_1k") in (None, "")
                        else float(row.get("file_search_per_1k")),
                        storage_per_gb_day=None
                        if row.get("storage_per_gb_day") in (None, "")
                        else float(row.get("storage_per_gb_day")),
                    )
                    if pr.model:
                        rows[pr.model] = pr
                except Exception:
                    continue
            if rows:
                break
        return rows

    @staticmethod
    def _extract_text_parts(resp: Dict[str, Any]) -> List[str]:
        parts = []
        if not isinstance(resp, dict):
            return parts
        for msg in resp.get("output", []) or []:
            if not isinstance(msg, dict):
                continue
            content = msg.get("content") or []
            for part in content:
                if isinstance(part, dict) and "text" in part:
                    parts.append(part["text"])
        if not parts:
            text = resp.get("text")
            if isinstance(text, str):
                parts.append(text)
        return parts
