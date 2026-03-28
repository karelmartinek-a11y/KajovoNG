from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from openpyxl import Workbook

from kajovospend.persistence.production_repository import ProductionRepository


class ReportingService:
    def _paged_dataset_batches(self, repo: ProductionRepository, dataset: str, *, page_size: int, **filters: Any):
        if dataset == 'documents':
            fetch_page = lambda page: repo.list_final_documents_page(page=page, page_size=page_size, **filters)
        elif dataset == 'items':
            fetch_page = lambda page: repo.list_final_items_page(page=page, page_size=page_size, **filters)
        elif dataset == 'suppliers':
            search = str(filters.get('search') or '')
            fetch_page = lambda page: repo.list_suppliers_page(page=page, page_size=page_size, search=search)
        else:
            raise ValueError('Neznamy dataset pro export.')
        page = 1
        total_count: int | None = None
        while True:
            payload = fetch_page(page)
            rows = [dict(row) for row in payload['rows']]
            if total_count is None:
                total_count = int(payload['total_count'])
            if not rows:
                break
            yield rows, int(total_count or 0)
            if page * page_size >= int(total_count or 0):
                break
            page += 1

    def _export_csv_batched(self, repo: ProductionRepository, dataset: str, destination: Path, *, page_size: int, progress_callback=None, **filters: Any) -> Path:
        written = 0
        fieldnames: list[str] | None = None
        with destination.open('w', encoding='utf-8-sig', newline='') as handle:
            writer = None
            for rows, total_count in self._paged_dataset_batches(repo, dataset, page_size=page_size, **filters):
                if fieldnames is None:
                    fieldnames = sorted({key for row in rows for key in row.keys()}) or ['empty']
                    writer = csv.DictWriter(handle, fieldnames=fieldnames)
                    writer.writeheader()
                assert writer is not None
                for row in rows:
                    writer.writerow(row)
                written += len(rows)
                if progress_callback is not None:
                    progress_callback(written, max(total_count, written), f'Exportuji {written}/{max(total_count, written)}')
            if fieldnames is None:
                writer = csv.DictWriter(handle, fieldnames=['empty'])
                writer.writeheader()
                if progress_callback is not None:
                    progress_callback(1, 1, 'Export hotov')
            elif progress_callback is not None:
                progress_callback(written, max(written, 1), 'Export hotov')
        return destination

    def _export_xlsx_batched(self, repo: ProductionRepository, dataset: str, destination: Path, *, page_size: int, progress_callback=None, **filters: Any) -> Path:
        workbook = Workbook(write_only=True)
        worksheet = workbook.create_sheet('Export')
        written = 0
        fieldnames: list[str] | None = None
        for rows, total_count in self._paged_dataset_batches(repo, dataset, page_size=page_size, **filters):
            if fieldnames is None:
                fieldnames = sorted({key for row in rows for key in row.keys()}) or ['empty']
                worksheet.append(fieldnames)
            for row in rows:
                worksheet.append([row.get(field, '') for field in fieldnames or ['empty']])
            written += len(rows)
            if progress_callback is not None:
                progress_callback(written, max(total_count, written), f'Exportuji {written}/{max(total_count, written)}')
        if fieldnames is None:
            worksheet.append(['empty'])
            if progress_callback is not None:
                progress_callback(1, 1, 'Export hotov')
        elif progress_callback is not None:
            progress_callback(written, max(written, 1), 'Export hotov')
        workbook.save(destination)
        return destination

    def dashboard_data(self, project_path: Path) -> dict[str, Any]:
        return ProductionRepository(project_path).dashboard_data()

    def expense_data(self, project_path: Path) -> dict[str, Any]:
        return ProductionRepository(project_path).expense_data()

    def list_final_documents(self, project_path: Path, **filters: Any):
        return ProductionRepository(project_path).list_final_documents(**filters)

    def list_final_documents_page(self, project_path: Path, **filters: Any):
        return ProductionRepository(project_path).list_final_documents_page(**filters)

    def get_final_document_detail(self, project_path: Path, document_id: int) -> dict[str, Any]:
        repo = ProductionRepository(project_path)
        return {
            'document': repo.get_final_document_detail(document_id),
            'items': repo.list_document_items(document_id),
        }

    def list_final_items(self, project_path: Path, **filters: Any):
        return ProductionRepository(project_path).list_final_items(**filters)

    def list_final_items_page(self, project_path: Path, **filters: Any):
        return ProductionRepository(project_path).list_final_items_page(**filters)

    def get_final_item_detail(self, project_path: Path, item_id: int):
        return ProductionRepository(project_path).get_final_item_detail(item_id)

    def update_final_document(self, project_path: Path, document_id: int, **changes: Any) -> None:
        ProductionRepository(project_path).update_final_document(document_id, **changes)

    def export_rows(self, project_path: Path, dataset: str, destination: Path, *, format: str, progress_callback=None, page_size: int = 500, **filters: Any) -> Path:
        repo = ProductionRepository(project_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if format == 'csv':
            return self._export_csv_batched(repo, dataset, destination, page_size=page_size, progress_callback=progress_callback, **filters)
        if format == 'xlsx':
            return self._export_xlsx_batched(repo, dataset, destination, page_size=page_size, progress_callback=progress_callback, **filters)
        raise ValueError('Nepodporovany format exportu.')
