"""Offline safety kontrakty explicitní live akceptace."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import live_acceptance


def test_gate_without_confirmation_blocks_before_key_access(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "secret-must-not-be-read")
    with pytest.raises(live_acceptance.Blocked, match="BLOCKED_AUTHORIZATION"):
        live_acceptance.safety_gate(False, live_acceptance.AUTHORIZATION, 1.0)


def test_gate_rejects_wrong_authorization(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    with pytest.raises(live_acceptance.Blocked, match="BLOCKED_AUTHORIZATION"):
        live_acceptance.safety_gate(True, "wrong", 1.0)


def test_gate_rejects_missing_api_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(live_acceptance.Blocked, match="BLOCKED_API_KEY"):
        live_acceptance.safety_gate(True, live_acceptance.AUTHORIZATION, 1.0)


def test_gate_cannot_raise_authorized_ceiling(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    with pytest.raises(live_acceptance.Blocked, match="BLOCKED_BUDGET"):
        live_acceptance.safety_gate(True, live_acceptance.AUTHORIZATION, 1.01)


def test_budget_blocks_known_next_liability():
    budget = live_acceptance.Budget(1.0)
    budget.add(0.8, text=True)
    with pytest.raises(live_acceptance.Blocked, match="BLOCKED_BUDGET"):
        budget.before(known_max=0.21, label="next")


def test_image_model_selection_is_fixed_to_allowed_candidates(monkeypatch):
    monkeypatch.setattr(
        "kajovo.core.photo_batch.image_edit_model_ids",
        lambda available: ["gpt-image-2.5-sunburst-2026-09-08"],
    )
    assert live_acceptance.choose_image_model([]) == "gpt-image-2.5-sunburst-2026-09-08"
    monkeypatch.setattr(
        "kajovo.core.photo_batch.image_edit_model_ids",
        lambda available: [],
    )
    with pytest.raises(live_acceptance.Blocked, match="BLOCKED_IMAGE_MODEL_UNAVAILABLE"):
        live_acceptance.choose_image_model(["gpt-image-2.5-terra"])


def test_summary_never_serializes_api_key(tmp_path: Path):
    budget = live_acceptance.Budget(1.0)
    report = live_acceptance._base_report("generate-live", "sha", live_acceptance.MODEL)
    live_acceptance.write_evidence(tmp_path, [report], budget, "sha")
    data = json.dumps(json.loads((tmp_path / "live_acceptance_report.json").read_text(encoding="utf-8")))
    assert "OPENAI_API_KEY" not in data
    assert "secret" not in data


def test_case_order_is_normative():
    assert live_acceptance.CASES == (
        "generate-live",
        "generate-batch",
        "photo-batch",
        "comic-panels",
    )
