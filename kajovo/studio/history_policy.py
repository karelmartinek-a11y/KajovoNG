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
    visible: bool = True


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
            if row.get("safe_to_continue") and row.get("_availability_valid", False)
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
        plan_ready = (
            status == "plan_ready"
            and mode in {"GENERATE", "MODIFY"}
            and any(row.get("checkpoint_type") == "plan_ready" for row in safe)
        )
        nonterminal = status in {
            "created", "preparing", "running", "response_pending", "stopped", "cancelled"
        } or plan_ready
        staged = state.get("staged_files") if isinstance(state.get("staged_files"), list) else []
        publishable_staged = bool(
            staged
            and not state.get("dry_run")
            and not state.get("published_files")
            and status in {"files_complete_unverified", "partial"}
            and status != "submission_unknown"
        )

        if mode not in DIRECT_MODES:
            direct_reason = "Tento typ běhu používá vlastní doménovou akci a nemá přímý Run Studio launcher."
        elif legacy:
            direct_reason = "Legacy běh nemá doložený bezpečný checkpoint."
        elif status == "submission_unknown":
            direct_reason = "Výsledek odeslání není potvrzen. Nový požadavek by mohl zdvojit placené zpracování."
        elif pending:
            direct_reason = "Běh má nedokončený místní import BATCH; použijte původní dávku a nevytvářejte druhý submit."
        elif not safe:
            direct_reason = "Běh nemá neporušený explicitní bezpečný checkpoint."
        else:
            direct_reason = ""
        direct_ok = not direct_reason
        return {
            "rerun": ActionDecision(direct_ok, direct_reason or "Vytvoří nový běh od bezpečného bodu.", mode in DIRECT_MODES),
            "repair": ActionDecision(
                direct_ok and has_error,
                direct_reason or ("Oprava je dostupná pouze pro doloženou chybu nebo částečný výsledek." if not has_error else "Vytvoří opravnou větev."), mode in DIRECT_MODES and has_error,
            ),
            "continue": ActionDecision(
                direct_ok and nonterminal,
                direct_reason or (
                    "Dokončený běh nemá smysluplné pokračování."
                    if not nonterminal
                    else "Pokračuje z ověřeného plánu bez opakování přípravy."
                    if plan_ready
                    else "Pokračuje v nové větvi."
                ),
                mode in DIRECT_MODES and nonterminal,
            ),
            "edit_branch": ActionDecision(
                direct_ok and mode == "QA" and any(row.get("checkpoint_type") == "input_ready" for row in safe),
                direct_reason or ("Upravené zadání vyžaduje ověřený bod před prvním QA požadavkem."
                                  if not any(row.get("checkpoint_type") == "input_ready" for row in safe)
                                  else "Upraví pouze nově prováděný QA request v nové větvi."), mode == "QA",
            ),
            "clone": ActionDecision(exact_ui, "Běh nemá přesně uložený ui_state." if not exact_ui else "Otevře upravitelné nové Zadání."),
            "clone_artifact": ActionDecision(
                exact_ui and any(row.get("reusable") for row in artifacts or []),
                ("Běh nemá přesně uložený ui_state." if not exact_ui
                 else "Běh nemá reusable ArtifactRecord." if not any(row.get("reusable") for row in artifacts or [])
                 else "Ověří hash a otevře explicitní clone variantu."),
            ),
            "publish_staged": ActionDecision(
                publishable_staged,
                (
                    "Dry-run nesmí publikovat OUT."
                    if state.get("dry_run")
                    else "Běh nemá nepřevzaté staged artefakty."
                    if not staged or state.get("published_files")
                    else "Výstup není ve stavu, který lze explicitně převzít."
                    if status not in {"files_complete_unverified", "partial"}
                    else "Výslovně převezme neověřené staged artefakty po nové kontrole target hashů."
                ),
                bool(staged) and not state.get("dry_run"),
            ),
            "complete_batch": ActionDecision(
                bool(remote_completed),
                ("Žádná již odeslaná dávka nečeká na místní převzetí." if not pending
                 else "Poslední doložený vzdálený stav ještě není completed." if not remote_completed
                 else "Převezme existující dávku bez nového submitu."), bool(submitted),
            ),
            "open_batch": ActionDecision(bool(submitted), "Otevře související dávku.", bool(submitted)),
            "comic": ActionDecision(mode == "COMIC" and bool(state.get("comic_operation_id")),
                                    "Otevře uloženou komiksovou operaci.", mode == "COMIC"),
            "parent": ActionDecision(bool(run.get("parent_run_id")), "Otevře zdrojový běh.", bool(run.get("parent_run_id"))),
            "detail": ActionDecision(True, "Zadání, výsledky a průběh běhu."),
            "integrity": ActionDecision(not legacy, "Ověří soubory proti otiskům.", not legacy),
            "bundle_open": ActionDecision(True, "Otevře složku se záznamem běhu."),
            "bundle_export": ActionDecision(True, "Uloží kopii záznamu běhu jako ZIP."),
        }


def apply_decision(button, decision: ActionDecision) -> None:
    button.setVisible(decision.visible)
    button.setEnabled(decision.enabled)
    button.setToolTip(decision.reason)
    button.setAccessibleDescription(decision.reason)
