import unittest
from unittest.mock import Mock

import pytest

from kajovo.core.contracts import (
    ContractError,
    file_response_format,
    parse_json_strict,
    validate_paths,
)


def test_file_response_contract_rejects_transport_chunking():
    with pytest.raises(ValueError, match="chunk_index=0"):
        file_response_format("A3_FILE", "app.py", 1)


@pytest.mark.parametrize("text", ['{"x":1,"x":2}', '{"x":NaN}', '{"x":Infinity}', 'prefix {"x":NaN} suffix', '{"x":1e999}'])
def test_json_rejects_ambiguous_or_nonfinite_values(text):
    with pytest.raises(ContractError):
        parse_json_strict(text)


@pytest.mark.parametrize("response", [
    {"status": "incomplete", "output_text": "partial"},
    {"status": "failed", "output_text": "partial"},
    {"output": [{"content": [{"type": "refusal", "refusal": "no"}]}]},
    {"output": []},
    {"error": {"code": "server_error"}, "output_text": "partial"},
])
def test_unusable_response_is_not_a_successful_text(response):
    from kajovo.core.contracts import extract_text_from_response
    with pytest.raises(ContractError):
        extract_text_from_response(response)


def test_structure_schema_rejects_wrong_contract_and_actions():
    import jsonschema
    from kajovo.core.contracts import structure_response_format
    schema = structure_response_format("B2_STRUCTURE")["format"]["schema"]
    jsonschema.validate({"contract": "B2_STRUCTURE", "touched_files": [{"path": "x", "action": "modify", "intent": "test"}]}, schema)
    for bad in ({"contract": "A2_STRUCTURE", "touched_files": []},
                {"contract": "B2_STRUCTURE", "touched_files": [{"path": "x", "action": "delete", "intent": "test"}]}):
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(bad, schema)


@pytest.mark.parametrize("paths", [["a", "a/b"], ["A.txt", "a.txt"], ["x/y", "x"]])
def test_manifest_path_conflicts(paths):
    with pytest.raises(ContractError):
        validate_paths([{"path": path} for path in paths])


def test_invalid_generated_json_fails_instead_of_returning_empty_file(tmp_path):
    from test_workflows import make_worker
    from delivery_fixtures import delivery_payloads
    worker = make_worker(tmp_path, "GENERATE")
    req, plan, struct = delivery_payloads(files=[{"path": "keep.txt"}])
    worker._delivery_snapshot = {"requirements": req, "plan": plan, "structure": struct}
    client = Mock()
    client.create_response.return_value = {"id": "response", "status": "completed", "output_text": "invalid"}
    with pytest.raises(ContractError):
        worker._gen_file_chunks(client, "previous", "A3_FILE", "keep.txt", None, [])
    assert client.create_response.call_count == 3


class ContractsTests(unittest.TestCase):
    def test_parse_json_strict_rejects_embedded_object(self):
        with self.assertRaises(ContractError):
            parse_json_strict("header\n{\"a\":1}\nfooter")

    def test_parse_json_strict_rejects_array(self):
        with self.assertRaises(ContractError):
            parse_json_strict("[1,2]")

    def test_validate_paths(self):
        validate_paths([{"path": "ok/file.txt"}])
        with self.assertRaises(ContractError):
            validate_paths([{"path": "../bad.txt"}])
