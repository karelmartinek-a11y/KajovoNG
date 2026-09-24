"""Obnova lokální přípravy vychází z atomického dispatch záznamu, ne z odhadu."""
from pathlib import Path

from ..utils import atomic_write_text
from .contracts import canonical_bytes
from .repository import OrchestrationRepository


def reconcile_unsubmitted(run_dir, state):
    """Volající vlastní zámek běhu; prepared dokládá, že dispatch nezačal."""
    repo = OrchestrationRepository(Path(run_dir).resolve().parent / "orchestration.sqlite3")
    with repo.connect() as db:
        rows = db.execute("SELECT p.attempt_id,p.state FROM provider_operations p JOIN work_orders w "
                          "ON w.work_order_hash=p.work_order_hash WHERE w.run_id=?", (Path(run_dir).name,)).fetchall()
    prepared = [identifier for identifier, status in rows if status == "prepared"]
    if prepared:
        repo.mark_batch_dispatch(prepared, rejected=True)
    states = {identifier: "not_submitted" if status == "prepared" else status for identifier, status in rows}
    pending = state.get("pending_batch_submission") or {}
    manifest = pending.get("manifest") or state.get("generate_batch") or {}
    identifiers = [row.get("attempt_id") for row in (manifest.get("work_orders") or {}).values()]
    if state.get("submission_unknown") and identifiers and all(states.get(key) == "not_submitted" for key in identifiers):
        state["submission_unknown"] = False
        state["status"] = "partial"
        state.pop("pending_batch_submission", None)
        for value in [state.get("batch_manifest_v4"), *(state.get("batch_manifests_v4") or {}).values()]:
            if value and value.get("state") in {"prepared", "input_uploaded", "submitting", "submission_unknown"}:
                if not pending.get("manifest_v4_id") or value["manifest_id"] == pending["manifest_v4_id"]:
                    value["state"] = "failed"
        atomic_write_text(str(Path(run_dir) / "run_state.json"), canonical_bytes(state).decode("utf-8"))
    return state
