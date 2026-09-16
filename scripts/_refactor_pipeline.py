"""Jednorázový codemod: odstraní tiché chyby v run log/evidence cestách."""

from __future__ import annotations

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
PIPELINE = ROOT / "kajovo" / "core" / "pipeline.py"
SELF = ROOT / "scripts" / "_refactor_pipeline.py"
WORKFLOW = ROOT / ".github" / "workflows" / "refactor-codemod.yml"


def replace_block(text: str, start_name: str, next_name: str, replacement: str) -> str:
    pattern = re.compile(
        rf"    def {re.escape(start_name)}\(.*?(?=    def {re.escape(next_name)}\()",
        re.DOTALL,
    )
    updated, count = pattern.subn(replacement, text, count=1)
    if count != 1:
        raise SystemExit(f"Refaktor odmítnut: {start_name}, nalezeno {count} bloků.")
    return updated


def main() -> None:
    text = PIPELINE.read_text(encoding="utf-8")
    anchor = "from .runs.polling import VectorStorePollingContext, wait_vector_store_files\n"
    observability_import = (
        "from .runs.observability import emit_signal, record_event, update_state as update_run_state\n"
    )
    if text.count(anchor) != 1:
        raise SystemExit("Refaktor odmítnut: polling import nemá očekávaný tvar.")
    if observability_import not in text:
        text = text.replace(anchor, anchor + observability_import, 1)

    text = replace_block(
        text,
        "_log_debug",
        "_log_api_action",
        '''    def _log_debug(self, msg: str) -> None:
        line = f"{self._ts()} | {msg}"
        emit_signal(self.logline, line, name="logline")
        record_event(self.log, "debug", {"ts": self._ts(), "msg": msg})

''',
    )

    text = replace_block(
        text,
        "_log_api_action",
        "_attachments_snapshot",
        '''    def _log_api_action(self, stage: str, action: str, details: Optional[Dict[str, Any]] = None) -> None:
        ts = self._ts()
        parts = [f"{stage}: {action}"]
        if details:
            for key, value in details.items():
                if value is None:
                    continue
                parts.append(f"{key}={value}")
        line = f"{ts} | " + " | ".join(parts)
        emit_signal(self.logline, line, name="logline")
        event = {"ts": ts, "stage": stage, "action": action}
        if details:
            event.update({k: v for k, v in details.items() if v is not None})
        record_event(self.log, "api.trace", event)
        if event.get("response_id"):
            self._final_response_id = str(event.get("response_id") or "")
            patch = {"last_response_id": str(event.get("response_id")), "last_response_stage": stage}
            contract = event.get("contract") or (details.get("contract") if details else None)
            if contract in ("A2_STRUCTURE", "B2_STRUCTURE"):
                patch["last_structure_response_id"] = str(event.get("response_id"))
            if contract in ("A1_PLAN", "A2_STRUCTURE", "B1_PLAN", "B2_STRUCTURE"):
                patch["last_plan_response_id"] = str(event.get("response_id"))
            update_run_state(self.log, patch)

''',
    )

    old = '''        try:
            self.log.event("request.attachments", snapshot)
        except Exception:
            pass
'''
    new = '''        record_event(self.log, "request.attachments", snapshot)
'''
    if text.count(old) != 1:
        raise SystemExit(f"Refaktor odmítnut: request.attachments block, nalezeno {text.count(old)}.")
    text = text.replace(old, new, 1)

    old = '''        try:
            self.log.event("ui.progress", {"p": p, "sp": sp, "msg": msg, "ts": self._ts()})
        except Exception:
            pass
'''
    new = '''        record_event(self.log, "ui.progress", {"p": p, "sp": sp, "msg": msg, "ts": self._ts()})
'''
    if text.count(old) != 1:
        raise SystemExit(f"Refaktor odmítnut: ui.progress block, nalezeno {text.count(old)}.")
    text = text.replace(old, new, 1)

    PIPELINE.write_text(text, encoding="utf-8", newline="\n")
    SELF.unlink()
    WORKFLOW.unlink()


if __name__ == "__main__":
    main()
