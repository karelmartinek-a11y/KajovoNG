"""Obnovení přehledu nesmí ponechat starou kartu přes nové ovladače."""

from PySide6.QtWidgets import QVBoxLayout, QWidget

from kajovo.studio.job_cards import build_job_card, clear_cards


def test_replaced_card_is_hidden_before_deferred_deletion(qtbot):
    host = QWidget()
    layout = QVBoxLayout(host)
    card, buttons = build_job_card(
        "Dávka", "Běží", "01.10.2026 12:00", "První stav",
        (("old.action", "Otevřít", lambda: None, "", True),),
    )
    layout.addWidget(card)
    qtbot.addWidget(host)
    host.show()
    assert card.isVisibleTo(host)
    clear_cards(layout)
    assert not card.isVisibleTo(host)
    assert not buttons[0].isVisibleTo(host)
