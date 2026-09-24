"""Obnova katalogu modelů zachovává výslovnou volbu uživatele."""


def refill_models(widget, values, recommended, *, inherit=False, selected=None):
    previous = selected if selected is not None else widget.currentData()
    if previous is None:
        previous = "" if inherit else recommended
    blocked = widget.blockSignals(True)
    try:
        widget.clear()
        if inherit:
            widget.addItem("Použít hlavní model", "")
        for value in values:
            widget.addItem(value, value)
        if previous and previous not in values:
            widget.addItem(previous + " · nedostupný pro tento režim", previous)
        widget.setCurrentIndex(widget.findData(previous) if previous is not None else -1)
        widget.setProperty("model_unavailable", bool(previous and previous not in values))
    finally:
        widget.blockSignals(blocked)
