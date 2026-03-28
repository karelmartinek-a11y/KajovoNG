from __future__ import annotations

import sqlite3
import zipfile
from pathlib import Path
from typing import Any

from kajovospend.app.constants import PROJECT_MARKER

REQUIRES_REVIEW_BASIS = ['quarantine', 'duplicate']
REQUIRES_REVIEW_DEFINITION = (
    'Do vyzaduje kontrolu patri doklady blokovane ve fronte KARANTENA a duplicity. '
    'Jde o pripady cekajici na kontrolu, potvrzeni nebo rucni zasah uzivatele.'
)
ALLOWED_QUARANTINE_CATEGORIES = [
    'Duplicita',
    'Chybí identifikace',
    'Nesedí součty',
    'Nesedí DPH',
    'Atypická sazba DPH',
    'Plátce DPH a na dokladu je neplátce DPH',
]

UNRECOGNIZED_BASIS = ['unrecognized']
UNRECOGNIZED_DEFINITION = (
    'Do nerozeznane patri pouze doklady ve skutecne fronte NEROZPOZNANE, '
    'kde se nepodarilo spolehlive vytezit obsah.'
)


def dashboard_data(repo) -> dict[str, Any]:
    with repo.prod_connection() as prod:
        production = repo._load_reporting_cache_snapshot(prod, 'dashboard')
    with repo.work_connection() as work:
        operations = repo._operational_dashboard_metrics(work)
    metrics = production.setdefault('metrics', {})
    metrics['program_state'] = 'produkci data'
    return {
        'operations': operations,
        'production': production,
        'metrics': metrics,
        'monthly_trend': production.get('monthly_trend', []),
        'success_breakdown': dict(operations['success_breakdown']),
    }


def expense_data(repo) -> dict[str, Any]:
    with repo.prod_connection() as prod:
        expenses = repo._load_reporting_cache_snapshot(prod, 'expenses')
    with repo.work_connection() as work:
        review_metrics = repo._expense_review_metrics(work)
    expenses['review_states'] = review_metrics['review_states']
    expenses['requires_review_total'] = review_metrics['requires_review_total']
    expenses['requires_review_basis'] = list(review_metrics['requires_review_basis'])
    expenses['requires_review_definition'] = review_metrics['requires_review_definition']
    expenses['unrecognized_count'] = review_metrics['unrecognized_count']
    expenses['unrecognized_basis'] = list(review_metrics['unrecognized_basis'])
    expenses['unrecognized_definition'] = review_metrics['unrecognized_definition']
    return expenses


def quarantine_category_case_sql(repo) -> str:
    return '''
        case
            when lower(coalesce(f.status, '')) = 'duplicate'
                or lower(coalesce(pd.quarantine_reason, f.last_error, '')) like '%duplic%'
                then 'Duplicita'
            when lower(coalesce(pd.quarantine_reason, f.last_error, '')) like '%chybejici identifikace%'
                or lower(coalesce(pd.quarantine_reason, f.last_error, '')) like '%chybejici identifikace%'
                then 'Chybí identifikace'
            when lower(coalesce(pd.quarantine_reason, f.last_error, '')) like '%nesoulad souct%'
                then 'Nesedí součty'
            when lower(coalesce(pd.quarantine_reason, f.last_error, '')) like '%nesoulad dph%'
                then 'Nesedí DPH'
            when lower(coalesce(pd.quarantine_reason, f.last_error, '')) like '%atypicka sazba dph%'
                then 'Atypická sazba DPH'
            when lower(coalesce(pd.quarantine_reason, f.last_error, '')) like '%rozpor platce%'
                or lower(coalesce(pd.quarantine_reason, f.last_error, '')) like '%platce dph%'
                or lower(coalesce(pd.quarantine_reason, f.last_error, '')) like '%na dokladu je neplatce%'
                or lower(coalesce(pd.quarantine_reason, f.last_error, '')) like '%neplatce%'
                then 'Plátce DPH a na dokladu je neplátce DPH'
            else ''
        end
    '''


def expense_review_metrics(repo, conn: sqlite3.Connection) -> dict[str, Any]:
    """Return explicit review metrics for the `$` card.

    Requires review:
    - processing documents blocked in KARANTENA
    - duplicate files

    Unrecognized:
    - processing documents in the standalone NEROZPOZNANE queue
    """
    category_sql = repo._quarantine_category_case_sql()
    rows = conn.execute(
        f'''
        select quarantine_category, count(*) as count
        from (
            select {category_sql} as quarantine_category
            from files f
            join processing_documents pd on pd.file_id = f.id
            where (
                lower(coalesce(pd.final_state, pd.status, '')) = 'quarantine'
                or lower(coalesce(f.status, '')) = 'duplicate'
            )
        ) review_categories
        group by quarantine_category
        order by count desc, quarantine_category asc
        '''
    ).fetchall()
    grouped = {str(row['quarantine_category']): int(row['count'] or 0) for row in rows}
    review_states = [
        {'status': key, 'count': value}
        for key, value in grouped.items()
        if value > 0 and key in ALLOWED_QUARANTINE_CATEGORIES
    ]
    requires_review_total = int(
        conn.execute(
            '''
            select count(*)
            from files f
            join processing_documents pd on pd.file_id = f.id
            where (
                lower(coalesce(pd.final_state, pd.status, '')) = 'quarantine'
                or lower(coalesce(f.status, '')) = 'duplicate'
            )
            '''
        ).fetchone()[0]
    )
    unrecognized_count = int(
        conn.execute(
            "select count(*) from processing_documents where lower(coalesce(final_state, status, ''))='unrecognized'"
        ).fetchone()[0]
    )
    return {
        'review_states': review_states,
        'requires_review_total': requires_review_total,
        'requires_review_basis': list(REQUIRES_REVIEW_BASIS),
        'requires_review_definition': REQUIRES_REVIEW_DEFINITION,
        'unrecognized_count': unrecognized_count,
        'unrecognized_basis': list(UNRECOGNIZED_BASIS),
        'unrecognized_definition': UNRECOGNIZED_DEFINITION,
    }


def list_unrecognized_documents(repo, *, search: str = ''):
    sql = '''
        select f.id as file_id, f.original_name, f.status, f.last_error, f.correlation_id,
               pd.id as document_id, pd.document_number, pd.issued_at, pd.total_with_vat,
               coalesce(nullif(pd.quarantine_reason, ''), f.last_error, '') as quarantine_reason,
               pd.segment_index, pd.page_from, pd.page_to, pd.split_status,
               coalesce(ps.ico, '') as ico, coalesce(ps.name, '') as supplier_name
        from files f
        join processing_documents pd on pd.file_id = f.id
        left join processing_suppliers ps on ps.document_id = pd.id
        where lower(coalesce(pd.final_state, pd.status, f.status, '')) = 'unrecognized'
    '''
    params: list[Any] = []
    if search:
        fts_query = repo._fts_query(search)
        if fts_query:
            sql += ' and exists (select 1 from working_documents_fts where working_documents_fts.rowid=f.id and working_documents_fts match ?)'
            params.append(fts_query)
        else:
            needle = f'%{search.lower()}%'
            sql += ' and (lower(coalesce(f.original_name, "")) like ? or lower(coalesce(pd.document_number, "")) like ? or lower(coalesce(ps.ico, "")) like ? or lower(coalesce(ps.name, "")) like ? or lower(coalesce(nullif(pd.quarantine_reason, ""), f.last_error, "")) like ?)'
            params.extend([needle, needle, needle, needle, needle])
    sql += ' order by f.id desc'
    with repo.work_connection() as conn:
        return conn.execute(sql, params).fetchall()


def list_unrecognized_documents_page(repo, *, search: str = '', page: int = 1, page_size: int = 100) -> dict[str, Any]:
    sql = '''
        from files f
        join processing_documents pd on pd.file_id = f.id
        left join processing_suppliers ps on ps.document_id = pd.id
        where lower(coalesce(pd.final_state, pd.status, f.status, '')) = 'unrecognized'
    '''
    params: list[Any] = []
    if search:
        fts_query = repo._fts_query(search)
        if fts_query:
            sql += ' and exists (select 1 from working_documents_fts where working_documents_fts.rowid=f.id and working_documents_fts match ?)'
            params.append(fts_query)
        else:
            needle = f'%{search.lower()}%'
            sql += ' and (lower(coalesce(f.original_name, "")) like ? or lower(coalesce(pd.document_number, "")) like ? or lower(coalesce(ps.ico, "")) like ? or lower(coalesce(ps.name, "")) like ? or lower(coalesce(nullif(pd.quarantine_reason, ""), f.last_error, "")) like ?)'
            params.extend([needle, needle, needle, needle, needle])
    page = max(1, int(page or 1))
    page_size = max(1, min(int(page_size or 100), 500))
    offset = (page - 1) * page_size
    with repo.work_connection() as conn:
        total = int(conn.execute(f'select count(*) {sql}', params).fetchone()[0])
        rows = conn.execute(
            f'''
            select f.id as file_id, f.original_name, f.status, f.last_error, f.correlation_id,
                   pd.id as document_id, pd.document_number, pd.issued_at, pd.total_with_vat,
                   coalesce(nullif(pd.quarantine_reason, ''), f.last_error, '') as quarantine_reason,
                   pd.segment_index, pd.page_from, pd.page_to, pd.split_status,
                   coalesce(ps.ico, '') as ico, coalesce(ps.name, '') as supplier_name
            {sql}
            order by f.id desc
            limit ? offset ?
            ''',
            [*params, page_size, offset],
        ).fetchall()
    return {'rows': rows, 'total_count': total, 'page': page, 'page_size': page_size}


def export_diagnostics_bundle(repo, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    import sqlite3
    import tempfile

    with zipfile.ZipFile(destination, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
        for src, arcname in [
            (repo.project_path / 'logs' / 'runtime.log', 'logs/runtime.log'),
            (repo.project_path / 'logs' / 'decisions.jsonl', 'logs/decisions.jsonl'),
            (repo.project_path / PROJECT_MARKER, PROJECT_MARKER),
        ]:
            if src.exists():
                zf.write(src, arcname=arcname)
        for source_db, arcname in [
            (repo.work_db_path, 'work.sqlite3'),
            (repo.prod_db_path, 'production.sqlite3'),
        ]:
            if source_db.exists():
                with tempfile.NamedTemporaryFile(delete=False, suffix='.sqlite3') as handle:
                    snapshot_path = Path(handle.name)
                try:
                    with sqlite3.connect(source_db) as src_conn, sqlite3.connect(snapshot_path) as snapshot_conn:
                        src_conn.backup(snapshot_conn)
                    zf.write(snapshot_path, arcname=arcname)
                finally:
                    try:
                        snapshot_path.unlink(missing_ok=True)
                    except PermissionError:
                        pass
    return destination


def apply_reporting_view_bindings(repository_cls) -> None:
    repository_cls.dashboard_data = dashboard_data
    repository_cls.expense_data = expense_data
    repository_cls._expense_review_metrics = expense_review_metrics
    repository_cls._quarantine_category_case_sql = quarantine_category_case_sql
    repository_cls.list_unrecognized_documents = list_unrecognized_documents
    repository_cls.list_unrecognized_documents_page = list_unrecognized_documents_page
    repository_cls.export_diagnostics_bundle = export_diagnostics_bundle
