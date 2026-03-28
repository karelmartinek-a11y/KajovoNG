from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from kajovospend.app.constants import APP_NAME, DEFAULT_PROD_DB_PATH, DEFAULT_WORK_DB_PATH, PROJECT_MARKER, REQUIRED_DIRS, SCHEMA_VERSION
from kajovospend.persistence.project_context import ProjectDatabasePaths, ProjectPathError
from kajovospend.persistence.repository import ProjectRepository


@dataclass
class ValidationResult:
    ok: bool
    message: str


class ProjectError(Exception):
    pass


class ProjectService:
    def create_project(
        self,
        directory: str | Path,
        project_name: str,
        *,
        working_db_path: str | Path | None = None,
        production_db_path: str | Path | None = None,
    ) -> Path:
        path = Path(directory).expanduser().resolve()
        path.mkdir(parents=True, exist_ok=True)
        marker = path / PROJECT_MARKER
        if marker.exists():
            raise ProjectError('Vybrany adresar uz obsahuje instanci KajovoSpend.')
        working = Path(working_db_path) if working_db_path else Path(DEFAULT_WORK_DB_PATH)
        production = Path(production_db_path) if production_db_path else Path(DEFAULT_PROD_DB_PATH)
        working_resolved = working if working.is_absolute() else (path / working)
        production_resolved = production if production.is_absolute() else (path / production)
        ProjectDatabasePaths(path, working_resolved.resolve(), production_resolved.resolve()).ensure_distinct()
        for item in REQUIRED_DIRS:
            (path / item).mkdir(parents=True, exist_ok=True)
        try:
            marker.write_text(
                json.dumps(
                    {
                        'app_name': APP_NAME,
                        'project_name': project_name.strip() or path.name,
                        'schema_version': SCHEMA_VERSION,
                        'database_paths': {
                            'working': str(working),
                            'production': str(production),
                        },
                    },
                    indent=2,
                    ensure_ascii=False,
                ),
                encoding='utf-8',
            )
            paths = ProjectDatabasePaths.from_project_root(path)
            paths.create_parents()
            self._init_db(paths.working_db, work=True)
            self._init_db(paths.production_db, work=False)
            ProjectRepository(path).ensure_schema()
        except Exception:
            if marker.exists():
                marker.unlink()
            raise
        return path

    def validate_project(self, directory: str | Path) -> ValidationResult:
        path = Path(directory).expanduser().resolve()
        if not path.exists() or not path.is_dir():
            return ValidationResult(False, 'Projektovy adresar neexistuje.')
        if not os.access(path, os.R_OK | os.W_OK):
            return ValidationResult(False, 'Projektovy adresar nema prava pro cteni a zapis.')
        marker = path / PROJECT_MARKER
        if not marker.exists():
            return ValidationResult(False, 'Chybi metadata projektu.')
        try:
            metadata = json.loads(marker.read_text(encoding='utf-8'))
        except json.JSONDecodeError as exc:
            return ValidationResult(False, f'Poskozena metadata projektu: {exc}')
        if metadata.get('schema_version') != SCHEMA_VERSION:
            return ValidationResult(False, 'Nekompatibilni verze schematu projektu.')
        for item in REQUIRED_DIRS:
            if not (path / item).exists():
                return ValidationResult(False, f'Chybi povinna struktura projektu: {item}')
        try:
            db_paths = ProjectDatabasePaths.from_project_root(path)
        except ProjectPathError as exc:
            return ValidationResult(False, str(exc))
        if not db_paths.working_db.exists() or not db_paths.production_db.exists():
            return ValidationResult(False, 'Chybi pracovni nebo produkcni databaze.')
        repo = ProjectRepository(path)
        repo.ensure_schema()
        report = repo.separation_report()
        if not report['ok']:
            return ValidationResult(False, report['message'])
        return ValidationResult(True, 'Projekt je validni a kompatibilni.')

    def load_metadata(self, directory: str | Path) -> dict:
        path = Path(directory).expanduser().resolve()
        return json.loads((path / PROJECT_MARKER).read_text(encoding='utf-8'))

    def database_paths(self, directory: str | Path) -> ProjectDatabasePaths:
        return ProjectDatabasePaths.from_project_root(directory)

    def _init_db(self, path: Path, *, work: bool) -> None:
        conn = sqlite3.connect(path)
        cur = conn.cursor()
        cur.executescript(self._work_schema() if work else self._prod_schema())
        conn.commit()
        conn.close()

    def _work_schema(self) -> str:
        return """
        create table if not exists files (
            id integer primary key,
            original_name text not null,
            source_import text,
            internal_path text,
            sha256 text,
            file_type text,
            size_bytes integer default 0,
            page_count integer default 0,
            received_at text default current_timestamp,
            source_type text,
            status text default 'new',
            correlation_id text,
            last_error text,
            imported_at text
        );
        create table if not exists processing_documents (
            id integer primary key,
            file_id integer not null,
            document_type text,
            family text,
            supplier_ico text,
            supplier_id integer,
            document_number text,
            issued_at text,
            total_with_vat real,
            status text default 'draft',
            final_document_id integer,
            last_attempt_id integer,
            final_state text default 'pending',
            quarantine_reason text default '',
            manual_payload_json text default '{}',
            preview_page integer default 1,
            promotion_status text default '',
            promotion_blocked_reason text default '',
            promoted_at text,
            foreign key(file_id) references files(id)
        );
        create table if not exists processing_items (
            id integer primary key,
            document_id integer not null,
            name text,
            quantity real,
            unit_price real,
            total_price real,
            vat_rate text,
            source_kind text default 'automatic',
            foreign key(document_id) references processing_documents(id)
        );
        create table if not exists processing_suppliers (
            id integer primary key,
            document_id integer,
            ico text,
            dic text,
            name text,
            vat_payer integer default 0,
            address text,
            ares_status text,
            ares_payload_json text default '{}',
            source_layer text default 'first_layer'
        );
        create table if not exists extraction_attempts (
            id integer primary key,
            file_id integer,
            document_id integer,
            attempt_order integer,
            attempt_type text,
            started_at text default current_timestamp,
            finished_at text,
            branch text,
            result text,
            reason text,
            blocking_error integer default 0,
            next_step text,
            document_state text,
            correlation_id text,
            processor_name text,
            payload_json text
        );
        create table if not exists ares_validations (
            id integer primary key,
            supplier_ico text,
            checked_at text default current_timestamp,
            status text,
            payload_excerpt text
        );
        create table if not exists visual_patterns (
            id integer primary key,
            name text not null,
            is_active integer default 1,
            document_path text,
            recognition_rules text,
            field_map text,
            page_no integer default 1,
            preview_state text default '{}',
            created_at text default current_timestamp,
            updated_at text default current_timestamp
        );
        create table if not exists document_pages (
            id integer primary key,
            file_id integer not null,
            page_no integer not null,
            width real default 0,
            height real default 0,
            rotation_deg real default 0,
            text_layer_present integer default 0,
            source_kind text default '',
            ocr_status text default '',
            confidence_avg real,
            extracted_text text default '',
            created_at text default current_timestamp,
            foreign key(file_id) references files(id),
            unique(file_id, page_no)
        );
        create table if not exists page_text_blocks (
            id integer primary key,
            page_id integer not null,
            block_no integer not null,
            raw_text text default '',
            normalized_text text default '',
            bbox_json text default '{}',
            confidence real,
            source_engine text default '',
            source_kind text default '',
            foreign key(page_id) references document_pages(id),
            unique(page_id, block_no)
        );
        create table if not exists field_candidates (
            id integer primary key,
            document_id integer not null,
            page_id integer,
            field_name text not null,
            raw_value text default '',
            normalized_value text default '',
            confidence real default 0,
            source_kind text default '',
            bbox_json text default '{}',
            chosen integer default 0,
            created_at text default current_timestamp,
            foreign key(document_id) references processing_documents(id),
            foreign key(page_id) references document_pages(id)
        );
        create table if not exists line_item_candidates (
            id integer primary key,
            document_id integer not null,
            page_id integer,
            line_no integer default 1,
            name_raw text default '',
            qty_raw text default '',
            unit_price_raw text default '',
            total_price_raw text default '',
            vat_raw text default '',
            confidence real default 0,
            bbox_json text default '{}',
            chosen integer default 0,
            created_at text default current_timestamp,
            foreign key(document_id) references processing_documents(id),
            foreign key(page_id) references document_pages(id)
        );
        """

    def _prod_schema(self) -> str:
        return """
        create table if not exists final_suppliers (
            id integer primary key,
            ico text,
            dic text,
            name text not null,
            vat_payer integer default 0,
            address text,
            ares_checked_at text,
            ares_payload_json text default '',
            source_supplier_id integer
        );
        create table if not exists final_documents (
            id integer primary key,
            source_document_id integer not null,
            source_file_id integer,
            source_correlation_id text,
            promotion_key text,
            business_document_key text,
            supplier_id integer,
            issued_at text,
            document_number text,
            total_without_vat real,
            total_with_vat real,
            vat_summary text,
            extraction_method text,
            inserted_at text default current_timestamp,
            original_file_path text,
            foreign key(supplier_id) references final_suppliers(id)
        );
        create table if not exists final_items (
            id integer primary key,
            document_id integer not null,
            source_item_id integer,
            name text,
            quantity real,
            unit_price real,
            total_price real,
            vat_rate text,
            foreign key(document_id) references final_documents(id)
        );
        """
