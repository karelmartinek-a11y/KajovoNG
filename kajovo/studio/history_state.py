"""Jednotné lidské stavy Run Studia; barva nikdy nenahrazuje text a ikonu."""

from __future__ import annotations

from dataclasses import dataclass

from .components import COLORS


@dataclass(frozen=True)
class PresentedState:
    key: str
    label: str
    symbol: str
    color: str
    terminal: bool = False


_STATES = {
    "created": ("Vytvořeno", "○", "muted", False),
    "preparing": ("Připravuje se", "◌", "focus", False),
    "running": ("Běží", "▶", "focus", False),
    "validating_result": ("Ověřuje výsledek", "◌", "focus", False),
    "repairing": ("Opravuje podklad", "↻", "warning", False),
    "active": ("Běží", "▶", "focus", False),
    "response_pending": ("Čeká na odpověď", "◷", "focus", False),
    "batch_prepared": ("Dávka připravena", "⚑", "focus", False),
    "batch_pending": ("Dávka se zpracovává", "☁", "focus", False),
    "importing": ("Přebírá se", "⇩", "focus", False),
    "completed": ("Dokončeno", "✓", "success", True),
    "partial": ("Částečně dokončeno", "⚠", "warning", True),
    "failed": ("Chyba", "!", "danger", True),
    "error": ("Chyba", "!", "danger", True),
    "cancelled": ("Zrušeno", "×", "muted", True),
    "stopped": ("Zrušeno", "×", "muted", True),
    "dry_run": ("Návrh bez změny souborů", "◇", "muted", True),
    "plan_ready": ("Plán připraven", "◆", "success", True),
    "qfile_plan_ready": ("Návrh jednoho souboru připraven", "◆", "warning", True),
    "submission_unknown": ("Neznámý výsledek", "?", "warning", False),
    "files_complete_unverified": ("Soubory připravené, funkčnost neověřena", "◇", "warning", True),
    "waiting_manual_resource": ("Čeká na ruční podklad", "▣", "warning", True),
    "needs_clarification": ("Čeká na upřesnění zadání", "?", "warning", True),
    "completed_unverified": ("Převzato bez úplného ověření", "◇", "warning", True),
    "closed": ("Dokončeno", "✓", "success", True),
    "corrupt_state": ("Chyba evidence", "!", "danger", True),
    "unknown": ("Neznámý výsledek", "?", "warning", False),
    "unfinished_record": ("Konec fáze nezapsán", "?", "muted", True),
    "ready_to_import": ("K převzetí", "⇩", "warning", False),
    "skipped": ("Přeskočeno", "»", "muted", True),
    "blocked": ("Nelze pokračovat", "▣", "muted", True),
    "not_started": ("Nelze pokračovat", "▣", "muted", False),
    "queued": ("Čeká na odpověď", "◷", "focus", False),
    "in_progress": ("Dávka se zpracovává", "☁", "focus", False),
    "validating": ("Dávka se zpracovává", "☁", "focus", False),
    "finalizing": ("Dávka se zpracovává", "☁", "focus", False),
    "cancelling": ("Ruší se", "×", "warning", False),
    "expired": ("Chyba", "!", "danger", True),
}


def present_state(value: object, *, batch_import_pending: bool = False) -> PresentedState:
    key = str(value or "unknown").strip().lower()
    if batch_import_pending and key in {"completed", "files_complete_unverified"}:
        return PresentedState("ready_to_import", "K převzetí", "⇩", COLORS["warning"], False)
    label, symbol, color, terminal = _STATES.get(key, _STATES["unknown"])
    return PresentedState(key, label, symbol, COLORS[color], terminal)


def is_error_state(value: object) -> bool:
    return str(value or "").lower() in {
        "failed", "partial", "corrupt_state", "submission_unknown", "expired", "error"
    }
