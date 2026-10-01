"""Inventář rozlišuje skutečné popisky od protokolových kódů a dokládá zdroj."""

from pathlib import Path

from scripts.audit_ui_texts import collect, enrich


def test_inventory_preserves_templates_and_does_not_translate_backend_codes(tmp_path):
    source = tmp_path / "kajovo/studio/example.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        'class Ukazka:\n'
        '    def __init__(self, stop_label="Zastavit"):\n'
        '        button("ui.stop", stop_label, self.stop)\n'
        '    def build(self):\n'
        '        action("ui.start", "Spustit práci", self.start)\n'
        '        form.choice("mode", "Způsob práce", [("Odpověď na dotaz", "QA")])\n'
        '        caption(f"Hotovo {done} z {total} kroků")\n'
        '        caption(f"Soubor {name!r}")\n'
        '        worker.start("Příprava", identifier=f"job:{record}")\n'
        '        viewer.setText(record.get("entity_id", "Postava není vybraná"))\n'
        '        for key, title in (("history", "Historie"), ("comics", "Komiks")):\n'
        '            action(key, title, self.select_page)\n'
        '        form.choice("ratio", "Poměr stran", [(name, name) for name in ("1:1", "DL")])\n'
        '    def start(self):\n'
        '        self.validate()\n'
        '        self.client.create_response()\n'
        '    def validate(self):\n'
        '        return True\n', encoding="utf-8",
    )
    records, methods, files, _views = collect(tmp_path)
    rows = {row["text"]: row for row in records}
    assert rows["Spustit práci"]["oblast"] == "UI"
    assert rows["Odpověď na dotaz"]["oblast"] == "UI"
    assert rows["QA"]["oblast"] == "Interní údaj"
    assert rows["ui.start"]["oblast"] == "Interní údaj"
    assert rows["job:{record}"]["oblast"] == "Interní údaj"
    assert rows["entity_id"]["oblast"] == "Interní údaj"
    assert rows["Postava není vybraná"]["oblast"] == "UI"
    for text in ("Historie", "Komiks", "1:1", "DL", "Zastavit"):
        assert rows[text]["oblast"] == "UI"
    assert rows["history"]["oblast"] == "Interní údaj"
    assert rows["Zastavit"]["obsluha"] == "self.stop"
    assert rows["Hotovo {done} z {total} kroků"]["proměnlivý"]
    assert rows["Soubor {name!r}"]["proměnlivý"]
    enrich(records, methods, tmp_path)
    button = rows["Spustit práci"]
    assert "Ukazka.start" in button["implementace"]
    assert "Ukazka.validate" in button["implementace"]
    assert button["navazující_volání"] == "self.client.create_response"
    assert "není doložen" in button["ověření"]
    assert files[0]["soubor"] == "kajovo/studio/example.py"
    assert len(files[0]["sha256"]) == 64


def test_inventory_ignores_appledouble_and_does_not_call_core_methods_ui(tmp_path):
    folder = tmp_path / "kajovo/core"
    folder.mkdir(parents=True)
    (folder / "example.py").write_text(
        'result = git.text("ls-files", "--cached")\n'
        'FORMATS = {"A0R": response_format("A0R_REQUIREMENTS_V2")}\n', encoding="utf-8",
    )
    (folder / "._example.py").write_bytes(b"\x00\x05\x16\x07")
    app = tmp_path / "kajovo/app/main.py"
    app.parent.mkdir(parents=True)
    app.write_text('app.setApplicationName("Kájovo NG")\napp.setOrganizationName("Kájovo")\n', encoding="utf-8")
    records, _methods, files, _views = collect(tmp_path)
    assert {row["oblast"] for row in records if row["soubor"] == "kajovo/core/example.py"} == {"Interní údaj"}
    rows = {row["text"]: row for row in records}
    assert rows["Kájovo NG"]["oblast"] == "UI"
    assert rows["Kájovo"]["oblast"] == "Interní údaj"
    assert files[0]["soubor"] == "kajovo/core/example.py"
    assert len(files) == 2
    assert Path(tmp_path / files[0]["soubor"]).is_file()
