"""CHANGE CTR-01: produkcni strict hranice, beze zmeny historicke ctecky."""
from __future__ import annotations

import hashlib
import json

import pytest

from kajovo.core.contracts import ContractError, parse_json_strict as read_core_json
from kajovo.core.orchestration.contracts import canonical_bytes, canonical_sha256, parse_json_strict
from kajovo.core.orchestration.errors import OrchestrationError
from kajovo.core.structured_output import OutputContractError, response_format, validate_output


@pytest.mark.parametrize(('raw', 'code'), [
    ('{"content":"a","content":"b"}', 'DUPLICATE_KEY'),
    ('{"x":{"a":1,"a":2}}', 'DUPLICATE_KEY'),
    ('{"x":NaN}', 'NONFINITE'),
    ('{"x":Infinity}', 'NONFINITE'),
    ('{"x":-Infinity}', 'NONFINITE'),
    ('{"x":1e9999}', 'NONFINITE'),
    ('{"x":"\\ud800"}', 'INVALID_JSON'),
    ('{"\\udfff":1}', 'INVALID_JSON'),
    ('[1,2]', 'ROOT_NOT_OBJECT'),
    ('null', 'ROOT_NOT_OBJECT'),
    ('true', 'ROOT_NOT_OBJECT'),
    ('1', 'ROOT_NOT_OBJECT'),
    ('{} {}', 'INVALID_JSON'),
    ('Text before {"x":1}', 'INVALID_JSON'),
    ('```json\n{"x":1}\n```', 'INVALID_JSON'),
    ('{"x":', 'INVALID_JSON'),
    ('\ufeff{}', 'INVALID_JSON'),
])
def test_strict_boundary_rejects_without_rewriting(raw, code, record_property):
    before = raw
    record_property('fixture_sha256', hashlib.sha256(raw.encode('utf-8')).hexdigest())
    record_property('observed_transport_call_count', 0)
    with pytest.raises(OrchestrationError) as caught:
        parse_json_strict(raw)
    assert caught.value.code == code
    assert raw == before


def test_completed_provider_response_uses_strict_parser(record_property):
    raw = '{"content":"first","content":"last"}'
    response = {'id': 'resp_fixture', 'status': 'completed', 'output_text': raw}
    payload = {'text': response_format('FILE_CONTENT_V1', {
        'type': 'object', 'properties': {'content': {'type': 'string'}},
        'required': ['content'], 'additionalProperties': False,
    })}
    with pytest.raises(OutputContractError) as caught:
        validate_output(response, payload)
    assert caught.value.code == 'DUPLICATE_KEY'
    assert caught.value.response is response
    assert caught.value.evidence()[0]['code'] == 'DUPLICATE_KEY'
    assert response['output_text'] == raw
    record_property('fixture_sha256', hashlib.sha256(raw.encode()).hexdigest())
    record_property('observed_transport_call_count', 0)


def test_canonical_hash_preserves_unicode_and_newline_identity():
    value = {'z': '\r\n', 'a': '\u017elu\u0165ou\u010dk\u00fd', 'nested': [True, None, 2, 1.5]}
    expected = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()
    assert canonical_bytes(value) == expected
    assert canonical_sha256(value) == hashlib.sha256(expected).hexdigest()
    assert parse_json_strict(expected.decode()) == value
    assert canonical_sha256({'a': '\u00e9'}) != canonical_sha256({'a': 'e\u0301'})
    assert canonical_sha256({'a': '\r\n'}) != canonical_sha256({'a': '\n'})


@pytest.mark.parametrize('value', [{1: 'not a string'}, {'x': (1, 2)}, {'x': object()}, {'x': float('nan')}])
def test_canonicalization_does_not_coerce_non_json_values(value):
    with pytest.raises(OrchestrationError):
        canonical_bytes(value)


def test_recursive_container_and_invalid_input_are_typed_failures():
    value = {}
    value['self'] = value
    with pytest.raises(OrchestrationError, match='INVALID_JSON'):
        canonical_bytes(value)
    with pytest.raises(OrchestrationError, match='INVALID_JSON'):
        parse_json_strict(None)
    with pytest.raises(OrchestrationError, match='INVALID_JSON'):
        parse_json_strict('{"x":' + '[' * 2000 + '0' + ']' * 2000 + '}')


def test_all_runtime_json_readers_reject_embedded_markdown():
    text = '```json\n{"historical":1}\n```'
    with pytest.raises(ContractError):
        read_core_json(text)
    with pytest.raises(OrchestrationError):
        parse_json_strict(text)


def test_interpreter_integer_limit_is_reported_as_typed_json_error():
    import sys

    limit = sys.get_int_max_str_digits()
    if not limit:
        pytest.skip("Interpreter nema limit desitkoveho cisla.")
    raw = '{"value":' + '9' * (limit + 1) + '}'
    with pytest.raises(OrchestrationError, match='INVALID_JSON'):
        parse_json_strict(raw)
