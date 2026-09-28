"""Nezávislý inventář runtime procesů pro forenzní sémantický audit.

Inventář nevychází z dokumentace ani z názvů CI jobů. Objevuje produkční
dispatchery a provider call-sites přímo z AST a porovnává je s explicitně
vlastněnými procesními variantami a cílenými regresními důkazy.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class StepEvidence:
    name: str
    implementation: str
    transition: str
    validator: str
    positive_test: str
    positive_marker: str
    negative_test: str = ""
    negative_marker: str = ""
    unverified_reason: str = ""


@dataclass(frozen=True)
class RuntimeVariant:
    id: str
    modes: tuple[str, ...]
    runtime_paths: tuple[str, ...]
    steps: tuple[StepEvidence, ...]


RUNTIME_VARIANTS = (
    RuntimeVariant(
        "generate-live",
        ("GENERATE",),
        (
            "kajovo/core/orchestration/preparation.py",
            "kajovo/core/runs/generate.py",
            "kajovo/core/runs/file_execution.py",
            "kajovo/core/runs/response_execution.py",
        ),
        (
            StepEvidence(
                "A0R/A1/A2 -> LIVE FILE_CONTENT_V1",
                "kajovo/core/orchestration/preparation.py::prepare_delivery_v2",
                "preparation_snapshot.canonical_stage -> files_complete_unverified",
                "kajovo/core/orchestration/preparation.py::validate_spine_v1",
                "tests/test_delivery_pipeline.py::test_delivery_eight_variants_use_v2_preparation_and_truthful_boundary",
                "files_complete_unverified",
                "tests/test_preparation_boundaries.py::test_missing_mandatory_component_owner_stops_before_detail_and_keeps_evidence",
                "COMPONENT-X",
            ),
        ),
    ),
    RuntimeVariant(
        "generate-batch",
        ("GENERATE",),
        (
            "kajovo/core/runs/batch_execution.py",
            "kajovo/core/generate_batch.py",
            "kajovo/core/batch_completion.py",
        ),
        (
            StepEvidence(
                "validated WorkOrders -> BATCH submit/import",
                "kajovo/core/runs/batch_execution.py::_submit_generate_batch",
                "batch_prepared -> submission_unknown|batch_pending -> files_complete_unverified",
                "kajovo/core/runs/batch_execution.py::_work_orders",
                "tests/test_delivery_pipeline.py::test_batch_import_stages_result_and_never_writes_out",
                "files_complete_unverified",
                "tests/test_batch_recovery_boundaries.py::test_file_submit_persists_rejection_or_unknown_without_retry",
                "submission_unknown",
            ),
        ),
    ),
    RuntimeVariant(
        "modify-live",
        ("MODIFY",),
        (
            "kajovo/core/orchestration/preparation.py",
            "kajovo/core/runs/modify.py",
            "kajovo/core/runs/file_execution.py",
            "kajovo/core/runs/response_execution.py",
        ),
        (
            StepEvidence(
                "B0R/B1/B2 -> LIVE FILE_CONTENT_V1",
                "kajovo/core/orchestration/preparation.py::prepare_delivery_v2",
                "preparation_snapshot.canonical_stage -> files_complete_unverified",
                "kajovo/core/orchestration/preparation.py::validate_spine_v1",
                "tests/test_delivery_pipeline.py::test_delivery_eight_variants_use_v2_preparation_and_truthful_boundary",
                "MODIFY",
                "tests/test_preparation_boundaries.py::test_missing_mandatory_component_owner_stops_before_detail_and_keeps_evidence",
                "COMPONENT-X",
            ),
        ),
    ),
    RuntimeVariant(
        "modify-batch",
        ("MODIFY",),
        (
            "kajovo/core/runs/batch_execution.py",
            "kajovo/core/generate_batch.py",
            "kajovo/core/batch_completion.py",
        ),
        (
            StepEvidence(
                "validated MODIFY WorkOrders -> BATCH submit/import",
                "kajovo/core/runs/batch_execution.py::_submit_generate_batch",
                "batch_prepared -> submission_unknown|batch_pending -> files_complete_unverified",
                "kajovo/core/runs/batch_execution.py::_work_orders",
                "tests/test_delivery_pipeline.py::test_delivery_eight_variants_use_v2_preparation_and_truthful_boundary",
                "batch_pending",
                "tests/test_batch_recovery_boundaries.py::test_file_submit_persists_rejection_or_unknown_without_retry",
                "not_submitted",
            ),
        ),
    ),
    RuntimeVariant(
        "qa",
        ("QA",),
        ("kajovo/core/runs/qa.py",),
        (
            StepEvidence(
                "QA input -> answer -> evidence validation",
                "kajovo/core/runs/qa.py::_run_qa",
                "QA_INPUT -> QA_RESPONSE -> QA_VALIDATION -> completed",
                "QA_ANSWER_V2 evidence_ids",
                "tests/test_qa_evidence_links.py::test_qa_evidence_resolves_to_supplied_sources",
                "SRC-USER-TEXT",
                "tests/test_qa_evidence_links.py::test_qa_evidence_resolves_to_supplied_sources",
                "missing-evidence",
            ),
        ),
    ),
    RuntimeVariant(
        "qfile",
        ("QFILE",),
        ("kajovo/core/runs/qfile.py",),
        (
            StepEvidence(
                "QFILE approved path -> FILE_CONTENT_V1",
                "kajovo/core/runs/qfile.py::_run_qfile",
                "qfile_plan_ready|files_complete_unverified",
                "QFILE_PLAN_V1 + FILE_CONTENT_V1",
                "tests/test_workflows.py::test_complete_offline_workflow",
                "qfile_output_path",
                unverified_reason=(
                    "Chybí samostatný backendový negativní test hranice "
                    "QFILE_PLAN_V1 -> nové uživatelské potvrzení -> výroba."
                ),
            ),
        ),
    ),
    RuntimeVariant(
        "cascade",
        ("KASKADA",),
        (
            "kajovo/core/cascade_pipeline.py",
            "kajovo/core/cascade_production.py",
        ),
        (
            StepEvidence(
                "deterministic step/branch/resume",
                "kajovo/core/cascade_pipeline.py::CascadeRunExecutor",
                "executed_step_ids -> next step|terminal",
                "runtime_schema_for_step + output validators",
                "tests/test_cascade_v2.py::test_decision_routes_to_future_step_and_skips_other_branch",
                "executed_step_ids",
                "tests/test_cascade_unknown_submission.py::test_unknown_preserves_checkpoint_and_prevents_restart",
                "submission_unknown",
            ),
        ),
    ),
    RuntimeVariant(
        "photo",
        ("PHOTO",),
        (
            "kajovo/core/photo_prompt.py",
            "kajovo/core/photo_batch.py",
        ),
        (
            StepEvidence(
                "PHOTO plan/batch/recovery",
                "kajovo/core/photo_batch.py::prepare_and_submit",
                "preparing -> submission_unknown|submitted -> downloaded|partial",
                "kajovo/core/photo_batch.py::validate_photo_job",
                "tests/test_photo_studio.py::test_photo_batch_submit_creates_work_order_and_provider_operation",
                "submitted",
                "tests/test_photo_studio.py::test_photo_batch_uncertain_submit_is_not_reposted",
                "submission_unknown",
            ),
        ),
    ),
    RuntimeVariant(
        "comic",
        ("COMIC",),
        ("kajovo/core/comic_service.py",),
        (
            StepEvidence(
                "comic panel batch/edit/recovery",
                "kajovo/core/comic_service.py::ComicService",
                "prepared -> submitted|submission_unknown -> completed|partial",
                "comic store/domain contracts",
                "tests/test_comic_domain.py::test_batch_partial_retry_and_resume",
                "partial",
                "tests/test_comic_recovery.py::test_cancel_before_submission_never_calls_provider",
                "assert_not_called",
            ),
        ),
    ),
    RuntimeVariant(
        "resource-delivery",
        ("GENERATE", "MODIFY"),
        ("kajovo/core/orchestration/resource_delivery.py",),
        (
            StepEvidence(
                "non-text resource dispatch",
                "kajovo/core/orchestration/resource_delivery.py::dispatch_resource_target",
                "waiting_manual_resource|completed_unverified|submission_unknown",
                "kajovo/core/orchestration/resource_delivery.py::validate_resource_plan",
                "tests/test_audit2_preparation.py::test_image_request_and_frozen_projection_keep_explicit_parameters",
                "image_production",
                "tests/test_audit2_preparation.py::test_legacy_image_plan_is_not_silently_given_new_parameters",
                "legacy plán",
            ),
        ),
    ),
    RuntimeVariant(
        "verification-profiles",
        ("GENERATE", "MODIFY"),
        ("kajovo/core/orchestration/verification.py",),
        (
            StepEvidence(
                "verification report/profile execution",
                "kajovo/core/orchestration/verification.py",
                "verification profile -> report",
                "VERIFICATION_REPORT_V3",
                "tests/test_canonical_contract_links.py::test_local_schema_files_match_runtime_contracts",
                "VERIFICATION_REPORT_V3",
                unverified_reason=(
                    "Schéma je svázané s runtime kontraktem, ale nebyl nalezen "
                    "cílený pozitivní i negativní test skutečného execution profilu."
                ),
            ),
        ),
    ),
    RuntimeVariant(
        "cancellation",
        ("GENERATE", "MODIFY", "QA", "QFILE"),
        (
            "kajovo/core/runs/cancellation.py",
            "kajovo/core/runs/executor.py",
        ),
        (
            StepEvidence(
                "cooperative cancellation",
                "kajovo/core/runs/cancellation.py::CancellationToken",
                "running -> cancelled|stopped",
                "kajovo/core/runs/cancellation.py::raise_if_cancelled",
                "tests/test_run_cancellation.py::test_cancellation_token_starts_active_and_cancels_idempotently",
                "cancel",
                unverified_reason=(
                    "Token má regresní test, ale úplný restart/cancel průchod každého "
                    "workflow nemá vlastní pozitivní i negativní end-to-end důkaz."
                ),
            ),
        ),
    ),
    RuntimeVariant(
        "restart-recovery",
        ("GENERATE", "MODIFY"),
        (
            "kajovo/core/response_journal.py",
            "kajovo/core/runs/response_execution.py",
            "kajovo/core/runs/live_continuation.py",
        ),
        (
            StepEvidence(
                "pending LIVE handoff",
                "kajovo/core/runs/live_continuation.py",
                "response_pending -> GET inherited response -> continue",
                "live continuation envelope + ResponseJournal",
                "tests/test_history_pending_live.py::test_child_gets_frozen_pending_and_completes_without_duplicate_post",
                "create_response.assert_not_called",
                "tests/test_history_pending_live.py::test_missing_journal_fails_before_client_creation",
                "create_response.assert_not_called",
            ),
        ),
    ),
)


_PROVIDER_CALL_NAMES = {
    "create_response",
    "create_batch",
    "create_image_batch",
    "create_image",
    "retrieve_response",
    "retrieve_batch",
}
_PROVIDER_INFRASTRUCTURE = {
    "kajovo/core/openai_client.py",
    "kajovo/core/batch_submit.py",
    "kajovo/core/structured_output.py",
}


def _assigned_literal(tree: ast.Module, name: str):
    for node in tree.body:
        target = None
        value = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign):
            target, value = node.target, node.value
        if isinstance(target, ast.Name) and target.id == name:
            try:
                return ast.literal_eval(value)
            except Exception:
                return None
    return None


def discover_workflow_modes() -> set[str]:
    path = ROOT / "kajovo/core/runs/executor.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    value = _assigned_literal(tree, "WORKFLOWS")
    if isinstance(value, dict):
        return {str(key) for key in value}
    # WORKFLOWS values jsou konstruktory, takže ast.literal_eval celé mapy selže.
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            target = node.targets[0] if isinstance(node, ast.Assign) else node.target
            raw = node.value
            if isinstance(target, ast.Name) and target.id == "WORKFLOWS" and isinstance(raw, ast.Dict):
                return {
                    str(key.value)
                    for key in raw.keys
                    if isinstance(key, ast.Constant) and isinstance(key.value, str)
                }
    return set()


def discover_history_modes() -> set[str]:
    path = ROOT / "kajovo/studio/history_launcher.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    value = _assigned_literal(tree, "SUPPORTED_DIRECT_MODES")
    return {str(item) for item in value} if isinstance(value, set) else set()


def discover_provider_sites() -> list[dict]:
    sites: list[dict] = []
    for path in sorted((ROOT / "kajovo/core").rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if rel in _PROVIDER_INFRASTRUCTURE:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = (
                node.func.attr
                if isinstance(node.func, ast.Attribute)
                else node.func.id
                if isinstance(node.func, ast.Name)
                else ""
            )
            if name in _PROVIDER_CALL_NAMES:
                sites.append({"path": rel, "line": node.lineno, "call": name})
    return sites


def _reference_parts(reference: str) -> tuple[Path, str]:
    raw_path, _, symbol = reference.partition("::")
    return ROOT / raw_path, symbol


def _test_source(reference: str) -> str | None:
    path, symbol = _reference_parts(reference)
    if not path.is_file() or not symbol:
        return None
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == symbol:
            segment = ast.get_source_segment(path.read_text(encoding="utf-8"), node)
            return segment or ""
    return None


def validate_runtime_inventory() -> tuple[list[dict], list[dict], dict]:
    errors: list[dict] = []
    unverified: list[dict] = []
    owners = {
        path: variant.id
        for variant in RUNTIME_VARIANTS
        for path in variant.runtime_paths
    }
    discovered_sites = discover_provider_sites()

    discovered_modes = discover_workflow_modes()
    inventory_modes = {
        mode
        for variant in RUNTIME_VARIANTS
        for mode in variant.modes
    }
    for mode in sorted(discovered_modes - inventory_modes):
        errors.append(
            {"scope": "workflow_dispatcher", "mode": mode, "error": "Workflow nemá vlastníka v runtime inventáři."}
        )
    for mode in sorted(discover_history_modes() - inventory_modes):
        errors.append(
            {"scope": "history_launcher", "mode": mode, "error": "Přímý history režim nemá vlastníka v runtime inventáři."}
        )

    for site in discovered_sites:
        if site["path"] not in owners:
            errors.append(
                {
                    "scope": "provider_site",
                    **site,
                    "error": "Provider call-site nemá vlastníka v runtime inventáři.",
                }
            )

    test_nodes: list[str] = []
    for variant in RUNTIME_VARIANTS:
        for path in variant.runtime_paths:
            if not (ROOT / path).is_file():
                errors.append(
                    {"variant": variant.id, "path": path, "error": "Runtime vlastník odkazuje na chybějící soubor."}
                )
        for step in variant.steps:
            impl_path, _ = _reference_parts(step.implementation)
            if not impl_path.is_file():
                errors.append(
                    {"variant": variant.id, "step": step.name, "error": "Chybí implementace.", "reference": step.implementation}
                )
            for polarity, reference, marker in (
                ("positive", step.positive_test, step.positive_marker),
                ("negative", step.negative_test, step.negative_marker),
            ):
                if not reference:
                    continue
                source = _test_source(reference)
                if source is None:
                    errors.append(
                        {"variant": variant.id, "step": step.name, "test": reference, "error": f"Chybí {polarity} test node."}
                    )
                    continue
                if marker and marker not in source:
                    errors.append(
                        {
                            "variant": variant.id,
                            "step": step.name,
                            "test": reference,
                            "error": f"{polarity} test neobsahuje očekávaný branch marker.",
                            "marker": marker,
                        }
                    )
                test_nodes.append(reference)
            if step.unverified_reason:
                unverified.append(
                    {"variant": variant.id, "step": step.name, "reason": step.unverified_reason}
                )
            elif not step.negative_test:
                errors.append(
                    {"variant": variant.id, "step": step.name, "error": "Krok nemá negativní regresní důkaz ani explicitní omezení."}
                )

    return errors, unverified, {
        "workflow_modes": sorted(discovered_modes),
        "history_modes": sorted(discover_history_modes()),
        "provider_sites": discovered_sites,
        "owners": owners,
        "test_nodes": list(dict.fromkeys(test_nodes)),
    }
