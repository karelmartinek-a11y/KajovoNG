"""Statický audit rozlišuje dohledatelnou dědičnost od neexistujícího handleru."""

from kajovo.desktop.ui_audit import audit_desktop


def test_inherited_handler_is_resolved_but_unknown_handler_is_reported(tmp_path):
    desktop = tmp_path / "kajovo" / "desktop"
    desktop.mkdir(parents=True)
    (desktop / "base.py").write_text(
        "class Base:\n    def known(self):\n        return True\n", encoding="utf-8")
    (desktop / "page.py").write_text(
        "from .base import Base as Parent\n"
        "class Page(Parent):\n"
        "    def build(self):\n"
        "        button('Known', self.known)\n"
        "        button('Missing', self.missing)\n", encoding="utf-8")
    result = audit_desktop(tmp_path)
    unresolved = [issue["target"] for module in result["modules"] for issue in module["unresolved"]]
    assert unresolved == ["self.missing"]
