"""Vynucené větve odpovídají předpokladům příjemců QA, QFILE a přípravy."""
import json

import jsonschema
import pytest

from kajovo.core.contracts import ContractError
from kajovo.core.orchestration.preparation import FORMATS
from kajovo.core.structured_output import qa_answer_format, qfile_plan_format, validate_output, validate_schema


FORMATS_UNDER_TEST = {**FORMATS, "QA": qa_answer_format(), "QFILE": qfile_plan_format()}


@pytest.mark.parametrize("stage", list(FORMATS_UNDER_TEST))
@pytest.mark.parametrize("question", [None, "", " \t\n", "Který vstup mám použít?"])
def test_blocked_wire_and_parser_require_meaningful_question(stage, question):
    wire = FORMATS_UNDER_TEST[stage]
    schema = wire["format"]["schema"]
    validate_schema(schema)
    value = {"result": {"status": "blocked", "questions": [] if question is None else [{
        "code": "missing_input", "source_refs": [], "question": question, "blocking": True,
    }]}}
    valid = bool(question and question.strip())
    assert jsonschema.Draft202012Validator(schema).is_valid(value) is valid
    response = {"status": "completed", "output_text": json.dumps(value)}
    if valid:
        assert validate_output(response, {"text": wire}) == value
    else:
        with pytest.raises(ContractError):
            validate_output(response, {"text": wire})


@pytest.mark.parametrize("certainty", ["supported", "inference", "unknown"])
@pytest.mark.parametrize("evidence", [[], ["source-1"]])
def test_qa_wire_requires_evidence_only_for_supported_claims(certainty, evidence):
    schema = qa_answer_format()["format"]["schema"]
    value = {"result": {"status": "ready", "data": {
        "answer": "Odpověď", "limitations": [], "claims": [{
            "id": "claim-1", "text": "Tvrzení", "certainty": certainty, "evidence_ids": evidence,
        }],
    }}}
    assert jsonschema.Draft202012Validator(schema).is_valid(value) is (certainty != "supported" or bool(evidence))
