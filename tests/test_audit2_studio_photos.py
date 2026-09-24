"""Odložený placený PHOTO návrh zůstává dostupný k použití či zahození."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from kajovo.core.config import AppSettings
from kajovo.studio.context import StudioContext
from kajovo.studio.photos import PhotosPage


@pytest.mark.parametrize("use", [True, False])
def test_pending_photo_proposal_can_be_used_or_discarded(qtbot, tmp_path, monkeypatch, use):
    settings = AppSettings(cache_dir=str(tmp_path / "cache"), log_dir=str(tmp_path / "LOG"))
    context = StudioContext(settings, Mock(), api_key="test")
    context.models = ["gpt-4.1"]
    page = PhotosPage(context)
    qtbot.addWidget(page)
    page.prompt_model.addItem("Test", "gpt-4.1")
    page.prompt_model.setCurrentIndex(page.prompt_model.findData("gpt-4.1"))
    page.prompt.setPlainText("Původní")
    execute = Mock()
    monkeypatch.setattr(page, "execute", execute)
    page.improve()
    receive = execute.call_args.args[2]
    page.prompt.setPlainText("Změněné")
    page.prompt.setPlainText("Původní")
    proposal = SimpleNamespace(original_prompt="Původní", professional_prompt="Profesionální návrh")
    receive(proposal)
    assert page.pending_professional is proposal
    assert page.prompt.toPlainText() == "Původní"
    assert page.pending_preview.toPlainText() == "Profesionální návrh"
    page.prompt.setPlainText("Další úprava")
    page.improve()
    assert execute.call_count == 1
    assert page.pending_professional is proposal
    if use:
        page.pending_use.click()
        assert page.prompt.toPlainText() == "Profesionální návrh"
        assert page.professional is proposal
    else:
        page.pending_discard.click()
        assert page.prompt.toPlainText() == "Další úprava"
    assert page.pending_professional is None
    assert page.pending_preview.isHidden()

