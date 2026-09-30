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
            StepEvidence(
                "QA blocked -> clarification",
                "kajovo/core/runs/qa.py::_run_qa",
                "blocked -> needs_clarification",
                "kajovo/core/orchestration/preparation.py::PreparationBlocked",
                "tests/test_qa_qfile_runtime_semantics.py::test_blocked_runtime_becomes_needs_clarification_without_followup_submit",
                "needs_clarification",
                "tests/test_qa_qfile_runtime_semantics.py::test_blocked_runtime_becomes_needs_clarification_without_followup_submit",
                "create_response.call_count == 1",
            ),
            StepEvidence(
                "QA conversation continuity",
                "kajovo/core/runs/qa.py::_run_qa",
                "response_id -> previous_response_id only on explicit continuation",
                "qa_continue_conversation",
                "tests/test_qa_qfile_runtime_semantics.py::test_qa_previous_response_id_only_when_explicitly_enabled",
                "previous_response_id",
                "tests/test_qa_qfile_runtime_semantics.py::test_qa_previous_response_id_only_when_explicitly_enabled",
                "not in payload",
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
                "tests/test_qfile_semantics.py::test_qfile_plan_requires_separate_confirmation_before_file_content",
                "requires_user_confirmation",
                "tests/test_qfile_semantics.py::test_qfile_rejects_unsafe_suggested_path_before_any_file_delivery",
                "../escape.md",
            ),
            StepEvidence(
                "QFILE blocked -> clarification",
                "kajovo/core/runs/qfile.py::_run_qfile",
                "blocked plan -> needs_clarification",
                "QFILE_PLAN_V1 + PreparationBlocked",
                "tests/test_qa_qfile_runtime_semantics.py::test_blocked_runtime_becomes_needs_clarification_without_followup_submit",
                "needs_clarification",
                "tests/test_qa_qfile_runtime_semantics.py::test_blocked_runtime_becomes_needs_clarification_without_followup_submit",
                "create_batch.assert_not_called",
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
                "deterministic decision branching",
                "kajovo/core/cascade_pipeline.py::CascadeRunExecutor",
                "decision output -> future target -> skipped branch",
                "runtime_schema_for_step + decision target validation",
                "tests/test_cascade_v2.py::test_decision_routes_to_future_step_and_skips_other_branch",
                "executed_step_ids",
                "tests/test_cascade_v2.py::test_forward_decision_target_is_allowed_in_draft_but_not_at_run",
                "pytest.raises",
            ),
            StepEvidence(
                "cascade strict output repair",
                "kajovo/core/cascade_pipeline.py::CascadeRunExecutor",
                "invalid contract -> bounded same-step retry -> failed|completed",
                "step output schema and semantic validation",
                "tests/test_cascade_v2.py::test_cascade_repair_instruction_is_only_in_new_step_request",
                "recovery_instruction",
                "tests/test_cascade_v2.py::test_step_contract_failure_retries_exactly_three_times",
                "call_count == 3",
            ),
            StepEvidence(
                "cascade binary child resume",
                "kajovo/core/cascade_production.py::produce_binary_outputs",
                "archived provider result -> child resume -> no repeated paid response",
                "container/file identity + archived bytes",
                "tests/test_cascade_production.py::test_first_step_child_resume_reuses_paid_responses_before_upload",
                "create_response.assert_not_called",
                "tests/test_cascade_production.py::test_document_rejects_foreign_container_before_download",
                "container_file_content.assert_not_called",
            ),
            StepEvidence(
                "cascade unknown submission",
                "kajovo/core/cascade_pipeline.py::CascadeRunExecutor",
                "submit uncertain -> submission_unknown -> restart blocked",
                "provider operation state + cascade_runtime checkpoint",
                "tests/test_cascade_unknown_submission.py::test_unknown_preserves_checkpoint_and_prevents_restart",
                "submission_unknown",
                "tests/test_cascade_unknown_submission.py::test_non_idempotent_transport_sends_once",
                "call_count == 1",
            ),
        ),
    ),
    RuntimeVariant(
        "photo",
        ("PHOTO",),
        (
            "kajovo/core/photo_prompt.py",
            "kajovo/core/photo_batch.py",
            "kajovo/studio/photos.py",
        ),
        (
            StepEvidence(
                "PHOTO batch submit identity",
                "kajovo/core/photo_batch.py::prepare_and_submit",
                "preparing -> submitted|submission_unknown",
                "kajovo/core/photo_batch.py::validate_photo_job",
                "tests/test_photo_studio.py::test_photo_batch_submit_creates_work_order_and_provider_operation",
                'operations == [("submitted", "batch_photo")]',
                "tests/test_photo_studio.py::test_photo_batch_uncertain_submit_is_not_reposted",
                "submission_unknown",
            ),
            StepEvidence(
                "PHOTO exact recovery",
                "kajovo/core/photo_batch.py::refresh_job",
                "submission_unknown -> exact batch match -> in_progress",
                "input_file_id + endpoint + provider identity",
                "tests/test_photo_studio.py::test_unknown_photo_batch_submit_recovers_exact_remote_match",
                "batch_recovered",
                "tests/test_batch_recovery_boundaries.py::test_photo_recovery_requires_unique_exact_provider_identity",
                "ambiguous",
            ),
            StepEvidence(
                "PHOTO result import validation",
                "kajovo/core/photo_batch.py::download_results",
                "completed remote batch -> validated image bytes -> downloaded|partial",
                "custom_id + provider result + image validation",
                "tests/test_photo_studio.py::test_download_results_preserves_original_and_maps_custom_id",
                "downloaded",
                "tests/test_batch_recovery_boundaries.py::test_photo_malformed_download_is_archived_without_output",
                "not list",
            ),
            StepEvidence(
                "PHOTO UI status monitor",
                "kajovo/studio/photos.py::PhotosPage.page_activated",
                "open page -> quiet batch refresh -> compact job cards",
                "Operations + list_batches + local PHOTO jobs",
                "tests/test_photo_studio.py::test_opening_photo_progress_schedules_quiet_refresh",
                "photos.jobs.refresh",
                "tests/test_provider_ui_semantics.py::test_photo_progress_has_no_cancel_action",
                "cancel_job",
            ),
        ),
    ),
    RuntimeVariant(
        "comic",
        ("COMIC",),
        ("kajovo/core/comic_service.py",),
        (
            StepEvidence(
                "comic partial batch retry/resume",
                "kajovo/core/comic_service.py::ComicService",
                "prepared -> submitted -> partial -> retry/resume",
                "comic batch item identity + revision store",
                "tests/test_comic_domain.py::test_batch_partial_retry_and_resume",
                "partial",
                "tests/test_comic_recovery.py::test_corrupt_image_does_not_discard_successful_panel",
                "status='failed'",
            ),
            StepEvidence(
                "comic unknown submit reconciliation",
                "kajovo/core/comic_service.py::ComicService",
                "submission_unknown -> exact reconciliation -> no second POST",
                "provider operation identity",
                "tests/test_comic_domain.py::test_unknown_submit_reconciles_without_second_post",
                "submits",
                "tests/test_audit2_comic_018_022_036.py::test_a2_018_intent_write_failure_after_started_is_known_unsent",
                "not_submitted",
            ),
            StepEvidence(
                "comic cancellation before submit",
                "kajovo/core/comic_service.py::ComicService.cancel",
                "prepared -> cancelled without provider submit",
                "operation status and provider batch absence",
                "tests/test_comic_recovery.py::test_cancel_before_submission_never_calls_provider",
                "client.submits == 0",
                "tests/test_comic_recovery.py::test_import_only_never_submits_prepared_batches",
                "client.submits == 0",
            ),
            StepEvidence(
                "comic downloaded-result restart",
                "kajovo/core/comic_service.py::ComicService",
                "downloaded archive -> local persistence retry -> completed",
                "archived asset hash + revision persistence",
                "tests/test_comic_recovery.py::test_storage_failure_resumes_from_downloaded_archive",
                "completed",
                "tests/test_comic_recovery.py::test_corrupt_image_does_not_discard_successful_panel",
                "invalid raster",
            ),
        ),
    ),
    RuntimeVariant(
        "resource-delivery",
        ("GENERATE", "MODIFY"),
        ("kajovo/core/orchestration/resource_delivery.py",),
        (
            StepEvidence(
                "image resource dispatch",
                "kajovo/core/orchestration/resource_delivery.py::dispatch_resource_target",
                "validated image target -> completed_unverified|submission_unknown",
                "kajovo/core/orchestration/resource_delivery.py::validate_resource_plan",
                "tests/test_audit2_preparation.py::test_image_request_and_frozen_projection_keep_explicit_parameters",
                "image_production",
                "tests/test_audit2_preparation.py::test_legacy_image_plan_is_not_silently_given_new_parameters",
                "legacy plán",
            ),
            StepEvidence(
                "manual resource handoff",
                "kajovo/core/orchestration/manual_resources.py::bind_manual_resource",
                "waiting_manual_resource -> bound immutable artifact -> files_complete_unverified",
                "MANUAL_RESOURCE_BINDINGS_V1 + artifact hash",
                "tests/test_remediation_resources.py::test_manual_input_completes_real_workflow_without_repeating_text",
                "waiting_manual_resource",
                "tests/test_remediation_resources.py::test_manual_resource_survives_child_without_provider_call",
                "match=\"hash\"",
            ),
            StepEvidence(
                "resource dependency validation",
                "kajovo/core/orchestration/resource_delivery.py::validate_resource_plan",
                "resource graph -> allowed provider/consumer relationships",
                "resource delivery contract",
                "tests/test_remediation_resources.py::test_skipped_original_is_available_as_exact_consumer_content",
                "consumer.md",
                "tests/test_remediation_resources.py::test_binary_content_provider_is_rejected_before_resource_dispatch",
                "textový provider",
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
                "tests/test_verification_profiles.py::test_verification_profile_accepts_valid_static_candidate_without_network",
                "format_result",
                "tests/test_verification_profiles.py::test_verification_profile_rejects_invalid_candidate_without_runner",
                "functional_result",
            ),
        ),
    ),
    RuntimeVariant(
        "source-and-input-runtime",
        ("GENERATE", "MODIFY", "QA", "QFILE"),
        (
            "kajovo/core/orchestration/source_pack.py",
            "kajovo/core/runs/attachments.py",
            "kajovo/core/runs/diagnostics.py",
        ),
        (
            StepEvidence(
                "freeze remote/local approved inputs",
                "kajovo/core/orchestration/source_pack.py::freeze_run_sources",
                "approved source -> immutable SourcePack",
                "SOURCE_PACK_V1 hashes and source policy",
                "tests/test_attachments.py::test_source_pack_freezes_remote_files_and_vector_store_members",
                "file_vector",
                "tests/test_attachments.py::test_source_pack_blocks_when_remote_bytes_cannot_be_frozen",
                "file_missing",
            ),
        ),
    ),
    RuntimeVariant(
        "resource-management-ui",
        (),
        ("kajovo/studio/resources.py",),
        (
            StepEvidence(
                "Studio file upload",
                "kajovo/studio/resources.py::ResourcesPage.upload",
                "selected local paths -> upload_file -> list_files refresh",
                "explicit file selection + operation lifecycle",
                "tests/test_provider_ui_semantics.py::test_resources_upload_executes_mock_provider_and_refreshes",
                "upload_file.call_args_list",
                "tests/test_provider_ui_semantics.py::test_resources_cancelled_upload_never_calls_provider",
                "upload_file.assert_not_called",
            ),
            StepEvidence(
                "Studio vector-store create",
                "kajovo/studio/resources.py::ResourcesPage.create_store",
                "confirmed name -> create_vector_store -> list refresh",
                "explicit confirmation + operation lifecycle",
                "tests/test_provider_ui_semantics.py::test_resources_create_store_executes_mock_provider",
                "create_vector_store.assert_called_once_with",
                "tests/test_provider_ui_semantics.py::test_resources_cancelled_store_dialog_never_calls_provider",
                "create_vector_store.assert_not_called",
            ),
            StepEvidence(
                "Studio vector-store attach file",
                "kajovo/studio/resources.py::ResourcesPage.add_files",
                "selected store/files -> add_file_to_vector_store -> store file refresh",
                "selected store identity",
                "tests/test_provider_ui_semantics.py::test_resource_store_add_calls_provider_and_missing_store_blocks_it",
                "add_file_to_vector_store.assert_called_once_with",
                "tests/test_provider_ui_semantics.py::test_resource_store_add_calls_provider_and_missing_store_blocks_it",
                "add_file_to_vector_store.assert_not_called",
            ),
            StepEvidence(
                "Studio partial delete",
                "kajovo/studio/resources.py::ResourcesPage.delete",
                "confirmed ids -> per-item delete -> local pruning even on later failure",
                "resource_deleted signal + current account key",
                "tests/test_audit2_studio_resources.py::test_partial_delete_prunes_successes_even_when_worker_fails",
                "expected",
                "tests/test_audit2_studio_resources.py::test_partial_delete_prunes_successes_even_when_worker_fails",
                "record.terminal == \"failed\"",
            ),
        ),
    ),
    RuntimeVariant(
        "batch-management-ui",
        (),
        ("kajovo/studio/batches.py",),
        (
            StepEvidence(
                "Studio batch monitoring",
                "kajovo/studio/batches.py::BatchesPage.refresh",
                "explicit refresh -> list_batches -> persisted remote state",
                "local timeout + batch identity",
                "tests/test_process_audit_regressions.py::test_batch_monitoring_starts_only_after_explicit_refresh",
                "automatic=True",
                "tests/test_process_audit_regressions.py::test_batch_monitoring_starts_only_after_explicit_refresh",
                "if not automatic",
            ),
            StepEvidence(
                "Studio batch cancel",
                "kajovo/studio/batches.py::BatchesPage.cancel",
                "cancellable selected batch -> one cancel_batch",
                "CANCELLABLE state + user confirmation",
                "tests/test_provider_ui_semantics.py::test_batch_cancel_calls_provider_once_for_cancellable_selection",
                "cancel_batch.assert_called_once_with",
                "tests/test_provider_ui_semantics.py::test_batch_cancel_ignores_non_cancellable_selection",
                "cancel_batch.assert_not_called",
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
                "kajovo/core/runs/cancellation.py::CancellationToken.raise_if_cancelled",
                "tests/test_run_cancellation.py::test_cancellation_token_starts_active_and_cancels_idempotently",
                "cancel",
                "tests/test_run_cancellation.py::test_executor_stop_before_provider_never_submits",
                "create_response.assert_not_called",
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
                'payload != pending["payload"]',
                "tests/test_history_pending_live.py::test_missing_journal_fails_before_client_creation",
                "create_response.assert_not_called",
            ),
            StepEvidence(
                "preparation checkpoint recovery",
                "kajovo/core/runs/recovery.py::prepare_runtime",
                "validated snapshot -> resume next preparation/production stage",
                "snapshot hash + source snapshot hash + runtime manifest whitelist",
                "tests/test_desktop_preparation_recovery.py::test_recovery_preserves_partial_preparation",
                "preparation_snapshot",
                "tests/test_desktop_preparation_recovery.py::test_recovery_rejects_changed_snapshot",
                "pytest.raises",
            ),
            StepEvidence(
                "definitive rejection recovery boundary",
                "kajovo/core/orchestration/batch_recovery.py::reconcile_unsubmitted",
                "definite rejection -> not_submitted without replay",
                "provider operation durable state",
                "tests/test_run_recovery_evidence.py::test_definitive_submit_rejection_is_durable_and_cannot_be_replayed",
                "rejected",
                "tests/test_run_recovery_evidence.py::test_recovery_does_not_replace_missing_canonical_state_with_legacy_request",
                "pytest.raises",
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
    "upload_file",
    "file_content",
    "cancel_batch",
    "create_vector_store",
    "add_file_to_vector_store",
    "delete_file",
    "delete_vector_store",
    "delete_vector_store_file",
    "list_files",
    "list_vector_stores",
    "list_vector_store_files",
    "list_batches",
    "update_vector_store_file_attributes",
    "retrieve_vector_store_file",
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
    for path in sorted((ROOT / "kajovo").rglob("*.py")):
        if path.name.startswith("._"):
            continue
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


def _symbol_exists(reference: str) -> bool:
    path, symbol = _reference_parts(reference)
    if not path.is_file():
        return False
    if not symbol:
        return True
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    parts = symbol.split(".")
    if len(parts) == 1:
        return any(
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and node.name == parts[0]
            for node in tree.body
        )
    if len(parts) == 2:
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and node.name == parts[0]:
                return any(
                    isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and child.name == parts[1]
                    for child in node.body
                )
    return False


def _test_source(reference: str) -> str | None:
    path, symbol = _reference_parts(reference)
    if not path.is_file() or not symbol:
        return None
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    source_text = path.read_text(encoding="utf-8")
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == symbol:
            # Parametrizace je součást důkazu větve stejně jako tělo testu.
            # Zahrň její literály, jinak manifest nemůže ověřit, že test
            # skutečně instanciuje deklarovaný režim nebo hodnotu.
            decorators = [
                ast.get_source_segment(source_text, decorator) or ""
                for decorator in node.decorator_list
            ]
            body = ast.get_source_segment(source_text, node) or ""
            return "\n".join((*decorators, body))
    return None


def validate_runtime_inventory() -> tuple[list[dict], list[dict], dict]:
    errors: list[dict] = []
    unverified: list[dict] = []
    owners: dict[str, list[str]] = {}
    for variant in RUNTIME_VARIANTS:
        for path in variant.runtime_paths:
            owners.setdefault(path, []).append(variant.id)
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
            if not _symbol_exists(step.implementation):
                errors.append(
                    {
                        "variant": variant.id,
                        "step": step.name,
                        "error": "Chybí deklarovaný implementační symbol.",
                        "reference": step.implementation,
                    }
                )
            if "::" in step.validator and not _symbol_exists(step.validator):
                errors.append(
                    {
                        "variant": variant.id,
                        "step": step.name,
                        "error": "Chybí deklarovaný validační symbol.",
                        "reference": step.validator,
                    }
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
