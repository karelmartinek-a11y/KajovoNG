from pathlib import Path

from verify_recovery_artifacts import check


def test_current_recovery_is_fail_closed():
    errors = check(Path(__file__).parent)
    assert errors
    assert any(error.startswith("B-01:") for error in errors)
    assert any(error.startswith("B-03:") for error in errors)
    assert any(error.startswith("A2:") for error in errors)
