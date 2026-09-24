"""BATCH workflow pro editaci fotografií přes /v1/images/edits."""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
import re
import tempfile
import uuid
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Iterable

from .batch_submit import exact_batch_matches, validate_batch_identity
from .comic_types import IMAGE_MODEL, ComicError
from .contracts import ContractError, parse_json_strict
from .image_runtime import image_capability, inspect_image, preferred_input_fidelity, validate_image_request
from .orchestration.contracts import canonical_bytes, canonical_sha256
from .orchestration.errors import OrchestrationError
from .orchestration.image_slots import image_policy
from .orchestration.repository import OrchestrationRepository
from .orchestration.publish import TargetPublishLock
from .orchestration.run_config import build_run_config_v2
from .orchestration.work_order import freeze_order
from .model_registry import model_spec, models_for_usage
from .openai_client import image_batch_submit_payload
from .photo_prompt import manual_photo_plan
from .runs.locking import ExecutionLock
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
    provider_result_sha256: str = ""
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


def inspect_photo_bytes(data: bytes, expected_format: str | None = None, *, model=IMAGE_MODEL, size="auto") -> dict:
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
    if size != "auto" and (info["width"], info["height"]) != tuple(map(int, size.split("x"))):
        raise OrchestrationError("IMAGE_INVALID", f"Rozměry výsledku neodpovídají požadované velikosti {size}.")
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
    body = {
        "model": model,
        "images": [{"file_id": item.uploaded_file_id}],
        "prompt": prompt,
        "n": 1,
        "size": size,
        "quality": quality,
        "output_format": output_format,
        "background": "auto",
    }
    fidelity = preferred_input_fidelity(model)
    if fidelity:
        body["input_fidelity"] = fidelity
    try:
        validate_image_request(IMAGE_EDIT_ENDPOINT, body)
    except ComicError as exc:
        raise ValueError(
            f"{item.source_name}: Image Edit payload porušuje kanonický kontrakt: {exc}"
        ) from exc
    return {
        "custom_id": item.custom_id,
        "method": "POST",
        "url": IMAGE_EDIT_ENDPOINT,
        "body": body,
    }


def image_edit_options(model: str) -> dict:
    """UI možnosti jsou odvozené ze stejné capability masky jako transport."""
    if not _is_image_edit_model(model):
        raise ValueError("Vybraný model nepodporuje dávkové úpravy fotografií.")
    cap = image_capability(model)
    fixed = cap.get("sizes")
    if fixed is not None:
        sizes = list(fixed)
        custom_size = False
    else:
        sizes = [
            "auto", "1024x1024", "1536x1024", "1024x1536",
            "2048x2048", "2048x1152", "1152x2048", "3840x2160", "2160x3840",
        ]
        custom_size = True
    return {
        "quality": list(cap["quality"]),
        "sizes": sizes,
        "custom_size": custom_size,
        "source": str(cap.get("source") or ""),
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
        if not isinstance(body, dict):
            raise ValueError("Image Edit BATCH body musí být JSON object.")
        model = str(body.get("model") or "")
        if not _is_image_edit_model(model):
            raise ValueError(f"{model}: pevná matice nepovoluje Image Edit BATCH model.")
        models.add(model)
        try:
            validate_image_request(IMAGE_EDIT_ENDPOINT, body)
        except ComicError as exc:
            raise ValueError(
                f"{cid}: Image Edit payload porušuje kanonický kontrakt: {exc}"
            ) from exc
        images = body.get("images")
        if not isinstance(images, list) or len(images) != 1:
            raise ValueError("PHOTO BATCH vyžaduje právě jednu zdrojovou fotografii na řádek.")
    if len(models) != 1:
        raise ValueError("Jeden Image Edit BATCH smí obsahovat právě jeden model.")
    return rows


PHOTO_BATCH_CREATE_SCHEMA = {
    "type": "object",
    "properties": {
        "input_file_id": {
            "type": "string",
            "pattern": "^[A-Za-z0-9_-]+$",
        },
        "endpoint": {"type": "string", "enum": [IMAGE_EDIT_ENDPOINT]},
        "completion_window": {"type": "string", "enum": ["24h"]},
        "output_expires_after": {
            "type": "object",
            "properties": {
                "anchor": {"type": "string", "enum": ["created_at"]},
                "seconds": {"type": "integer", "enum": [2592000]},
            },
            "required": ["anchor", "seconds"],
            "additionalProperties": False,
        },
    },
    "required": [
        "input_file_id",
        "endpoint",
        "completion_window",
        "output_expires_after",
    ],
    "additionalProperties": False,
}



def image_edit_batch_submit_payload(input_file_id: str) -> dict:
    """Compatibility facade over the single canonical image-batch submit mask."""
    return image_batch_submit_payload(input_file_id, IMAGE_EDIT_ENDPOINT)



class ImageEditBatchAdapter:
    """Lokálně ověřený endpointový adaptér; právě jeden pracovní POST /batches."""

    def __init__(self, client):
        self.client = client

    def submit(self, input_file_id: str, rows: Iterable[dict]) -> dict:
        self.client._validate_resource_id(input_file_id)
        verified = validate_image_edit_rows(rows)
        if not verified:
            raise ValueError("Pracovní Image Edit BATCH nemá žádné řádky.")
        return self.client.create_image_batch(input_file_id, verified)


def copy_photo_plan(value, final_prompt: str) -> dict:
    if not isinstance(value, dict) or type(value.get("version")) is not int or value.get("version") != 1:
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
    if not isinstance(value["professional_prompt"], str) or not isinstance(final_prompt, str) or not final_prompt.strip() or value["professional_prompt"].strip() != final_prompt.strip():
        raise ValueError("PHOTO_PLAN_V1 neodpovídá finálnímu promptu.")
    for key in ("edit_actions", "preserve_invariants", "acceptance_criteria"):
        rows = value[key]
        if not isinstance(rows, list) or any(
            not isinstance(item, str) or not item.strip() for item in rows
        ):
            raise ValueError(f"PHOTO_PLAN_V1.{key} musí být seznam neprázdných textů.")
    if not value["edit_actions"] or not value["acceptance_criteria"]:
        raise ValueError("PHOTO_PLAN_V1 vyžaduje edit_actions a acceptance_criteria.")
    return copy.deepcopy(value)


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
    plan_hash = canonical_sha256(plan)
    stamp = _now()
    items = make_items(source_paths)
    job = PhotoBatchJob(
        3,
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
    validate_photo_job(job)
    return job


def save_job(job: PhotoBatchJob, log_dir: str | Path) -> Path:
    validate_photo_job(job)
    root = Path(log_dir) / "PHOTO" / job.job_id
    root.mkdir(parents=True, exist_ok=True)
    job.updated_at = _now()
    atomic_write_text(
        str(root / "photo_job.json"),
        canonical_bytes(asdict(job)).decode("utf-8") + "\n",
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
            data = parse_json_strict(path.read_text(encoding="utf-8"))
            items = data.get("items", [])
            if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
                raise ValueError("Photo job items musí být seznam objektů.")
            for item in items:
                item.setdefault("provider_result_sha256", "")
            data["items"] = [PhotoBatchItem(**item) for item in items]
            data.setdefault("schema_version", 1)
            job = PhotoBatchJob(**data)
            validate_photo_job(job)
            if job.job_id != path.parent.name:
                raise ValueError("Photo job identity neodpovídá adresáři.")
            jobs.append(job)
        except (OSError, ContractError, ValueError, TypeError) as exc:
            raise ValueError(f"Photo job evidence je poškozená: {path}") from exc
    return jobs


def validate_photo_job(job: PhotoBatchJob) -> None:
    """Stejná hranice pro nové i obnovené úlohy; legacy evidence se nedoplňuje."""
    if not isinstance(job, PhotoBatchJob):
        raise ValueError("PHOTO: úloha nemá platný datový kontrakt.")
    def types(record):
        for spec in fields(record):
            value = getattr(record, spec.name)
            expected = {"str": str, "int": int, "dict": dict, "list[PhotoBatchItem]": list}[spec.type]
            if type(value) is not expected or (expected is int and value < 0):
                raise ValueError(f"PHOTO: neplatný typ nebo rozsah {spec.name}.")
    def digest(value, optional=False):
        if optional and value == "":
            return
        if re.fullmatch(r"[0-9a-f]{64}", value) is None:
            raise ValueError("PHOTO: neplatný SHA-256.")
    types(job)
    if job.schema_version not in {1, 2, 3}:
        raise ValueError("PHOTO: neznámá verze úlohy.")
    if not re.fullmatch(r"photojob_[A-Za-z0-9_-]+", job.job_id):
        raise ValueError("PHOTO: neplatná identita úlohy.")
    if job.status not in {"preparing", "submission_unknown", "validating", "in_progress", "finalizing", "completed", "failed", "expired", "cancelling", "cancelled", "downloaded", "partial", "submitted"}:
        raise ValueError("PHOTO: neplatný stav úlohy.")
    digest(job.final_prompt_sha256)
    if not job.final_prompt.strip() or hashlib.sha256(job.final_prompt.encode("utf-8")).hexdigest() != job.final_prompt_sha256:
        raise ValueError("PHOTO: změněný finální prompt.")
    if job.schema_version == 3 or job.photo_plan:
        plan = copy_photo_plan(job.photo_plan, job.final_prompt)
        if canonical_sha256(plan) != job.photo_plan_sha256:
            raise ValueError("PHOTO: změněný plán.")
    if not job.items or job.request_total != len(job.items):
        raise ValueError("PHOTO: nesouhlasí počet položek.")
    if job.request_completed + job.request_failed > job.request_total:
        raise ValueError("PHOTO: nesouhlasí počty výsledků.")
    for identity in ("item_id", "custom_id"):
        values = [getattr(item, identity) for item in job.items if isinstance(item, PhotoBatchItem)]
        if len(values) != len(job.items) or len(set(values)) != len(values) or any(not isinstance(value, str) or not value.strip() for value in values):
            raise ValueError("PHOTO: neplatné nebo duplicitní identity položek.")
    for item in job.items:
        types(item)
        digest(item.source_sha256)
        digest(item.output_sha256, True)
        digest(item.provider_result_sha256, True)
        if item.status not in {"pending", "uploaded", "failed", "downloaded"}:
            raise ValueError("PHOTO: neplatný stav položky.")
        if item.technical_validation not in {"pending", "passed", "failed"} or item.content_acceptance not in {"unverified", "accepted", "rejected"}:
            raise ValueError("PHOTO: neplatná evidence přijetí položky.")
        if item.status == "downloaded" and (not item.output_path or not item.output_sha256):
            raise ValueError("PHOTO: převzatá položka nemá výstup.")


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
            "provider_endpoint": "/v1/batches",
            "target_id": job.job_id,
            "target_path": None,
            "expected_target_hash": None,
            "contract_name": "PHOTO_BATCH_V1",
            "schema": copy.deepcopy(PHOTO_BATCH_CREATE_SCHEMA),
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
    submit_payload = image_edit_batch_submit_payload(job.input_file_id)
    submit_hash = canonical_sha256(submit_payload)
    persisted_hash = repo.register_work_order(
        order,
        body_ref=submit_hash,
        input_hash=order.input_projection_hash,
    )
    repo.prepare_provider_operation(
        attempt_id=order.attempt_id,
        work_order_hash=persisted_hash,
        endpoint=order.provider_endpoint,
        request_hash=submit_hash,
    )
    repo.bind_physical_request(
        order.attempt_id,
        physical_request_hash=submit_hash,
        remote_input_file_id=job.input_file_id,
    )
    root = save_job(job, log_dir)
    atomic_write_text(
        str(root / "work_order_v2.json"),
        canonical_bytes(
            {**order.to_dict(), "order_hash": order.order_hash}
        ).decode("utf-8")
        + "\n",
    )
    return repo, order


def _photo_operation_present(job, log_dir):
    present = (
        Path(log_dir)
        / "PHOTO"
        / job.job_id
        / "work_order_v2.json"
    ).is_file()
    if not present and job.schema_version >= 2:
        raise ValueError("Moderní PHOTO job nemá povinný WorkOrder work_order_v2.json.")
    return present


def _verify_photo_operation_binding(job, rows, log_dir) -> None:
    if not _photo_operation_present(job, log_dir):
        return
    _cfg, order, _projection = _photo_work_order(job, rows)
    repo = _photo_repo(log_dir)
    expected_request_hash = canonical_sha256(
        image_edit_batch_submit_payload(job.input_file_id)
    )
    with repo.connect() as db:
        row = db.execute(
            """
            SELECT w.work_order_hash,w.provider_endpoint,p.endpoint,p.request_hash,
                   p.remote_input_file_id,p.provider_id,p.state
            FROM work_orders w
            JOIN provider_operations p ON p.work_order_hash=w.work_order_hash
            WHERE p.attempt_id=?
            """,
            (order.attempt_id,),
        ).fetchone()
    if not row:
        raise ValueError("Photo Batch nemá centrální provider-operation kontrakt.")
    (
        work_hash,
        work_endpoint,
        operation_endpoint,
        request_hash,
        remote_file,
        provider_id,
        state,
    ) = row
    if (
        work_hash != order.order_hash
        or work_endpoint != "/v1/batches"
        or operation_endpoint != "/v1/batches"
        or request_hash != expected_request_hash
        or remote_file != job.input_file_id
    ):
        raise ValueError("Photo Batch fyzický provider kontrakt neodpovídá jobu.")
    if job.batch_id and provider_id and provider_id != job.batch_id:
        raise ValueError("Photo Batch centrální a lokální provider ID se liší.")
    if state in {"submitted", "completed"} and not (provider_id or job.batch_id):
        raise ValueError("Photo Batch potvrzený submit nemá provider ID.")


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
    validate_photo_job(job)
    validate_image_edit_parameters(job.image_model, job.quality, job.size, job.output_format)
    root = save_job(job, log_dir)
    frozen_sources = {}
    for item in job.items:
        target = root / "sources" / (item.source_sha256 + Path(item.source_name).suffix)
        raw = target.read_bytes() if target.is_file() else Path(item.source_path).read_bytes()
        if hashlib.sha256(raw).hexdigest() != item.source_sha256:
            raise ValueError(f"{item.source_name}: vstup se od výběru změnil.")
        if not target.is_file():
            _atomic_bytes(target, raw)
        frozen_sources[item.custom_id] = str(target)
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
        uploaded = client.upload_file(frozen_sources[item.custom_id], purpose="user_data")
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
        canonical_bytes(row).decode("utf-8") + "\n"
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
    _verify_photo_operation_binding(job, rows, log_dir)
    save_job(job, log_dir)
    report(f"BATCH vytvořen: {job.batch_id}", 100)
    return job


def apply_batch_status(job: PhotoBatchJob, payload: dict) -> PhotoBatchJob:
    try:
        validate_batch_identity(payload, input_file_id=job.input_file_id,
                                batch_id=job.batch_id or None, endpoint=IMAGE_EDIT_ENDPOINT)
    except ContractError as exc:
        raise ValueError(str(exc)) from exc
    provider_id = payload["id"]
    status = payload.get("status")
    allowed = {
        "validating", "in_progress",
        "finalizing", "cancelling", "completed", "failed", "expired",
        "cancelled",
    }
    if not isinstance(status, str) or status not in allowed:
        raise ValueError("Image Edit BATCH vrátil neznámý stav.")
    counts = payload.get("request_counts") or {}
    if not isinstance(counts, dict):
        raise ValueError("Image Edit BATCH request_counts musí být objekt.")
    total = int(counts.get("total") or job.request_total or len(job.items))
    if total != len(job.items):
        raise ValueError("Image Edit BATCH počet provider položek neodpovídá jobu.")
    completed = int(counts.get("completed") or 0)
    failed = int(counts.get("failed") or 0)
    if min(completed, failed) < 0 or completed + failed > total:
        raise ValueError("Image Edit BATCH request_counts jsou nekonzistentní.")
    job.status = status
    if provider_id:
        job.batch_id = provider_id
    job.output_file_id = str(payload.get("output_file_id") or job.output_file_id)
    job.error_file_id = str(payload.get("error_file_id") or job.error_file_id)
    job.request_total = total
    job.request_completed = completed
    job.request_failed = failed
    return job


def refresh_job(client, job: PhotoBatchJob, log_dir: str | Path) -> PhotoBatchJob:
    _photo_operation_present(job, log_dir)
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
            _verify_photo_operation_binding(job, rows, log_dir)
    apply_batch_status(job, client.retrieve_batch(job.batch_id))
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
        _verify_photo_operation_binding(job, rows, log_dir)
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
            row = parse_json_strict(line)
        except ContractError as exc:
            if exc.code == "ROOT_NOT_OBJECT":
                message = "JSON řádek musí být JSON objekt"
            elif exc.code == "INVALID_JSON":
                message = "neplatný JSON"
            else:
                message = "nekanonický JSON"
            raise ValueError(f"{name}: {message} na řádku {no}.") from exc
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


def _stable_output_target(
    output: Path,
    item: PhotoBatchItem,
    ext: str,
    result_sha256: str,
) -> Path:
    stem = re.sub(
        r"[^\w.-]+",
        "_",
        Path(item.source_name).stem,
        flags=re.UNICODE,
    ).strip("._") or "photo"
    primary = output / f"{stem}_edited.{ext}"
    stable_suffix = hashlib.sha256(
        f"{item.custom_id}:{result_sha256}".encode("utf-8")
    ).hexdigest()[:12]
    candidates = [
        primary,
        output / f"{stem}_edited_{stable_suffix}.{ext}",
    ]
    for target in candidates:
        if not target.exists():
            return target
        if _hash_file(target) == result_sha256:
            return target
    raise ValueError(
        f"{item.source_name}: cílové názvy již obsahují cizí soubor; "
        "výsledek nebyl přepsán."
    )


def _photo_job_lock(root: Path) -> ExecutionLock:
    key = hashlib.sha256(os.path.normcase(str(root.resolve())).encode("utf-8")).hexdigest()
    return ExecutionLock(Path(tempfile.gettempdir()) / "kajovo-photo-job-locks" / (key + ".lock"))


def download_results(client, job, log_dir, reporter=None, progress=None):
    validate_photo_job(job)
    root = Path(log_dir) / "PHOTO" / job.job_id
    with _photo_job_lock(root):
        return _download_results_locked(client, job, log_dir, reporter, progress)


def _download_results_locked(client, job, log_dir, reporter=None, progress=None):
    refresh_job(client, job, log_dir)
    terminal = {"completed", "failed", "expired", "cancelled"}
    if job.status not in terminal:
        raise ValueError(f"BATCH není v konečném stavu (stav: {job.status}).")
    if not (job.output_file_id or job.error_file_id):
        raise ValueError(
            f"BATCH skončil stavem {job.status}, ale provider neposkytl "
            "output_file_id ani error_file_id."
        )

    root = save_job(job, log_dir)
    output = Path(job.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    by_id = {item.custom_id: item for item in job.items}
    seen: set[str] = set()

    result_sets = {}
    for file_id, name in ((job.output_file_id, "batch_output.jsonl"),
                          (job.error_file_id, "batch_errors.jsonl")):
        rows = []
        if file_id:
            raw = client.file_content(file_id)
            _atomic_bytes(root / name, raw)
            rows = _jsonl(raw, name)
        for row in rows:
            cid = str(row.get("custom_id") or "")
            if cid not in by_id or cid in seen:
                raise ValueError(f"Neznámé nebo duplicitní custom_id: {cid}")
            seen.add(cid)
        result_sets[name] = rows

    if job.output_file_id:
        rows = result_sets["batch_output.jsonl"]
        for index, row in enumerate(rows, 1):
            cid = str(row.get("custom_id") or "")
            item = by_id[cid]
            from .batch_result import response_body
            body, error = response_body(row)
            provider_result_hash = canonical_sha256(row)
            if job.schema_version >= 2 and isinstance(body, dict):
                _record_photo_usage(job, cid, body.get("usage") or {}, log_dir)
            if error is not None:
                item.status = "failed"
                item.error_message = json.dumps(
                    error, ensure_ascii=False
                )
                item.provider_result_sha256 = provider_result_hash
                save_job(job, log_dir)
                continue
            data = body.get("data") or []
            if (
                not isinstance(data, list)
                or len(data) != 1
                or not isinstance(data[0], dict)
                or not data[0].get("b64_json")
            ):
                item.status = "failed"
                item.error_message = (
                    "Výsledek musí obsahovat právě jeden obrázek "
                    "s data[0].b64_json."
                )
                item.provider_result_sha256 = provider_result_hash
                save_job(job, log_dir)
                continue
            try:
                binary = base64.b64decode(data[0]["b64_json"], validate=True)
                image_info = inspect_photo_bytes(
                    binary, job.output_format, model=job.image_model, size=job.size
                )
            except (ValueError, TypeError, OrchestrationError) as exc:
                item.status = "failed"
                item.technical_validation = "failed"
                item.error_message = str(exc)
                item.provider_result_sha256 = provider_result_hash
                save_job(job, log_dir)
                continue

            result_hash = hashlib.sha256(binary).hexdigest()
            previous_hash = item.output_sha256
            previous_acceptance = item.content_acceptance
            previous_note = item.content_acceptance_note
            existing = Path(item.output_path) if item.output_path else None
            with TargetPublishLock(output):
                if (
                    existing
                    and existing.is_file()
                    and _hash_file(existing) == result_hash
                ):
                    target = existing
                else:
                    ext = "jpg" if job.output_format == "jpeg" else job.output_format
                    target = _stable_output_target(output, item, ext, result_hash)
                    if not target.exists():
                        _atomic_bytes(target, binary)
                    elif _hash_file(target) != result_hash:
                        raise ValueError(
                            f"{item.source_name}: existující cizí výstup nebyl přepsán."
                        )

            item.output_path = str(target)
            item.output_sha256 = result_hash
            item.provider_result_sha256 = provider_result_hash
            item.output_width = int(image_info["width"])
            item.output_height = int(image_info["height"])
            item.output_format_detected = str(image_info["format"])
            item.technical_validation = "passed"
            if (
                previous_hash == result_hash
                and previous_acceptance in {"accepted", "rejected", "unverified"}
            ):
                item.content_acceptance = previous_acceptance
                item.content_acceptance_note = previous_note
            else:
                item.content_acceptance = "unverified"
                item.content_acceptance_note = ""
            item.status = "downloaded"
            item.error_message = ""
            save_job(job, log_dir)
            if reporter:
                reporter(f"Ukládám {index}/{len(rows)}: {target.name}")
            if progress:
                progress(10 + int(index / max(len(rows), 1) * 80))

    if job.error_file_id:
        for row in result_sets["batch_errors.jsonl"]:
            cid = str(row.get("custom_id") or "")
            item = by_id.get(cid)
            if item and item.status != "downloaded":
                item.status = "failed"
                item.provider_result_sha256 = canonical_sha256(row)
                item.error_message = json.dumps(
                    row.get("error") or row, ensure_ascii=False
                )
                save_job(job, log_dir)

    for cid, item in by_id.items():
        if cid not in seen and item.status != "downloaded":
            item.status = "failed"
            item.error_message = "missing_result: terminální dávka neobsahuje výsledek položky."
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
