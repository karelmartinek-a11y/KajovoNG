"""Architektonické regrese: validace nesmí sama vytvářet placenou práci."""
from pathlib import Path
from unittest.mock import Mock

from kajovo.core.response_policy import ResponsePolicy
from kajovo.core.structured_output import text_format


ROOT = Path(__file__).resolve().parents[1]


def _payload():
    return {"model": "gpt-5.2", "input": "pracovní zadání", "text": text_format()}


def test_response_policy_contains_no_paid_probe_transport():
    source = (ROOT / "kajovo" / "core" / "response_policy.py").read_text(encoding="utf-8")
    forbidden = (
        "_send_response(",
        "trial_live",
        "PREFLIGHT_BATCH_",
        '"PREFLIGHT_"',
        "kajovong_preflight",
        'POST", "/batches"',
        "POST', '/batches'",
    )
    for marker in forbidden:
        assert marker not in source, f"Validační politika znovu obsahuje placenou probe cestu: {marker}"


def test_validation_policy_does_not_create_response_or_batch():
    client = Mock()
    client.base_url = "https://api.openai.com/v1"
    client.api_key = "test"
    client.list_models.return_value = [{"id": "gpt-5.2"}]
    client.validate_resources = Mock()
    policy = ResponsePolicy(client)

    policy.ensure(_payload())
    policy.ensure_batch([_payload()])

    client.validate_resources.assert_any_call(_payload())
    assert client.validate_resources.call_count == 2
    assert not any(
        call.args and call.args[0] == "POST" and call.args[1] in ("/responses", "/batches")
        for call in client._req.call_args_list
    )


def test_paid_live_verification_scripts_are_not_part_of_repository():
    scripts = ROOT / "scripts"
    forbidden_names = {
        "verify_openai_live.py",
        "verify_batch_live.py",
        "verify_generate_batch_live.py",
        "verify_response_contracts_live.py",
        "verify_workflows_live.py",
    }
    existing = {path.name for path in scripts.glob("verify_*_live.py")}
    assert existing.isdisjoint(forbidden_names)


def test_ssot_forbids_paid_preflight():
    ssot = (ROOT / "docs" / "SSOT.md").read_text(encoding="utf-8")
    assert "Žádný samostatný placený preflight" in ssot
    assert "kajovong_preflight" not in ssot
