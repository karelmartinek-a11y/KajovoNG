"""Jediná politika dostupnosti skutečných akcí Run Studia."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from kajovo.core.batch_completion import batch_ids, pending_batch_ids

from .history_state import is_error_state


DIRECT_MODES = {"GENERATE", "MODIFY", "QA", "QFILE", "KASKADA"}


@dataclass(frozen=True)
class ActionDecision:
    enabled: bool
    reason: str


class ActionAvailabilityPolicy:
    DIRECT = ("rerun", "repair", "continue")

    def evaluate(
        self,
        run: dict[str, Any],
        state: dict[str, Any],
        checkpoints: list[dict[str, Any]],
        *,
        legacy: bool,
        selected_step: dict[str, Any] | None = None,
        artifacts: list[dict[str, Any]] | None = None,
    ) -> dict[str, ActionDecision]:
        ui = state.get("ui_state")
        exact_ui = isinstance(ui, dict) and bool(ui)
        safe = [
            row for row in checkpoints
            if row.get("safe_to_continue") and row.get("_availability_valid", True)
        ]
        submitted = batch_ids(state)
        pending = pending_batch_ids(state)
        batch_records = state.get("batch_records") if isinstance(state.get("batch_records"), dict) else {}
        remote_completed = [
            identifier for identifier in pending
            if isinstance(batch_records.get(identifier), dict)
            and batch_records[identifier].get("status") == "completed"
        ]
        status = str(run.get("status") or state.get("status") or "unknown")
        mode = str(run.get("mode") or state.get("mode") or (ui or {}).get("mode") or "")
        step_status = str((selected_step or {}).get("status") or "")
        has_error = is_error_state(status) or is_error_state(step_status)
        nonterminal = status in {
            "created", "preparing", "running", "response_pending", "stopped", "cancelled"
        }

        if mode not in DIRECT_MODES:
            direct_reason = "Tento typ běhu používá vlastní doménovou akci a nemá přímý Run Studio launcher."
        elif legacy:
            direct_reason = "Legacy běh nemá doložený bezpečný checkpoint."
        elif pending:
            direct_reason = "Běh má nedokončený místní import BATCH; použijte původní dávku a nevytvářejte druhý submit."
        elif not safe:
            direct_reason = "Běh nemá neporušený explicitní bezpečný checkpoint."
        else:
            direct_reason = ""
        direct_ok = not direct_reason
        return {
            "rerun": ActionDecision(direct_ok, direct_reason or "Vytvoří nový běh od bezpečného bodu."),
            "repair": ActionDecision(
                direct_ok and has_error,
                direct_reason or ("Oprava je dostupná pouze pro doloženou chybu nebo částečný výsledek." if not has_error else "Vytvoří opravnou větev."),
            ),
            "continue": ActionDecision(
                direct_ok and nonterminal,
                direct_reason or ("Dokončený běh nemá smysluplné pokračování." if not nonterminal else "Pokračuje v nové větvi."),
            ),
            "edit_branch": ActionDecision(
                direct_ok and mode == "QA",
                direct_reason or ("Upravené zadání je podporováno pouze pro QA checkpoint před prvním requestem."
                                  if mode != "QA" else "Upraví pouze nově prováděný QA request v nové větvi."),
            ),
            "clone": ActionDecision(exact_ui, "Běh nemá přesně uložený ui_state." if not exact_ui else "Otevře upravitelné nové Zadání."),
            "clone_artifact": ActionDecision(
                exact_ui and any(row.get("reusable") for row in artifacts or []),
                ("Běh nemá přesně uložený ui_state." if not exact_ui
                 else "Běh nemá reusable ArtifactRecord." if not any(row.get("reusable") for row in artifacts or [])
                 else "Ověří hash a otevře explicitní clone variantu."),
            ),
            "complete_batch": ActionDecision(
                bool(remote_completed),
                ("Žádná již odeslaná dávka nečeká na místní převzetí." if not pending
                 else "Poslední doložený vzdálený stav ještě není completed." if not remote_completed
                 else "Převezme existující dávku bez nového submitu."),
            ),
            "open_batch": ActionDecision(bool(submitted), "Běh nemá související dávku." if not submitted else "Otevře související dávku."),
        }


def apply_decision(button, decision: ActionDecision) -> None:
    button.setEnabled(decision.enabled)
    button.setToolTip(decision.reason)
    button.setAccessibleDescription(decision.reason)
