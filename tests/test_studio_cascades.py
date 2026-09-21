"""Změny kroků musí zachovat hodnoty i při neplatné návaznosti."""

from kajovo.core.cascade_types import CascadeInput, CascadeOutput, CascadeStep
from kajovo.studio.cascade_items import CascadeItemDialog
from kajovo.studio.components import Form


def test_missing_choice_is_preserved_for_validation(qtbot):
    form = Form()
    qtbot.addWidget(form)
    widget = form.choice("source", "Zdroj", [("Existující", "known")], "missing")
    assert widget.currentData() == "missing"


def test_input_references_select_real_previous_outputs(qtbot):
    output = CascadeOutput(name="Výsledek")
    step = CascadeStep(title="Příprava", outputs=[output])
    record = CascadeInput(name="Podklad", source="output", source_step_id=step.id, source_output_id=output.id)
    dialog = CascadeItemDialog(record, [step])
    qtbot.addWidget(dialog)
    assert dialog.form.fields["source_step_id"].currentData() == step.id
    assert dialog.form.fields["source_output_id"].currentData() == output.id
    dialog.submit()
    assert dialog.record.to_dict() == record.to_dict()



def test_json_output_editor_preserves_exact_mask(qtbot):
    mask = {
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "required": ["name"],
        "additionalProperties": False,
    }
    record = CascadeOutput(name="Data", kind="json", json_schema=mask)
    dialog = CascadeItemDialog(record, [])
    qtbot.addWidget(dialog)
    dialog.submit()
    assert dialog.record.kind == "json"
    assert dialog.record.json_schema == mask
