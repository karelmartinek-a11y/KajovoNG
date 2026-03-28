from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from kajovospend.processing.service import ProcessingService
from kajovospend.project.project_service import ProjectService
from kajovospend.persistence.repository import ProjectRepository


@dataclass
class _Supplier:
    ico: str
    name: str
    dic: str
    vat_payer: bool
    address: str
    raw_payload: dict


class _AresClient:
    def get_supplier(self, ico: str):
        return _Supplier(ico=ico, name='Dodavatel s.r.o.', dic='CZ12345678', vat_payer=True, address='Praha', raw_payload={'ico': ico})


class _SecretStore:
    def get_openai_key(self) -> str:
        return 'sk-test'


class _OpenAIClient:
    pass


def test_processing_smoke_persists_real_file_into_working_and_production_db(tmp_path: Path) -> None:
    project_root = ProjectService().create_project(tmp_path / 'project', 'Test Project')
    input_dir = project_root / 'input'
    input_dir.mkdir(parents=True, exist_ok=True)
    source = input_dir / 'doc.txt'
    source.write_text(
        'Dodavatel\nIČO 12345678\nČíslo dokladu FA2025001\nDatum 2025-01-10\nCelkem k úhradě 1210,00 Kč',
        encoding='utf-8',
    )
    service = ProcessingService(ares_client=_AresClient(), secret_store=_SecretStore())
    summary = service.process_import_directory(project_root, input_dir, openai_enabled=False, block_without_ares=False)
    repo = ProjectRepository(project_root)
    docs = repo.list_documents()
    final_docs = repo.list_final_documents()
    assert summary.processed == 1
    assert len(docs) == 1
    assert docs[0]['status'] == 'final'
    assert final_docs
    detail = repo.get_processing_document_detail(int(docs[0]['file_id']), int(docs[0]['document_id']))
    assert detail['document_number'] == 'FA2025001'
    assert detail['ico'] == '12345678'
    assert repo.work_db_path.exists()
    assert repo.prod_db_path.exists()
