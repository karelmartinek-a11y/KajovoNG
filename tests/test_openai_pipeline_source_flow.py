from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from kajovospend.integrations.openai_client import OpenAIAuditRecord, OpenAIExtractionResult
from kajovospend.persistence.repository import ProjectRepository
from kajovospend.processing.service import ProcessingService
from kajovospend.project.project_service import ProjectService


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
    def __init__(self) -> None:
        self.last_source_descriptor = None
        self.last_file_name = ''
        self.last_text_hint = ''

    def extract_document(self, api_key, model, file_name, text_hint, *, image_inputs=None, source_descriptor=None):
        self.last_file_name = file_name
        self.last_text_hint = text_hint
        self.last_source_descriptor = dict(source_descriptor or {})
        return OpenAIExtractionResult(
            supplier_ico='12345678',
            supplier_name='Dodavatel s.r.o.',
            supplier_dic='CZ12345678',
            document_number='FA2025009',
            total_with_vat=1210.0,
            issued_at='2025-01-10',
            vat_rate='21',
            items=[{'name': 'Služba', 'quantity': 1.0, 'unit_price': 1210.0, 'total_price': 1210.0, 'vat_rate': '21'}],
            valid=True,
            raw_text='{}',
            audit=OpenAIAuditRecord(endpoint='https://api.openai.com/v1/responses', method='POST', model=model, request_fingerprint='fp-1', response_id='resp_1', validation_status='verified'),
        )


def test_openai_pipeline_receives_real_source_descriptor_and_finalizes_to_db(tmp_path: Path) -> None:
    project_root = ProjectService().create_project(tmp_path / 'project', 'OpenAI Project')
    input_dir = project_root / 'input'
    input_dir.mkdir(parents=True, exist_ok=True)
    source = input_dir / 'invoice.txt'
    source.write_text('Dodavatel\nIČO 12345678\nČíslo dokladu FA2025009\nDatum 2025-01-10\nCelkem k úhradě 1210,00 Kč', encoding='utf-8')
    openai_client = _OpenAIClient()
    service = ProcessingService(ares_client=_AresClient(), secret_store=_SecretStore(), openai_client=openai_client)
    summary = service.process_import_directory(project_root, input_dir, openai_enabled=True, openai_model='gpt-4.1-mini', openai_usage_policy='openai_only', block_without_ares=False)
    repo = ProjectRepository(project_root)
    finals = repo.list_final_documents()
    assert summary.finalized == 1
    assert openai_client.last_file_name == 'invoice.txt'
    assert openai_client.last_source_descriptor is not None
    assert openai_client.last_source_descriptor['source_sha256']
    assert openai_client.last_source_descriptor['page_count'] == 1
    assert 'Dodavatel' in openai_client.last_text_hint
    assert finals and finals[0]['document_number'] == 'FA2025009'
