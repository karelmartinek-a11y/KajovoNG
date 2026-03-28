from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from kajovospend.integrations.ares_client import AresClient, AresError
from kajovospend.persistence.production_repository import ProductionRepository
from kajovospend.persistence.working_repository import WorkingRepository


class SupplierService:
    def __init__(self, ares_client: AresClient) -> None:
        self.ares_client = ares_client

    @staticmethod
    def _ares_supplier_payload(supplier: AresSupplier) -> dict[str, object]:
        return {
            'ico': supplier.ico,
            'name': supplier.name,
            'dic': supplier.dic,
            'vat_payer': supplier.vat_payer,
            'address': supplier.address,
            'ares_payload': asdict(supplier),
        }

    def list_suppliers(self, project_path: Path):
        return ProductionRepository(project_path).list_suppliers()

    def list_suppliers_page(self, project_path: Path, **filters):
        return ProductionRepository(project_path).list_suppliers_page(**filters)

    def get_supplier_detail(self, project_path: Path, supplier_id: int):
        return ProductionRepository(project_path).get_supplier_detail(supplier_id)

    def load_from_ares(self, ico: str) -> dict[str, object]:
        supplier = self.ares_client.get_supplier(ico)
        return self._ares_supplier_payload(supplier)

    def create_supplier(self, project_path: Path, *, ico: str, name: str, dic: str, vat_payer: bool, address: str) -> int:
        return ProductionRepository(project_path).create_supplier(ico=ico, name=name, dic=dic, vat_payer=vat_payer, address=address)

    def update_supplier(self, project_path: Path, supplier_id: int, *, ico: str, name: str, dic: str, vat_payer: bool, address: str) -> None:
        ProductionRepository(project_path).update_supplier(supplier_id, ico=ico, name=name, dic=dic, vat_payer=vat_payer, address=address)

    def delete_supplier(self, project_path: Path, supplier_id: int) -> None:
        ProductionRepository(project_path).delete_supplier(supplier_id)

    def merge_suppliers(self, project_path: Path, source_supplier_id: int, target_supplier_id: int) -> None:
        ProductionRepository(project_path).merge_suppliers(source_supplier_id, target_supplier_id)

    def refresh_from_ares(self, project_path: Path, supplier_id: int) -> None:
        repo = ProductionRepository(project_path)
        detail = repo.get_supplier_detail(supplier_id)
        if not detail:
            raise ValueError('Dodavatel neexistuje.')
        ico = str(detail['ico'] or '')
        supplier = self.ares_client.get_supplier(ico)
        repo.update_supplier(
            supplier_id,
            ico=supplier.ico,
            name=supplier.name,
            dic=supplier.dic,
            vat_payer=supplier.vat_payer,
            address=supplier.address,
            ares_payload=asdict(supplier),
        )
        WorkingRepository(project_path).record_ares_validation(supplier.ico, 'success', supplier.raw_payload)

    def validate_processing_supplier(self, project_path: Path, document_id: int, ico: str) -> tuple[bool, str]:
        repo = WorkingRepository(project_path)
        try:
            supplier = self.ares_client.get_supplier(ico)
        except AresError as exc:
            repo.record_ares_validation(ico, 'failed', {'error': str(exc)})
            repo.update_processing_supplier(document_id, ico=ico, ares_status='failed')
            return False, str(exc)
        repo.record_ares_validation(supplier.ico, 'success', supplier.raw_payload)
        repo.update_processing_supplier(
            document_id,
            ico=supplier.ico,
            name=supplier.name,
            dic=supplier.dic,
            vat_payer=supplier.vat_payer,
            address=supplier.address,
            ares_status='verified',
            ares_payload=supplier.raw_payload,
        )
        return True, 'ARES validace byla úspěšná.'
