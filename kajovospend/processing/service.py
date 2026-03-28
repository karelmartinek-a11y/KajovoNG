from __future__ import annotations

import json
import math
import re
import threading
import unicodedata
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from kajovospend.integrations.ares_client import AresClient, AresError
from kajovospend.integrations.openai_client import OpenAIClient, OpenAIClientError
from kajovospend.integrations.secret_store import SecretStore, SecretStoreError
from kajovospend.ocr import DocumentLoader, LoadedDocument, LoadedPage
from kajovospend.persistence.repository import ProjectRepository


@dataclass(slots=True)
class ProcessingSummary:
    processed: int = 0
    finalized: int = 0
    quarantined: int = 0
    unrecognized: int = 0
    stopped: bool = False


@dataclass(slots=True)
class DocumentSegment:
    segment_index: int
    page_from: int
    page_to: int
    split_status: str = 'single'
    split_confidence: float = 1.0


class ProcessingService:
    SUPPORTED_IMPORT_SUFFIXES = {'.pdf', '.png', '.jpg', '.jpeg', '.bmp', '.txt'}

    def __init__(
        self,
        openai_client: OpenAIClient | None = None,
        ares_client: AresClient | None = None,
        secret_store: SecretStore | None = None,
        document_loader: DocumentLoader | None = None,
    ) -> None:
        self._stop_event = threading.Event()
        self.openai_client = openai_client or OpenAIClient()
        self.ares_client = ares_client or AresClient()
        self.secret_store = secret_store or SecretStore()
        self.document_loader = document_loader or DocumentLoader()

    def request_stop(self) -> None:
        self._stop_event.set()

    def reset_stop(self) -> None:
        self._stop_event.clear()

    def extract_offline_result(
        self,
        loaded_document: LoadedDocument,
        *,
        repo: ProjectRepository | None = None,
        pattern_match_fields: list[str] | None = None,
        source_name: str = '',
    ) -> dict[str, Any]:
        page_id_by_no = {page.page_no: page.page_no for page in loaded_document.pages}
        candidates: list[dict[str, Any]] = []
        for page in loaded_document.pages:
            candidates.extend(self._extract_page_candidates(page, page.page_no))
        candidates.extend(self._extract_filename_candidates(source_name, next(iter(page_id_by_no.values()), None)))
        if repo is not None:
            pattern_match = self._match_visual_pattern(repo, loaded_document, pattern_match_fields)
            if pattern_match:
                page_id = page_id_by_no.get(int(pattern_match.get('page_no') or 1))
                field_map = {
                    'ico': str(pattern_match.get('ico') or '').strip(),
                    'document_number': str(pattern_match.get('document_number') or '').strip(),
                    'issued_at': str(pattern_match.get('issued_at') or '').strip(),
                    'total_with_vat': str(pattern_match.get('total_with_vat') or '').strip(),
                }
                for field_name, raw_value in field_map.items():
                    normalized_value = raw_value
                    if field_name == 'total_with_vat':
                        normalized_value = self._normalize_amount(raw_value) or ''
                    elif field_name == 'issued_at':
                        normalized_value = self._normalize_date(raw_value) or ''
                    if normalized_value:
                        candidates.append(
                            {
                                'document_id': 0,
                                'page_id': page_id,
                                'field_name': field_name,
                                'raw_value': raw_value,
                                'normalized_value': normalized_value,
                                'confidence': 1.0,
                                'source_kind': 'visual-pattern',
                            }
                        )
        selected = self._select_best_candidates(candidates)
        return {
            'selected': selected,
            'candidates': candidates,
            'complete': self._is_complete_result(selected),
            'readable_text': loaded_document.has_readable_text,
            'reason': '' if self._is_complete_result(selected) else self._incomplete_reason(loaded_document, selected),
        }

    def process_import_directory(
        self,
        project_path: Path,
        input_dir: Path,
        *,
        output_directory: Path | None = None,
        openai_enabled: bool,
        openai_model: str = '',
        openai_usage_policy: str = 'manual_only',
        automatic_retry_limit: int = 2,
        openai_retry_limit: int = 1,
        block_without_ares: bool = True,
        quarantine_duplicate: bool = True,
        quarantine_missing_identification: bool = True,
        pattern_match_fields: list[str] | None = None,
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> ProcessingSummary:
        repo = ProjectRepository(project_path)
        self.reset_stop()
        summary = ProcessingSummary()
        source_files = sorted(
            [
                p
                for p in input_dir.rglob('*')
                if p.is_file() and p.suffix.lower() in self.SUPPORTED_IMPORT_SUFFIXES
            ]
        )
        total = len(source_files)
        self._report_progress(progress_callback, 0, total, 'Priprava importu')
        for position, source_file in enumerate(source_files, start=1):
            if self._stop_event.is_set():
                summary.stopped = True
                break
            try:
                loaded_document = self.document_loader.load(source_file)
                file_id = repo.import_file(source_file, reject_duplicates=quarantine_duplicate, loaded_document=loaded_document, output_directory=output_directory)
                file_row = repo.get_file_record(file_id)
                if file_row and file_row['status'] in ('final', 'quarantine', 'unrecognized', 'duplicate'):
                    summary.processed += 1
                    continue
                self.process_file(
                    project_path,
                    file_id,
                    repo=repo,
                    output_directory=output_directory,
                    openai_enabled=openai_enabled,
                    openai_model=openai_model,
                    openai_usage_policy=openai_usage_policy,
                    automatic_retry_limit=automatic_retry_limit,
                    openai_retry_limit=openai_retry_limit,
                    block_without_ares=block_without_ares,
                    quarantine_missing_identification=quarantine_missing_identification,
                    pattern_match_fields=pattern_match_fields,
                    loaded_document=loaded_document,
                )
                summary.processed += 1
            except Exception as exc:
                repo.log_event('error', 'import.file_failed', f'Soubor {source_file.name} selhal bez zastaveni davky.', uuid.uuid4().hex, payload={'error': str(exc), 'source': str(source_file)})
                repo.log_runtime(f'ERROR import.file_failed source={source_file} error={exc}')
            self._report_progress(progress_callback, position, total, source_file.name)
        return self._summarize(repo, summary)

    def process_pending(
        self,
        project_path: Path,
        *,
        output_directory: Path | None = None,
        openai_enabled: bool,
        openai_model: str = '',
        openai_usage_policy: str = 'manual_only',
        automatic_retry_limit: int = 2,
        openai_retry_limit: int = 1,
        block_without_ares: bool = True,
        quarantine_missing_identification: bool = True,
        pattern_match_fields: list[str] | None = None,
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> ProcessingSummary:
        repo = ProjectRepository(project_path)
        self.reset_stop()
        summary = ProcessingSummary()
        pending_rows = repo.get_pending_files()
        total = len(pending_rows)
        self._report_progress(progress_callback, 0, total, 'Priprava cekajicich pokusu')
        for position, row in enumerate(pending_rows, start=1):
            if self._stop_event.is_set():
                summary.stopped = True
                break
            try:
                self.process_file(
                    project_path,
                    int(row['id']),
                    repo=repo,
                    output_directory=output_directory,
                    openai_enabled=openai_enabled,
                    openai_model=openai_model,
                    openai_usage_policy=openai_usage_policy,
                    automatic_retry_limit=automatic_retry_limit,
                    openai_retry_limit=openai_retry_limit,
                    block_without_ares=block_without_ares,
                    quarantine_missing_identification=quarantine_missing_identification,
                    pattern_match_fields=pattern_match_fields,
                )
                summary.processed += 1
            except Exception as exc:
                repo.record_error(row['correlation_id'] or uuid.uuid4().hex, 'pending_failed', str(exc), file_id=int(row['id']), document_id=int(row['document_id']))
                repo.mark_file_status(int(row['id']), 'quarantine', str(exc))
            self._report_progress(progress_callback, position, total, str(row['original_name'] or row['id']))
        return self._summarize(repo, summary)

    def process_file(
        self,
        project_path: Path,
        file_id: int,
        *,
        document_id: int | None = None,
        repo: ProjectRepository | None = None,
        output_directory: Path | None = None,
        openai_enabled: bool,
        openai_model: str = '',
        openai_usage_policy: str = 'manual_only',
        initial_attempt_type: str = 'automatic',
        automatic_retry_limit: int = 2,
        openai_retry_limit: int = 1,
        block_without_ares: bool = True,
        quarantine_missing_identification: bool = True,
        pattern_match_fields: list[str] | None = None,
        loaded_document: LoadedDocument | None = None,
    ) -> None:
        repo = repo or ProjectRepository(project_path)
        file_row = repo.get_file_record(file_id)
        if file_row is None:
            return
        correlation_id = file_row['correlation_id'] or uuid.uuid4().hex
        source_path = project_path / str(file_row['internal_path'])
        loaded = loaded_document or self.document_loader.load(source_path)
        self._store_loaded_document(repo, file_id, loaded)

        segment_plan = self._detect_document_segments(loaded)
        if document_id is None:
            repo.sync_processing_document_segments(
                file_id,
                [
                    {
                        'segment_index': segment.segment_index,
                        'page_from': segment.page_from,
                        'page_to': segment.page_to,
                        'split_status': segment.split_status,
                        'split_confidence': segment.split_confidence,
                    }
                    for segment in segment_plan['segments']
                ],
            )
        document_rows = [dict(row) for row in repo.list_processing_documents_for_file(file_id)]
        if not document_rows:
            return
        if document_id is not None:
            document_rows = [row for row in document_rows if int(row['id']) == int(document_id)]
            if not document_rows:
                return
        if segment_plan.get('ambiguous'):
            ambiguous_reason = str(segment_plan.get('reason') or 'Nejista hranice vice dokladu v jednom PDF. Vyžaduje ruční rozdělení.')
            repo.mark_file_status(file_id, 'quarantine', ambiguous_reason)
            for row in document_rows:
                repo.update_processing_document(int(row['id']), quarantine_reason=ambiguous_reason)
                repo.finalize_result(file_id, int(row['id']), 'quarantine', ambiguous_reason, correlation_id, output_directory=output_directory)
            return

        repo.mark_file_status(file_id, 'processing')
        filename_context = str(file_row['original_name'] or '') if len(document_rows) == 1 else ''
        for row in document_rows:
            self._process_single_document(
                project_path,
                file_id,
                int(row['id']),
                repo=repo,
                output_directory=output_directory,
                openai_enabled=openai_enabled,
                openai_model=openai_model,
                openai_usage_policy=openai_usage_policy,
                initial_attempt_type=initial_attempt_type,
                automatic_retry_limit=automatic_retry_limit,
                openai_retry_limit=openai_retry_limit,
                block_without_ares=block_without_ares,
                quarantine_missing_identification=quarantine_missing_identification,
                pattern_match_fields=pattern_match_fields,
                loaded_document=self._segment_loaded_document(
                    loaded,
                    page_from=int(row.get('page_from') or 1),
                    page_to=int(row.get('page_to') or loaded.page_count),
                ),
                segment_meta=row,
                source_name=filename_context,
            )

    def _process_single_document(
        self,
        project_path: Path,
        file_id: int,
        document_id: int,
        *,
        repo: ProjectRepository,
        output_directory: Path | None,
        openai_enabled: bool,
        openai_model: str,
        openai_usage_policy: str,
        initial_attempt_type: str,
        automatic_retry_limit: int,
        openai_retry_limit: int,
        block_without_ares: bool,
        quarantine_missing_identification: bool,
        pattern_match_fields: list[str] | None,
        loaded_document: LoadedDocument,
        segment_meta: dict[str, Any],
        source_name: str,
    ) -> None:
        file_row = repo.get_file_record(file_id)
        if file_row is None:
            return
        correlation_id = file_row['correlation_id'] or uuid.uuid4().hex
        if initial_attempt_type == 'automatic' and automatic_retry_limit >= 0 and repo.count_document_attempts(document_id, attempt_types=['automatic']) >= automatic_retry_limit:
            repo.mark_file_status(file_id, 'quarantine', 'Byl vycerpan limit automatickych pokusu.')
            repo.finalize_result(file_id, document_id, 'quarantine', 'Byl vycerpan limit automatickych pokusu.', correlation_id, output_directory=output_directory)
            return

        self._store_document_context(
            repo,
            file_id,
            document_id,
            loaded_document,
            page_from=int(segment_meta.get('page_from') or 1),
            page_to=int(segment_meta.get('page_to') or loaded_document.page_count),
            split_status=str(segment_meta.get('split_status') or 'single'),
            split_confidence=float(segment_meta.get('split_confidence') or 1.0),
        )

        if self._process_manual_completion_if_available(repo, file_id, document_id, correlation_id, block_without_ares=block_without_ares, output_directory=output_directory):
            return

        page_rows = repo.list_document_pages(file_id)
        segment_page_nos = {int(page.page_no) for page in loaded_document.pages}
        page_id_by_no = {int(row['page_no']): int(row['id']) for row in page_rows if int(row['page_no']) in segment_page_nos}
        candidates = self._collect_field_candidates(
            repo,
            document_id,
            loaded_document,
            page_id_by_no=page_id_by_no,
            pattern_match_fields=pattern_match_fields,
            source_name=source_name,
        )
        selected = self._select_best_candidates(candidates)
        repo.replace_field_candidates(document_id, candidates)
        repo.replace_line_item_candidates(document_id, self._build_line_item_candidates(selected, page_id_by_no))
        self._apply_selected_values(repo, document_id, selected)

        merged_text = loaded_document.merged_text
        openai_direct_allowed = (
            initial_attempt_type != 'openai'
            and openai_enabled
            and bool(openai_model)
            and bool(merged_text.strip())
            and openai_usage_policy == 'openai_only'
            and repo.count_document_attempts(document_id, attempt_types=['openai']) < openai_retry_limit
        )
        if initial_attempt_type == 'openai':
            self._run_openai_attempt(
                repo,
                file_id,
                document_id,
                correlation_id,
                file_row=file_row,
                source_path=project_path / str(file_row['internal_path']),
                loaded=loaded_document,
                openai_model=openai_model,
                block_without_ares=block_without_ares,
                output_directory=output_directory,
            )
            return
        if openai_direct_allowed:
            self._run_openai_attempt(
                repo,
                file_id,
                document_id,
                correlation_id,
                file_row=file_row,
                source_path=project_path / str(file_row['internal_path']),
                loaded=loaded_document,
                openai_model=openai_model,
                block_without_ares=block_without_ares,
                output_directory=output_directory,
                branch='openai_direct',
                finalized_reason='Doklad byl vytěžen přes OpenAI.',
                success_reason='OpenAI dodala validní výsledek.',
            )
            return
        attempt1 = repo.create_attempt(file_id, document_id, initial_attempt_type, 'offline_content_extraction', 'local-offline-content', correlation_id)

        if self._is_complete_result(selected):
            ok, msg = self._validate_ares(repo, document_id, correlation_id, str(selected['ico']), attempt1)
            if ok:
                repo.finish_attempt(attempt1, result='success', reason='Offline vytazeni nad obsahem dokumentu dalo kompletni vysledek a ARES jej potvrdil.', next_step='finalize', document_state='final')
                repo.finalize_result(file_id, document_id, 'final', 'Offline vytazeni bylo uspesne.', correlation_id, output_directory=output_directory)
                return
            if not block_without_ares:
                self._mark_ares_bypassed(repo, file_id, document_id, str(selected['ico']), msg)
                repo.finish_attempt(attempt1, result='success', reason=f'{msg} Doklad byl dokoncen podle workflow pravidel bez ARES blokace.', next_step='finalize', document_state='final')
                repo.finalize_result(file_id, document_id, 'final', 'Doklad byl dokoncen bez ARES validace podle workflow pravidel.', correlation_id, output_directory=output_directory)
                return
            repo.finish_attempt(attempt1, result='failed', reason=msg, next_step='quarantine', document_state='quarantine')
            repo.finalize_result(file_id, document_id, 'quarantine', msg, correlation_id, output_directory=output_directory)
            return
        else:
            reason = self._incomplete_reason(loaded_document, selected)
            if reason == 'chybějící identifikace' and quarantine_missing_identification:
                repo.finish_attempt(attempt1, result='failed', reason=reason, next_step='quarantine', document_state='quarantine', blocking_error=True)
                repo.update_processing_document(document_id, quarantine_reason=reason)
                repo.finalize_result(file_id, document_id, 'quarantine', reason, correlation_id, output_directory=output_directory)
                return
            next_step = 'unrecognized'
            document_state = 'unrecognized'
            repo.finish_attempt(attempt1, result='failed', reason=reason, next_step=next_step, document_state=document_state, blocking_error=True)

        if self._stop_event.is_set():
            repo.finalize_result(file_id, document_id, 'quarantine', 'Zpracovani bylo zastaveno uzivatelem.', correlation_id, output_directory=output_directory)
            return

        reason = self._incomplete_reason(loaded_document, selected)
        repo.record_error(correlation_id, 'offline_incomplete', reason, file_id=file_id, document_id=document_id, payload={'warnings': loaded_document.warnings})
        repo.finalize_result(file_id, document_id, 'unrecognized', reason, correlation_id, output_directory=output_directory)

    def _process_manual_completion_if_available(self, repo: ProjectRepository, file_id: int, document_id: int, correlation_id: str, *, block_without_ares: bool = True, output_directory: Path | None = None) -> bool:
        manual_detail = repo.get_processing_document_detail(file_id, document_id)
        if not manual_detail:
            return False
        try:
            payload = json.loads(manual_detail['manual_payload_json'] or '{}')
        except Exception:
            payload = {}
        if not payload or payload.get('mode') != 'manual' or not manual_detail['ico']:
            return False
        attempt_manual = repo.create_attempt(file_id, document_id, 'manual', 'manual_completion', 'manual-ui', correlation_id)
        ok, msg = self._validate_ares(repo, document_id, correlation_id, str(manual_detail['ico']), attempt_manual)
        if ok:
            repo.finish_attempt(attempt_manual, result='success', reason='Rucni doplneni bylo validovano a dokonceno.', next_step='finalize', document_state='final')
            repo.finalize_result(file_id, document_id, 'final', 'Doklad byl dokoncen rucnim doplnenim.', correlation_id, output_directory=output_directory)
            return True
        if not block_without_ares:
            self._mark_ares_bypassed(repo, file_id, document_id, str(manual_detail['ico']), msg)
            repo.finish_attempt(attempt_manual, result='success', reason=f'{msg} Doklad byl dokoncen podle workflow pravidel bez ARES blokace.', next_step='finalize', document_state='final')
            repo.finalize_result(file_id, document_id, 'final', 'Doklad byl dokoncen rucnim doplnenim bez ARES validace.', correlation_id, output_directory=output_directory)
            return True
        repo.finish_attempt(attempt_manual, result='failed', reason=msg, next_step='quarantine', document_state='quarantine', blocking_error=True)
        repo.finalize_result(file_id, document_id, 'quarantine', msg, correlation_id, output_directory=output_directory)
        return True

    def _run_openai_attempt(
        self,
        repo: ProjectRepository,
        file_id: int,
        document_id: int,
        correlation_id: str,
        *,
        file_row: Any,
        source_path: Path,
        loaded: LoadedDocument,
        openai_model: str,
        block_without_ares: bool,
        output_directory: Path | None = None,
        branch: str = 'openai_direct',
        finalized_reason: str = 'Doklad byl dokoncen pomoci OpenAI.',
        success_reason: str = 'OpenAI dodala validni vysledek.',
    ) -> None:
        attempt_id = repo.create_attempt(file_id, document_id, 'openai', branch, 'openai', correlation_id)
        try:
            api_key = self.secret_store.get_openai_key()
            if not api_key:
                raise SecretStoreError('Chybi ulozeny OpenAI API key.')
            merged_text = loaded.merged_text
            if not merged_text.strip():
                raise OpenAIClientError('Doklad neobsahuje citelny text pro OpenAI vetev.')
            context = self._build_openai_context(repo, file_id, document_id, file_row=file_row, loaded=loaded)
            selected_page_numbers = self._select_openai_page_numbers(repo, file_id, document_id, loaded)
            image_inputs = self.document_loader.build_openai_image_inputs(source_path, max_pages=4, page_numbers=selected_page_numbers)
            result = self.openai_client.extract_document(
                api_key,
                openai_model,
                str(file_row['original_name']),
                context,
                image_inputs=image_inputs,
                source_descriptor={
                    'source_sha256': loaded.provenance.get('source_sha256', ''),
                    'source_path': loaded.provenance.get('source_path', ''),
                    'page_count': loaded.page_count,
                    'selected_page_numbers': selected_page_numbers,
                },
            )
        except (SecretStoreError, OpenAIClientError) as exc:
            repo.finish_attempt(attempt_id, result='failed', reason=str(exc), next_step='unrecognized', document_state='unrecognized', blocking_error=True)
            repo.record_error(correlation_id, 'openai_failed', str(exc), file_id=file_id, document_id=document_id, attempt_id=attempt_id)
            repo.log_event('error', 'openai.call_failed', str(exc), correlation_id, file_id=file_id, document_id=document_id, attempt_id=attempt_id, payload={'endpoint': getattr(self.openai_client, 'RESPONSES_URL', ''), 'model': openai_model, 'source_path': str(source_path), 'source_sha256': loaded.provenance.get('source_sha256', '')})
            repo.finalize_result(file_id, document_id, 'unrecognized', 'OpenAI větev selhala.', correlation_id, output_directory=output_directory)
            return
        repo.log_event('info', 'openai.call_completed', 'OpenAI request doběhl.', correlation_id, file_id=file_id, document_id=document_id, attempt_id=attempt_id, payload={
            'endpoint': getattr(result.audit, 'endpoint', ''),
            'model': getattr(result.audit, 'model', openai_model),
            'request_fingerprint': getattr(result.audit, 'request_fingerprint', ''),
            'duration_ms': getattr(result.audit, 'duration_ms', 0),
            'status_code': getattr(result.audit, 'status_code', 0),
            'response_id': getattr(result.audit, 'response_id', ''),
            'validation_status': getattr(result.audit, 'validation_status', ''),
            'error_kind': getattr(result.audit, 'error_kind', ''),
            'source_path': loaded.provenance.get('source_path', ''),
            'source_sha256': loaded.provenance.get('source_sha256', ''),
            'image_inputs': len(image_inputs),
            'selected_page_numbers': selected_page_numbers,
        })
        normalized = self._normalize_openai_result(repo, file_id, document_id, result)
        if self._is_openai_result_complete(normalized):
            repo.update_processing_document(
                document_id,
                document_number=normalized['document_number'],
                issued_at=normalized['issued_at'],
                total_with_vat=normalized['total_with_vat'],
            )
            items = self._build_openai_items(repo, document_id, normalized)
            if items:
                repo.replace_processing_items(document_id, items)
            repo.update_processing_supplier(
                document_id,
                ico=normalized['supplier_ico'],
                name=normalized['supplier_name'],
                dic=normalized['supplier_dic'],
                ares_status='pending',
            )
            ok, msg = self._validate_ares(repo, document_id, correlation_id, normalized['supplier_ico'], attempt_id)
            payload = {
                'supplier_ico': normalized['supplier_ico'],
                'document_number': normalized['document_number'],
                'issued_at': normalized['issued_at'],
                'total_with_vat': normalized['total_with_vat'],
                'vat_rate': normalized['vat_rate'],
            }
            if ok:
                repo.finalize_result(file_id, document_id, 'final', finalized_reason, correlation_id, output_directory=output_directory)
                if self._finish_openai_attempt_after_finalize(
                    repo,
                    attempt_id,
                    file_id,
                    document_id,
                    correlation_id,
                    success_reason=success_reason,
                    payload=payload,
                ):
                    return
                return
            if not block_without_ares:
                self._mark_ares_bypassed(repo, file_id, document_id, normalized['supplier_ico'], msg)
                repo.finalize_result(file_id, document_id, 'final', 'Doklad byl dokoncen pomoci OpenAI bez ARES validace.', correlation_id, output_directory=output_directory)
                if self._finish_openai_attempt_after_finalize(
                    repo,
                    attempt_id,
                    file_id,
                    document_id,
                    correlation_id,
                    success_reason=f'{msg} Doklad byl dokoncen podle workflow pravidel bez ARES blokace.',
                    payload=payload,
                ):
                    return
                return
            repo.finish_attempt(attempt_id, result='failed', reason=msg, next_step='quarantine', document_state='quarantine', blocking_error=True)
            repo.record_error(correlation_id, 'ares_failed', msg, file_id=file_id, document_id=document_id, attempt_id=attempt_id, payload=payload)
            repo.finalize_result(file_id, document_id, 'quarantine', 'OpenAI vystup neprosel ARES validaci.', correlation_id, output_directory=output_directory)
            return
        repo.finish_attempt(attempt_id, result='failed', reason='OpenAI nedodal validní výsledek.', next_step='unrecognized', document_state='unrecognized', blocking_error=True)
        repo.record_error(correlation_id, 'openai_invalid', 'OpenAI nedodal validní výsledek.', file_id=file_id, document_id=document_id, attempt_id=attempt_id, payload={'raw_text': result.raw_text, 'validation_errors': list(getattr(result, 'validation_errors', [])), 'response_id': getattr(result.audit, 'response_id', ''), 'request_fingerprint': getattr(result.audit, 'request_fingerprint', '')})
        repo.finalize_result(file_id, document_id, 'unrecognized', 'OpenAI cesta selhala.', correlation_id, output_directory=output_directory)

    def _mark_ares_bypassed(self, repo: ProjectRepository, file_id: int, document_id: int, ico: str, reason: str) -> None:
        detail = repo.get_processing_document_detail(file_id, document_id)
        if not detail:
            return
        repo.update_processing_supplier(
            document_id,
            ico=ico,
            name=str(detail['supplier_name'] or ''),
            dic=str(detail['dic'] or ''),
            vat_payer=bool(detail['vat_payer']),
            address=str(detail['address'] or ''),
            ares_status='verified',
            ares_payload={'workflow_override': True, 'reason': reason},
        )

    def _build_openai_context(
        self,
        repo: ProjectRepository,
        file_id: int,
        document_id: int,
        *,
        file_row: Any,
        loaded: LoadedDocument,
    ) -> str:
        detail = repo.get_processing_document_detail(file_id, document_id)
        field_candidates = repo.list_field_candidates(document_id)
        line_item_candidates = repo.list_line_item_candidates(document_id)
        selected_page_numbers = self._select_openai_page_numbers(repo, file_id, document_id, loaded)
        detail_payload = {
            'file_name': str(file_row['original_name'] or ''),
            'page_count': loaded.page_count,
            'warnings': list(loaded.warnings),
            'selected_openai_pages': selected_page_numbers,
            'current_ico': str(detail['ico'] or '') if detail else '',
            'current_supplier_name': str(detail['supplier_name'] or '') if detail else '',
            'current_document_number': str(detail['document_number'] or '') if detail else '',
            'current_issued_at': str(detail['issued_at'] or '') if detail else '',
            'current_total_with_vat': str(detail['total_with_vat'] or '') if detail else '',
        }
        sections = ['METADATA', json.dumps(detail_payload, ensure_ascii=False), 'PAGE_ORDER', ','.join(str(page.page_no) for page in loaded.pages), 'SELECTED_OPENAI_PAGES', ','.join(str(page_no) for page_no in selected_page_numbers), 'OCR_PAGES']
        for page in loaded.pages:
            sections.append(
                json.dumps(
                    {
                        'page_no': page.page_no,
                        'source_kind': page.source_kind,
                        'ocr_status': page.ocr_status,
                        'text': page.text[:2500],
                    },
                    ensure_ascii=False,
                )
            )
        best_by_field: dict[str, dict[str, Any]] = {}
        page_field_map: dict[int, list[dict[str, Any]]] = {}
        if field_candidates:
            sections.append('OFFLINE_FIELD_CANDIDATES')
            for candidate in field_candidates[:24]:
                page_no = int(candidate['page_id'] or 0)
                page_field_map.setdefault(page_no, []).append(dict(candidate))
                best = best_by_field.get(str(candidate['field_name']))
                if best is None or float(candidate['confidence'] or 0) > float(best.get('confidence') or 0):
                    best_by_field[str(candidate['field_name'])] = dict(candidate)
                sections.append(
                    json.dumps(
                        {
                            'page_id': candidate['page_id'],
                            'field_name': candidate['field_name'],
                            'normalized_value': candidate['normalized_value'],
                            'confidence': candidate['confidence'],
                            'source_kind': candidate['source_kind'],
                            'chosen': candidate['chosen'],
                        },
                        ensure_ascii=False,
                    )
                )
            sections.append('BEST_OFFLINE_FIELDS')
            for field_name in sorted(best_by_field):
                candidate = best_by_field[field_name]
                sections.append(
                    json.dumps(
                        {
                            'field_name': field_name,
                            'page_id': candidate.get('page_id'),
                            'normalized_value': candidate.get('normalized_value'),
                            'confidence': candidate.get('confidence'),
                            'source_kind': candidate.get('source_kind'),
                        },
                        ensure_ascii=False,
                    )
                )
            sections.append('PAGE_FIELD_SUMMARY')
            for page_no in sorted(page_field_map):
                page_entries = [
                    {
                        'field_name': row.get('field_name'),
                        'normalized_value': row.get('normalized_value'),
                        'confidence': row.get('confidence'),
                    }
                    for row in sorted(page_field_map[page_no], key=lambda row: (-float(row.get('confidence') or 0), str(row.get('field_name') or '')))
                ]
                sections.append(json.dumps({'page_id': page_no, 'fields': page_entries[:8]}, ensure_ascii=False))
        page_line_map: dict[int, list[dict[str, Any]]] = {}
        if line_item_candidates:
            sections.append('LINE_ITEM_CANDIDATES')
            for candidate in line_item_candidates[:12]:
                page_no = int(candidate['page_id'] or 0)
                page_line_map.setdefault(page_no, []).append(dict(candidate))
                sections.append(
                    json.dumps(
                        {
                            'page_id': candidate['page_id'],
                            'line_no': candidate['line_no'],
                            'name_raw': candidate['name_raw'],
                            'total_price_raw': candidate['total_price_raw'],
                            'vat_raw': candidate['vat_raw'],
                            'confidence': candidate['confidence'],
                        },
                        ensure_ascii=False,
                    )
                )
            sections.append('PAGE_LINE_ITEM_SUMMARY')
            for page_no in sorted(page_line_map):
                page_entries = [
                    {
                        'line_no': row.get('line_no'),
                        'name_raw': row.get('name_raw'),
                        'total_price_raw': row.get('total_price_raw'),
                        'vat_raw': row.get('vat_raw'),
                        'confidence': row.get('confidence'),
                    }
                    for row in sorted(page_line_map[page_no], key=lambda row: (int(row.get('line_no') or 0), -float(row.get('confidence') or 0)))
                ]
                sections.append(json.dumps({'page_id': page_no, 'line_items': page_entries[:8]}, ensure_ascii=False))
        return '\n'.join(section for section in sections if section).strip()

    def _select_openai_page_numbers(
        self,
        repo: ProjectRepository,
        file_id: int,
        document_id: int,
        loaded: LoadedDocument,
        *,
        max_pages: int = 4,
    ) -> list[int]:
        available_pages = [int(page.page_no) for page in loaded.pages]
        if not available_pages:
            return [1]
        page_rows = repo.list_document_pages(file_id)
        page_no_by_id = {int(row['id']): int(row['page_no']) for row in page_rows}
        page_scores = {page_no: (2.0 if page_no == 1 else 0.0) + (0.5 if page.text.strip() else 0.0) for page_no, page in ((int(item.page_no), item) for item in loaded.pages)}
        field_weight = {
            'ico': 4.0,
            'document_number': 4.0,
            'issued_at': 3.5,
            'total_with_vat': 4.5,
        }
        for candidate in repo.list_field_candidates(document_id):
            page_no = page_no_by_id.get(int(candidate['page_id'] or 0))
            if page_no is None:
                continue
            confidence = float(candidate['confidence'] or 0)
            chosen_bonus = 2.0 if int(candidate['chosen'] or 0) else 0.0
            field_bonus = field_weight.get(str(candidate['field_name'] or ''), 1.0)
            page_scores[page_no] = page_scores.get(page_no, 0.0) + field_bonus + confidence + chosen_bonus
        for candidate in repo.list_line_item_candidates(document_id):
            page_no = page_no_by_id.get(int(candidate['page_id'] or 0))
            if page_no is None:
                continue
            confidence = float(candidate['confidence'] or 0)
            chosen_bonus = 1.0 if int(candidate['chosen'] or 0) else 0.0
            page_scores[page_no] = page_scores.get(page_no, 0.0) + 1.5 + confidence + chosen_bonus
        ordered = [page_no for page_no, _score in sorted(page_scores.items(), key=lambda item: (-item[1], item[0]))]
        selected: list[int] = []
        if 1 in available_pages:
            selected.append(1)
        for page_no in ordered:
            if page_no not in selected:
                selected.append(page_no)
            if len(selected) >= max_pages:
                break
        for page_no in available_pages:
            if page_no not in selected:
                selected.append(page_no)
            if len(selected) >= max_pages:
                break
        return selected[:max_pages]

    def _store_loaded_document(self, repo: ProjectRepository, file_id: int, loaded: LoadedDocument) -> None:
        repo.update_file_page_count(file_id, loaded.page_count)
        page_ids = repo.replace_document_pages(
            file_id,
            [
                {
                    'page_no': page.page_no,
                    'width': page.width,
                    'height': page.height,
                    'rotation_deg': page.rotation_deg,
                    'text_layer_present': page.text_layer_present,
                    'source_kind': page.source_kind,
                    'ocr_status': page.ocr_status,
                    'confidence_avg': page.confidence_avg,
                    'extracted_text': page.text,
                }
                for page in loaded.pages
            ],
        )
        for page_id, page in zip(page_ids, loaded.pages, strict=False):
            blocks = []
            if page.text.strip():
                blocks.append(
                    {
                        'block_no': 1,
                        'raw_text': page.text,
                        'normalized_text': self._normalize_text(page.text),
                        'confidence': page.confidence_avg,
                        'source_engine': 'pypdf' if page.source_kind.startswith('pdf') else ('tesseract' if page.source_kind == 'image-ocr' else 'text'),
                        'source_kind': page.source_kind,
                    }
                )
            repo.replace_page_text_blocks(page_id, blocks)

    def _store_document_context(
        self,
        repo: ProjectRepository,
        file_id: int,
        document_id: int,
        loaded: LoadedDocument,
        *,
        page_from: int,
        page_to: int,
        split_status: str,
        split_confidence: float,
    ) -> None:
        payload = {
            'mode': 'offline',
            'page_count': loaded.page_count,
            'warnings': loaded.warnings,
            'segment': {
                'page_from': int(page_from),
                'page_to': int(page_to),
                'split_status': split_status,
                'split_confidence': float(split_confidence),
            },
        }
        detail = repo.get_processing_document_detail(file_id, document_id)
        if detail:
            try:
                existing_payload = json.loads(detail['manual_payload_json'] or '{}')
            except Exception:
                existing_payload = {}
            if isinstance(existing_payload, dict) and existing_payload.get('mode') == 'manual':
                existing_payload['page_count'] = loaded.page_count
                existing_payload['warnings'] = loaded.warnings
                existing_payload['segment'] = payload['segment']
                payload = existing_payload
        repo.update_processing_document(
            document_id,
            manual_payload=payload,
        )

    def _segment_loaded_document(self, loaded: LoadedDocument, *, page_from: int, page_to: int) -> LoadedDocument:
        selected_pages = [page for page in loaded.pages if int(page.page_no) >= int(page_from) and int(page.page_no) <= int(page_to)]
        if not selected_pages:
            selected_pages = list(loaded.pages[:1])
        return LoadedDocument(file_type=loaded.file_type, pages=selected_pages, warnings=list(loaded.warnings), source_path=loaded.source_path, source_sha256=loaded.source_sha256, source_bytes=loaded.source_bytes, provenance=dict(loaded.provenance))

    def _detect_document_segments(self, loaded: LoadedDocument) -> dict[str, Any]:
        default_segment = [DocumentSegment(segment_index=1, page_from=1, page_to=max(1, loaded.page_count))]
        if loaded.file_type != 'pdf' or loaded.page_count <= 1:
            return {'segments': default_segment, 'ambiguous': False, 'reason': ''}
        page_profiles: list[dict[str, Any]] = []
        for page in loaded.pages:
            page_candidates = self._extract_page_candidates(page, page.page_no)
            page_selected = self._select_best_candidates(page_candidates)
            completeness = sum(1 for key in ('ico', 'document_number', 'issued_at', 'total_with_vat') if page_selected.get(key))
            has_header = bool(page_selected.get('ico') or page_selected.get('document_number'))
            page_profiles.append(
                {
                    'page_no': int(page.page_no),
                    'selected': page_selected,
                    'completeness': completeness,
                    'has_header': has_header,
                }
            )
        boundary_pages: list[int] = []
        ambiguous = False
        for index in range(1, len(page_profiles)):
            current = page_profiles[index]
            previous = page_profiles[index - 1]
            current_selected = current['selected']
            previous_selected = previous['selected']
            differing_identity = any(
                current_selected.get(field) and previous_selected.get(field) and current_selected.get(field) != previous_selected.get(field)
                for field in ('ico', 'document_number', 'issued_at')
            )
            strong_new_document = current['completeness'] >= 3 and current['has_header'] and (
                differing_identity or previous['completeness'] >= 3
            )
            weak_new_document = current['completeness'] == 2 and current['has_header'] and differing_identity
            if strong_new_document:
                boundary_pages.append(int(current['page_no']))
            elif weak_new_document:
                ambiguous = True
        if not boundary_pages:
            return {'segments': default_segment, 'ambiguous': ambiguous, 'reason': 'Nejista hranice vice dokladu v jednom PDF.' if ambiguous else ''}
        segments: list[DocumentSegment] = []
        starts = [1, *boundary_pages]
        ends = [page - 1 for page in boundary_pages] + [loaded.page_count]
        for index, (start, end) in enumerate(zip(starts, ends, strict=False), start=1):
            segments.append(DocumentSegment(segment_index=index, page_from=int(start), page_to=int(end), split_status='split', split_confidence=0.95))
        return {'segments': segments, 'ambiguous': False, 'reason': ''}

    def _collect_field_candidates(
        self,
        repo: ProjectRepository,
        document_id: int,
        loaded: LoadedDocument,
        *,
        page_id_by_no: dict[int, int],
        pattern_match_fields: list[str] | None = None,
        source_name: str = '',
    ) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        for page in loaded.pages:
            page_id = page_id_by_no.get(page.page_no)
            candidates.extend(self._extract_page_candidates(page, page_id))
        candidates.extend(self._extract_filename_candidates(source_name, next(iter(page_id_by_no.values()), None), document_id=document_id))
        pattern_match = self._match_visual_pattern(repo, loaded, pattern_match_fields)
        if pattern_match:
            page_id = page_id_by_no.get(int(pattern_match.get('page_no') or 1))
            field_map = {
                'ico': str(pattern_match.get('ico') or '').strip(),
                'document_number': str(pattern_match.get('document_number') or '').strip(),
                'issued_at': str(pattern_match.get('issued_at') or '').strip(),
                'total_with_vat': str(pattern_match.get('total_with_vat') or '').strip(),
            }
            for field_name, raw_value in field_map.items():
                normalized_value = raw_value
                if field_name == 'total_with_vat':
                    normalized_value = self._normalize_amount(raw_value) or ''
                elif field_name == 'issued_at':
                    normalized_value = self._normalize_date(raw_value) or ''
                if normalized_value:
                    candidates.append(
                        {
                            'document_id': document_id,
                            'page_id': page_id,
                            'field_name': field_name,
                            'raw_value': raw_value,
                            'normalized_value': normalized_value,
                            'confidence': 1.0,
                            'source_kind': 'visual-pattern',
                        }
                    )
        return candidates

    def _extract_page_candidates(self, page: LoadedPage, page_id: int | None) -> list[dict[str, Any]]:
        text = page.text or ''
        if not text.strip():
            return []
        candidates: list[dict[str, Any]] = []
        lines = [line.strip() for line in text.splitlines() if line.strip()]

        for ico, confidence in self._find_ico_candidates(lines):
            candidates.append(self._candidate(page_id, 'ico', ico, ico, confidence, page.source_kind))

        document_number = self._extract_document_number(lines)
        if document_number:
            candidates.append(self._candidate(page_id, 'document_number', document_number, document_number, 0.8, page.source_kind))

        issued_at = self._extract_issued_at(lines)
        if issued_at:
            candidates.append(self._candidate(page_id, 'issued_at', issued_at, issued_at, 0.8, page.source_kind))

        for raw_amount, confidence in self._extract_total_candidates(lines):
            normalized = self._normalize_amount(raw_amount)
            if normalized:
                candidates.append(self._candidate(page_id, 'total_with_vat', raw_amount, normalized, confidence, page.source_kind))
        return candidates

    def _extract_filename_candidates(self, source_name: str, page_id: int | None, *, document_id: int | None = None) -> list[dict[str, Any]]:
        normalized_name = self._normalize_text(Path(source_name).stem if source_name else '')
        if not normalized_name:
            return []
        candidates: list[dict[str, Any]] = []
        filename_patterns = [
            r'(?:invoice-fa|fa[_-]?|faktura[_ -]?|invoice[_ -]?|doklad[_ -]?|zf[_ -]?)([a-z0-9/_-]{4,})',
            r'\b(\d{6,}[a-z0-9/_-]*)\b',
        ]
        for index, pattern in enumerate(filename_patterns):
            for match in re.finditer(pattern, normalized_name, re.IGNORECASE):
                raw_candidate = match.group(1).strip().upper()
                cleaned = self._normalize_document_number_candidate(raw_candidate)
                if cleaned:
                    confidence = 0.83 if index == 0 else 0.6
                    candidates.append(
                        {
                            'document_id': document_id,
                            'page_id': page_id,
                            'field_name': 'document_number',
                            'raw_value': raw_candidate,
                            'normalized_value': cleaned,
                            'confidence': confidence,
                            'source_kind': 'file-name',
                        }
                    )
            if candidates:
                break
        return candidates

    def _candidate(self, page_id: int | None, field_name: str, raw_value: str, normalized_value: str, confidence: float, source_kind: str) -> dict[str, Any]:
        return {
            'page_id': page_id,
            'field_name': field_name,
            'raw_value': raw_value,
            'normalized_value': normalized_value,
            'confidence': confidence,
            'source_kind': source_kind,
        }

    def _find_ico_candidates(self, lines: list[str]) -> list[tuple[str, float]]:
        seen: set[str] = set()
        ranked: list[tuple[str, float]] = []
        supplier_tokens = ('dodavatel', 'supplier', 'prodejce', 'prodavajici', 'vystavil', 'zhotovitel', 'poskytovatel')
        buyer_tokens = ('odberatel', 'odběratel', 'customer', 'buyer', 'bill to', 'prijemce', 'recipient', 'invoice to')
        neutral_tokens = ('ico', 'ic', 'dic')
        for index, line in enumerate(lines):
            context_window = ' '.join(lines[max(0, index - 1): min(len(lines), index + 2)])
            normalized_context = self._normalize_text(context_window)
            for value in re.findall(r'\b\d{8}\b', line):
                if value in seen:
                    continue
                confidence = 0.7
                if any(token in normalized_context for token in supplier_tokens):
                    confidence = 0.98
                elif any(token in normalized_context for token in buyer_tokens):
                    confidence = 0.2
                elif any(token in normalized_context for token in neutral_tokens):
                    confidence = 0.82
                ranked.append((value, confidence))
                seen.add(value)
        return ranked

    def _extract_document_number(self, lines: list[str]) -> str:
        primary_context_tokens = (
            'cislo dokladu',
            'evidencni cislo',
            'doklad',
            'faktura',
            'invoice',
            'variabilni symbol',
            'objednav',
            'reference',
            'ref',
            'cislo',
        )
        secondary_context_tokens = ('doklad', 'faktura', 'invoice', 'variabil', 'symbol', 'objednav', 'reference', 'cislo', 'číslo', 'ref')
        for line in lines:
            normalized = self._normalize_text(line)
            if not any(token in normalized for token in primary_context_tokens):
                continue
            tokens = [
                self._normalize_document_number_candidate(match.group(0))
                for match in re.finditer(r'[A-Z0-9][A-Z0-9_/\-]{3,}', normalized.upper())
            ]
            valid_tokens = [token for token in tokens if token]
            if valid_tokens:
                return sorted(
                    valid_tokens,
                    key=lambda token: (-sum(char.isdigit() for char in token), -len(token), token),
                )[0]
        for line in lines:
            normalized = self._normalize_text(line)
            if not any(token in normalized for token in secondary_context_tokens):
                continue
            secondary_tokens = [
                self._normalize_document_number_candidate(match.group(0))
                for match in re.finditer(r'[A-Z0-9][A-Z0-9_/\-]{3,}', normalized.upper())
            ]
            valid_tokens = [token for token in secondary_tokens if token]
            if valid_tokens:
                return sorted(
                    valid_tokens,
                    key=lambda token: (-sum(char.isdigit() for char in token), -len(token), token),
                )[0]
        return ''

    def _extract_issued_at(self, lines: list[str]) -> str:
        for line in lines:
            normalized = self._normalize_text(line)
            if any(token in normalized for token in ('datum', 'vystaven', 'issued', 'date')):
                match = re.search(r'(\d{4}-\d{2}-\d{2}|\d{1,2}[./-]\d{1,2}[./-]\d{4})', normalized)
                if match:
                    parsed = self._normalize_date(match.group(1))
                    if parsed:
                        return parsed
        for line in lines:
            normalized = self._normalize_text(line)
            match = re.search(r'\b(\d{4}-\d{2}-\d{2}|\d{1,2}[./-]\d{1,2}[./-]\d{4})\b', normalized)
            if match:
                parsed = self._normalize_date(match.group(1))
                if parsed:
                    return parsed
        return ''

    def _extract_total_candidates(self, lines: list[str]) -> list[tuple[str, float]]:
        amounts: list[tuple[str, float]] = []
        total_keywords = ('celkem', 'k uhrade', 'k úhradě', 'total', 'celkova castka', 'castka k uhrade', 'uhrada celkem', 'k zaplaceni', 'amount due', 'price to pay')
        currency_keywords = ('kc', 'kč', 'czk', 'eur', 'usd')
        subtotal_keywords = ('zaklad', 'dph', 'sazba', 'bez dph', 'vat')
        date_keywords = ('datum', 'vystaven', 'issued', 'date', 'splatnost', 'duzp')
        for line in lines:
            normalized = self._normalize_text(line)
            values = re.findall(r'\d{1,3}(?:[ .]\d{3})*(?:[,.]\d{2})|\d+(?:[,.]\d{2})', normalized)
            if not values:
                continue
            if any(keyword in normalized for keyword in date_keywords) and not any(keyword in normalized for keyword in total_keywords + currency_keywords):
                continue
            confidence = 0.45
            if any(keyword in normalized for keyword in total_keywords):
                confidence = 0.95
            elif any(keyword in normalized for keyword in currency_keywords):
                confidence = 0.7
            elif any(keyword in normalized for keyword in subtotal_keywords):
                confidence = 0.35
            filtered_values = [value for value in values if not self._looks_like_date_fragment(value)]
            if not filtered_values:
                continue
            amounts.append((filtered_values[-1], confidence))
        return amounts

    def _select_best_candidates(self, candidates: list[dict[str, Any]]) -> dict[str, str]:
        selected: dict[str, str] = {}
        grouped: dict[str, list[dict[str, Any]]] = {}
        for candidate in candidates:
            candidate['chosen'] = False
            grouped.setdefault(str(candidate['field_name']), []).append(candidate)
        for field_name, rows in grouped.items():
            if field_name == 'total_with_vat':
                best = sorted(
                    rows,
                    key=lambda row: (
                        -float(row.get('confidence') or 0),
                        -self._safe_float(str(row.get('normalized_value') or '')),
                        int(row.get('page_id') or 0),
                    ),
                )[0]
            elif field_name == 'document_number':
                best = sorted(
                    rows,
                    key=lambda row: (
                        -float(row.get('confidence') or 0),
                        -sum(char.isdigit() for char in str(row.get('normalized_value') or '')),
                        -len(str(row.get('normalized_value') or '')),
                        int(row.get('page_id') or 0),
                    ),
                )[0]
            else:
                best = sorted(rows, key=lambda row: (-float(row.get('confidence') or 0), int(row.get('page_id') or 0), str(row.get('normalized_value') or '')))[0]
            best['chosen'] = True
            selected[field_name] = str(best.get('normalized_value') or '')
        return selected

    def _normalize_document_number_candidate(self, value: str) -> str:
        candidate = re.sub(r'^[^A-Z0-9]+|[^A-Z0-9/_-]+$', '', str(value or '').strip().upper())
        candidate = candidate.strip('/_-')
        if not candidate:
            return ''
        stopwords = {
            'SLOUZI',
            'CELKEM',
            'BYL',
            'CISLO',
            'PRODEJKA',
            'DLE',
            'REF',
            'PRI',
            'HIT',
            'EAE',
            'CNI',
            'U/VARIABILNI',
        }
        if candidate in stopwords:
            return ''
        if len(candidate) < 4:
            return ''
        if not any(char.isdigit() for char in candidate):
            return ''
        return candidate

    def _looks_like_date_fragment(self, value: str) -> bool:
        return bool(re.fullmatch(r'\d{1,2}[,.]\d{2}', value))

    def _apply_selected_values(self, repo: ProjectRepository, document_id: int, selected: dict[str, str]) -> None:
        document_number = selected.get('document_number')
        issued_at = selected.get('issued_at')
        total_with_vat = self._safe_float(selected.get('total_with_vat'))
        repo.update_processing_document(
            document_id,
            document_number=document_number if document_number else None,
            issued_at=issued_at if issued_at else None,
            total_with_vat=total_with_vat if total_with_vat else None,
            quarantine_reason='' if selected.get('ico') else None,
        )
        if selected.get('ico'):
            repo.update_processing_supplier(document_id, ico=selected['ico'], ares_status='pending')
        if total_with_vat:
            repo.replace_processing_items(document_id, self._build_items_from_amount(total_with_vat, source_kind='offline', name='Offline vytazena polozka'))

    def _build_line_item_candidates(self, selected: dict[str, str], page_id_by_no: dict[int, int]) -> list[dict[str, Any]]:
        total = selected.get('total_with_vat')
        if not total:
            return []
        return [
            {
                'page_id': next(iter(page_id_by_no.values()), None),
                'line_no': 1,
                'name_raw': 'Offline vytazena polozka',
                'qty_raw': '1',
                'unit_price_raw': total,
                'total_price_raw': total,
                'vat_raw': '21',
                'confidence': 0.6,
                'chosen': True,
            }
        ]

    def _match_visual_pattern(
        self,
        repo: ProjectRepository,
        loaded_document: LoadedDocument,
        pattern_match_fields: list[str] | None = None,
    ) -> dict[str, str]:
        selected_fields = [field.strip() for field in (pattern_match_fields if pattern_match_fields is not None else ['ico', 'anchor_text']) if field.strip()]
        page_by_no = {page.page_no: page for page in loaded_document.pages}
        for row in repo.list_visual_patterns():
            if not int(row['is_active']):
                continue
            try:
                rules = json.loads(row['recognition_rules'] or '{}')
                fields = json.loads(row['field_map'] or '{}')
                preview_state = json.loads(row['preview_state'] or '{}')
            except Exception:
                continue
            target_page = page_by_no.get(int(row['page_no'] or 1))
            if target_page is None:
                continue
            match_text = self._page_matching_text(target_page.text, preview_state)
            normalized_page = self._normalize_text(match_text)
            region_tokens = self._pattern_region_tokens(str(row['document_path'] or ''), int(row['page_no'] or 1), preview_state)
            if region_tokens and not all(token in normalized_page for token in region_tokens):
                continue
            tokens = []
            for field_name in selected_fields:
                if field_name == 'ico':
                    token = str(rules.get('ico') or fields.get('supplier_ico') or '').strip()
                elif field_name == 'anchor_text':
                    token = str(rules.get('anchor_text') or '').strip()
                else:
                    token = str(rules.get(field_name) or fields.get(field_name) or '').strip()
                if token:
                    tokens.append(self._normalize_text(token))
            if (tokens or region_tokens) and all(token in normalized_page for token in tokens):
                return {
                    'page_no': str(row['page_no'] or 1),
                    'ico': str(rules.get('ico') or fields.get('supplier_ico') or '').strip(),
                    'document_number': str(fields.get('document_number') or '').strip(),
                    'issued_at': str(fields.get('issued_at') or '').strip(),
                    'total_with_vat': str(fields.get('total_with_vat') or '').strip(),
            }
        return {}

    def _pattern_region_tokens(self, document_path: str, page_no: int, preview_state: dict[str, Any]) -> list[str]:
        if not preview_state or not document_path:
            return []
        try:
            loaded = self.document_loader.load(Path(document_path))
        except Exception:
            return []
        page = next((item for item in loaded.pages if int(item.page_no) == int(page_no)), None)
        if page is None:
            return []
        region_text = self._page_matching_text(page.text, preview_state)
        tokens: list[str] = []
        for token in re.findall(r'[a-z0-9]{4,}', self._normalize_text(region_text)):
            if token not in tokens:
                tokens.append(token)
            if len(tokens) >= 6:
                break
        return tokens

    def _page_matching_text(self, text: str, preview_state: dict[str, Any] | None) -> str:
        if not preview_state:
            return text
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if len(lines) <= 1:
            return text
        canvas_height = float(
            preview_state.get('canvas_h')
            or preview_state.get('canvas_height')
            or preview_state.get('preview_height')
            or 0
        )
        region_height = float(preview_state.get('h') or 0)
        if canvas_height <= 0 or region_height <= 0:
            return text
        top_ratio = max(0.0, min(1.0, float(preview_state.get('y') or 0) / canvas_height))
        bottom_ratio = max(top_ratio, min(1.0, (float(preview_state.get('y') or 0) + region_height) / canvas_height))
        start = min(len(lines) - 1, int(top_ratio * len(lines)))
        end = max(start + 1, min(len(lines), int(math.ceil(bottom_ratio * len(lines)))))
        return '\n'.join(lines[start:end])

    def _is_complete_result(self, selected: dict[str, str]) -> bool:
        return bool(
            selected.get('ico')
            and selected.get('document_number')
            and selected.get('issued_at')
            and self._safe_float(selected.get('total_with_vat')) > 0
        )

    def _incomplete_reason(self, loaded: LoadedDocument, selected: dict[str, str]) -> str:
        if not loaded.has_readable_text:
            return 'Doklad neobsahuje čitelný text pro offline vytěžení.'
        completeness_signals = sum(1 for key in ('document_number', 'issued_at', 'total_with_vat') if selected.get(key))
        if not selected.get('ico') and completeness_signals >= 2:
            return 'chybějící identifikace'
        return 'Offline vytěžení nedodalo kompletní validní výsledek.'

    def _normalize_amount(self, raw_value: str) -> str:
        cleaned = raw_value.replace(' ', '').replace('\xa0', '').replace(',', '.')
        try:
            return f'{float(cleaned):.2f}'
        except ValueError:
            return ''

    def _normalize_date(self, raw_value: str) -> str:
        value = raw_value.strip()
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            return value
        match = re.fullmatch(r'(\d{1,2})[./-](\d{1,2})[./-](\d{4})', value)
        if not match:
            return ''
        day, month, year = match.groups()
        return f'{int(year):04d}-{int(month):02d}-{int(day):02d}'

    def _normalize_text(self, value: str) -> str:
        normalized = unicodedata.normalize('NFKD', value).encode('ascii', 'ignore').decode('ascii')
        return normalized.lower()

    def _normalize_ico(self, value: str) -> str:
        return ''.join(ch for ch in str(value or '').strip() if ch.isdigit())[:8]

    def _normalize_vat_rate(self, value: str) -> str:
        text = str(value or '').strip().replace('%', '').replace(',', '.')
        if not text:
            return ''
        try:
            parsed = float(text)
        except ValueError:
            return ''
        return str(int(parsed)) if parsed.is_integer() else str(parsed)

    def _safe_float(self, value: str | None) -> float:
        if not value:
            return 0.0
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    def _normalize_openai_result(
        self,
        repo: ProjectRepository,
        file_id: int,
        document_id: int,
        result: Any,
    ) -> dict[str, Any]:
        detail = repo.get_processing_document_detail(file_id, document_id)
        issued_at = self._normalize_date(str(result.issued_at or '')) or str(detail['issued_at'] or '') if detail else ''
        total_with_vat = float(result.total_with_vat or 0) if float(result.total_with_vat or 0) > 0 else float(detail['total_with_vat'] or 0) if detail else 0.0
        return {
            'supplier_ico': self._normalize_ico(str(result.supplier_ico or '')) or (self._normalize_ico(str(detail['ico'] or '')) if detail else ''),
            'supplier_name': str(result.supplier_name or '').strip() or (str(detail['supplier_name'] or '').strip() if detail else ''),
            'supplier_dic': str(result.supplier_dic or '').strip() or (str(detail['dic'] or '').strip() if detail else ''),
            'document_number': str(result.document_number or '').strip() or (str(detail['document_number'] or '').strip() if detail else ''),
            'issued_at': issued_at,
            'total_with_vat': round(total_with_vat, 2),
            'vat_rate': self._normalize_vat_rate(str(getattr(result, 'vat_rate', '') or '')),
            'items': self._normalize_openai_items(getattr(result, 'items', None)),
        }

    def _is_openai_result_complete(self, normalized: dict[str, Any]) -> bool:
        return bool(
            normalized.get('supplier_ico')
            and normalized.get('document_number')
            and normalized.get('issued_at')
            and float(normalized.get('total_with_vat') or 0) > 0
        )

    def _build_openai_items(
        self,
        repo: ProjectRepository,
        document_id: int,
        normalized: dict[str, Any],
    ) -> list[dict[str, float | str]]:
        explicit_items = self._validated_openai_items(normalized)
        if explicit_items:
            return explicit_items
        vat_rate = str(normalized.get('vat_rate') or '').strip()
        if vat_rate:
            return self._build_items_from_amount(
                float(normalized.get('total_with_vat') or 0),
                source_kind='openai',
                name='OpenAI polozka',
                vat_rate=vat_rate,
            )
        existing_items = repo.list_processing_items(document_id)
        if existing_items:
            total = round(sum(float(item['total_price'] or 0) for item in existing_items), 2)
            expected_total = round(float(normalized.get('total_with_vat') or 0), 2)
            if total == expected_total and all(str(item['vat_rate'] or '').strip() for item in existing_items):
                return [
                    {
                        'name': str(item['name'] or '').strip(),
                        'quantity': float(item['quantity'] or 1),
                        'unit_price': float(item['unit_price'] or item['total_price'] or 0),
                        'total_price': float(item['total_price'] or 0),
                        'vat_rate': str(item['vat_rate'] or '').strip(),
                        'source_kind': str(item['source_kind'] or 'openai'),
                    }
                    for item in existing_items
                ]
        return []

    def _normalize_openai_items(self, items: Any) -> list[dict[str, float | str]]:
        if not isinstance(items, list):
            return []
        normalized_items: list[dict[str, float | str]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            name = str(item.get('name', '') or '').strip()
            vat_rate = self._normalize_vat_rate(str(item.get('vat_rate', '') or ''))
            quantity = float(item.get('quantity') or 1)
            unit_price = float(item.get('unit_price') or item.get('total_price') or 0)
            total_price = float(item.get('total_price') or 0)
            if not name or total_price <= 0:
                continue
            if quantity <= 0:
                quantity = 1.0
            if unit_price <= 0:
                unit_price = total_price
            normalized_items.append(
                {
                    'name': name,
                    'quantity': quantity,
                    'unit_price': unit_price,
                    'total_price': total_price,
                    'vat_rate': vat_rate,
                    'source_kind': 'openai',
                }
            )
        return normalized_items

    def _validated_openai_items(self, normalized: dict[str, Any]) -> list[dict[str, float | str]]:
        items = list(normalized.get('items') or [])
        if not items:
            return []
        if any(not str(item.get('vat_rate') or '').strip() for item in items):
            return []
        total = round(sum(float(item.get('total_price') or 0) for item in items), 2)
        expected_total = round(float(normalized.get('total_with_vat') or 0), 2)
        if total != expected_total:
            return []
        return items

    def _finish_openai_attempt_after_finalize(
        self,
        repo: ProjectRepository,
        attempt_id: int,
        file_id: int,
        document_id: int,
        correlation_id: str,
        *,
        success_reason: str,
        payload: dict[str, Any],
    ) -> bool:
        file_row = repo.get_file_record(file_id)
        detail = repo.get_processing_document_detail(file_id, document_id)
        final_status = str(file_row['status'] or '') if file_row else ''
        if final_status == 'final':
            repo.finish_attempt(attempt_id, result='success', reason=success_reason, next_step='finalize', document_state='final', payload=payload)
            return True
        blocked_reason = ''
        if detail:
            blocked_reason = str(detail['promotion_blocked_reason'] or detail['last_error'] or '').strip()
        blocked_reason = blocked_reason or 'Promotion OpenAI vysledku bylo zablokovano validacnimi pravidly.'
        repo.finish_attempt(attempt_id, result='failed', reason=blocked_reason, next_step='retry_pending', document_state='retry_pending', blocking_error=True, payload=payload)
        repo.record_error(correlation_id, 'openai_promotion_blocked', blocked_reason, file_id=file_id, document_id=document_id, attempt_id=attempt_id, payload=payload)
        return False

    def _validate_ares(self, repo: ProjectRepository, document_id: int, correlation_id: str, ico: str, attempt_id: int) -> tuple[bool, str]:
        try:
            supplier = self.ares_client.get_supplier(ico)
        except AresError as exc:
            repo.record_ares_validation(ico, 'failed', {'error': str(exc)})
            repo.record_error(correlation_id, 'ares_failed', str(exc), document_id=document_id, attempt_id=attempt_id)
            repo.update_processing_supplier(document_id, ico=ico, ares_status='failed')
            return False, str(exc)
        repo.record_ares_validation(supplier.ico, 'success', supplier.raw_payload)
        repo.update_processing_supplier(document_id, ico=supplier.ico, name=supplier.name, dic=supplier.dic, vat_payer=supplier.vat_payer, address=supplier.address, ares_status='verified', ares_payload=supplier.raw_payload)
        return True, 'ARES validace byla uspesna.'

    def _report_progress(self, callback: Callable[[int, int, str], None] | None, current: int, total: int, label: str) -> None:
        if callback is None:
            return
        percent = int(round((current / total) * 100)) if total > 0 else 0
        payload = {
            'label': label,
            'detail': f'Zpracován krok {current} z {total}.' if total > 0 else label,
            'step': label.lower().replace(' ', '_'),
            'percent': max(0, min(percent, 100)),
        }
        callback(current, total, json.dumps(payload, ensure_ascii=False))

    def _build_items_from_amount(self, amount: float, *, source_kind: str, name: str, vat_rate: str = '21') -> list[dict[str, float | str]]:
        amount = round(float(amount or 0), 2)
        normalized_vat_rate = self._normalize_vat_rate(vat_rate)
        return [{'name': name, 'quantity': 1, 'unit_price': amount, 'total_price': amount, 'vat_rate': normalized_vat_rate, 'source_kind': source_kind}] if amount > 0 and normalized_vat_rate else []

    def _summarize(self, repo: ProjectRepository, summary: ProcessingSummary) -> ProcessingSummary:
        for row in repo.list_documents():
            if row['status'] == 'final':
                summary.finalized += 1
            elif row['status'] == 'quarantine':
                summary.quarantined += 1
            elif row['status'] == 'unrecognized':
                summary.unrecognized += 1
        return summary
