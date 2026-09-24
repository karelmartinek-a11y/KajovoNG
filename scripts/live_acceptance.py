"""Explicitní, izolovaná a obnovitelná live akceptace OpenAI cest.

Tento modul se nikdy nespouští z pytestu, CI ani startu aplikace. Generativní
klient vznikne až po explicitním autorizačním gate a API klíč se do evidence
nikdy nezapisuje. Hospodárnost určuje minimální rozsah scénářů, nikoli cenový engine.
"""
from __future__ import annotations

import argparse
import ast
from dataclasses import fields, MISSING
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

AUTHORIZATION = "KAJOVONG-LIVE-ACCEPTANCE-2026-09-20"
MODEL = "gpt-5.6-luna"
CASES = ("generate-live", "generate-batch", "photo-batch", "comic-panels")


class Blocked(RuntimeError):
    """Akceptaci nelze bezpečně spustit nebo pokračovat v provider práci."""


class RemotePending(RuntimeError):
    """Vzdálená dávka stále běží a nesmí být duplikována."""


def read_checkpoint(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def write_checkpoint(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safety_gate(confirm_paid: bool, authorization: str | None) -> str:
    """Vrátí klíč až po explicitním potvrzení skutečných provider requestů."""
    if not confirm_paid or authorization != AUTHORIZATION:
        raise Blocked("BLOCKED_AUTHORIZATION")
    key = os.environ.get("OPENAI_API_KEY", "")
    if not key.strip():
        raise Blocked("BLOCKED_API_KEY")
    return key


def ensure_workspace(root: Path, main_sha: str) -> dict[str, Any]:
    path = root / "acceptance_state.json"
    state = read_checkpoint(path)
    if state:
        if state.get("authorization") != AUTHORIZATION:
            raise Blocked("BLOCKED_CHECKPOINT_MISMATCH")
        if state.get("program_sha") not in {None, "", main_sha}:
            raise Blocked("BLOCKED_CHECKPOINT_MISMATCH")
    else:
        state = {
            "schema_version": 2,
            "authorization": AUTHORIZATION,
            "program_sha": main_sha,
            "cases": {},
        }
        write_checkpoint(path, state)
    return state


def acceptance_run_dir(root: Path, case: str) -> Path:
    return root / "_runtime" / "LOG" / ("RUN_LIVE_ACCEPTANCE_" + case.replace("-", "_"))



def case_needs_new_submit(root: Path, case: str) -> bool:
    """Rozliší nový provider submit od bezpečného obnovení existující práce."""
    if case in {"generate-live", "generate-batch"}:
        state = read_checkpoint(acceptance_run_dir(root, case) / "run_state.json")
        return not state
    return not read_checkpoint(root / case / "acceptance_state.json")


def _empty_cfg_values() -> dict[str, Any]:
    from kajovo.core.runs.config import UiRunConfig

    result: dict[str, Any] = {}
    for field in fields(UiRunConfig):
        if field.default is not MISSING:
            result[field.name] = field.default
        elif field.default_factory is not MISSING:  # type: ignore[comparison-overlap]
            result[field.name] = field.default_factory()  # type: ignore[misc]
        else:
            result[field.name] = [] if "list" in str(field.type) else ""
    return result


def make_run_config(*, batch: bool, prompt: str, out: Path):
    from kajovo.core.runs.config import UiRunConfig

    values = _empty_cfg_values()
    values.update(
        project="KájovoNG live acceptance",
        prompt=prompt,
        mode="GENERATE",
        send_as_c=batch,
        model=MODEL,
        model_a1=MODEL,
        model_a2=MODEL,
        model_a3=MODEL,
        response_id="",
        attached_file_ids=[],
        input_file_ids=[],
        attached_vector_store_ids=[],
        in_dir="",
        out_dir=str(out),
        in_equals_out=False,
        versing=False,
        temperature=0.2,
        use_file_search=False,
        diag_windows_in=False,
        diag_windows_out=False,
        diag_ssh_in=False,
        diag_ssh_out=False,
        ssh_user="",
        ssh_host="",
        ssh_key="",
        ssh_password="",
        skip_paths=[],
        skip_exts=[],
        model_caps={},
        resume_files=None,
        resume_prev_id=None,
        available_models=None,
        maximum_quality=False,
        stop_after_plan=False,
        dry_run=False,
        auto_repair="off",
        verification_profile_ids=[],
        execution_approval_id="",
    )
    return UiRunConfig(**values)


def _run_executor(cfg, settings, key, logger) -> tuple[dict[str, Any] | None, str | None]:
    from kajovo.core.runs.executor import RunExecutor

    result: dict[str, Any] | None = None
    error: str | None = None
    worker = RunExecutor(cfg, settings, key, logger)
    # EventPort is synchronous. Keep the callback state local through a tiny box.
    box: dict[str, Any] = {}
    worker.finished_ok.connect(lambda value: box.__setitem__("result", value))
    worker.finished_err.connect(lambda value: box.__setitem__("error", str(value)))
    worker.execute()
    result = box.get("result")
    error = box.get("error")
    return result, error


def collect_raw_usage(root: Path, report: dict[str, Any]) -> None:
    """Archivuje provider usage metadata beze změny a bez odvozování ceny."""
    seen = {
        json.dumps(item, ensure_ascii=False, sort_keys=True)
        for item in report.get("raw_usage", [])
        if isinstance(item, dict)
    }
    values = list(report.get("raw_usage", []))
    if not root.exists():
        report["raw_usage"] = values
        return
    for path in root.rglob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        stack = [payload]
        while stack:
            value = stack.pop()
            if isinstance(value, dict):
                current = value.get("usage")
                if isinstance(current, dict):
                    item = {
                        "provider_item_id": str(
                            value.get("id")
                            or value.get("response_id")
                            or value.get("provider_item_id")
                            or value.get("batch_id")
                            or ""
                        ),
                        "usage": current,
                    }
                    key = json.dumps(item, ensure_ascii=False, sort_keys=True)
                    if key not in seen:
                        seen.add(key)
                        values.append(item)
                for child in value.values():
                    if isinstance(child, (dict, list)):
                        stack.append(child)
            elif isinstance(value, list):
                stack.extend(value)
    report["raw_usage"] = values


def enrich_ids(root: Path, report: dict[str, Any]) -> None:
    response_ids = set(report.get("provider_response_ids", []))
    work_orders = set(report.get("work_order_hashes", []))
    for path in root.rglob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        stack = [payload]
        while stack:
            value = stack.pop()
            if isinstance(value, dict):
                for key, item in value.items():
                    if key in {"response_id", "last_response_id"} and isinstance(item, str) and item:
                        response_ids.add(item)
                    if key in {"order_hash", "work_order_hash"} and isinstance(item, str) and item:
                        work_orders.add(item)
                    if isinstance(item, (dict, list)):
                        stack.append(item)
            elif isinstance(value, list):
                stack.extend(value)
    report["provider_response_ids"] = sorted(response_ids)
    report["work_order_hashes"] = sorted(work_orders)


def _model_check(client, *, batch: bool = False) -> list[str]:
    from kajovo.core.model_registry import model_spec
    available = [str(row.get("id")) for row in client.list_models() if isinstance(row, dict) and row.get("id")]
    if MODEL not in available:
        raise Blocked("BLOCKED_MODEL_UNAVAILABLE")
    spec = model_spec(MODEL)
    if not spec.get("responses") or (batch and not spec.get("batch")):
        raise Blocked("BLOCKED_MODEL_MATRIX")
    return available


def _base_report(case: str, main_sha: str, model: str | list[str]) -> dict[str, Any]:
    return {
        "case": case, "status": "running", "started_at": now(), "finished_at": None,
        "main_sha": main_sha, "models": model, "execution": "live", "provider_response_ids": [],
        "batch_ids": [], "input_file_ids": [], "output_file_ids": [], "raw_usage": [],
        "work_order_hashes": [], "artifact_sha256": [], "technical_validation": "pending",
        "content_acceptance": "not_applicable", "human_review_required": False, "errors": [],
    }


def _validate_generated(path: Path, function_name: str, literal: int) -> str:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)]
    fn = next((node for node in functions if node.name == function_name), None)
    returns = [node for node in ast.walk(fn)] if fn else []
    values = [node for node in returns if isinstance(node, ast.Return)]
    if fn is None or fn.args.args or len(functions) != 1 or len(values) != 1:
        raise RuntimeError(f"{path.name} nesplňuje statický kontrakt")
    if not isinstance(values[0].value, ast.Constant) or values[0].value.value != literal:
        raise RuntimeError(f"{path.name} nevrací požadovaný literál")
    return sha256_file(path)


def _generated_path(run_dir: Path, out: Path, name: str) -> Path:
    candidates = (out / name, run_dir / "staging" / "candidate" / "generated" / name)
    for path in candidates:
        if path.is_file():
            return path
    raise RuntimeError(f"Výstup {name} nebyl nalezen v OUT ani stagingu")



def run_generate(case: str, root: Path, client, key: str, settings, main_sha: str, batch: bool) -> dict[str, Any]:
    """Idempotentní GENERATE resume; stav bez batch_id nesmí znamenat nový LIVE submit."""
    from kajovo.core.batch_completion import complete_saved_batch, read_state
    from kajovo.core.runlog import RunLogger

    out = root / case / "OUT"
    out.mkdir(parents=True, exist_ok=True)
    run_dir = acceptance_run_dir(root, case)
    state = read_state(run_dir) if run_dir.exists() else {}
    report = _base_report(case, main_sha, MODEL)
    report["execution"] = "batch" if batch else "live"
    name, function, literal = (
        ("batch_answer.py", "batch_answer", 7) if batch
        else ("answer.py", "answer", 42)
    )
    completed_statuses = {"completed", "files_complete_unverified"}
    try:
        artifact = _generated_path(run_dir, out, name) if state.get("status") in completed_statuses else None
        if not batch and state.get("status") in completed_statuses:
            report["artifact_sha256"] = [_validate_generated(artifact, function, literal)]
            report["status"] = "passed"
            return report
        if not batch and state.get("status") in {"response_pending", "submission_unknown", "running", "remote_work"}:
            write_checkpoint(
                root / case / "acceptance_state.json",
                {"case": case, "status": state.get("status")},
            )
            report["status"] = "pending"
            report["errors"] = [str(state.get("status"))]
            raise RemotePending(str(state.get("status")))
        if state.get("status") in completed_statuses and artifact is None:
            raise Blocked("BLOCKED_CHECKPOINT_MISMATCH")
        if batch and state.get("status") in completed_statuses and artifact is not None:
            report["artifact_sha256"] = [_validate_generated(artifact, function, literal)]
            report["batch_ids"] = [state.get("batch_id")] if state.get("batch_id") else []
            report["status"] = "passed"
            return report
        if batch and state.get("status") in {"submission_unknown", "response_pending", "running"} and not state.get("batch_id"):
            report["status"] = "pending"
            report["errors"] = [str(state.get("status"))]
            raise RemotePending(str(state.get("status")))
        prompt = (
            "Create exactly one file named answer.py. The file must define one top-level function answer() with no parameters. The function must return the integer literal 42. Do not create any other project files."
            if not batch else
            "Create exactly one file named batch_answer.py. It must define one top-level function batch_answer() with no parameters. The function must return the integer literal 7. Do not create any other project files."
        )
        logger = RunLogger(settings.log_dir, run_dir.name, project_name="KájovoNG live acceptance", resume=bool(state))
        if not state.get("batch_id"):
            cfg = make_run_config(
                batch=batch,
                prompt=prompt,
                out=out,
            )
            result, error = _run_executor(cfg, settings, key, logger)
            state = read_state(run_dir)
            if error:
                if state.get("status") in {"response_pending", "submission_unknown"}:
                    write_checkpoint(
                        root / case / "acceptance_state.json",
                        {"case": case, "status": state["status"]},
                    )
                    report["status"] = "pending"
                    report["errors"] = [state["status"]]
                    raise RemotePending(state["status"])
                raise RuntimeError(error)
            result = result or {}
            report["provider_response_ids"] = [x for x in (result.get("response_id"), result.get("last_response_id")) if x]
            if batch:
                batch_id = str(result.get("batch_id") or state.get("batch_id") or "")
                if not batch_id:
                    raise RuntimeError("GENERATE BATCH nevrátil batch_id")
                report["batch_ids"] = [batch_id]
                report["input_file_ids"] = [result.get("input_file_id")] if result.get("input_file_id") else []
                write_checkpoint(root / case / "acceptance_state.json", {
                    "case": case, "batch_id": batch_id,
                    "input_file_id": result.get("input_file_id", ""),
                    "status": "batch_pending",
                })
            else:
                artifact = _generated_path(run_dir, out, name)
                report["artifact_sha256"] = [_validate_generated(artifact, function, literal)]
                report["status"] = "passed"
                write_checkpoint(root / case / "acceptance_state.json", {
                    "case": case, "status": "completed", "artifact_sha256": report["artifact_sha256"],
                })
                return report
        if batch:
            state = read_state(run_dir)
            batch_id = str(state.get("batch_id") or read_checkpoint(root / case / "acceptance_state.json").get("batch_id") or "")
            if not batch_id:
                raise RemotePending("batch_without_reconstructable_id")
            completed = complete_saved_batch(client, str(run_dir), batch_id, settings)
            if completed.get("status") == "batch_pending":
                raise RemotePending(batch_id)
            if completed.get("status") not in completed_statuses:
                raise RuntimeError(json.dumps(completed, ensure_ascii=False))
            artifact = _generated_path(run_dir, out, name)
            report["artifact_sha256"] = [_validate_generated(artifact, function, literal)]
            report["batch_ids"] = [batch_id]
            report["status"] = "passed"
            write_checkpoint(root / case / "acceptance_state.json", {
                **read_checkpoint(root / case / "acceptance_state.json"),
                "status": "completed", "batch_id": batch_id,
                "artifact_sha256": report["artifact_sha256"],
            })
        return report
    finally:
        report["finished_at"] = now()


def _synthetic_png(path: Path) -> None:
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (256, 256), "white")
    ImageDraw.Draw(image).rectangle((96, 96, 160, 160), fill="red")
    image.save(path, format="PNG")


def choose_image_model(available: list[str]) -> str:
    from kajovo.core.photo_batch import image_edit_model_ids

    allowed = set(image_edit_model_ids(available))
    for candidate in ("gpt-image-2.5-flare", "gpt-image-2.5-flare-2026-09-08", "gpt-image-2.5-sunburst", "gpt-image-2.5-sunburst-2026-09-08"):
        if candidate in allowed:
            return candidate
    raise Blocked("BLOCKED_IMAGE_MODEL_UNAVAILABLE")


def run_photo(root: Path, client, settings, main_sha: str, available: list[str]) -> dict[str, Any]:
    from kajovo.core import photo_batch

    case_root = root / "photo-batch"
    source = case_root / "source.png"
    source.parent.mkdir(parents=True, exist_ok=True)
    _synthetic_png(source)
    model = choose_image_model(available)
    report = _base_report("photo-batch", main_sha, model)
    checkpoint_path = case_root / "acceptance_state.json"
    checkpoint = read_checkpoint(checkpoint_path)
    prompt = "Change the red square to blue. Preserve the rest of the image."
    prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    if checkpoint and (
        checkpoint.get("case") != "photo-batch"
        or checkpoint.get("source_sha256") != sha256_file(source)
        or checkpoint.get("prompt_hash") != prompt_hash
        or checkpoint.get("model") != model
    ):
        raise Blocked("BLOCKED_CHECKPOINT_MISMATCH")
    job = None
    try:
        if checkpoint.get("job_id"):
            jobs = photo_batch.load_jobs(settings.log_dir)
            job = next((candidate for candidate in jobs if candidate.job_id == checkpoint["job_id"]), None)
            if job is None:
                raise Blocked("BLOCKED_PHOTO_CHECKPOINT_MISSING")
        else:
            job = photo_batch.new_job(
                source_paths=[str(source)], human_prompt=prompt,
                professional_prompt=prompt, final_prompt=prompt,
                prompt_source="manual", template_id="manual_photo_plan",
                prompt_model="", prompt_response_id="", image_model=model,
                quality="low", size="1024x1024", output_format="png",
                output_dir=str(case_root / "OUT"),
            )
            write_checkpoint(checkpoint_path, {
                "case": "photo-batch", "job_id": job.job_id,
                "photo_job_path": str(Path(settings.log_dir) / "PHOTO" / job.job_id / "photo_job.json"),
                "batch_id": "", "input_file_id": "", "source_sha256": sha256_file(source),
                "model": model, "prompt_hash": prompt_hash, "status": "preparing",
            })
            job = photo_batch.prepare_and_submit(client, job, settings.log_dir)
        report["job_id"] = job.job_id
        report["batch_ids"] = [job.batch_id]
        report["input_file_ids"] = [
            *(item.uploaded_file_id for item in job.items if item.uploaded_file_id),
            job.input_file_id,
        ]
        write_checkpoint(checkpoint_path, {
            **checkpoint, "case": "photo-batch", "job_id": job.job_id,
            "photo_job_path": str(Path(settings.log_dir) / "PHOTO" / job.job_id / "photo_job.json"),
            "batch_id": job.batch_id, "input_file_id": job.input_file_id,
            "source_sha256": sha256_file(source), "model": model,
            "prompt_hash": prompt_hash, "status": job.status,
        })
        while True:
            job = photo_batch.refresh_job(client, job, settings.log_dir)
            if job.status not in {"completed", "failed", "expired", "cancelled"}:
                report["status"] = "pending"
                report["errors"] = ["REMOTE_PENDING"]
                write_checkpoint(checkpoint_path, {
                    **read_checkpoint(checkpoint_path), "batch_id": job.batch_id,
                    "input_file_id": job.input_file_id, "status": job.status,
                })
                raise RemotePending(job.batch_id)
            job = photo_batch.download_results(client, job, settings.log_dir)
            if job.status != "completed":
                raise RuntimeError(job.status)
            item = job.items[0]
            if item.technical_validation != "passed":
                raise RuntimeError(item.error_message or "technická validace obrazu selhala")
            report["output_file_ids"] = [job.output_file_id] if job.output_file_id else []
            report["artifact_sha256"] = [item.output_sha256]
            report["technical_validation"] = "passed"
            report["content_acceptance"] = "unverified"
            report["human_review_required"] = True
            report["status"] = "technical_pass_human_pending"
            write_checkpoint(checkpoint_path, {
                **read_checkpoint(checkpoint_path), "batch_id": job.batch_id,
                "input_file_id": job.input_file_id, "status": job.status,
            })
            break
    except RemotePending:
        raise
    except Blocked:
        raise
    except Exception as exc:
        report["status"] = "failed"
        report["errors"].append(str(exc))
    finally:
        report["finished_at"] = now()
    return report



def run_comic(root: Path, client, settings, main_sha: str, available: list[str]) -> dict[str, Any]:
    """Resumovatelná COMIC acceptance nad jedním persistentním stavem."""
    from kajovo.core.comic_service import ComicService
    from kajovo.core.config import AppSettings

    case_root = root / "comic-panels"
    checkpoint_path = case_root / "acceptance_state.json"
    checkpoint = read_checkpoint(checkpoint_path)
    exact_image_model = "gpt-image-2.5-sunburst-2026-09-08"
    if exact_image_model not in available:
        raise Blocked("BLOCKED_IMAGE_MODEL_UNAVAILABLE")
    comic_settings = AppSettings(
        comic_library_dir=str(case_root / "COMICS"),
        log_dir=str(case_root / "LOG"),
        response_timeout_s=600,
        response_poll_timeout_s=3600,
        batch_poll_interval_s=5,
        batch_timeout_s=3600,
    )
    service = ComicService(comic_settings, client)
    report = _base_report("comic-panels", main_sha, [MODEL, exact_image_model])
    try:
        project = checkpoint.get("project_id")
        if not project:
            project = service.store.project(
                "Live acceptance", "synthetic panel",
                {"description": "clean comic", "line": "jemná", "color": "barevná", "balloon": "dialogová", "sfx": False},
            )
            checkpoint = {
                "case": "comic-panels", "project_id": project,
                "bible_operation_id": "", "panel_id": "", "panel_operation_id": "",
                "stage": "project",
            }
            write_checkpoint(checkpoint_path, checkpoint)
        bible = checkpoint.get("bible_operation_id")
        if not bible:
            bible = service.start_bible(project)
            checkpoint = {**read_checkpoint(checkpoint_path), "bible_operation_id": bible, "stage": "bible_pending"}
            write_checkpoint(checkpoint_path, checkpoint)
        bible_status = service.store.get("operations", bible)["status"]
        if bible_status not in {"completed", "failed", "partial"}:
            value = service.run(bible)
            bible_status = service.store.get("operations", bible)["status"]
            if value.get("status") in {"batch_pending", "response_pending", "submission_unknown"} or bible_status in {"preparing", "running", "response_pending", "submission_unknown"}:
                raise RemotePending("comic-bible")
        if bible_status != "completed":
            raise RuntimeError("Bible nebyla dokončena")
        panel = checkpoint.get("panel_id")
        if not panel:
            panel = service.store.panel(project, "Live panel")
            service.store.save_panel(
                panel, 2, "Live panel",
                {"version": 1, "nodes": [{"type": "text", "text": "A simple clean comic panel: a blue circle centered on a plain white background. No text."}]},
                {"width": 816, "height": 816, "dpi": 300, "fit": "pad", "experimental": False}, [],
            )
            checkpoint = {**read_checkpoint(checkpoint_path), "panel_id": panel, "stage": "panel_prepared"}
            write_checkpoint(checkpoint_path, checkpoint)
        operation = checkpoint.get("panel_operation_id")
        if not operation:
            operation = service.start_panels(project, [panel])
            checkpoint = {**read_checkpoint(checkpoint_path), "panel_operation_id": operation, "stage": "panel_pending"}
            write_checkpoint(checkpoint_path, checkpoint)
        panel_status = service.store.get("operations", operation)["status"]
        if panel_status not in {"completed", "failed", "partial"}:
            value = service.run(operation)
            panel_status = service.store.get("operations", operation)["status"]
            if value.get("status") in {"batch_pending", "response_pending", "submission_unknown"} or panel_status in {"preparing", "running", "response_pending", "submission_unknown", "batch_pending"}:
                raise RemotePending("comic-panel")
        if panel_status != "completed":
            raise RuntimeError("Panelová dávka nebyla dokončena")
        version = service.store.get("panels", panel)["active_version"]
        asset = service.store.get("panel_versions", version)["asset_id"]
        report["batch_ids"] = [row["provider_id"] for row in service.store.rows("batches", "operation_id=?", (operation,)) if row.get("provider_id")]
        report["artifact_sha256"] = [sha256_file(service.store.asset_path(asset))]
        report["technical_validation"] = "passed"
        report["content_acceptance"] = "unverified"
        report["human_review_required"] = True
        report["status"] = "technical_pass_human_pending"
        write_checkpoint(checkpoint_path, {**read_checkpoint(checkpoint_path), "stage": "completed"})
    except RemotePending:
        raise
    except Blocked:
        raise
    except Exception as exc:
        report["status"] = "failed"
        report["errors"].append(str(exc))
    finally:
        report["finished_at"] = now()
    return report


def write_evidence(
    root: Path,
    reports: list[dict[str, Any]],
    main_sha: str,
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    summary = {
        "main_sha": main_sha,
        "authorization": AUTHORIZATION,
        "provider_response_count": sum(
            len(r.get("provider_response_ids", [])) for r in reports
        ),
        "batch_count": sum(len(r.get("batch_ids", [])) for r in reports),
        "artifact_count": sum(len(r.get("artifact_sha256", [])) for r in reports),
    }
    (root / "live_acceptance_report.json").write_text(
        json.dumps(
            {"summary": summary, "cases": reports},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    lines = [
        "# KájovoNG live acceptance",
        "",
        f"Main SHA: `{main_sha}`",
        "",
        "| Case | Backend | Human review |",
        "|---|---|---|",
    ]
    for report in reports:
        lines.append(
            f"| {report['case']} | {report['status']} | "
            f"{report['human_review_required']} |"
        )
    lines.extend(["", json.dumps(summary, ensure_ascii=False, indent=2)])
    (root / "live_acceptance_summary.md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )


def cleanup_remote_inputs(client, reports: list[dict[str, Any]]) -> None:
    """Po uzavření odstraní jen vstupní uploady; provider evidence zůstává v reportu."""
    for report in reports:
        if report.get("status") not in {"passed", "technical_pass_human_pending"}:
            continue
        for file_id in report.get("input_file_ids", []):
            if file_id:
                try:
                    client.delete_file(str(file_id))
                except Exception as exc:
                    report.setdefault("errors", []).append(f"remote cleanup: {type(exc).__name__}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm-paid", action="store_true")
    parser.add_argument("--authorization")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--case", choices=CASES, action="append")
    args = parser.parse_args(argv)
    try:
        key = safety_gate(args.confirm_paid, args.authorization)
    except Blocked as exc:
        print(str(exc), file=sys.stderr)
        return 2
    cases = tuple(args.case or CASES)
    from kajovo.core.config import AppSettings
    from kajovo.core.openai_client import OpenAIClient

    root = args.out.resolve()
    root.mkdir(parents=True, exist_ok=True)
    settings = AppSettings(
        log_dir=str(root / "_runtime" / "LOG"),
        cache_dir=str(root / "_runtime" / "cache"),
    )
    client = OpenAIClient(key, timeout_s=600)
    main_sha = os.environ.get("KAJOVONG_MAIN_SHA", "")
    if not main_sha:
        try:
            main_sha = subprocess.check_output(
                ["git", "rev-parse", "HEAD"],
                cwd=ROOT,
                text=True,
                stderr=subprocess.DEVNULL,
            ).strip()
        except (OSError, subprocess.SubprocessError):
            main_sha = "unknown"
    try:
        ensure_workspace(root, main_sha)
        available = _model_check(client)
        reports: list[dict[str, Any]] = []
        for case in cases:
            if case == "generate-live":
                report = run_generate(
                    case, root, client, key, settings, main_sha, False
                )
                usage_root = acceptance_run_dir(root, case)
            elif case == "generate-batch":
                report = run_generate(
                    case, root, client, key, settings, main_sha, True
                )
                usage_root = acceptance_run_dir(root, case)
            elif case == "photo-batch":
                choose_image_model(available)
                report = run_photo(
                    root, client, settings, main_sha, available
                )
                usage_root = (
                    Path(settings.log_dir)
                    / "PHOTO"
                    / str(report.get("job_id") or "")
                )
            else:
                report = run_comic(
                    root, client, settings, main_sha, available
                )
                usage_root = root / "comic-panels"

            collect_raw_usage(usage_root, report)
            enrich_ids(usage_root, report)
            reports.append(report)
            root_state = read_checkpoint(root / "acceptance_state.json")
            root_state.setdefault("cases", {})[case] = {
                "status": report.get("status"),
                "identity": (
                    report.get("batch_ids")
                    or report.get("provider_response_ids")
                    or report.get("job_id")
                ),
            }
            write_checkpoint(root / "acceptance_state.json", root_state)
            write_evidence(root, reports, main_sha)
        cleanup_remote_inputs(client, reports)
        write_evidence(root, reports, main_sha)
        if any(
            r["status"] == "technical_pass_human_pending" for r in reports
        ):
            return 4
        return 0 if all(r["status"] == "passed" for r in reports) else 1
    except RemotePending as exc:
        print(f"REMOTE_PENDING: {exc}", file=sys.stderr)
        return 3
    except Blocked as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"ACCEPTANCE_ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
