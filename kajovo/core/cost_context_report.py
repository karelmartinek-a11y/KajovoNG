"""Obnovitelný lokální přehled měření; nikdy neobsahuje text zadání či souborů."""
from __future__ import annotations

import json
from pathlib import Path

from .context_budget import measure_request
from .context_compiler import content_hash
from .utils import atomic_write_text


class CostContextReport:
    def __init__(self, run_dir):
        self.path = Path(run_dir) / "cost_context_report.json"

    def record(self, payload, *, response=None, custom_id=None, path=None, measurement=None, status=None):
        data = json.loads(self.path.read_text("utf-8")) if self.path.exists() else {"version": 1, "requests": {}}
        if data.get("version") != 1 or not isinstance(data.get("requests"), dict):
            raise ValueError("Neplatná verze cost/context reportu.")
        key = custom_id or content_hash(payload)
        entry = data["requests"].get(key) or measurement or measure_request(payload, batch=custom_id is not None)
        entry.update(request_id=key, path=path or entry.get("path"),
                     stage=payload.get("text", {}).get("format", {}).get("name"),
                     retry_count=int(payload.get("metadata", {}).get("kajovo_repair_attempt", 0)))
        if response is not None:
            entry.update(response_id=response.get("id"), actual_usage=response.get("usage"),
                         status=response.get("status", "failed" if response.get("error") else "unknown"),
                         error_code=(response.get("error") or {}).get("code"),
                         incomplete_reason=(response.get("incomplete_details") or {}).get("reason"))
        elif status:
            entry["status"] = status
        data["requests"][key] = entry
        rows = list(data["requests"].values())
        actual = [r.get("actual_usage") or {} for r in rows]
        data["summary"] = {
            "requests": len(rows), "estimated_input_tokens": sum(r["input_tokens"] for r in rows),
            "actual_input_tokens": sum(u.get("input_tokens", 0) for u in actual),
            "actual_output_tokens": sum(u.get("output_tokens", 0) for u in actual),
            "actual_reasoning_tokens": sum((u.get("output_tokens_details") or {}).get("reasoning_tokens", 0) for u in actual),
            "usage_missing": sum(not u for u in actual),
            "projected_cost_usd_known_rates": sum((r.get("projected_cost") or {}).get("usd", 0) for r in rows),
            "requests_without_price": sum(r.get("projected_cost") is None for r in rows), "actual_cost": None,
            "saved_estimated_input_tokens": sum(r.get("saved_estimated_input_tokens", 0) for r in rows),
            "legacy_estimated_input_tokens": sum(r.get("legacy_estimated_input_tokens", 0) for r in rows),
            "largest_requests": [r["request_id"] for r in sorted(rows, key=lambda r: r["input_tokens"], reverse=True)[:10]],
            "over_budget": [r["request_id"] for r in rows if r.get("blockers")],
            "incomplete": [r["request_id"] for r in rows if r.get("status") == "incomplete"],
        }
        atomic_write_text(str(self.path), json.dumps(data, ensure_ascii=False, indent=2))
        return entry
