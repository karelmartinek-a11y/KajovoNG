"""Lokální pozitivní a negativní důkazy verification profilů."""
from __future__ import annotations

import pytest

from kajovo.core.orchestration.errors import OrchestrationError
from kajovo.core.orchestration.verification import plan_checks, run_checks


def test_verification_profile_accepts_valid_static_candidate_without_network(tmp_path):
    root = tmp_path / "candidate"
    root.mkdir()
    (root / "main.py").write_text("value = 1\n", encoding="utf-8")
    profile = {
        "id": "static-only",
        "commands": [],
        "required_checks": ["format_parser"],
    }

    plan = plan_checks(root, profile, target_id="candidate")
    report = run_checks(plan)

    assert report["target_id"] == "candidate"
    assert report["format_result"] == "passed"
    assert report["functional_result"] == "not_applicable"
    assert report["result"] == "needs_human"
    assert any(
        row["criterion_id"] == "format_parser" and row["status"] == "passed"
        for row in report["checks"]
    )


def test_verification_profile_rejects_invalid_candidate_without_runner(tmp_path):
    root = tmp_path / "candidate"
    root.mkdir()
    (root / "broken.py").write_text("def broken(:\n", encoding="utf-8")
    profile = {
        "id": "static-only",
        "commands": [],
        "required_checks": ["format_parser"],
    }

    plan = plan_checks(root, profile, target_id="candidate")
    report = run_checks(plan)

    assert report["result"] == "failed"
    assert report["format_result"] == "failed"
    assert report["functional_result"] == "not_run"
    assert any(row["status"] == "failed" for row in report["checks"])


@pytest.mark.parametrize(
    "profile",
    [
        {"id": "", "commands": [], "required_checks": []},
        {"id": "bad", "commands": "python", "required_checks": []},
        {"id": "bad", "commands": [["python", ""]], "required_checks": []},
    ],
)
def test_verification_profile_rejects_invalid_profile_before_execution(tmp_path, profile):
    root = tmp_path / "candidate"
    root.mkdir()
    (root / "main.py").write_text("value = 1\n", encoding="utf-8")

    with pytest.raises(OrchestrationError):
        plan_checks(root, profile, target_id="candidate")
