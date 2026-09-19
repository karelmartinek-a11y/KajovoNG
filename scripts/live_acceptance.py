"""Explicitní, izolovaná a rozpočtově omezená live akceptace OpenAI cest.

Tento modul se nikdy nespouští z pytestu, CI ani startu aplikace. Generativní
klient vznikne až po dvojitém autorizačním gate a API klíč se do evidence
nikdy nezapisuje.
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
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

AUTHORIZATION = "KAJOVONG-LIVE-ACCEPTANCE-2026-09-20-MAX-USD-1.00"
MODEL = "gpt-5.6-luna"
MAX_TOTAL_USD = 1.00
CASES = ("generate-live", "generate-batch", "photo-batch", "comic-panels")


class Blocked(RuntimeError):
    """Akceptaci nelze bezpečně spustit nebo pokračovat v placené práci."""


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


def safety_gate(confirm_paid: bool, authorization: str | None, max_total_usd: float) -> str:
    """Vrátí klíč až po všech kontrolách; předtím se klient nevytváří."""
    if not confirm_paid or authorization != AUTHORIZATION:
        raise Blocked("BLOCKED_AUTHORIZATION")
    if max_total_usd <= 0 or max_total_usd > MAX_TOTAL_USD:
        raise Blocked("BLOCKED_BUDGET")
    key = os.environ.get("OPENAI_API_KEY", "")
    if not key.strip():
        raise Blocked("BLOCKED_API_KEY")
    return key


class Budget:
    def __init__(self, limit: float):
        self.limit = limit
        self.cost = 0.0
        self.paid_requests = 0
        self.text_requests = 0
        self.batch_requests = 0
        self.image_items = 0
        self.unknown: list[str] = []
        self.seen_usage: set[str] = set()

    def before(self, *, known_max: float | None, label: str) -> None:
        if self.unknown:
            raise Blocked("BLOCKED_BUDGET")
        if known_max is None:
            self.unknown.append(label)
            return
        if self.cost + known_max > self.limit:
            raise Blocked("BLOCKED_BUDGET")

    def add(self, usd: float | None, *, text=False, batch=False, images=0) -> None:
        self.paid_requests += 1
        self.text_requests += int(text)
        self.batch_requests += int(batch)
        self.image_items += images
        if usd is None:
            self.unknown.append("provider usage/cena nebyla úplná")
        else:
            self.cost += usd
        if self.cost > self.limit:
            raise Blocked("BLOCKED_BUDGET")


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
        max_cost_microusd=1_000_000,
        max_input_tokens=100_000,
        max_output_tokens=20_000,
        max_paid_requests=8,
        unknown_pricing="block",
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


def _usage_cost(usage: Any, model: str, *, batch: bool) -> float | None:
    from kajovo.core.context_pricing import projected_cost

    if not isinstance(usage, dict):
        return None
    return_value = projected_cost(model, usage.get("input_tokens"), usage.get("output_tokens"), batch=batch)
    return float(return_value["usd"]) if return_value else None


def _find_usage(value: Any, model: str = "", identity: str = ""):
    if isinstance(value, dict):
        identifiers = [
            str(value[key]) for key in (
                "id", "response_id", "provider_item_id", "reservation_id", "custom_id", "batch_id"
            ) if value.get(key)
        ]
        current_identity = ":".join(identifiers) or identity
        usage = value.get("usage")
        if isinstance(usage, dict):
            yield model or str(value.get("model") or "") or MODEL, usage, current_identity
        child_model = str(value.get("model") or model)
        for child in value.values():
            yield from _find_usage(child, child_model, current_identity)
    elif isinstance(value, list):
        for child in value:
            yield from _find_usage(child, model, identity)


def settle_evidence(root: Path, budget: Budget, report: dict[str, Any], *, image_model: str | None = None) -> None:
    """Sečte jen doložené usage; při neúplném usage zachová UNKNOWN."""
    seen: set[str] = set()
    found_usage = False
    for path in root.rglob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for model, usage, identity in _find_usage(payload):
            found_usage = True
            key = sha256_bytes(json.dumps(
                {"identity": identity, "model": model, "usage": usage},
                sort_keys=True,
            ).encode())
            if key in seen or key in budget.seen_usage:
                continue
            seen.add(key)
            budget.seen_usage.add(key)
            if image_model and model == image_model:
                from kajovo.core.context_pricing import observed_image_cost

                cost = observed_image_cost(model, usage, batch=True)
                usd = float(cost["usd"]) if cost else None
                report["image_usage"].append(usage)
                budget.add(usd, batch=True, images=1)
            else:
                usd = _usage_cost(usage, model or MODEL, batch=report["execution"] == "batch")
                report["token_usage"].append(usage)
                budget.add(usd, text=True, batch=report["execution"] == "batch")
    if not found_usage:
        budget.unknown.append(f"{report['case']}: provider usage nebylo nalezeno")


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
        "batch_ids": [], "input_file_ids": [], "output_file_ids": [], "paid_request_count": 0,
        "text_requests": 0, "batch_requests": 0, "image_items": 0, "token_usage": [],
        "image_usage": [], "settled_cost_usd": None, "unknown_cost": False,
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


def _state_report(report: dict[str, Any], budget: Budget, *, cost_before: float = 0.0, paid_before: int = 0, text_before: int = 0, batch_before: int = 0, image_before: int = 0) -> None:
    report["paid_request_count"] = budget.paid_requests - paid_before
    report["text_requests"] = budget.text_requests - text_before
    report["batch_requests"] = budget.batch_requests - batch_before
    report["image_items"] = budget.image_items - image_before
    report["settled_cost_usd"] = round(budget.cost - cost_before, 8) if not budget.unknown else None
    report["unknown_cost"] = bool(budget.unknown)
    report["unknown_cost_items"] = list(budget.unknown)


def run_generate(case: str, root: Path, client, key: str, settings, budget: Budget, main_sha: str, batch: bool) -> dict[str, Any]:
    from kajovo.core.runlog import RunLogger
    from kajovo.core.batch_completion import complete_saved_batch, read_state

    out = root / case / "OUT"
    out.mkdir(parents=True, exist_ok=True)
    report = _base_report(case, main_sha, MODEL)
    report["execution"] = "batch" if batch else "live"
    prompt = (
        "Create exactly one file named answer.py. The file must define one top-level function "
        "answer() with no parameters. The function must return the integer literal 42. "
        "Do not create any other project files."
        if not batch else
        "Create exactly one file named batch_answer.py. It must define one top-level function "
        "batch_answer() with no parameters. The function must return the integer literal 7. "
        "Do not create any other project files."
    )
    run_id = "RUN_LIVE_ACCEPTANCE_" + case.replace("-", "_")
    run_dir = Path(settings.log_dir) / run_id
    state = read_state(run_dir) if run_dir.exists() else {}
    logger = RunLogger(settings.log_dir, run_id, project_name="KájovoNG live acceptance", resume=bool(state))
    try:
        if not state.get("batch_id"):
            cfg = make_run_config(batch=batch, prompt=prompt, out=out)
            result, error = _run_executor(cfg, settings, key, logger)
            if error:
                state = read_state(run_dir)
                if state.get("status") in {"response_pending", "submission_unknown"}:
                    report["status"] = "pending"
                    report["errors"] = [state["status"]]
                    raise RemotePending(state["status"])
                raise RuntimeError(error)
            result = result or {}
            report["provider_response_ids"] = [x for x in (result.get("response_id"), result.get("last_response_id")) if x]
            if batch:
                if not result.get("batch_id"):
                    raise RuntimeError("GENERATE BATCH nevrátil batch_id")
                report["batch_ids"] = [result["batch_id"]]
                report["input_file_ids"] = [result.get("input_file_id")] if result.get("input_file_id") else []
            else:
                target = _generated_path(run_dir, out, "answer.py")
                report["artifact_sha256"] = [_validate_generated(target, "answer", 42)]
                report["technical_validation"] = "passed"
                report["content_acceptance"] = "passed"
        if batch:
            bid = (read_state(run_dir).get("batch_id") or report["batch_ids"][0])
            while True:
                completed = complete_saved_batch(client, str(run_dir), bid, settings)
                if completed.get("status") == "batch_pending":
                    report["status"] = "pending"
                    report["errors"] = ["REMOTE_PENDING"]
                    raise RemotePending(bid)
                if completed.get("status") not in {"files_complete_unverified", "completed"}:
                    raise RuntimeError(json.dumps(completed, ensure_ascii=False))
                state = read_state(run_dir)
                target = _generated_path(run_dir, out, "batch_answer.py")
                report["artifact_sha256"] = [_validate_generated(target, "batch_answer", 7)]
                report["technical_validation"] = "passed"
                report["content_acceptance"] = "passed"
                break
        report["status"] = "passed"
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


def run_photo(root: Path, client, settings, budget: Budget, main_sha: str, available: list[str]) -> dict[str, Any]:
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


def run_comic_legacy(root: Path, client, settings, budget: Budget, main_sha: str, available: list[str]) -> dict[str, Any]:
    from kajovo.core.comic_service import ComicService
    from kajovo.core.config import AppSettings

    case_root = root / "comic-panels"
    comic_settings = AppSettings(comic_library_dir=str(case_root / "COMICS"), log_dir=str(case_root / "LOG"), response_timeout_s=600, response_poll_timeout_s=3600, batch_poll_interval_s=5, batch_timeout_s=3600)
    service = ComicService(comic_settings, client)
    report = _base_report("comic-panels", main_sha, [MODEL, "gpt-image-2.5-sunburst-2026-09-08"])
    try:
        if "gpt-image-2.5-sunburst-2026-09-08" not in available and "gpt-image-2.5-sunburst" not in available:
            raise Blocked("BLOCKED_IMAGE_MODEL_UNAVAILABLE")
        project = service.store.project("Live acceptance", "Jednoduchý syntetický panel", {"description": "Čistý komiks", "line": "jemná", "color": "barevná", "balloon": "dialogová", "sfx": False})
        bible = service.start_bible(project)
        while service.store.get("operations", bible)["status"] not in {"completed", "failed", "partial"}:
            value = service.run(bible)
            if value.get("status") in {"batch_pending", "response_pending", "submission_unknown"}:
                raise RemotePending("comic-bible")
        if service.store.get("operations", bible)["status"] != "completed":
            raise RuntimeError("Bible nebyla dokončena")
        panel = service.store.panel(project, "Live panel")
        service.store.save_panel(panel, 2, "Live panel", {"version": 1, "nodes": [{"type": "text", "text": "A simple clean comic panel: a blue circle centered on a plain white background. No text."}]}, {"width": 816, "height": 816, "dpi": 300, "fit": "pad", "experimental": False}, [])
        operation = service.start_panels(project, [panel])
        while service.store.get("operations", operation)["status"] not in {"completed", "failed", "partial"}:
            value = service.run(operation)
            if value.get("status") in {"batch_pending", "response_pending", "submission_unknown"}:
                raise RemotePending("comic-panel")
        if service.store.get("operations", operation)["status"] != "completed":
            raise RuntimeError("Panelová dávka nebyla dokončena")
        version = service.store.get("panels", panel)["active_version"]
        asset = service.store.get("panel_versions", version)["asset_id"]
        path = service.store.asset_path(asset)
        report["batch_ids"] = [row["provider_id"] for row in service.store.rows("batches", "operation_id=?", (operation,)) if row.get("provider_id")]
        report["artifact_sha256"] = [sha256_file(path)]
        report["technical_validation"] = "passed"
        report["content_acceptance"] = "unverified"
        report["human_review_required"] = True
        report["status"] = "technical_pass_human_pending"
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


def run_comic(root: Path, client, settings, budget: Budget, main_sha: str, available: list[str]) -> dict[str, Any]:
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


def write_evidence(root: Path, reports: list[dict[str, Any]], budget: Budget, main_sha: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    summary = {
        "main_sha": main_sha, "authorization": AUTHORIZATION, "authorization_ceiling_usd": MAX_TOTAL_USD,
        "known_actual_cost_usd": round(budget.cost, 8) if not budget.unknown else None,
        "unknown_cost_items": budget.unknown, "total_paid_requests": budget.paid_requests,
        "text_requests": budget.text_requests, "batch_requests": budget.batch_requests,
        "image_items": budget.image_items, "total_batches_created": sum(len(r.get("batch_ids", [])) for r in reports),
        "total_images_generated_or_edited": sum(r.get("image_items", 0) for r in reports),
    }
    (root / "live_acceptance_report.json").write_text(json.dumps({"summary": summary, "cases": reports}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = ["# KájovoNG live acceptance", "", f"Main SHA: `{main_sha}`", f"Known cost USD: `{summary['known_actual_cost_usd'] if summary['known_actual_cost_usd'] is not None else 'TOTAL_COST_NOT_FULLY_KNOWN'}`", "", "| Case | Backend | Human review |", "|---|---|---|"]
    for report in reports:
        lines.append(f"| {report['case']} | {report['status']} | {report['human_review_required']} |")
    lines.extend(["", f"Authorization ceiling: ${MAX_TOTAL_USD:.2f}", "Ceiling exceeded: NO", "", json.dumps(summary, ensure_ascii=False, indent=2)])
    (root / "live_acceptance_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


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
    parser.add_argument("--max-total-usd", type=float, default=MAX_TOTAL_USD)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--case", choices=CASES, action="append")
    args = parser.parse_args(argv)
    try:
        key = safety_gate(args.confirm_paid, args.authorization, args.max_total_usd)
    except Blocked as exc:
        print(str(exc), file=sys.stderr)
        return 2
    cases = tuple(args.case or CASES)
    from kajovo.core.config import AppSettings
    from kajovo.core.openai_client import OpenAIClient

    root = args.out.resolve()
    root.mkdir(parents=True, exist_ok=True)
    settings = AppSettings(log_dir=str(root / "_runtime" / "LOG"), cache_dir=str(root / "_runtime" / "cache"))
    client = OpenAIClient(key, timeout_s=600)
    main_sha = os.environ.get("KAJOVONG_MAIN_SHA", "unknown")
    try:
        available = _model_check(client)
        budget = Budget(args.max_total_usd)
        reports: list[dict[str, Any]] = []
        for case in cases:
            before = {
                "cost": budget.cost, "paid": budget.paid_requests,
                "text": budget.text_requests, "batch": budget.batch_requests,
                "image": budget.image_items,
            }
            if case == "generate-live":
                budget.before(known_max=0.20, label=case)
                report = run_generate(case, root, client, key, settings, budget, main_sha, False)
                settle_evidence(root / "_runtime" / "LOG" / "RUN_LIVE_ACCEPTANCE_generate_live", budget, report)
            elif case == "generate-batch":
                budget.before(known_max=0.10, label=case)
                report = run_generate(case, root, client, key, settings, budget, main_sha, True)
                settle_evidence(root / "_runtime" / "LOG" / "RUN_LIVE_ACCEPTANCE_generate_batch", budget, report)
            elif case == "photo-batch":
                choose_image_model(available)
                budget.before(known_max=0.50, label=case)
                report = run_photo(root, client, settings, budget, main_sha, available)
                settle_evidence(Path(settings.log_dir) / "PHOTO" / str(report.get("job_id") or ""), budget, report, image_model=report["models"])
            else:
                budget.before(known_max=0.50, label=case)
                report = run_comic(root, client, settings, budget, main_sha, available)
                settle_evidence(root / "comic-panels", budget, report, image_model="gpt-image-2.5-sunburst-2026-09-08")
            _state_report(
                report, budget, cost_before=before["cost"], paid_before=before["paid"],
                text_before=before["text"], batch_before=before["batch"], image_before=before["image"],
            )
            enrich_ids(root, report)
            reports.append(report)
            write_evidence(root, reports, budget, main_sha)
        cleanup_remote_inputs(client, reports)
        write_evidence(root, reports, budget, main_sha)
        return 4 if any(r["status"] == "technical_pass_human_pending" for r in reports) else 0 if all(r["status"] == "passed" for r in reports) else 1
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
