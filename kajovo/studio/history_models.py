"""Rebuildovatelný view-model a virtualizovaný Qt model Run Studia."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt

from .history_state import PresentedState, present_state


STAGE_TITLES = {
    "A0R": "Upřesnění požadavků", "A1": "Plán řešení", "A2": "Struktura projektu",
    "A2Q": "Kontrola návrhu", "A3": "Výsledné soubory", "B0R": "Upřesnění změn",
    "B1": "Plán změn", "B2": "Struktura změn", "B2Q": "Kontrola změn",
    "B3": "Výsledné soubory", "QA": "Odpověď", "QFILE": "Výsledný soubor",
    "BATCH": "BATCH", "Upload": "Nahrání podkladů", "RUN": "Běh",
}


def _timestamp(value: object) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


@dataclass(frozen=True)
class StageView:
    step_id: str
    stage: str
    title: str
    status: PresentedState
    started: float | None
    finished: float | None
    duration: float | None
    model: str = ""
    reasoning: str = ""
    artifact_count: int = 0
    response_count: int = 0
    error_count: int = 0
    checkpoint_id: str = ""
    technical: dict[str, Any] = field(default_factory=dict, compare=False)


@dataclass(frozen=True)
class RunView:
    run_id: str
    project: str
    mode: str
    created_at: str
    finished_at: str
    status: PresentedState
    transport: str
    models: tuple[str, ...]
    input_count: int
    output_count: int
    error_count: int
    legacy: bool
    stages: tuple[StageView, ...]
    has_batch: bool = False
    has_checkpoint: bool = False
    has_output: bool = False
    has_lineage: bool = False
    parent_run_id: str = ""
    child_count: int = 0
    search_text: str = ""
    raw: dict[str, Any] = field(default_factory=dict, compare=False)

    @property
    def duration(self) -> float | None:
        start, end = _timestamp(self.created_at), _timestamp(self.finished_at)
        if start is not None and end is not None and end >= start:
            return end - start
        # Trvání fází se může překrývat a nenahrazuje čas celého běhu.
        return None


def build_stage(record: dict[str, Any], *, errors: set[str] | None = None) -> StageView:
    started, finished = _timestamp(record.get("started_at")), _timestamp(record.get("finished_at"))
    duration = finished - started if started is not None and finished is not None and finished >= started else None
    stage = str(record.get("stage") or "")
    step_id = str(record.get("step_id") or "")
    return StageView(
        step_id=step_id,
        stage=stage,
        title=str((STAGE_TITLES.get(stage) or stage or "Není evidováno")
                  if record.get("title") in (None, "", stage) else record["title"]),
        status=present_state(record.get("status")),
        started=started,
        finished=finished,
        duration=duration,
        model=str(record.get("model") or ""),
        reasoning=str(record.get("reasoning_effort") or ""),
        artifact_count=len(record.get("artifact_ids") or []),
        response_count=len(record.get("response_ids") or []),
        error_count=1 if errors and step_id in errors else 0,
        checkpoint_id=str(record.get("checkpoint_id") or ""),
        technical=dict(record),
    )


def build_run(
    summary: dict[str, Any],
    *,
    steps: list[dict[str, Any]] | None = None,
    state: dict[str, Any] | None = None,
    events: list[dict[str, Any]] | None = None,
    reverse_lineage: dict[str, list[dict[str, Any]]] | None = None,
) -> RunView:
    state = state or {}
    events = events or []
    failed_steps = {
        str(row.get("step_id") or "") for row in events
        if str(row.get("severity") or "").lower() == "error" and row.get("step_id")
    }
    # Transportní záznamy zůstávají v technickém inspektoru. Nejsou fázemi workflow.
    standard = summary.get("mode") in {"GENERATE", "MODIFY", "QA", "QFILE"}
    stages = tuple(build_stage(row, errors=failed_steps) for row in (steps or [])
                   if not (standard and row.get("stage") in {"background", "provider", "received"}))
    if present_state(summary.get("status")).terminal:
        stages = tuple(replace(stage, status=present_state("unfinished_record"))
                       if stage.status.key in {"running", "created", "preparing"} else stage for stage in stages)
    batches = summary.get("related_batch_ids") or state.get("generate_batches") or []
    if state.get("batch_id"):
        batches = [*batches, state["batch_id"]]
    imports_value = state.get("batch_imports", summary.get("batch_imports"))
    imports = imports_value if isinstance(imports_value, dict) else {}
    pending_import = bool(batches) and any(
        not isinstance(imports.get(identifier), dict)
        or imports[identifier].get("import_status") not in {"files_complete_unverified", "completed", "comic_completed"}
        for identifier in batches
    )
    remote_value = state.get("batch_records", summary.get("batch_records"))
    remote_records = remote_value if isinstance(remote_value, dict) else {}
    remote_complete = any(
        isinstance(remote_records.get(identifier), dict)
        and remote_records[identifier].get("status") == "completed"
        for identifier in batches
    )
    if pending_import and remote_complete:
        stages = tuple(replace(stage, status=present_state("completed", batch_import_pending=True))
                       if stage.stage in {"A3", "B3", "BATCH"} and stage.status.key in {"batch_pending", "batch_prepared"}
                       else stage for stage in stages)
    artifacts = summary.get("artifacts") if isinstance(summary.get("artifacts"), list) else []
    input_count = sum(1 for row in artifacts or [] if row.get("role") in {"user_input", "attached_file", "in_project_file", "input"})
    output_count = sum(1 for row in artifacts or [] if row.get("role") in {"generated_file", "modified_file", "batch_output", "log_export"})
    models = summary.get("models") or summary.get("model_summary") or []
    if isinstance(models, str):
        models = [models]
    run_id = str(summary.get("run_id") or "")
    children = (reverse_lineage or {}).get(run_id, [])
    parent = str(summary.get("parent_run_id") or "")
    return RunView(
        run_id=run_id,
        project=str(summary.get("project") or "Není evidováno"),
        mode=str(summary.get("mode") or "Není evidováno"),
        created_at=str(summary.get("created_at") or ""),
        finished_at=str(summary.get("finished_at") or ""),
        status=present_state("completed" if pending_import and remote_complete else summary.get("status"),
                             batch_import_pending=pending_import),
        transport="BATCH" if batches or summary.get("has_batch") else "LIVE",
        models=tuple(str(value) for value in models if value),
        input_count=input_count or int(summary.get("input_count") or 0),
        output_count=output_count or int(summary.get("output_count") or 0),
        error_count=int(summary.get("error_count") or (1 if summary.get("has_error") else 0)),
        legacy=bool(summary.get("legacy")),
        stages=stages,
        has_batch=bool(batches or summary.get("has_batch")),
        has_checkpoint=bool(summary.get("checkpoints") or summary.get("has_checkpoint")),
        has_output=bool(summary.get("has_output") or output_count),
        has_lineage=bool(summary.get("has_lineage") or parent or children),
        parent_run_id=parent,
        child_count=len(children),
        search_text=str(summary.get("search_text") or "").casefold(),
        raw=dict(summary),
    )


class RunTableModel(QAbstractTableModel):
    MetaColumn = 0
    TrackColumn = 1
    RunRole = Qt.UserRole + 1

    def __init__(self, parent=None):
        super().__init__(parent)
        self._all: list[RunView] = []
        self._rows: list[RunView] = []

    def rowCount(self, parent=None):
        parent = parent or QModelIndex()
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=None):
        parent = parent or QModelIndex()
        return 0 if parent.isValid() else 2

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self._rows):
            return None
        run = self._rows[index.row()]
        if role == self.RunRole:
            return run
        if role == Qt.DisplayRole:
            if index.column() == self.MetaColumn:
                duration = "Není evidováno" if run.duration is None else format_duration(run.duration)
                models = " / ".join(run.models) or "Není evidováno"
                legacy = " · LEGACY" if run.legacy else ""
                return (
                    f"{run.mode}  ·  {run.project}\n{run.run_id}  ·  {format_datetime(run.created_at)}  ·  {duration}\n"
                    f"{run.status.symbol} {run.status.label}  ·  {run.transport}  ·  {models}{legacy}\n"
                    f"{run.input_count} vstupů · {run.output_count} výstupů · {run.error_count} chyb"
                )
            return run.run_id
        if role == Qt.AccessibleTextRole:
            return f"{run.mode}, {run.project}, {run.status.label}, {run.run_id}"
        if role == Qt.ToolTipRole and run.legacy:
            return "Legacy běh je pouze ke čtení; chybějící kroky ani checkpointy se nedopočítávají."
        return None

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return ("Běh / stopa", "Časová osa · relativní čas")[section] if section in (0, 1) else None
        return super().headerData(section, orientation, role)

    def set_runs(self, runs: list[RunView]):
        self.beginResetModel()
        self._all = list(runs)
        self._rows = list(runs)
        self.endResetModel()

    def apply_filters(self, values: dict[str, Any]):
        self.set_filtered(self.filter_runs(self._all, values))

    @staticmethod
    def filter_runs(runs, values):
        query = str(values.get("search") or "").casefold().strip()
        lower, upper = values.get("date_from"), values.get("date_to")
        rows = []
        for run in runs:
            created = _timestamp(run.created_at)
            if query and query not in (run.search_text or " ".join((run.run_id, run.project, run.mode)).casefold()):
                continue
            if values.get("project") and values["project"].casefold() not in run.project.casefold():
                continue
            if values.get("mode") and values["mode"] != run.mode:
                continue
            if values.get("status") and values["status"] != run.status.key:
                continue
            if values.get("transport") and values["transport"] != run.transport:
                continue
            if values.get("model") and values["model"].casefold() not in " ".join(run.models).casefold():
                continue
            if values.get("errors") and not run.error_count:
                continue
            if values.get("batch") and not run.has_batch:
                continue
            if values.get("checkpoint") and not run.has_checkpoint:
                continue
            if values.get("output") and not run.has_output:
                continue
            if values.get("lineage") and not run.has_lineage:
                continue
            if created is not None and ((lower is not None and created < lower) or (upper is not None and created > upper)):
                continue
            rows.append(run)
        return rows

    def set_filtered(self, rows):
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()

    def run_at(self, row: int) -> RunView | None:
        return self._rows[row] if 0 <= row < len(self._rows) else None


def format_duration(seconds: float) -> str:
    value = max(0, int(round(seconds)))
    if value < 60:
        return f"{value} s"
    minutes, seconds = divmod(value, 60)
    return f"{minutes} min {seconds} s" if seconds else f"{minutes} min"


def format_datetime(value: str) -> str:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone().strftime("%d. %m. %Y · %H:%M")
    except (ValueError, TypeError):
        return "Není evidováno"
