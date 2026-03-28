from __future__ import annotations

from pathlib import Path
import sqlite3

from kajovospend.app.constants import PROJECT_INPUT_DIR
from kajovospend.app.container import ServiceContainer
from kajovospend.integrations.secret_store import SecretStoreError
from kajovospend.persistence.repository import ProjectRepository
from kajovospend.processing.service import ProcessingSummary
from kajovospend.project.models import ProjectStatus
from kajovospend.project.project_service import ProjectError


class AppController:
    OPENAI_USAGE_POLICIES = {'manual_only', 'openai_only'}
    SUPPORTED_INPUT_SUFFIXES = {'.pdf', '.png', '.jpg', '.jpeg', '.bmp', '.txt'}

    def __init__(self, container: ServiceContainer) -> None:
        self.container = container
        self.settings_store = container.settings_store
        self.settings = container.settings
        self.project_service = container.project_service
        self.processing_service = container.processing_service
        self.supplier_service = container.supplier_service
        self.reporting_service = container.reporting_service
        self.secret_store = container.secret_store
        self.openai_client = container.openai_client
        self.status = ProjectStatus(path=None, is_connected=False)
        self._runtime_progress = {'active': False, 'current': 0, 'total': 0, 'label': '', 'mode': '', 'percent': 0, 'eta_seconds': None, 'detail': '', 'step': '', 'started_at_monotonic': None}

    def bootstrap(self) -> ProjectStatus:
        if self.settings.last_project_path:
            result = self.project_service.validate_project(self.settings.last_project_path)
            if result.ok:
                self.connect_project(self.settings.last_project_path, save=False)
            else:
                self.status = ProjectStatus(path=None, is_connected=False, message=result.message)
        return self.status

    def create_project(self, directory: str, name: str, *, working_db_path: str = '', production_db_path: str = '') -> ProjectStatus:
        path = self.project_service.create_project(directory, name, working_db_path=working_db_path or None, production_db_path=production_db_path or None)
        return self.connect_project(str(path))

    def connect_project(self, directory: str, *, save: bool = True) -> ProjectStatus:
        result = self.project_service.validate_project(directory)
        if not result.ok:
            raise ProjectError(result.message)
        path = Path(directory).expanduser().resolve()
        metadata = self.project_service.load_metadata(path)
        repo = ProjectRepository(path)
        snapshot = repo.status_snapshot()
        self._sync_project_directories(path)
        self.status = ProjectStatus(path=path, is_connected=True, name=metadata.get('project_name', path.name), message=result.message, queue_size=int(snapshot['queue_size']), last_success=str(snapshot['last_success']), last_error=str(snapshot['last_error']), processing_running=False, input_dir_status=self._input_dir_status())
        if save:
            self.settings.last_project_path = str(path)
            self.settings_store.save(self.settings)
        return self.status

    def disconnect_project(self) -> ProjectStatus:
        self.settings.last_project_path = ''
        self.settings.input_directory = ''
        self.settings.output_directory = ''
        self.settings_store.save(self.settings)
        self.status = ProjectStatus(path=None, is_connected=False, input_dir_status=self._input_dir_status())
        return self.status

    def set_input_dir(self, path: str) -> None:
        self.settings.input_directory = path
        self.settings_store.save(self.settings)
        self.status.input_dir_status = self._input_dir_status()

    def ensure_output_structure(self, path: str) -> dict[str, str]:
        output_dir = Path(path).expanduser()
        output_dir.mkdir(parents=True, exist_ok=True)
        quarantine_dir = output_dir / 'KARANTENA'
        unrecognized_dir = output_dir / 'NEROZPOZNANE'
        quarantine_dir.mkdir(parents=True, exist_ok=True)
        unrecognized_dir.mkdir(parents=True, exist_ok=True)
        if not output_dir.exists() or not output_dir.is_dir():
            raise OSError(f'Output adresář se nepodařilo připravit: {output_dir}')
        if not quarantine_dir.exists() or not quarantine_dir.is_dir():
            raise OSError(f'Adresář KARANTENA se nepodařilo připravit: {quarantine_dir}')
        if not unrecognized_dir.exists() or not unrecognized_dir.is_dir():
            raise OSError(f'Adresar NEROZPOZNANE se nepodarilo pripravit: {unrecognized_dir}')
        return {
            'output_directory': str(output_dir),
            'quarantine_directory': str(quarantine_dir),
            'unrecognized_directory': str(unrecognized_dir),
        }

    def set_output_dir(self, path: str) -> dict[str, str]:
        normalized = path.strip()
        if not normalized:
            self.settings.output_directory = ''
            self.settings_store.save(self.settings)
            return {'output_directory': '', 'quarantine_directory': '', 'unrecognized_directory': ''}
        prepared = self.ensure_output_structure(normalized)
        self.settings.output_directory = prepared['output_directory']
        self.settings_store.save(self.settings)
        return prepared

    def count_importable_input_files(self) -> int:
        if self._input_dir_status() != 'Připraven':
            return 0
        input_dir = self.project_input_dir()
        if input_dir is None:
            return 0
        return sum(1 for candidate in input_dir.rglob('*') if candidate.is_file() and candidate.suffix.lower() in self.SUPPORTED_INPUT_SUFFIXES)

    def get_openai_key_masked(self) -> str:
        return '—'

    def get_openai_key(self) -> str:
        try:
            return self.secret_store.get_openai_key()
        except SecretStoreError:
            return ''

    def has_openai_key(self) -> bool:
        try:
            return bool(self.secret_store.get_openai_key())
        except SecretStoreError:
            return False

    def save_openai_key(self, key: str) -> None:
        if not key.strip():
            raise ValueError('API key nesmí být prázdný.')
        self.secret_store.set_openai_key(key.strip())
        self.settings.openai_api_key_masked = ''
        self.settings_store.save(self.settings)

    def delete_openai_key(self) -> None:
        self.secret_store.delete_openai_key()
        self.settings.openai_api_key_masked = ''
        self.settings.openai_enabled = False
        self.settings_store.save(self.settings)

    def fetch_openai_models(self) -> list[str]:
        key = self.secret_store.get_openai_key()
        if not key:
            raise ValueError('Nejdřív uložte platný API key.')
        return self.openai_client.list_models(key)

    def update_openai_settings(self, enabled: bool, model: str, usage_policy: str) -> None:
        if enabled and (not self.has_openai_key() or not model.strip()):
            raise ValueError('Pro zapnutí OpenAI větve je nutný uložený API key a vybraný model.')
        normalized_policy = usage_policy.strip() or 'manual_only'
        if normalized_policy not in self.OPENAI_USAGE_POLICIES:
            raise ValueError('Neznámé pravidlo použití OpenAI.')
        self.settings.openai_enabled = enabled
        self.settings.openai_model = model.strip()
        self.settings.openai_usage_policy = normalized_policy
        self.settings_store.save(self.settings)

    def start_import(self, progress_callback=None) -> ProcessingSummary:
        if not self.status.is_connected or not self.status.path:
            raise ProjectError('Projekt není připojen.')
        if self.settings.require_valid_input_directory and self._input_dir_status() != 'Připraven':
            raise ProjectError('Vstupní adresář není validní.')
        self.status.processing_running = True
        self._set_runtime_progress(active=True, current=0, total=0, label='Příprava importu', mode='import')
        try:
            return self.processing_service.process_import_directory(
                self.status.path,
                self.project_input_dir(),
                output_directory=None,
                openai_enabled=self.settings.openai_enabled,
                openai_model=self.settings.openai_model,
                openai_usage_policy=self.settings.openai_usage_policy,
                automatic_retry_limit=self.settings.automatic_retry_limit,
                openai_retry_limit=self.settings.openai_retry_limit,
                block_without_ares=self.settings.block_without_ares,
                quarantine_duplicate=self.settings.quarantine_duplicate,
                quarantine_missing_identification=self.settings.quarantine_missing_identification,
                pattern_match_fields=self.settings.pattern_match_fields,
                progress_callback=self._progress_callback_wrapper(progress_callback, mode='import'),
            )
        finally:
            self.status.processing_running = False
            self._clear_runtime_progress()
            self.refresh_status()

    def process_pending_attempts(self, progress_callback=None) -> ProcessingSummary:
        if not self.status.is_connected or not self.status.path:
            raise ProjectError('Projekt není připojen.')
        self.status.processing_running = True
        self._set_runtime_progress(active=True, current=0, total=0, label='Příprava čekajících pokusů', mode='pending')
        try:
            return self.processing_service.process_pending(
                self.status.path,
                output_directory=None,
                openai_enabled=self.settings.openai_enabled,
                openai_model=self.settings.openai_model,
                openai_usage_policy=self.settings.openai_usage_policy,
                automatic_retry_limit=self.settings.automatic_retry_limit,
                openai_retry_limit=self.settings.openai_retry_limit,
                block_without_ares=self.settings.block_without_ares,
                quarantine_missing_identification=self.settings.quarantine_missing_identification,
                pattern_match_fields=self.settings.pattern_match_fields,
                progress_callback=self._progress_callback_wrapper(progress_callback, mode='pending'),
            )
        finally:
            self.status.processing_running = False
            self._clear_runtime_progress()
            self.refresh_status()

    def stop_processing(self) -> None:
        self.processing_service.request_stop()
        self.status.processing_running = False
        self._clear_runtime_progress()
        self.refresh_status()

    def list_attempts(self, **filters):
        return [] if not self.status.path else self._normalize_result(ProjectRepository(self.status.path).list_attempts(**filters))

    def list_attempts_page(self, **filters):
        return {'rows': [], 'total_count': 0, 'page': 1, 'page_size': filters.get('page_size', 100)} if not self.status.path else self._normalize_result(ProjectRepository(self.status.path).list_attempts_page(**filters))

    def get_attempt_detail(self, attempt_id: int):
        return None if not self.status.path else self._normalize_result(ProjectRepository(self.status.path).get_attempt_detail(attempt_id))

    def list_documents(self):
        return [] if not self.status.path else self._normalize_result(ProjectRepository(self.status.path).list_documents())

    def list_documents_page(self, **filters):
        return {'rows': [], 'total_count': 0, 'page': 1, 'page_size': filters.get('page_size', 100)} if not self.status.path else self._normalize_result(ProjectRepository(self.status.path).list_documents_page(**filters))

    def queue_manual_retry(self, file_ids: list[int]) -> int:
        changed = 0
        if self.status.path:
            repo = ProjectRepository(self.status.path)
            eligible = [
                file_id
                for file_id in file_ids
                if repo.count_file_attempts(file_id, attempt_types=['automatic_local_retry', 'manual']) < self.settings.manual_retry_limit
            ]
            changed = repo.mark_retry_pending(eligible)
        self.refresh_status()
        return changed

    def bulk_mark(self, file_ids: list[int], target_state: str, note: str) -> int:
        changed = 0 if not self.status.path else ProjectRepository(self.status.path).bulk_mark(file_ids, target_state, note)
        self.refresh_status()
        return changed

    def dashboard_data(self):
        if not self.status.path:
            return {}
        data = self._normalize_result(ProjectRepository(self.status.path).dashboard_data())
        data['runtime'] = {
            'processing_running': bool(self.status.processing_running),
            'program_status': self._program_status_label(),
            'progress': dict(self._runtime_progress),
        }
        return data

    def operational_panel_data(self):
        return {} if not self.status.path else self._normalize_result(ProjectRepository(self.status.path).operational_panel_data())

    def expense_data(self):
        return {} if not self.status.path else self._normalize_result(self.reporting_service.expense_data(self.status.path))

    def list_final_documents(self, **filters):
        return [] if not self.status.path else self._normalize_result(self.reporting_service.list_final_documents(self.status.path, **filters))

    def list_final_documents_page(self, **filters):
        return {'rows': [], 'total_count': 0, 'page': 1, 'page_size': filters.get('page_size', 100)} if not self.status.path else self._normalize_result(self.reporting_service.list_final_documents_page(self.status.path, **filters))

    def get_final_document_detail(self, document_id: int):
        return None if not self.status.path else self._normalize_result(self.reporting_service.get_final_document_detail(self.status.path, document_id))

    def list_final_items(self, **filters):
        return [] if not self.status.path else self._normalize_result(self.reporting_service.list_final_items(self.status.path, **filters))

    def list_final_items_page(self, **filters):
        return {'rows': [], 'total_count': 0, 'page': 1, 'page_size': filters.get('page_size', 200)} if not self.status.path else self._normalize_result(self.reporting_service.list_final_items_page(self.status.path, **filters))

    def get_final_item_detail(self, item_id: int):
        return None if not self.status.path else self._normalize_result(self.reporting_service.get_final_item_detail(self.status.path, item_id))

    def update_final_document(self, document_id: int, **changes):
        if not self.status.path:
            raise ProjectError('Projekt není připojen.')
        self.reporting_service.update_final_document(self.status.path, document_id, **changes)

    def export_dataset(self, dataset: str, destination: str, *, format: str, progress_callback=None, **filters):
        if not self.status.path:
            raise ProjectError('Projekt není připojen.')
        return self.reporting_service.export_rows(self.status.path, dataset, Path(destination), format=format, progress_callback=progress_callback, **filters)

    def list_suppliers(self):
        return [] if not self.status.path else self._normalize_result(self.supplier_service.list_suppliers(self.status.path))

    def list_suppliers_page(self, **filters):
        return {'rows': [], 'total_count': 0, 'page': 1, 'page_size': filters.get('page_size', 100)} if not self.status.path else self._normalize_result(self.supplier_service.list_suppliers_page(self.status.path, **filters))

    def get_supplier_detail(self, supplier_id: int):
        if not self.status.path:
            return None
        repo = ProjectRepository(self.status.path)
        return {
            'supplier': self._normalize_result(self.supplier_service.get_supplier_detail(self.status.path, supplier_id)),
            'documents': self._normalize_result(repo.list_supplier_documents(supplier_id)),
            'items': self._normalize_result(repo.list_supplier_items(supplier_id)),
        }

    def list_supplier_documents_page(self, supplier_id: int, **filters):
        return {'rows': [], 'total_count': 0, 'page': 1, 'page_size': filters.get('page_size', 100)} if not self.status.path else self._normalize_result(ProjectRepository(self.status.path).list_supplier_documents_page(supplier_id, **filters))

    def list_supplier_items_page(self, supplier_id: int, **filters):
        return {'rows': [], 'total_count': 0, 'page': 1, 'page_size': filters.get('page_size', 100)} if not self.status.path else self._normalize_result(ProjectRepository(self.status.path).list_supplier_items_page(supplier_id, **filters))

    def create_supplier(self, *, ico: str, name: str, dic: str, vat_payer: bool, address: str) -> int:
        if not self.status.path:
            raise ProjectError('Projekt není připojen.')
        return self.supplier_service.create_supplier(self.status.path, ico=ico, name=name, dic=dic, vat_payer=vat_payer, address=address)

    def update_supplier(self, supplier_id: int, *, ico: str, name: str, dic: str, vat_payer: bool, address: str) -> None:
        if not self.status.path:
            raise ProjectError('Projekt není připojen.')
        self.supplier_service.update_supplier(self.status.path, supplier_id, ico=ico, name=name, dic=dic, vat_payer=vat_payer, address=address)

    def delete_supplier(self, supplier_id: int) -> None:
        if not self.status.path:
            raise ProjectError('Projekt není připojen.')
        self.supplier_service.delete_supplier(self.status.path, supplier_id)

    def merge_suppliers(self, source_supplier_id: int, target_supplier_id: int) -> None:
        if not self.status.path:
            raise ProjectError('Projekt není připojen.')
        self.supplier_service.merge_suppliers(self.status.path, source_supplier_id, target_supplier_id)

    def refresh_supplier_from_ares(self, supplier_id: int) -> None:
        if not self.status.path:
            raise ProjectError('Projekt není připojen.')
        self.supplier_service.refresh_from_ares(self.status.path, supplier_id)

    def get_supplier_data_from_ares(self, ico: str) -> dict[str, object]:
        if not self.status.path:
            raise ProjectError('Projekt není připojen.')
        return self._normalize_result(self.supplier_service.load_from_ares(ico))

    def list_quarantine_documents(self, *, reason: str = '', search: str = ''):
        return [] if not self.status.path else self._normalize_result(ProjectRepository(self.status.path).list_quarantine_documents(reason=reason, search=search))

    def list_quarantine_documents_page(self, **filters):
        return {'rows': [], 'total_count': 0, 'page': 1, 'page_size': filters.get('page_size', 100)} if not self.status.path else self._normalize_result(ProjectRepository(self.status.path).list_quarantine_documents_page(**filters))

    def list_unrecognized_documents(self, *, search: str = ''):
        return [] if not self.status.path else self._normalize_result(ProjectRepository(self.status.path).list_unrecognized_documents(search=search))

    def list_unrecognized_documents_page(self, **filters):
        return {'rows': [], 'total_count': 0, 'page': 1, 'page_size': filters.get('page_size', 100)} if not self.status.path else self._normalize_result(ProjectRepository(self.status.path).list_unrecognized_documents_page(**filters))

    def get_processing_document_detail(self, file_id: int, document_id: int | None = None):
        return None if not self.status.path else self._normalize_result(ProjectRepository(self.status.path).get_processing_document_detail(file_id, document_id))

    def list_processing_items(self, document_id: int):
        return [] if not self.status.path else self._normalize_result(ProjectRepository(self.status.path).list_processing_items(document_id))

    def list_document_pages(self, file_id: int):
        return [] if not self.status.path else self._normalize_result(ProjectRepository(self.status.path).list_document_pages(file_id))

    def list_page_text_blocks(self, file_id: int):
        return [] if not self.status.path else self._normalize_result(ProjectRepository(self.status.path).list_page_text_blocks(file_id))

    def list_field_candidates(self, document_id: int, field_name: str = ''):
        return [] if not self.status.path else self._normalize_result(ProjectRepository(self.status.path).list_field_candidates(document_id, field_name))

    def list_line_item_candidates(self, document_id: int):
        return [] if not self.status.path else self._normalize_result(ProjectRepository(self.status.path).list_line_item_candidates(document_id))

    def save_manual_processing_data(self, file_id: int, *, document_id: int | None = None, **payload) -> None:
        if not self.status.path:
            raise ProjectError('Projekt není připojen.')
        repo = ProjectRepository(self.status.path)
        repo.save_manual_processing_data(file_id, document_id=document_id, **payload)
        self.refresh_status()

    def retry_single_file(self, file_id: int, *, document_id: int | None = None, force_openai: bool = False) -> None:
        if not self.status.path:
            raise ProjectError('Projekt není připojen.')
        repo = ProjectRepository(self.status.path)
        if force_openai and not (self.settings.openai_enabled and self.settings.openai_model):
            raise ProjectError('OpenAI větev není připravená.')
        if force_openai and not self.settings.allow_manual_openai_retry:
            raise ProjectError('Ruční OpenAI pokus je v nastavení workflow zakázaný.')
        count_openai = repo.count_document_attempts(document_id, attempt_types=['openai']) if document_id is not None else repo.count_file_attempts(file_id, attempt_types=['openai'])
        if force_openai and count_openai >= self.settings.openai_retry_limit:
            raise ProjectError('Byl vyčerpán limit OpenAI pokusů pro tento doklad.')
        count_manual = repo.count_document_attempts(document_id, attempt_types=['automatic_local_retry', 'manual']) if document_id is not None else repo.count_file_attempts(file_id, attempt_types=['automatic_local_retry', 'manual'])
        if not force_openai and count_manual >= self.settings.manual_retry_limit:
            raise ProjectError('Byl vyčerpán limit ručních pokusů pro tento doklad.')
        if force_openai:
            repo.mark_retry_pending([file_id], attempt_type='openai')
        self.processing_service.process_file(
            self.status.path,
            file_id,
            document_id=document_id,
            repo=repo,
            output_directory=None,
            openai_enabled=self.settings.openai_enabled,
            openai_model=self.settings.openai_model,
            openai_usage_policy='manual_only' if force_openai else self.settings.openai_usage_policy,
            initial_attempt_type='openai' if force_openai else 'automatic_local_retry',
            automatic_retry_limit=self.settings.automatic_retry_limit,
            openai_retry_limit=self.settings.openai_retry_limit,
            block_without_ares=self.settings.block_without_ares,
            quarantine_missing_identification=self.settings.quarantine_missing_identification,
            pattern_match_fields=self.settings.pattern_match_fields,
        )
        self.refresh_status()

    def processing_document_page_for_file(self, file_id: int, *, page_size: int = 100) -> int:
        return 1 if not self.status.path else ProjectRepository(self.status.path).processing_document_page_for_file(file_id, page_size=page_size)

    def final_document_page_for_id(self, document_id: int, *, page_size: int = 100) -> int:
        return 1 if not self.status.path else ProjectRepository(self.status.path).final_document_page_for_id(document_id, page_size=page_size)

    def final_item_page_for_id(self, item_id: int, *, page_size: int = 200) -> int:
        return 1 if not self.status.path else ProjectRepository(self.status.path).final_item_page_for_id(item_id, page_size=page_size)

    def list_visual_patterns(self):
        return [] if not self.status.path else self._normalize_result(ProjectRepository(self.status.path).list_visual_patterns())

    def list_visual_patterns_page(self, **filters):
        return {'rows': [], 'total_count': 0, 'page': 1, 'page_size': filters.get('page_size', 100)} if not self.status.path else self._normalize_result(ProjectRepository(self.status.path).list_visual_patterns_page(**filters))

    def create_visual_pattern(self, **payload) -> int:
        if not self.status.path:
            raise ProjectError('Projekt není připojen.')
        return ProjectRepository(self.status.path).create_visual_pattern(**payload)

    def update_visual_pattern(self, pattern_id: int, **payload) -> None:
        if not self.status.path:
            raise ProjectError('Projekt není připojen.')
        ProjectRepository(self.status.path).update_visual_pattern(pattern_id, **payload)

    def delete_visual_pattern(self, pattern_id: int) -> None:
        if not self.status.path:
            raise ProjectError('Projekt není připojen.')
        ProjectRepository(self.status.path).delete_visual_pattern(pattern_id)

    def create_backup(self):
        if not self.status.path:
            raise ProjectError('Projekt není připojen.')
        return ProjectRepository(self.status.path).create_backup()

    def restore_backup(self, backup_path: str) -> None:
        if not self.status.path:
            raise ProjectError('Projekt není připojen.')
        ProjectRepository(self.status.path).restore_backup(Path(backup_path))

    def export_diagnostics_bundle(self, destination: str):
        if not self.status.path:
            raise ProjectError('Projekt není připojen.')
        return ProjectRepository(self.status.path).export_diagnostics_bundle(Path(destination))

    def project_integrity_report(self) -> dict:
        if not self.status.path:
            return {'ok': False, 'message': 'Projekt není připojen.'}
        validation = self.project_service.validate_project(self.status.path)
        repo = ProjectRepository(self.status.path)
        metrics = repo.metrics()
        separation = repo.separation_report()
        return {
            'ok': validation.ok and separation['ok'],
            'message': validation.message,
            'working_db': separation['working_db'],
            'production_db': separation['production_db'],
            'separation': separation,
            'metrics': metrics,
            'input_directory_status': self._input_dir_status(),
        }

    def reset_data_area(self, area: str) -> int:
        if not self.status.path:
            raise ProjectError('Projekt není připojen.')
        changed = ProjectRepository(self.status.path).reset_data_area(area)
        self.refresh_status()
        return changed

    def list_item_groups(self):
        return [] if not self.status.path else self._normalize_result(ProjectRepository(self.status.path).list_item_groups())

    def list_item_groups_page(self, **filters):
        return {'rows': [], 'total_count': 0, 'page': 1, 'page_size': filters.get('page_size', 100)} if not self.status.path else self._normalize_result(ProjectRepository(self.status.path).list_item_groups_page(**filters))

    def create_item_group(self, name: str, description: str = '', is_active: bool = True) -> int:
        if not self.status.path:
            raise ProjectError('Projekt není připojen.')
        if not name.strip():
            raise ValueError('Název skupiny je povinný.')
        return ProjectRepository(self.status.path).create_item_group(name, description, is_active)

    def update_item_group(self, group_id: int, name: str, description: str = '', is_active: bool = True) -> None:
        if not self.status.path:
            raise ProjectError('Projekt není připojen.')
        if not name.strip():
            raise ValueError('Název skupiny je povinný.')
        ProjectRepository(self.status.path).update_item_group(group_id, name, description, is_active)

    def delete_item_group(self, group_id: int) -> None:
        if not self.status.path:
            raise ProjectError('Projekt není připojen.')
        ProjectRepository(self.status.path).delete_item_group(group_id)

    def list_item_catalog(self):
        return [] if not self.status.path else self._normalize_result(ProjectRepository(self.status.path).list_item_catalog())

    def list_item_catalog_page(self, **filters):
        return {'rows': [], 'total_count': 0, 'page': 1, 'page_size': filters.get('page_size', 100)} if not self.status.path else self._normalize_result(ProjectRepository(self.status.path).list_item_catalog_page(**filters))

    def create_item_catalog_entry(self, name: str, vat_rate: str = '', group_id: int | None = None, notes: str = '', is_active: bool = True) -> int:
        if not self.status.path:
            raise ProjectError('Projekt není připojen.')
        if not name.strip():
            raise ValueError('Název číselníkové položky je povinný.')
        return ProjectRepository(self.status.path).create_item_catalog_entry(name, vat_rate, group_id, notes, is_active)

    def update_item_catalog_entry(self, entry_id: int, name: str, vat_rate: str = '', group_id: int | None = None, notes: str = '', is_active: bool = True) -> None:
        if not self.status.path:
            raise ProjectError('Projekt není připojen.')
        if not name.strip():
            raise ValueError('Název číselníkové položky je povinný.')
        ProjectRepository(self.status.path).update_item_catalog_entry(entry_id, name, vat_rate, group_id, notes, is_active)

    def delete_item_catalog_entry(self, entry_id: int) -> None:
        if not self.status.path:
            raise ProjectError('Projekt není připojen.')
        ProjectRepository(self.status.path).delete_item_catalog_entry(entry_id)

    def update_workflow_settings(self, **values) -> None:
        for key, value in values.items():
            if hasattr(self.settings, key):
                if key == 'pattern_match_fields':
                    value = [item.strip() for item in value if item.strip()]
                setattr(self.settings, key, value)
        self.settings_store.save(self.settings)

    def refresh_status(self) -> ProjectStatus:
        if self.status.path:
            self._sync_project_directories(self.status.path)
            snapshot = ProjectRepository(self.status.path).status_snapshot()
            self.status.queue_size = int(snapshot['queue_size'])
            self.status.last_success = str(snapshot['last_success'])
            self.status.last_error = str(snapshot['last_error'])
            self.status.input_dir_status = self._input_dir_status()
        return self.status

    def project_input_dir(self) -> Path | None:
        if not self.status.path:
            return None
        return self.status.path / PROJECT_INPUT_DIR

    def _input_dir_status(self) -> str:
        path = self.project_input_dir()
        if path is None:
            return 'Nevybrán'
        if not path.exists() or not path.is_dir():
            return 'Nedostupný'
        return 'Připraven'

    def _set_runtime_progress(self, *, active: bool, current: int, total: int, label: str, mode: str, percent: int = 0, eta_seconds: float | None = None, detail: str = '', step: str = '', started_at_monotonic: float | None = None) -> None:
        previous_started = self._runtime_progress.get('started_at_monotonic') if isinstance(self._runtime_progress, dict) else None
        self._runtime_progress = {
            'active': bool(active),
            'current': int(current),
            'total': int(total),
            'label': str(label),
            'mode': str(mode),
            'percent': int(percent),
            'eta_seconds': eta_seconds,
            'detail': str(detail),
            'step': str(step),
            'started_at_monotonic': started_at_monotonic if started_at_monotonic is not None else previous_started,
        }

    def _clear_runtime_progress(self) -> None:
        self._set_runtime_progress(active=False, current=0, total=0, label='', mode='', percent=0, eta_seconds=None, detail='', step='', started_at_monotonic=None)

    def _progress_callback_wrapper(self, progress_callback, *, mode: str):
        import json
        import time

        started = time.monotonic()
        self._set_runtime_progress(active=True, current=0, total=0, label='Příprava', mode=mode, percent=0, eta_seconds=None, detail='', step='prepare', started_at_monotonic=started)

        def _wrapped(current: int, total: int, label: str) -> None:
            payload = {'label': label, 'detail': '', 'step': '', 'percent': 0, 'eta_seconds': None}
            text = str(label or '')
            if text.startswith('{') and text.endswith('}'):
                try:
                    parsed = json.loads(text)
                except json.JSONDecodeError:
                    parsed = None
                if isinstance(parsed, dict):
                    payload.update(parsed)
            else:
                payload['label'] = text
            percent = int(payload.get('percent') or (round((current / total) * 100) if total > 0 else 0))
            elapsed = max(time.monotonic() - started, 0.001)
            eta_seconds = payload.get('eta_seconds')
            if eta_seconds in (None, '') and total > 0 and current > 0:
                per_item = elapsed / max(current, 1)
                eta_seconds = max((total - current) * per_item, 0.0)
            self._set_runtime_progress(
                active=True,
                current=current,
                total=total,
                label=str(payload.get('label') or text),
                mode=mode,
                percent=percent,
                eta_seconds=float(eta_seconds) if eta_seconds not in (None, '') else None,
                detail=str(payload.get('detail') or ''),
                step=str(payload.get('step') or ''),
                started_at_monotonic=started,
            )
            if progress_callback is not None:
                progress_callback(current, total, json.dumps(payload, ensure_ascii=False))

        return _wrapped

    def _sync_project_directories(self, project_path: Path) -> None:
        input_dir = project_path / PROJECT_INPUT_DIR
        input_dir.mkdir(parents=True, exist_ok=True)
        self.settings.input_directory = str(input_dir)
        self.settings.output_directory = ''

    def _normalize_result(self, value):
        if isinstance(value, sqlite3.Row):
            return {key: self._normalize_result(value[key]) for key in value.keys()}
        if isinstance(value, dict):
            return {key: self._normalize_result(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._normalize_result(item) for item in value]
        if isinstance(value, tuple):
            return tuple(self._normalize_result(item) for item in value)
        return value

    def _program_status_label(self) -> str:
        if not self.status.is_connected:
            return 'Projekt není připojen'
        if self.status.processing_running:
            return 'Zpracování probíhá'
        return 'Nic neběží'
