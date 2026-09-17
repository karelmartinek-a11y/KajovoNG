"""Studio audit resolves inherited methods, signals and callable attributes."""

from kajovo.studio.ui_audit import audit_studio


def _unresolved(result):
    return [
        issue["target"]
        for module in result["modules"]
        for issue in module["unresolved"]
    ]


def test_inherited_handler_is_resolved_but_unknown_handler_is_reported(tmp_path):
    studio = tmp_path / "kajovo" / "studio"
    studio.mkdir(parents=True)
    (studio / "base.py").write_text(
        "class Base:\n    def known(self):\n        return True\n", encoding="utf-8"
    )
    (studio / "page.py").write_text(
        "from .base import Base as Parent\n"
        "class Page(Parent):\n"
        "    def build(self):\n"
        "        button('Known', self.known)\n"
        "        button('Missing', self.missing)\n",
        encoding="utf-8",
    )
    assert _unresolved(audit_studio(tmp_path)) == ["self.missing"]


def test_signal_and_typed_callable_attributes_are_valid_connection_targets(tmp_path):
    studio = tmp_path / "kajovo" / "studio"
    studio.mkdir(parents=True)
    (studio / "page.py").write_text(
        "from collections.abc import Callable\n"
        "class Page:\n"
        "    changed = Signal()\n"
        "    def __init__(self, refreshed: Callable[[], None] | None = None):\n"
        "        self.refreshed = refreshed or (lambda: None)\n"
        "    def build(self, widget):\n"
        "        widget.changed.connect(self.changed)\n"
        "        widget.finished.connect(self.refreshed)\n",
        encoding="utf-8",
    )
    assert _unresolved(audit_studio(tmp_path)) == []
