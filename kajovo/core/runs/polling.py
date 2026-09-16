from __future__ import annotations

from dataclasses import dataclass
import logging
import time
from typing import Any, Callable, Dict, Iterable, Mapping, Optional

from ..progress import ProgressEvent


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class VectorStorePollingContext:
    retrieve: Callable[[str, str], Mapping[str, Any]]
    check_stop: Callable[[], None]
    progress_emit: Callable[[ProgressEvent], None]
    evidence_emit: Optional[Callable[[str, Dict[str, Any]], None]] = None
    timeout_s: int = 180
    poll_interval_s: float = 2.0
    max_consecutive_failures: int = 5


def _emit_evidence(context: VectorStorePollingContext, event: str, payload: Dict[str, Any]) -> None:
    if context.evidence_emit is None:
        return
    try:
        context.evidence_emit(event, payload)
    except Exception as exc:  # evidence nesmí změnit provider operaci
        LOGGER.warning("Evidence callback failed during vector-store polling: %s", type(exc).__name__)


def wait_vector_store_files(
    context: VectorStorePollingContext,
    vector_store_id: str,
    vector_store_file_ids: Iterable[str],
) -> None:
    """Čeká na indexaci a rozlišuje provider nedostupnost od skutečného timeoutu."""
    file_ids = [str(value) for value in vector_store_file_ids if value]
    if not file_ids:
        return
    if context.timeout_s <= 0:
        raise ValueError("timeout_s musí být kladný.")
    if context.max_consecutive_failures <= 0:
        raise ValueError("max_consecutive_failures musí být kladný.")

    start = time.monotonic()
    pending = set(file_ids)
    failures = {file_id: 0 for file_id in pending}
    last_error_type: Dict[str, str] = {}

    while pending:
        context.check_stop()
        elapsed = time.monotonic() - start
        if elapsed > context.timeout_s:
            detail = ", ".join(
                f"{file_id}:{last_error_type[file_id]}"
                for file_id in sorted(pending)
                if file_id in last_error_type
            )
            suffix = f" Poslední chyby: {detail}." if detail else ""
            raise RuntimeError(f"Vector store index timeout ({vector_store_id}).{suffix}")

        completed: list[str] = []
        for file_id in list(pending):
            try:
                info = context.retrieve(vector_store_id, file_id)
            except Exception as exc:
                failures[file_id] += 1
                error_type = type(exc).__name__
                last_error_type[file_id] = error_type
                _emit_evidence(
                    context,
                    "vector_store.poll_error",
                    {
                        "vector_store_id": vector_store_id,
                        "vector_store_file_id": file_id,
                        "attempt": failures[file_id],
                        "error_class": error_type,
                    },
                )
                context.progress_emit(
                    ProgressEvent(
                        "Indexace",
                        detail=(
                            "Files API dočasně nevrátilo stav souboru; "
                            f"opakování {failures[file_id]}/{context.max_consecutive_failures}."
                        ),
                        source="files_api",
                    )
                )
                if failures[file_id] >= context.max_consecutive_failures:
                    raise RuntimeError(
                        "Stav souboru ve vector store nelze opakovaně ověřit "
                        f"({vector_store_id}/{file_id}, {error_type})."
                    ) from exc
                continue

            failures[file_id] = 0
            last_error_type.pop(file_id, None)
            status = str(info.get("status") or "")
            context.progress_emit(
                ProgressEvent(
                    "Indexace",
                    detail=f"API ověřilo stav souboru: {status}",
                    source="files_api",
                )
            )
            if status == "completed":
                completed.append(file_id)
            elif status == "failed":
                last_error = info.get("last_error") or {}
                message = (
                    last_error.get("message")
                    if isinstance(last_error, Mapping)
                    else None
                ) or "Vector store indexing failed."
                raise RuntimeError(
                    f"Vector store indexing failed ({vector_store_id}): {message}"
                )

        for done in completed:
            pending.discard(done)
        context.progress_emit(
            ProgressEvent(
                "Indexace",
                completed=len(set(file_ids)) - len(pending),
                total=len(set(file_ids)),
                unit="souborů",
                source="files_api",
            )
        )
        if pending:
            time.sleep(context.poll_interval_s)
