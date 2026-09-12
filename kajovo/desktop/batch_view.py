"""Společné popisky pracovních dávek a read-only historických záznamů."""

from ..core.batch_completion import batch_ids, preflight_ids
from .dialogs import STATES


def project_name(state):
    value = state.get("project") or (state.get("ui_state") or {}).get("project")
    return value if value and value != "NO_PROJECT" else "Bez projektu"


def saved_record(state, bid, snapshots):
    """Přečte stav pracovní dávky nebo starý preflight záznam bez aktivace workflow."""
    legacy = state.get("preflight_batches") or []
    legacy = legacy if isinstance(legacy, list) else []
    historical = next(
        (item for item in legacy if isinstance(item, dict) and item.get("id") == bid), {}
    )
    records = [historical, state.get("batch_records", {}).get(bid, {}), snapshots.get(bid, {})]
    records.sort(key=lambda record: record.get("checked_at", 0) or 0)
    result = {"id": bid}
    for record in records:
        result.update(record)
    return result


def server_label(record, historical_preflight=False):
    status = record.get("status", "")
    value = STATES.get(status, status) or "Stav dosud neověřen"
    if historical_preflight:
        return value + " · historický preflight (neaktivní)"
    return value


def history_batch_detail(state, snapshots):
    work = batch_ids(state)
    legacy = preflight_ids(state)
    parts = [
        f"Pracovní dávka {bid}: " + server_label(saved_record(state, bid, snapshots))
        for bid in work
    ]
    parts.extend(
        f"Historický preflight {bid}: "
        + server_label(saved_record(state, bid, snapshots), historical_preflight=True)
        for bid in legacy
        if bid not in work
    )
    detail = " · ".join(parts)
    if detail and state.get("error"):
        detail += " · " + str(state["error"])
    errors = [saved_record(state, bid, snapshots).get("errors") for bid in [*work, *legacy]]
    if any(errors):
        detail += " · " + "; ".join(str(error) for error in errors if error)
    return detail
