from __future__ import annotations

from kajovospend.integrations.openai_client import OpenAIClient, OpenAIClientError


class _Session:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def post(self, url, headers=None, json=None, timeout=None):
        self.calls.append({'url': url, 'headers': headers, 'json': json, 'timeout': timeout})
        return self.response


class _Response:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


def test_openai_client_uses_official_responses_endpoint_and_carries_source_payload() -> None:
    session = _Session(
        _Response(
            payload={
                'id': 'resp_123',
                'output_text': '{"supplier_ico":"12345678","supplier_name":"Dodavatel","document_number":"FA2025001","total_with_vat":1210,"issued_at":"2025-01-10","vat_rate":"21","items":[{"name":"Služba","quantity":1,"unit_price":1210,"total_price":1210,"vat_rate":"21"}]}'
            }
        )
    )
    client = OpenAIClient(session=session)
    result = client.extract_document(
        'sk-test',
        'gpt-4.1-mini',
        'faktura.pdf',
        'Dodavatel ICO 12345678',
        source_descriptor={'source_sha256': 'deadbeef', 'source_path': '/tmp/faktura.pdf', 'page_count': 2, 'selected_page_numbers': [1, 2]},
    )
    call = session.calls[0]
    assert call['url'] == 'https://api.openai.com/v1/responses'
    assert call['headers']['Authorization'].startswith('Bearer ')
    assert call['json']['model'] == 'gpt-4.1-mini'
    assert call['json']['input'][0]['role'] == 'developer'
    source_text = call['json']['input'][1]['content'][0]['text']
    assert 'ZDROJ_SOUBORU' in source_text
    assert 'deadbeef' in source_text
    assert 'Dodavatel ICO 12345678' in source_text
    assert result.valid is True
    assert result.audit is not None
    assert result.audit.endpoint == 'https://api.openai.com/v1/responses'
    assert result.audit.request_fingerprint
    assert result.audit.response_id == 'resp_123'
    assert result.audit.validation_status == 'verified'


def test_openai_client_parses_nested_responses_output_shape() -> None:
    session = _Session(
        _Response(
            payload={
                'id': 'resp_nested',
                'output': [
                    {
                        'type': 'message',
                        'content': [
                            {
                                'type': 'output_text',
                                'text': {
                                    'value': '{"supplier_ico":"12345678","supplier_name":"Dodavatel","document_number":"FA2025002","total_with_vat":2420,"issued_at":"2025-01-11","vat_rate":"21","items":[{"name":"Služba","quantity":1,"unit_price":2420,"total_price":2420,"vat_rate":"21"}]}'
                                },
                            }
                        ],
                    }
                ],
            }
        )
    )
    client = OpenAIClient(session=session)
    result = client.extract_document('sk-test', 'gpt-4.1-mini', 'faktura.pdf', 'Kontext')
    assert result.valid is True
    assert result.document_number == 'FA2025002'
    assert result.audit is not None and result.audit.response_id == 'resp_nested'


def test_openai_client_rejects_invalid_json_with_explicit_error() -> None:
    session = _Session(_Response(payload={'output_text': 'nevalidní text'}))
    client = OpenAIClient(session=session)
    try:
        client.extract_document('sk-test', 'gpt-4.1-mini', 'faktura.pdf', 'test')
    except OpenAIClientError as exc:
        assert 'validní JSON' in str(exc)
    else:
        raise AssertionError('Expected OpenAIClientError')


def test_openai_client_rejects_payload_without_supplier_name() -> None:
    session = _Session(
        _Response(
            payload={
                'output_text': '{"supplier_ico":"12345678","supplier_name":"","document_number":"FA2025001","total_with_vat":1210,"issued_at":"2025-01-10","vat_rate":"21","items":[{"name":"Služba","quantity":1,"unit_price":1210,"total_price":1210,"vat_rate":"21"}]}'
            }
        )
    )
    client = OpenAIClient(session=session)
    result = client.extract_document('sk-test', 'gpt-4.1-mini', 'faktura.pdf', 'Kontext')
    assert result.valid is False
    assert 'supplier_name' in result.validation_errors
