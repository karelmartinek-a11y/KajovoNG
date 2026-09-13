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
from typing import Iterable

from .model_registry import model_ids, model_spec
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
    return (
        not spec["deprecated"]
        and spec["batch"]
        and "inpainting" in spec["features"]
        and IMAGE_EDIT_ENDPOINT.lstrip("/") in _endpoints(spec)
    )


def image_edit_model_ids(available_models: Iterable[str] | None = None) -> list[str]:
    available = set(available_models or ())
    result = [
        model
        for model in model_ids()
        if _is_image_edit_model(model) and (not available or model in available)
    ]
    preferred = {
        name: index
        for index, name in enumerate(
            (
                "gpt-image-2.5-sunburst",
                "gpt-image-2.5-sunburst-2026-09-08",
                "gpt-image-2.5-flare",
                "gpt-image-2.5-flare-2026-09-08",
                "gpt-image-2",
                "gpt-image-2-2026-04-21",
                "gpt-image-1.5",
                "gpt-image-1.5-2025-12-16",
            )
        )
    }
    return sorted(result, key=lambda value: (preferred.get(value, 1000), value))


def response_prompt_models(available_models: Iterable[str] | None = None) -> list[str]:
    available = set(available_models or ())
    result = []
    for model in model_ids():
        spec = model_spec(model)
        if (
            not spec["deprecated"]
            and spec["responses"]
            and "structured_outputs" in spec["features"]
            and (not available or model in available)
        ):
            result.append(model)
    preferred = {
        name: index
        for index, name in enumerate(
            ("gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol", "gpt-5.4-mini")
        )
    }
    return sorted(result, key=lambda value: (preferred.get(value, 1000), value))


def validate_source_image(path: str | Path) -> Path:
    source = Path(path).expanduser().resolve()
    if not source.is_file() or source.suffix.lower() not in SUPPORTED_IMAGE_EXTENSIONS:
        raise ValueError(f"Neplatná nebo nepodporovaná fotografie: {source}")
    if not 0 < source.stat().st_size <= 100 * 1024 * 1024:
        raise ValueError(f"Neplatná velikost fotografie: {source.name}")
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
):
    prompt = final_prompt.strip()
    if not prompt:
        raise ValueError("Prompt je prázdný.")
    if not _is_image_edit_model(image_model):
        raise ValueError(f"{image_model}: model není povolený pro Image Edit BATCH.")
    output = Path(output_dir).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    stamp = _now()
    items = make_items(source_paths)
    return PhotoBatchJob(
        1,
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
            jobs.append(PhotoBatchJob(**data))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
    return jobs


def prepare_and_submit(client, job, log_dir, reporter=None, progress=None):
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

    report("Odesílám pracovní Image Edit BATCH.", 92)
    submitted = ImageEditBatchAdapter(client).submit(job.input_file_id, rows)
    apply_batch_status(job, submitted)
    client._validate_resource_id(job.batch_id)
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
        raise ValueError("Photo Job nemá batch_id.")
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
            if row.get("error") or int(response.get("status_code") or 200) >= 400:
                item.status = "failed"
                item.error_message = json.dumps(
                    row.get("error") or body, ensure_ascii=False
                )
                continue
            data = body.get("data") or []
            if not data or not isinstance(data[0], dict) or not data[0].get("b64_json"):
                item.status = "failed"
                item.error_message = "Výsledek neobsahuje data[0].b64_json."
                continue
            try:
                binary = base64.b64decode(data[0]["b64_json"], validate=True)
            except (ValueError, TypeError) as exc:
                item.status = "failed"
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
