from kajovo.core.model_capabilities import _err_indicates_param_unsupported


def test_invalid_response_id_is_not_an_unsupported_parameter():
    assert not _err_indicates_param_unsupported("Invalid request: previous_response_id expired", "previous_response_id")
    assert _err_indicates_param_unsupported("Unsupported parameter: previous_response_id", "previous_response_id")


def test_reasoning_probe_does_not_claim_or_send_default_temperature():
    from unittest.mock import Mock, patch
    from kajovo.core.config import AppSettings
    from kajovo.core.model_capabilities import ModelProbeWorker
    worker = ModelProbeWorker(AppSettings(), "test", Mock(), ["gpt-5-nano"])
    with patch("kajovo.core.model_capabilities._try_response", return_value=(True, {"id": "resp_test"}, None)) as call:
        caps = worker._probe_one(Mock(), Mock(), "gpt-5-nano", None)
    assert not caps.supports_temperature
    assert all("temperature" not in args.args[-1] for args in call.call_args_list)
