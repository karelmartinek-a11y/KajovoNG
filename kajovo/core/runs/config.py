from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class UiRunConfig:
    """Serializovatelný vstup doménového běhu bez závislosti na Qt.

    Tvar je během první refaktorovací fáze záměrně kompatibilní s původním
    `kajovo.core.pipeline.UiRunConfig`; změna je pouze vlastnická, nikoliv
    behaviorální.
    """

    project: str
    prompt: str
    mode: str  # GENERATE|MODIFY|QA|QFILE
    send_as_c: bool
    model: str
    model_a1: str
    model_a2: str
    model_a3: str
    response_id: str
    attached_file_ids: list[str]
    input_file_ids: list[str]
    attached_vector_store_ids: list[str]
    in_dir: str
    out_dir: str
    in_equals_out: bool
    versing: bool
    temperature: float
    use_file_search: bool

    diag_windows_in: bool
    diag_windows_out: bool
    diag_ssh_in: bool
    diag_ssh_out: bool
    ssh_user: str
    ssh_host: str
    ssh_key: str
    ssh_password: str
    skip_paths: list[str]
    skip_exts: list[str]

    # Snímek schopností vybraného modelu z lokální validace a pevné matice.
    model_caps: dict[str, Any]
    # None zde má existující význam „nebyly předány rerun podklady“.
    resume_files: list[dict[str, Any]] | None = None
    resume_prev_id: str | None = None
    ssh_pin: str = ""
    ssh_pin_required: bool = False
    caps_by_model: dict[str, Any] | None = None
    # Aktuální katalog modelů z API; při jeho předání se vyžaduje povolení v pevné matici.
    available_models: list[str] | None = None
    maximum_quality: bool = False
    preparation_snapshot: dict[str, Any] | None = None
    completed_hashes: dict[str, str] | None = None
    # Explicitní pokyn opravné větve. Používá se výhradně v nově
    # prováděné části za ověřeným checkpointem.
    recovery_instruction: str = ""
    source_checkpoint_id: str = ""
