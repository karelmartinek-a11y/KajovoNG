"""BATCH workflow pro editaci fotografií přes /v1/images/edits."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import tempfile
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Iterable

from .batch_submit import exact_batch_matches
from .comic_types import IMAGE_MODEL, ComicError
from .image_runtime import inspect_image
from .orchestration.contracts import canonical_sha256
from .orchestration.errors import OrchestrationError
from .orchestration.image_slots import image_policy
from .orchestration.repository import OrchestrationRepository
from .orchestration.run_config import build_run_config_v2
from .orchestration.work_order import freeze_order
from .model_registry import model_spec, models_for_usage
from .photo_prompt import manual_photo_plan
from .utils import atomic_write_text

IMAGE_EDIT_ENDPOINT = "/v1/images/edits"
SUPPORTED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}


@dataclass
class PhotoBatchItem:
    item_id: str
    custom_id: str
    source_path: str
    source_name: str
    source_sha256: str
    uploaded_file_id: str = ""
    status: str = "pending"
    output_path: str = ""
    output_sha256: str = ""
    output_width: int = 0
    output_height: int = 0
    output_format_detected: str = ""
    technical_validation: str = "pending"
    content_acceptance: str = "unverified"
    content_acceptance_note: str = ""
    error_message: str = ""


@dataclass
class PhotoBatchJob:
    schema_version: int
    job_id: str
    created_at: str
    updated_at: str
    status: str
    human_prompt: str
    professional_prompt: str
    final_prompt: str
    final_prompt_sha256: str
    prompt_source: str
    template_id: str
    prompt_model: str
    prompt_response_id: str
    image_model: str
    quality: str
    size: str
    output_format: str
    output_dir: str
    photo_plan: dict = field(default_factory=dict)
    photo_plan_sha256: str = ""
    input_file_id: str = ""
    batch_id: str = ""
    output_file_id: str = ""
    error_file_id: str = ""
    request_total: int = 0
    request_completed: int = 0
    request_failed: int = 0
    items: list[PhotoBatchItem] = field(default_factory=list)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _endpoints(spec: dict) -> set[str]:
    return {
        entry[1]
        for entry in spec.get("endpoints", [])
        if isinstance(entry, list) and len(entry) >= 2
    }


def _is_image_edit_model(model: str) -> bool:
    """Povolí jen skutečné image-edit modely, ne obecné modely se stejným endpointem.

    Pevná matice označuje GPT Image modely schopné inpaint/edit funkcí feature
    `inpainting`. Samotná přítomnost `v1/images/edits` v dokumentovaném seznamu
    endpointů nestačí: obecné Responses modely mohou mít endpoint v dokumentaci,
    ale nejsou platnou hodnotou parametru model pro Image API.
    """
    spec = model_spec(model)
    image_capabilities = spec.get("image_capabilities") or {}
    image_batch = bool(image_capabilities.get("batch", spec["batch"]))
    return (
        not spec["deprecated"]
        and image_batch
        and "inpainting" in spec["features"]
        and IMAGE_EDIT_ENDPOINT.lstrip("/") in _endpoints(spec)
    )


def image_edit_model_ids(available_models: Iterable[str] | None = None) -> list[str]:
    return models_for_usage(available_models, "photo_edit_batch")


def response_prompt_models(available_models: Iterable[str] | None = None) -> list[str]:
    return models_for_usage(available_models, "photo_prompt")


def inspect_photo_bytes(data: bytes, expected_format: str | None = None, *, model=IMAGE_MODEL) -> dict:
    """Společná technická validace PHOTO/COMIC; base64 samo není důkaz obrazu."""
    try:
        info = inspect_image(data, model)
    except ComicError as exc:
        raise OrchestrationError("IMAGE_INVALID", str(exc)) from exc
    expected = {"png": "PNG", "jpeg": "JPEG", "jpg": "JPEG", "webp": "WEBP"}.get(
        str(expected_format or "").lower()
    )
    if expected and info["format"] != expected:
        raise OrchestrationError("IMAGE_INVALID", f"Výsledek má formát {info['format']}, očekáván byl {expected}.")
    return {key: info[key] for key in ("format", "width", "height")}


def validate_source_image(path: str | Path) -> Path:
    source = Path(path).expanduser().resolve()
    if not source.is_file() or source.suffix.lower() not in SUPPORTED_IMAGE_EXTENSIONS:
        raise ValueError(f"Neplatná nebo nepodporovaná fotografie: {source}")
    if not 0 < source.stat().st_size <= image_policy()["max_input_bytes_policy"]:
        raise ValueError(f"Neplatná velikost fotografie: {source.name}")
    expected = {
        ".png": "png",
        ".jpg": "jpeg",
        ".jpeg": "jpeg",
        ".webp": "webp",
    }[source.suffix.lower()]
    inspect_photo_bytes(source.read_bytes(), expected)
    return source


def make_items(paths: Iterable[str]) -> list[PhotoBatchItem]:
    items: list[PhotoBatchItem] = []
    seen: set[str] = set()
    for index, raw in enumerate(paths, 1):
        source = validate_source_image(raw)
        key = os.path.normcase(str(source))
        if key in seen:
            continue
        seen.add(key)
        items.append(
            PhotoBatchItem(
                "photo_" + uuid.uuid4().hex,
                f"photo-{index:05d}-{uuid.uuid4().hex[:10]}",
                str(source),
                source.name,
                _hash_file(source),
            )
        )
    if not items:
        raise ValueError("Nejsou vybrané žádné fotografie.")
    return items


def image_edit_row(item, *, model, prompt, quality, size, output_format) -> dict:
    if not item.uploaded_file_id:
        raise ValueError(f"{item.source_name}: chybí file_id.")
    return {
        "custom_id": item.custom_id,
        "method": "POST",
        "url": IMAGE_EDIT_ENDPOINT,
        "body": {
            "model": model,
            "images": [{"file_id": item.uploaded_file_id}],
            "prompt": prompt,
            "n": 1,
            "size": size,
            "quality": quality,
            "output_format": output_format,
            "background": "auto",
        },
    }


def image_edit_options(model: str) -> dict:
    """Lokální omezení podle oficiální specifikace rodiny GPT Image."""
    if not _is_image_edit_model(model):
        raise ValueError("Vybraný model nepodporuje dávkové úpravy fotografií.")
    modern = model.startswith("gpt-image-2")
    extended = model.startswith("gpt-image-2.5-")
    return {
        "quality": ["auto", "low", "medium", "high", *(["xhigh", "max"] if extended else [])],
        "sizes": ["auto", "1024x1024", "1536x1024", "1024x1536",
                  *(["2048x2048", "2048x1152", "1152x2048", "3840x2160", "2160x3840"] if modern else [])],
        "custom_size": modern,
        "source": "https://developers.openai.com/api/docs/guides/image-generation",
    }


def validate_image_edit_parameters(model, quality, size, output_format):
    options = image_edit_options(model)
    if quality not in options["quality"]:
        raise ValueError("Zvolená kvalita není podporována vybraným obrazovým modelem.")
    if output_format not in {"png", "jpeg", "webp"}:
        raise ValueError("Zvolený formát fotografie není podporován.")
    if size == "auto":
        return
    if not options["custom_size"]:
        if size not in options["sizes"]:
            raise ValueError("Zvolené rozměry nejsou podporovány vybraným obrazovým modelem.")
        return
    if not isinstance(size, str) or not re.fullmatch(r"\d{2,5}x\d{2,5}", size):
        raise ValueError("Rozměry zadejte jako šířkaxvýška v pixelech.")
    width, height = map(int, size.split("x"))
    if not (width % 16 == 0 and height % 16 == 0 and max(width, height) <= 3840
            and max(width, height) <= 3 * min(width, height)
            and 655360 <= width * height <= 8294400):
        raise ValueError("Rozměry musí být násobky šestnácti, nejvýše 3840 pixelů na hranu, s poměrem nejvýše tři ku jedné a plochou 655 360 až 8 294 400 pixelů.")


def validate_image_edit_rows(rows: Iterable[dict]) -> list[dict]:
    rows = list(rows)
    ids: set[str] = set()
    models: set[str] = set()
    if not 1 <= len(rows) <= 50000:
        raise ValueError("Image Edit BATCH vyžaduje 1 až 50 000 řádků.")
    required = {
        "model",
        "images",
        "prompt",
        "n",
        "size",
        "quality",
        "output_format",
        "background",
    }
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"custom_id", "method", "url", "body"}:
            raise ValueError("Neplatný řádek Image Edit BATCH.")
        cid = row["custom_id"]
        if (
            not isinstance(cid, str)
            or not cid
            or cid in ids
            or row["method"] != "POST"
            or row["url"] != IMAGE_EDIT_ENDPOINT
        ):
            raise ValueError("Neplatné custom_id, metoda nebo endpoint Image Edit BATCH.")
        ids.add(cid)
        body = row["body"]
        if not isinstance(body, dict) or set(body) != required:
            raise ValueError("Neplatné parametry Image Edit BATCH.")
        model = str(body["model"])
        if not _is_image_edit_model(model):
            raise ValueError(f"{model}: pevná matice nepovoluje Image Edit BATCH model.")
        models.add(model)
        validate_image_edit_parameters(model, body["quality"], body["size"], body["output_format"])
        images = body["images"]
        if (
            not isinstance(images, list)
            or len(images) != 1
            or not isinstance(images[0], dict)
            or set(images[0]) != {"file_id"}
            or not re.fullmatch(r"[A-Za-z0-9_-]+", str(images[0]["file_id"]))
        ):
            raise ValueError("Každý řádek vyžaduje právě jeden platný file_id.")
        if not isinstance(body["prompt"], str) or not body["prompt"].strip() or body["n"] != 1:
            raise ValueError("Image Edit vyžaduje prompt a n=1.")
        if body["quality"] not in {"auto", "low", "medium", "high", "xhigh", "max"}:
            raise ValueError("Neplatná kvalita Image Edit.")
        if body["output_format"] not in {"png", "jpeg", "webp"}:
            raise ValueError("Neplatný formát Image Edit.")
        if not (
            body["size"] == "auto"
            or re.fullmatch(r"\d{2,5}x\d{2,5}", str(body["size"]))
        ):
            raise ValueError("Neplatná velikost Image Edit.")
    if len(models) != 1:
        raise ValueError("Jeden Image Edit BATCH smí obsahovat právě jeden model.")
    return rows


class ImageEditBatchAdapter:
    """Lokálně ověřený endpointový adaptér; právě jeden pracovní POST /batches."""

    def __init__(self, client):
        self.client = client

    def submit(self, input_file_id: str, rows: Iterable[dict]) -> dict:
        self.client._validate_resource_id(input_file_id)
        verified = validate_image_edit_rows(rows)
        if not verified:
            raise ValueError("Pracovní Image Edit BATCH nemá žádné řádky.")
        return self.client._req(
            "POST",
            "/batches",
            json_body={
                "input_file_id": input_file_id,
                "endpoint": IMAGE_EDIT_ENDPOINT,
                "completion_window": "24h",
            },
            max_attempts=1,
        )


def copy_photo_plan(value, final_prompt: str) -> dict:
    if not isinstance(value, dict) or value.get("version") != 1:
        raise ValueError("PHOTO_PLAN_V1 má neplatnou verzi.")
    required = {
        "version",
        "professional_prompt",
        "edit_actions",
        "preserve_invariants",
        "acceptance_criteria",
    }
    if set(value) != required:
        raise ValueError("PHOTO_PLAN_V1 má neplatná pole.")
    if str(value["professional_prompt"]).strip() != final_prompt.strip():
        raise ValueError("PHOTO_PLAN_V1 neodpovídá finálnímu promptu.")
    for key in ("edit_actions", "preserve_invariants", "acceptance_criteria"):
        rows = value[key]
        if not isinstance(rows, list) or any(
            not isinstance(item, str) or not item.strip() for item in rows
        ):
            raise ValueError(f"PHOTO_PLAN_V1.{key} musí být seznam neprázdných textů.")
    if not value["edit_actions"] or not value["acceptance_criteria"]:
        raise ValueError("PHOTO_PLAN_V1 vyžaduje edit_actions a acceptance_criteria.")
    return json.loads(
        json.dumps(value, ensure_ascii=False, sort_keys=True)
    )


def new_job(
    *,
    source_paths,
    human_prompt,
    professional_prompt,
    final_prompt,
    prompt_source,
    template_id,
    prompt_model,
    prompt_response_id,
    image_model,
    quality,
    size,
    output_format,
    output_dir,
    photo_plan=None,
):
    prompt = final_prompt.strip()
    if not prompt:
        raise ValueError("Prompt je prázdný.")
    if not _is_image_edit_model(image_model):
        raise ValueError(f"{image_model}: model není povolený pro Image Edit BATCH.")
    validate_image_edit_parameters(image_model, quality, size, output_format)
    output = Path(output_dir).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    plan = copy_photo_plan(photo_plan or manual_photo_plan(prompt), prompt)
    plan_hash = hashlib.sha256(
        json.dumps(
            plan,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    stamp = _now()
    items = make_items(source_paths)
    return PhotoBatchJob(
        2,
        "photojob_" + uuid.uuid4().hex,
        stamp,
        stamp,
        "preparing",
        human_prompt,
        professional_prompt,
        prompt,
        hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        prompt_source,
        template_id,
        prompt_model,
        prompt_response_id,
        image_model,
        quality,
        size,
        output_format,
        str(output),
        photo_plan=plan,
        photo_plan_sha256=plan_hash,
        request_total=len(items),
        items=items,
    )


def save_job(job: PhotoBatchJob, log_dir: str | Path) -> Path:
    root = Path(log_dir) / "PHOTO" / job.job_id
    root.mkdir(parents=True, exist_ok=True)
    job.updated_at = _now()
    atomic_write_text(
        str(root / "photo_job.json"),
        json.dumps(asdict(job), ensure_ascii=False, indent=2) + "\n",
    )
    return root


def load_jobs(log_dir: str | Path) -> list[PhotoBatchJob]:
    jobs: list[PhotoBatchJob] = []
    root = Path(log_dir) / "PHOTO"
    if not root.exists():
        return jobs
    for path in sorted(
        root.glob("photojob_*/photo_job.json"),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    ):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            data["items"] = [PhotoBatchItem(**item) for item in data.get("items", [])]
            if not data.get("photo_plan"):
                data["photo_plan"] = manual_photo_plan(data.get("final_prompt", ""))
            if not data.get("photo_plan_sha256"):
                data["photo_plan_sha256"] = hashlib.sha256(
                    json.dumps(
                        data["photo_plan"],
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest()
            jobs.append(PhotoBatchJob(**data))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
    return jobs


def _photo_cfg(job):
    return SimpleNamespace(
        mode="PHOTO",
        model=job.image_model,
        send_as_c=True,
        maximum_quality=job.quality in {"xhigh", "max"},
        auto_repair="off",
        verification_profile_ids=[],
        stop_after_plan=False,
        dry_run=False,
        execution_approval_id=f"user-start:{job.job_id}",
        project="Photo Studio",
        prompt=job.final_prompt,
        in_dir="",
        out_dir=job.output_dir,
        attached_file_ids=[],
        input_file_ids=[],
        attached_vector_store_ids=[],
        qfile_output_path="",
        qfile_output_format="",
        qfile_suggest_path=False,
        qa_continue_conversation=False,
        response_id="",
    )


def _photo_repo(log_dir):
    return OrchestrationRepository(Path(log_dir) / "orchestration.sqlite3")


def _photo_work_order(job, rows):
    cfg = _photo_cfg(job)
    projection = {
        "photo_plan_sha256": job.photo_plan_sha256,
        "items": [
            {
                "custom_id": row["custom_id"],
                "body_sha256": canonical_sha256(row["body"]),
            }
            for row in rows
        ],
        "source_sha256": [
            item.source_sha256 for item in job.items
        ],
    }
    order = freeze_order(
        cfg,
        {
            "run_id": job.job_id,
            "step_id": "PHOTO_BATCH_SUBMIT",
            "task_id": "PHOTO_BATCH_SUBMIT",
            "stage": "PHOTO",
            "route": "image_batch",
            "target_id": job.job_id,
            "target_path": None,
            "expected_target_hash": None,
            "contract_name": "PHOTO_BATCH_V1",
            "schema": {
                "endpoint": IMAGE_EDIT_ENDPOINT,
                "row_count": len(rows),
                "photo_plan_sha256": job.photo_plan_sha256,
            },
            "prompt": job.final_prompt,
            "model": job.image_model,
            "model_capability": model_spec(job.image_model),
            "source_snapshot": projection,
            "attempt_no": 1,
            "approval_id": cfg.execution_approval_id,
        },
        projection,
    )
    return cfg, order, projection


def _prepare_photo_submit(job, rows, log_dir):
    cfg, order, projection = _photo_work_order(job, rows)
    repo = _photo_repo(log_dir)
    run_config = build_run_config_v2(cfg)
    repo.register_run(
        job.job_id,
        lineage_id=job.job_id,
        scope_hash=canonical_sha256(
            {
                "run_config_v2": run_config,
                "photo_plan_sha256": job.photo_plan_sha256,
                "sources": [item.source_sha256 for item in job.items],
                "output_dir": job.output_dir,
            }
        ),
        policy_hash=canonical_sha256(run_config),
        config=run_config,
        approval_id=order.approval_id,
        status="running",
    )
    persisted_hash = repo.register_work_order(
        order,
        body_ref=canonical_sha256(rows),
        input_hash=order.input_projection_hash,
    )
    repo.prepare_provider_operation(
        attempt_id=order.attempt_id,
        work_order_hash=persisted_hash,
        endpoint="/v1/batches",
        request_hash=canonical_sha256(rows),
    )
    if job.input_file_id:
        repo.set_remote_input_file(order.attempt_id, job.input_file_id)
    root = save_job(job, log_dir)
    atomic_write_text(
        str(root / "work_order_v2.json"),
        json.dumps(
            {**order.to_dict(), "order_hash": order.order_hash},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
    )
    return repo, order


def _photo_operation_present(job, log_dir):
    return (
        Path(log_dir)
        / "PHOTO"
        / job.job_id
        / "work_order_v2.json"
    ).is_file()


def _mark_photo_submission_started(job, rows, log_dir):
    if not _photo_operation_present(job, log_dir):
        return
    _cfg, order, _projection = _photo_work_order(job, rows)
    _photo_repo(log_dir).mark_submission_started(order.attempt_id)


def _mark_photo_submission(job, rows, log_dir, provider_id=None, *, unknown):
    if not _photo_operation_present(job, log_dir):
        return
    _cfg, order, _projection = _photo_work_order(job, rows)
    _photo_repo(log_dir).mark_submitted(
        order.attempt_id,
        provider_id,
        unknown=unknown,
    )


def _mark_photo_not_submitted(job, rows, log_dir):
    if not _photo_operation_present(job, log_dir):
        return
    _cfg, order, _projection = _photo_work_order(job, rows)
    _photo_repo(log_dir).mark_not_submitted(order.attempt_id)


def _record_photo_usage(job, custom_id, usage, log_dir):
    if (
        not job.batch_id
        or not isinstance(usage, dict)
        or not usage
        or not _photo_operation_present(job, log_dir)
    ):
        return
    rows = [
        image_edit_row(
            item,
            model=job.image_model,
            prompt=job.final_prompt,
            quality=job.quality,
            size=job.size,
            output_format=job.output_format,
        )
        for item in job.items
    ]
    _cfg, order, _projection = _photo_work_order(job, rows)
    provider_item_id = f"{job.batch_id}:{custom_id}"
    _photo_repo(log_dir).record_usage(
        order.attempt_id,
        provider="openai-image",
        provider_item_id=provider_item_id,
        usage=usage,
        raw_response_ref="batch-item:" + provider_item_id,
    )


def prepare_and_submit(client, job, log_dir, reporter=None, progress=None):
    validate_image_edit_parameters(job.image_model, job.quality, job.size, job.output_format)
    def report(text: str, pct: int) -> None:
        if reporter:
            reporter(text)
        if progress:
            progress(pct)

    for index, item in enumerate(job.items, 1):
        report(
            f"Nahrávám {index}/{len(job.items)}: {item.source_name}",
            int(index / len(job.items) * 70),
        )
        uploaded = client.upload_file(item.source_path, purpose="user_data")
        item.uploaded_file_id = str(uploaded.get("id") or "")
        item.status = "uploaded"
        client._validate_resource_id(item.uploaded_file_id)
        save_job(job, log_dir)

    rows = [
        image_edit_row(
            item,
            model=job.image_model,
            prompt=job.final_prompt,
            quality=job.quality,
            size=job.size,
            output_format=job.output_format,
        )
        for item in job.items
    ]
    validate_image_edit_rows(rows)
    root = save_job(job, log_dir)
    jsonl = root / "batch_input.jsonl"
    text = "".join(
        json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
        for row in rows
    )
    if len(text.encode("utf-8")) > 200_000_000:
        raise ValueError("Pracovní JSONL překračuje 200 MB.")
    atomic_write_text(str(jsonl), text)

    report("Nahrávám pracovní JSONL.", 82)
    batch_file = client.upload_file(str(jsonl), purpose="batch")
    job.input_file_id = str(batch_file.get("id") or "")
    client._validate_resource_id(job.input_file_id)
    save_job(job, log_dir)

    _prepare_photo_submit(job, rows, log_dir)
    _mark_photo_submission_started(job, rows, log_dir)
    report("Odesílám pracovní Image Edit BATCH.", 92)
    job.status = "submission_unknown"
    save_job(job, log_dir)
    try:
        submitted = ImageEditBatchAdapter(client).submit(job.input_file_id, rows)
    except Exception as exc:
        definite_reject = (
            getattr(exc, "request_sent", None) is False
            or getattr(exc, "status_code", None)
            in {400, 401, 403, 404, 422, 429}
        )
        if definite_reject:
            _mark_photo_not_submitted(job, rows, log_dir)
            job.status = "failed"
        else:
            _mark_photo_submission(
                job, rows, log_dir, None, unknown=True
            )
            job.status = "submission_unknown"
        save_job(job, log_dir)
        raise
    apply_batch_status(job, submitted)
    if not job.batch_id:
        _mark_photo_submission(job, rows, log_dir, None, unknown=True)
        job.status = "submission_unknown"
        save_job(job, log_dir)
        raise ValueError(
            "Image Edit BATCH nemá potvrzené provider ID; nový submit je zablokován."
        )
    client._validate_resource_id(job.batch_id)
    _mark_photo_submission(
        job, rows, log_dir, job.batch_id, unknown=False
    )
    save_job(job, log_dir)
    report(f"BATCH vytvořen: {job.batch_id}", 100)
    return job


def apply_batch_status(job: PhotoBatchJob, payload: dict) -> PhotoBatchJob:
    job.status = str(payload.get("status") or job.status)
    job.batch_id = str(payload.get("id") or job.batch_id)
    job.output_file_id = str(payload.get("output_file_id") or job.output_file_id)
    job.error_file_id = str(payload.get("error_file_id") or job.error_file_id)
    counts = payload.get("request_counts") or {}
    job.request_total = int(counts.get("total") or job.request_total or len(job.items))
    job.request_completed = int(counts.get("completed") or 0)
    job.request_failed = int(counts.get("failed") or 0)
    return job


def refresh_job(client, job: PhotoBatchJob, log_dir: str | Path) -> PhotoBatchJob:
    if not job.batch_id:
        if job.status != "submission_unknown" or not job.input_file_id:
            raise ValueError("Photo Job nemá batch_id.")
        matches = exact_batch_matches(
            client.list_batches(),
            job.input_file_id,
            IMAGE_EDIT_ENDPOINT,
        )
        if not matches:
            raise ValueError(
                "Neurčitý Image Edit submit zatím nelze přesně dohledat; "
                "novou dávku neposílejte, aby nevznikl duplicitní provider běh."
            )
        if len(matches) != 1:
            raise ValueError(
                "Neurčitý Image Edit submit odpovídá více dávkám; "
                "automatické přiřazení není bezpečné."
            )
        apply_batch_status(job, matches[0])
        if not job.batch_id:
            raise ValueError("Dohledaná dávka nemá platné batch_id.")
        if job.schema_version >= 2 and _photo_operation_present(job, log_dir):
            rows = [
                image_edit_row(
                    item,
                    model=job.image_model,
                    prompt=job.final_prompt,
                    quality=job.quality,
                    size=job.size,
                    output_format=job.output_format,
                )
                for item in job.items
            ]
            _mark_photo_submission(
                job, rows, log_dir, job.batch_id, unknown=False
            )
    apply_batch_status(job, client.retrieve_batch(job.batch_id))
    save_job(job, log_dir)
    return job


def _atomic_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _jsonl(data: bytes, name: str) -> list[dict]:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{name}: obsah není UTF-8.") from exc
    rows = []
    for no, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{name}: neplatný JSON na řádku {no}: {exc}") from exc
        if not isinstance(row, dict):
            raise ValueError(f"{name}: řádek {no} není JSON objekt.")
        rows.append(row)
    return rows



def mark_content_acceptance(
    job: PhotoBatchJob,
    item_id: str,
    acceptance: str,
    log_dir: str | Path,
    note: str = "",
) -> PhotoBatchJob:
    """Explicit human/model acceptance; never inferred from valid image bytes."""
    if acceptance not in {"accepted", "rejected", "unverified"}:
        raise ValueError("Content acceptance musí být accepted, rejected nebo unverified.")
    item = next((row for row in job.items if row.item_id == item_id), None)
    if item is None:
        raise ValueError("Fotografie nepatří do tohoto Photo Jobu.")
    if item.technical_validation != "passed" or not item.output_path:
        raise ValueError("Obsah lze posoudit až po úspěšné technické validaci výsledku.")
    item.content_acceptance = acceptance
    item.content_acceptance_note = str(note or "").strip()
    save_job(job, log_dir)
    return job


def download_results(client, job, log_dir, reporter=None, progress=None):
    refresh_job(client, job, log_dir)
    if job.status != "completed" or not (job.output_file_id or job.error_file_id):
        raise ValueError(f"BATCH není připraven ke stažení (stav: {job.status}).")

    root = save_job(job, log_dir)
    output = Path(job.output_dir)
    by_id = {item.custom_id: item for item in job.items}
    seen: set[str] = set()

    if job.output_file_id:
        raw = client.file_content(job.output_file_id)
        _atomic_bytes(root / "batch_output.jsonl", raw)
        rows = _jsonl(raw, "batch_output.jsonl")
        for index, row in enumerate(rows, 1):
            cid = str(row.get("custom_id") or "")
            if cid not in by_id or cid in seen:
                raise ValueError(f"Neznámé nebo duplicitní custom_id: {cid}")
            seen.add(cid)
            item = by_id[cid]
            response = row.get("response") or {}
            body = response.get("body") or {}
            if job.schema_version >= 2 and isinstance(body, dict):
                _record_photo_usage(
                    job,
                    cid,
                    body.get("usage") or {},
                    log_dir,
                )
            if row.get("error") or int(response.get("status_code") or 200) >= 400:
                item.status = "failed"
                item.error_message = json.dumps(
                    row.get("error") or body, ensure_ascii=False
                )
                continue
            data = body.get("data") or []
            if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict) or not data[0].get("b64_json"):
                item.status = "failed"
                item.error_message = "Výsledek musí obsahovat právě jeden obrázek s data[0].b64_json."
                continue
            try:
                binary = base64.b64decode(data[0]["b64_json"], validate=True)
                image_info = inspect_photo_bytes(binary, job.output_format, model=job.image_model)
            except (ValueError, TypeError) as exc:
                item.status = "failed"
                item.technical_validation = "failed"
                item.error_message = str(exc)
                continue
            ext = "jpg" if job.output_format == "jpeg" else job.output_format
            stem = re.sub(
                r"[^\w.-]+",
                "_",
                Path(item.source_name).stem,
                flags=re.UNICODE,
            ).strip("._") or "photo"
            target = output / f"{stem}_edited.{ext}"
            sequence = 2
            while target.exists():
                target = output / f"{stem}_edited_{sequence}.{ext}"
                sequence += 1
            _atomic_bytes(target, binary)
            item.output_path = str(target)
            item.output_sha256 = hashlib.sha256(binary).hexdigest()
            item.output_width = int(image_info["width"])
            item.output_height = int(image_info["height"])
            item.output_format_detected = str(image_info["format"])
            item.technical_validation = "passed"
            item.content_acceptance = "unverified"
            item.content_acceptance_note = ""
            item.status = "downloaded"
            item.error_message = ""
            if reporter:
                reporter(f"Ukládám {index}/{len(rows)}: {target.name}")
            if progress:
                progress(10 + int(index / max(len(rows), 1) * 80))

    if job.error_file_id:
        raw = client.file_content(job.error_file_id)
        _atomic_bytes(root / "batch_errors.jsonl", raw)
        for row in _jsonl(raw, "batch_errors.jsonl"):
            cid = str(row.get("custom_id") or "")
            item = by_id.get(cid)
            if item:
                item.status = "failed"
                item.error_message = json.dumps(
                    row.get("error") or row, ensure_ascii=False
                )

    done = sum(item.status == "downloaded" for item in job.items)
    failed = sum(item.status == "failed" for item in job.items)
    job.request_completed = done
    job.request_failed = failed
    job.status = (
        "downloaded"
        if done == len(job.items)
        else "partial"
        if done
        else "failed"
    )
    save_job(job, log_dir)
    if reporter:
        reporter(f"Stažení dokončeno: {done} výsledků, {failed} chyb.")
    if progress:
        progress(100)
    return job
