from __future__ import annotations

from typing import Any


def apply_ares_supplier(dialog: Any, supplier: dict[str, object]) -> None:
    dialog.ico.setText(str(supplier.get('ico', '') or ''))
    dialog.name.setText(str(supplier.get('name', '') or ''))
    dialog.dic.setText(str(supplier.get('dic', '') or ''))
    dialog.address.setText(str(supplier.get('address', '') or ''))
    dialog.vat_payer.setChecked(bool(supplier.get('vat_payer', False)))


def on_fill_from_ares(dialog: Any) -> None:
    if dialog.controller is None:
        dialog.show_error('Chybí controller pro ARES aktualizaci.')
        return
    ico = dialog.ico.text().strip()
    if not ico:
        dialog.show_error('Nejdřív zadejte IČO dodavatele.')
        dialog.ico.setFocus()
        return
    try:
        supplier = dialog.controller.get_supplier_data_from_ares(ico)
    except Exception as exc:
        dialog.show_error(str(exc))
        return
    dialog._apply_ares_supplier(supplier)
