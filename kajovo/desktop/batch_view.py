"""Společné popisky a poslední známé stavy dávek v obou přehledech."""

from ..core.batch_completion import batch_ids, preflight_ids
from .dialogs import STATES


def project_name(state):
    value = state.get("project") or (state.get("ui_state") or {}).get("project")
    return value if value and value != "NO_PROJECT" else "Bez projektu"


def saved_record(state, bid, snapshots):
    trials = state.get("preflight_batches") or []
    trials = trials if isinstance(trials, list) else []
    trial = next((item for item in trials
                  if isinstance(item, dict) and item.get("id") == bid), {})
    records = [trial, state.get("batch_records", {}).get(bid, {}), snapshots.get(bid, {})]
    records.sort(key=lambda record: record.get("checked_at", 0) or 0)
    result = {"id": bid}
    for record in records:
        result.update(record)
    return result


def server_label(record, preflight=False):
    status = record.get("status", "")
    if preflight and status == "completed":
        return "Zkouška dokončena – čeká na převzetí ověření"
    return STATES.get(status, status) or "Stav dosud neověřen"


def history_batch_detail(state, snapshots):
    work = batch_ids(state)
    detail = " · ".join(
        f"{'Pracovní dávka' if bid in work else 'Zkušební dávka'} {bid}: "
        + server_label(saved_record(state, bid, snapshots), bid not in work and not work)
        for bid in dict.fromkeys([*work, *preflight_ids(state)])
    )
    if detail and state.get("error"):
        detail += " · " + str(state["error"])
    errors = [saved_record(state, bid, snapshots).get("errors") for bid in [*work, *preflight_ids(state)]]
    if any(errors):
        detail += " · " + "; ".join(str(error) for error in errors if error)
    return detail
