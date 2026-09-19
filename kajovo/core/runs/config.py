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

    # Kanonický per-run RUN_CONFIG_V2. Tyto hodnoty nejsou globální
    # nastavení a musí být součástí persistence, checkpointů a run-scope hash.
    stop_after_plan: bool = False
    dry_run: bool = False
    max_cost_microusd: int | None = 25_000_000
    max_input_tokens: int = 2_000_000
    max_output_tokens: int = 500_000
    max_paid_requests: int = 200
    unknown_pricing: str = "block"
    auto_repair: str = "off"
    verification_profile_ids: list[str] | None = None

    # QFILE: cesta je důvěryhodný lokální vstup. Pokud chybí, plánovací
    # request je povolen pouze při explicitním qfile_suggest_path=True.
    qfile_output_path: str = ""
    qfile_output_format: str = "txt"
    qfile_suggest_path: bool = False
    qfile_plan: dict[str, Any] | None = None

    preparation_snapshot: dict[str, Any] | None = None
    completed_hashes: dict[str, str] | None = None
    # Explicitní pokyn opravné větve. Používá se výhradně v nově
    # prováděné části za ověřeným checkpointem.
    recovery_instruction: str = ""
    source_checkpoint_id: str = ""
