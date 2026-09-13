"""Interaktivní akce Run Exploreru bez přepisování zdrojového běhu."""
from __future__ import annotations

import copy
import hashlib
import shutil
import uuid
from pathlib import Path

from PySide6.QtWidgets import QMessageBox

from ..core.batch_completion import batch_ids, read_state
from ..core.delivery_preparation import validate_preparation_snapshot
from ..core.run_bundle import LegacyRunAdapter, RunBundle
from ..core.runlog import verified_output_evidence
from .dialogs import msg_info, msg_question, msg_warning


def _source_dir(window, run_id: str) -> Path:
    directory = Path(window.s.log_dir) / run_id
    if not directory.is_dir():
        raise ValueError(f"Běh {run_id} nebyl nalezen.")
    return directory


def _exact_ui_state(state: dict) -> dict:
    ui = state.get("ui_state")
    if not isinstance(ui, dict) or not ui:
        raise ValueError(
            "Běh nemá explicitně uložený ui_state. Run Explorer jej nebude doplňovat odhadem."
        )
    return copy.deepcopy(ui)


def _prepare_checkpoint(window, run_id: str, checkpoint_id: str) -> tuple[dict, dict]:
    bundle = RunBundle(_source_dir(window, run_id))
    checkpoint = bundle.validate_checkpoint(checkpoint_id)
    state = checkpoint.get("state_snapshot")
    if not isinstance(state, dict):
        raise ValueError("Checkpoint neobsahuje stav běhu.")
    ui = _exact_ui_state(state)
    window._apply_state(ui)
    window._resume_files = []
    window._resume_prev_id = None
    window.skip_paths_current = []
    window._completed_hashes = {}
    mode = str(ui.get("mode") or "GENERATE")
    snapshot = state.get("preparation_snapshot")
    if isinstance(snapshot, dict) and mode in {"GENERATE", "MODIFY"}:
        snapshot = validate_preparation_snapshot(
            snapshot,
            mode,
            state.get("maximum_quality", ui.get("maximum_quality", False)),
        )
        structure = snapshot.get("structure") or {}
        files_key = "touched_files" if mode == "MODIFY" else "files"
        window._resume_files = copy.deepcopy(structure.get(files_key) or [])
        window._resume_prev_id = str(snapshot.get("response_id") or "") or None
    else:
        previous = str(state.get("last_response_id") or "")
        window._resume_prev_id = previous or None
    if hasattr(window, "ed_response_id"):
        window.ed_response_id.setText(window._resume_prev_id or "")
    out_dir = str(ui.get("out_dir") or "")
    if out_dir:
        entries = verified_output_evidence(_source_dir(window, run_id), out_dir)
        window._completed_hashes = {
            str(entry["path"]): str(entry["sha256"])
            for entry in entries
            if entry.get("sha256")
        }
        window.skip_paths_current = sorted(window._completed_hashes)
    return checkpoint, state


def _pending_lineage(
    window,
    *,
    source_run_id: str,
    relation_type: str,
    checkpoint_id: str = "",
    artifacts: list[str] | None = None,
    configuration=None,
    notes: str = "",
) -> None:
    window._history_pending_lineage = {
        "source_run_id": source_run_id,
        "relation_type": relation_type,
        "source_checkpoint_id": checkpoint_id,
        "user_action": relation_type,
        "inherited_artifact_ids": list(artifacts or []),
        "inherited_configuration": copy.deepcopy(configuration),
        "notes": notes,
    }


def _record_pending_lineage(window, before: set[str]) -> None:
    pending = getattr(window, "_history_pending_lineage", None)
    if not isinstance(pending, dict):
        return
    created = [run_id for run_id in window._run_contexts if run_id not in before]
    if len(created) != 1:
        return
    run_id = created[0]
    context = window._run_contexts.get(run_id) or {}
    logger = context.get("run_logger")
    if logger is not None:
        logger.record_lineage(**pending)
        window._history_pending_lineage = None
        window.history_panel.refresh_runs()
        return
    worker = context.get("worker")
    if worker is None:
        return

    def finish_lineage():
        logger_value = getattr(worker, "logger", None)
        current = getattr(window, "_history_pending_lineage", None)
        if logger_value is not None and isinstance(current, dict):
            try:
                logger_value.record_lineage(**current)
                window._history_pending_lineage = None
                window.history_panel.refresh_runs()
            except Exception as exc:
                msg_warning(window, "Návaznost běhu", str(exc))

    worker.finished.connect(finish_lineage)


def _run_with_lineage(window) -> None:
    before = set(window._run_contexts)
    window._history_original_on_go()
    _record_pending_lineage(window, before)


def _paid_steps_text(state: dict, checkpoint: dict) -> str:
    stage = str(checkpoint.get("checkpoint_type") or checkpoint.get("checkpoint_id") or "")
    mode = str((state.get("ui_state") or {}).get("mode") or "")
    if mode in {"GENERATE", "MODIFY"}:
        return (
            "Kroky po checkpointu mohou znovu vytvořit placené Responses/BATCH požadavky. "
            "Již doložené výstupy se přeskočí pouze tehdy, pokud jejich hash stále odpovídá evidenci."
        )
    return f"Navazující práce po checkpointu {stage} může vytvořit nový placený modelový požadavek."


def continue_from_checkpoint(window, run_id: str, checkpoint_id: str, relation: str = "continue") -> None:
    try:
        state = read_state(_source_dir(window, run_id))
        if batch_ids(state):
            raise ValueError(
                "Běh má již odeslanou pracovní dávku. Tu dokončete v původním běhu; Run Explorer nevytvoří duplicitní submit."
            )
        checkpoint, snapshot_state = _prepare_checkpoint(window, run_id, checkpoint_id)
        if (
            msg_question(
                window,
                "Pokračovat jako nový běh?",
                "Zdrojový běh: "
                + run_id
                + "\nCheckpoint: "
                + str(checkpoint.get("checkpoint_type") or checkpoint_id)
                + "\n\n"
                + str(checkpoint.get("reason") or "")
                + "\n\n"
                + _paid_steps_text(snapshot_state, checkpoint)
                + "\n\nPůvodní běh zůstane beze změny; vznikne nový navázaný RUN.",
            )
            != QMessageBox.Yes
        ):
            return
        _pending_lineage(
            window,
            source_run_id=run_id,
            relation_type=relation,
            checkpoint_id=checkpoint_id,
            configuration=snapshot_state.get("ui_state"),
            notes="Nový běh vytvořen z explicitně validovaného checkpointu Run Exploreru.",
        )
        window.select_page("run")
        _run_with_lineage(window)
    except Exception as exc:
        msg_warning(window, "Pokračování z Historie", str(exc))


def rerun_as_new(window, run_id: str) -> None:
    try:
        state = read_state(_source_dir(window, run_id))
        if batch_ids(state):
            window.batch_panel.complete_run(run_id)
            return
        adapter = LegacyRunAdapter(_source_dir(window, run_id))
        checkpoints = [item for item in adapter.checkpoints() if item.get("safe_to_continue")]
        if not checkpoints:
            raise ValueError(
                "Běh nemá explicitní bezpečný checkpoint. ReRun se proto nespustí heuristicky; použijte Klonovat."
            )
        continue_from_checkpoint(
            window,
            run_id,
            str(checkpoints[-1]["checkpoint_id"]),
            relation="rerun",
        )
    except Exception as exc:
        msg_warning(window, "ReRun", str(exc))


def clone_as_new(window, run_id: str) -> None:
    try:
        state = read_state(_source_dir(window, run_id))
        ui = _exact_ui_state(state)
        window._apply_state(copy.deepcopy(ui))
        window._resume_files = []
        window._resume_prev_id = None
        window.skip_paths_current = []
        window._completed_hashes = {}
        if hasattr(window, "ed_response_id"):
            window.ed_response_id.clear()
        _pending_lineage(
            window,
            source_run_id=run_id,
            relation_type="clone",
            configuration=ui,
            notes="Klon převzal pouze explicitně uložené zadání a nastavení; nepřebírá response historii.",
        )
        window.select_page("run")
        msg_info(
            window,
            "Klon připraven",
            "Zadání bylo načteno jako nový klon. Upravte je podle potřeby a stiskněte Spustit práci. "
            "Při vytvoření nového běhu se automaticky zapíše lineage na zdrojový RUN.",
        )
    except Exception as exc:
        msg_warning(window, "Klonovat běh", str(exc))


def repair_from_checkpoint(window, run_id: str, checkpoint_id: str) -> None:
    try:
        source_state = read_state(_source_dir(window, run_id))
        error = str(source_state.get("error") or "")
        if error:
            window.log(f"Repair zdroj {run_id}: {error}")
        continue_from_checkpoint(window, run_id, checkpoint_id, relation="repair")
    except Exception as exc:
        msg_warning(window, "Opravit běh", str(exc))


def reuse_artifacts(window, run_id: str, artifact_ids) -> None:
    try:
        adapter = LegacyRunAdapter(_source_dir(window, run_id))
        if adapter.legacy:
            raise ValueError(
                "Legacy soubory nemají stabilní ArtifactRecord ID. Použití znovu je povoleno pouze z nového Run Bundle."
            )
        wanted = set(str(value) for value in artifact_ids)
        artifacts = [
            item
            for item in adapter.artifacts()
            if item.get("artifact_id") in wanted and item.get("reusable")
        ]
        if len(artifacts) != len(wanted):
            raise ValueError("Některý vybraný artefakt chybí nebo není označen jako reusable.")
        staging = (
            Path(window.s.cache_dir)
            / "history_reuse"
            / run_id
            / ("reuse_" + uuid.uuid4().hex)
        )
        staging.mkdir(parents=True, exist_ok=False)
        used_names: set[str] = set()
        for artifact in artifacts:
            relative = str(artifact.get("path_in_bundle") or "")
            source = adapter.root / relative
            if not source.is_file():
                raise ValueError(f"Chybí archivovaný artefakt {artifact.get('display_name')}.")
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            if digest != artifact.get("sha256"):
                raise ValueError(f"Hash artefaktu {artifact.get('display_name')} nesouhlasí.")
            name = Path(str(artifact.get("display_name") or source.name)).name
            base, suffix = Path(name).stem, Path(name).suffix
            counter = 2
            while name.casefold() in used_names:
                name = f"{base}_{counter}{suffix}"
                counter += 1
            used_names.add(name.casefold())
            shutil.copy2(source, staging / name)
        window.cb_mode.setCurrentText("MODIFY")
        window.ed_in.setText(str(staging))
        window.on_mode_changed()
        _pending_lineage(
            window,
            source_run_id=run_id,
            relation_type="reuse_artifacts",
            artifacts=[str(item["artifact_id"]) for item in artifacts],
            configuration={"staging_in": str(staging)},
            notes="Do nového MODIFY zadání byly vloženy hashově ověřené archivované artefakty zdrojového běhu.",
        )
        window.select_page("run")
        msg_info(
            window,
            "Soubory připraveny",
            f"{len(artifacts)} artefaktů bylo ověřeno a zkopírováno do dočasného IN. "
            "Doplňte zadání a OUT a spusťte nový běh.",
        )
    except Exception as exc:
        msg_warning(window, "Použít soubory znovu", str(exc))


def install_history_run_explorer(window):
    """Připojí nové akce k MainWindow; starý běh se při nich nikdy nepřepisuje."""
    if getattr(window, "_history_run_explorer_installed", False):
        return window.history_panel
    window._history_run_explorer_installed = True
    window._history_pending_lineage = None
    window._history_original_on_go = window.on_go
    try:
        window.history_panel.rerun.disconnect(window.rerun)
    except (RuntimeError, TypeError):
        pass
    window.history_panel.rerun.connect(lambda run_id: rerun_as_new(window, run_id))
    window.history_panel.continue_run.connect(
        lambda run_id, checkpoint_id: continue_from_checkpoint(
            window, run_id, checkpoint_id, "continue"
        )
    )
    window.history_panel.clone_run.connect(lambda run_id: clone_as_new(window, run_id))
    window.history_panel.repair_run.connect(
        lambda run_id, checkpoint_id: repair_from_checkpoint(window, run_id, checkpoint_id)
    )
    window.history_panel.reuse_artifacts.connect(
        lambda run_id, artifact_ids: reuse_artifacts(window, run_id, artifact_ids)
    )
    try:
        window.btn_go.clicked.disconnect()
    except (RuntimeError, TypeError):
        pass
    window.btn_go.clicked.connect(lambda _checked=False: _run_with_lineage(window))
    if hasattr(window, "btn_new"):
        window.btn_new.clicked.connect(
            lambda _checked=False: setattr(window, "_history_pending_lineage", None)
        )
    return window.history_panel
