from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QProgressBar,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from kajovospend.application.controller import AppController
from kajovospend.ui.design_contract import BreakpointName, breakpoint_for_width
from kajovospend.ui.widgets.previews import DocumentPreviewWidget, RegionSelectorWidget
from kajovospend.ui.widgets.primitives import BrandLockup, Card, DataTable, KeyValueText


class BaseDialog(QDialog):
    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.resize(840, 620)
        self.setMinimumSize(360, 420)
        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(24, 24, 24, 24)
        self.root.setSpacing(16)
        self.setProperty('brandHost', True)
        self.root.addWidget(BrandLockup('KajovoSpendNG'))
        self.title_label = QLabel(title)
        self.title_label.setProperty('overlayTitle', True)
        self.title_label.setWordWrap(True)
        self.root.addWidget(self.title_label)
        self.error_label = QLabel('')
        self.error_label.setProperty('statusType', 'error')
        self.error_label.setVisible(False)
        self.error_label.setWordWrap(True)
        self.root.addWidget(self.error_label)

    def show_error(self, message: str) -> None:
        self.error_label.setText(message)
        self.error_label.setVisible(bool(message))

    def accept(self) -> None:  # type: ignore[override]
        try:
            self.validate()
        except ValueError as exc:
            self.show_error(str(exc))
            return
        self.show_error('')
        super().accept()

    def validate(self) -> None:
        return None

    def _button_row(self, accept_text: str = 'Uložit', reject_text: str = 'Zrušit') -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addStretch(1)
        reject_btn = QPushButton(reject_text)
        accept_btn = QPushButton(accept_text)
        accept_btn.setObjectName('PrimaryButton')
        reject_btn.clicked.connect(self.reject)
        accept_btn.clicked.connect(self.accept)
        layout.addWidget(reject_btn)
        layout.addWidget(accept_btn)
        return row


class MessageDialog(BaseDialog):
    def __init__(
        self,
        title: str,
        message: str,
        parent: QWidget | None = None,
        *,
        confirm_text: str = 'Rozumím',
        reject_text: str | None = None,
        tone: str = 'info',
    ) -> None:
        super().__init__(title, parent)
        card = Card(title, 'Brandovaný systémový overlay podle KDGS.')
        self.message_label = QLabel(message)
        self.message_label.setProperty('statusType', tone if tone in {'info', 'warning', 'error'} else 'neutral')
        self.message_label.setWordWrap(True)
        self.message_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        card.body_layout.addWidget(self.message_label)
        self.root.addWidget(card, 1)
        self.root.addWidget(self._button_row(confirm_text, reject_text or 'Zavřít'))


class ConfirmDialog(MessageDialog):
    def __init__(self, title: str, message: str, parent: QWidget | None = None) -> None:
        super().__init__(title, message, parent, confirm_text='Potvrdit', reject_text='Zrušit', tone='warning')


class InfoDialog(MessageDialog):
    def __init__(self, title: str, message: str, parent: QWidget | None = None) -> None:
        super().__init__(title, message, parent, confirm_text='Rozumím', reject_text='Zavřít', tone='info')


class WarningDialog(MessageDialog):
    def __init__(self, title: str, message: str, parent: QWidget | None = None) -> None:
        super().__init__(title, message, parent, confirm_text='Rozumím', reject_text='Zavřít', tone='warning')


class ErrorDialog(MessageDialog):
    def __init__(self, title: str, message: str, parent: QWidget | None = None) -> None:
        super().__init__(title, message, parent, confirm_text='Rozumím', reject_text='Zavřít', tone='error')


class SupplierEditorDialog(BaseDialog):
    def __init__(self, parent: QWidget | None = None, *, supplier: dict | None = None, controller: AppController | None = None) -> None:
        super().__init__('Dodavatel', parent)
        self.controller = controller
        data = supplier or {}
        form_card = Card('Základní údaje dodavatele', 'Produkční dodavatel pro finální data a editaci.')
        form = QFormLayout()
        form.setSpacing(12)
        self.ico = QLineEdit(str(data.get('ico', '')))
        self.name = QLineEdit(str(data.get('name', '')))
        self.dic = QLineEdit(str(data.get('dic', '')))
        self.address = QLineEdit(str(data.get('address', '')))
        self.vat_payer = QCheckBox('Plátce DPH')
        self.vat_payer.setChecked(bool(data.get('vat_payer', False)))
        ico_row = QWidget()
        ico_layout = QHBoxLayout(ico_row)
        ico_layout.setContentsMargins(0, 0, 0, 0)
        ico_layout.setSpacing(8)
        ico_layout.addWidget(self.ico, 1)
        self.ares_button = QPushButton('ARES aktualizace')
        self.ares_button.clicked.connect(self.on_fill_from_ares)
        ico_layout.addWidget(self.ares_button)
        form.addRow('IČO', ico_row)
        form.addRow('Název', self.name)
        form.addRow('DIČ', self.dic)
        form.addRow('Sídlo', self.address)
        form.addRow('', self.vat_payer)
        form_card.body_layout.addLayout(form)
        self.root.addWidget(form_card)
        self.root.addWidget(self._button_row())

    def on_fill_from_ares(self) -> None:
        if self.controller is None:
            self.show_error('ARES aktualizace není dostupná bez controlleru.')
            return
        try:
            supplier = self.controller.get_supplier_data_from_ares(self.ico.text().strip())
        except Exception as exc:
            self.show_error(str(exc))
            return
        self.ico.setText(str(supplier.get('ico', '') or ''))
        self.name.setText(str(supplier.get('name', '') or ''))
        self.dic.setText(str(supplier.get('dic', '') or ''))
        self.address.setText(str(supplier.get('address', '') or ''))
        self.vat_payer.setChecked(bool(supplier.get('vat_payer', False)))
        self.show_error('')

    def validate(self) -> None:
        digits = ''.join(ch for ch in self.ico.text() if ch.isdigit())
        if len(digits) != 8:
            raise ValueError('IČO musí obsahovat 8 číslic.')
        if not self.name.text().strip():
            raise ValueError('Název dodavatele je povinný.')

    def values(self) -> dict[str, Any]:
        return {
            'ico': self.ico.text().strip(),
            'name': self.name.text().strip(),
            'dic': self.dic.text().strip(),
            'address': self.address.text().strip(),
            'vat_payer': self.vat_payer.isChecked(),
        }


class DocumentEditorDialog(BaseDialog):
    def __init__(self, parent: QWidget | None = None, *, document: dict | None = None) -> None:
        super().__init__('Upravit finální doklad', parent)
        data = document or {}
        card = Card('Finální doklad', 'Upravují se produkční data ve 2. vrstvě.')
        form = QFormLayout()
        self.number = QLineEdit(str(data.get('document_number', '')))
        self.issued_at = QLineEdit(str(data.get('issued_at', '')))
        self.total_without = QLineEdit(str(data.get('total_without_vat', '0')))
        self.total_with = QLineEdit(str(data.get('total_with_vat', '0')))
        self.vat_summary = QLineEdit(str(data.get('vat_summary', '')))
        self.supplier_id = QLineEdit(str(data.get('supplier_id', '')))
        form.addRow('Číslo dokladu', self.number)
        form.addRow('Datum', self.issued_at)
        form.addRow('Bez DPH', self.total_without)
        form.addRow('S DPH', self.total_with)
        form.addRow('DPH souhrn', self.vat_summary)
        form.addRow('Supplier ID', self.supplier_id)
        card.body_layout.addLayout(form)
        self.root.addWidget(card)
        self.root.addWidget(self._button_row())

    def validate(self) -> None:
        if not self.number.text().strip():
            raise ValueError('Číslo dokladu je povinné.')
        if not self.issued_at.text().strip():
            raise ValueError('Datum dokladu je povinné.')
        for label, widget in [('Bez DPH', self.total_without), ('S DPH', self.total_with)]:
            try:
                float((widget.text() or '0').replace(',', '.'))
            except ValueError as exc:
                raise ValueError(f'{label} musí být číslo.') from exc

    def values(self) -> dict[str, Any]:
        return {
            'document_number': self.number.text().strip(),
            'issued_at': self.issued_at.text().strip(),
            'total_without_vat': float((self.total_without.text() or '0').replace(',', '.')),
            'total_with_vat': float((self.total_with.text() or '0').replace(',', '.')),
            'vat_summary': self.vat_summary.text().strip(),
            'supplier_id': int(self.supplier_id.text()) if self.supplier_id.text().strip() else None,
        }


class ManualCompletionDialog(BaseDialog):
    ITEM_HEADERS = ['Název', 'Množství', 'Cena za kus', 'Cena celkem', 'DPH %']

    def __init__(self, parent: QWidget | None = None, *, detail: dict | None = None, items: list[dict] | None = None) -> None:
        super().__init__('Ruční doplnění dokladu', parent)
        detail = detail or {}
        info_card = Card('Kontext dokladu', 'První vrstva: doplnění rozpoznání před promotion.')
        self.info = QTextEdit()
        self.info.setReadOnly(True)
        self.info.setPlainText(
            f"Soubor: {detail.get('original_name', '—')}\n"
            f"Stav: {detail.get('status', '—')}\n"
            f"Poslední chyba: {detail.get('last_error', '—')}\n"
        )
        info_card.body_layout.addWidget(self.info)
        self.root.addWidget(info_card)
        form_card = Card('Pole dokladu')
        form = QFormLayout()
        self.supplier_ico = QLineEdit(str(detail.get('ico', '')))
        self.supplier_name = QLineEdit(str(detail.get('supplier_name', '')))
        self.supplier_dic = QLineEdit(str(detail.get('dic', '')))
        self.address = QLineEdit(str(detail.get('address', '')))
        self.vat_payer = QCheckBox('Plátce DPH')
        self.vat_payer.setChecked(bool(detail.get('vat_payer', False)))
        self.document_number = QLineEdit(str(detail.get('document_number', '')))
        self.issued_at = QLineEdit(str(detail.get('issued_at', '')))
        self.total_with_vat = QLineEdit(str(detail.get('total_with_vat', '0')))
        form.addRow('IČO', self.supplier_ico)
        form.addRow('Název', self.supplier_name)
        form.addRow('DIČ', self.supplier_dic)
        form.addRow('Sídlo', self.address)
        form.addRow('', self.vat_payer)
        form.addRow('Číslo dokladu', self.document_number)
        form.addRow('Datum', self.issued_at)
        form.addRow('Celkem s DPH', self.total_with_vat)
        form_card.body_layout.addLayout(form)
        self.root.addWidget(form_card)
        items_card = Card('Položky dokladu', 'Položky se ukládají do working DB a musí sedět na sumu dokladu.')
        self.items_table = QTableWidget(0, len(self.ITEM_HEADERS))
        self.items_table.setHorizontalHeaderLabels(self.ITEM_HEADERS)
        self.items_table.horizontalHeader().setStretchLastSection(True)
        self.items_table.verticalHeader().setVisible(False)
        items_card.body_layout.addWidget(self.items_table)
        actions = QWidget()
        actions_layout = QHBoxLayout(actions)
        actions_layout.setContentsMargins(0, 0, 0, 0)
        self.add_item_button = QPushButton('Přidat položku')
        self.remove_item_button = QPushButton('Odstranit položku')
        self.add_item_button.clicked.connect(self._add_empty_item_row)
        self.remove_item_button.clicked.connect(self._remove_selected_item_row)
        actions_layout.addWidget(self.add_item_button)
        actions_layout.addWidget(self.remove_item_button)
        actions_layout.addStretch(1)
        items_card.body_layout.addWidget(actions)
        self.root.addWidget(items_card)
        for item in items or []:
            self._add_item_row(item)
        if not items:
            self._add_empty_item_row()
        self.root.addWidget(self._button_row('Uložit do první vrstvy'))

    def _add_empty_item_row(self) -> None:
        self._add_item_row({})

    def _add_item_row(self, item: dict[str, Any]) -> None:
        row = self.items_table.rowCount()
        self.items_table.insertRow(row)
        values = [
            item.get('name', ''),
            item.get('quantity', ''),
            item.get('unit_price', ''),
            item.get('total_price', ''),
            item.get('vat_rate', ''),
        ]
        for column, value in enumerate(values):
            self.items_table.setItem(row, column, QTableWidgetItem(str(value)))

    def _remove_selected_item_row(self) -> None:
        row = self.items_table.currentRow()
        if row >= 0:
            self.items_table.removeRow(row)

    def validate(self) -> None:
        digits = ''.join(ch for ch in self.supplier_ico.text() if ch.isdigit())
        if len(digits) != 8:
            raise ValueError('IČO musí obsahovat 8 číslic.')
        if not self.document_number.text().strip():
            raise ValueError('Číslo dokladu je povinné.')
        if not self.issued_at.text().strip():
            raise ValueError('Datum dokladu je povinné.')
        if self.items_table.rowCount() == 0:
            raise ValueError('Doklad musí mít alespoň jednu položku.')

    def values(self) -> dict[str, Any]:
        items: list[dict[str, Any]] = []
        for row in range(self.items_table.rowCount()):
            name = self.items_table.item(row, 0).text().strip() if self.items_table.item(row, 0) else ''
            if not name:
                continue
            items.append(
                {
                    'name': name,
                    'quantity': float((self.items_table.item(row, 1).text() if self.items_table.item(row, 1) else '0').replace(',', '.')),
                    'unit_price': float((self.items_table.item(row, 2).text() if self.items_table.item(row, 2) else '0').replace(',', '.')),
                    'total_price': float((self.items_table.item(row, 3).text() if self.items_table.item(row, 3) else '0').replace(',', '.')),
                    'vat_rate': (self.items_table.item(row, 4).text() if self.items_table.item(row, 4) else '').strip(),
                }
            )
        return {
            'supplier_ico': self.supplier_ico.text().strip(),
            'supplier_name': self.supplier_name.text().strip(),
            'supplier_dic': self.supplier_dic.text().strip(),
            'address': self.address.text().strip(),
            'vat_payer': self.vat_payer.isChecked(),
            'document_number': self.document_number.text().strip(),
            'issued_at': self.issued_at.text().strip(),
            'total_with_vat': float((self.total_with_vat.text() or '0').replace(',', '.')),
            'items': items,
        }


class PatternEditorDialog(BaseDialog):
    def __init__(self, parent: QWidget | None = None, *, pattern: dict | None = None, project_path: Path | None = None) -> None:
        super().__init__('Vizuální vzor', parent)
        self.resize(1080, 760)
        self.project_path = project_path
        self.pattern = pattern or {}
        self.shell_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.shell_splitter.setChildrenCollapsible(False)
        self.root.addWidget(self.shell_splitter, 1)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(16)
        self.shell_splitter.addWidget(left)

        meta_card = Card('Definice vzoru', 'Pravidla rozpoznání, anchor text a mapování výstupních polí.')
        form = QFormLayout()
        self.name = QLineEdit(str(self.pattern.get('name', '')))
        self.document_path = QLineEdit(str(self.pattern.get('document_path', '')))
        browse_row = QWidget()
        browse_layout = QHBoxLayout(browse_row)
        browse_layout.setContentsMargins(0, 0, 0, 0)
        browse_layout.setSpacing(8)
        browse_layout.addWidget(self.document_path, 1)
        self.browse = QPushButton('Vybrat dokument')
        self.browse.clicked.connect(self._browse_document)
        browse_layout.addWidget(self.browse)
        self.page_no = QSpinBox()
        self.page_no.setRange(1, 999)
        self.page_no.setValue(int(self.pattern.get('page_no', 1) or 1))
        self.page_no.valueChanged.connect(self._on_page_changed)
        self.ico_rule = QLineEdit(self._decode_json_field(self.pattern, 'recognition_rules', 'ico'))
        self.anchor_rule = QLineEdit(self._decode_json_field(self.pattern, 'recognition_rules', 'anchor_text'))
        self.coords = QLineEdit(json.dumps(self._decode_json(self.pattern, 'preview_state') or {}, ensure_ascii=False))
        self.active = QCheckBox('Aktivní vzor')
        self.active.setChecked(bool(int(self.pattern.get('is_active', 1) or 0)))
        form.addRow('Název vzoru', self.name)
        form.addRow('Vzorový dokument', browse_row)
        form.addRow('Strana', self.page_no)
        form.addRow('Rozpoznání podle IČO', self.ico_rule)
        form.addRow('Anchor text', self.anchor_rule)
        form.addRow('Souřadnice oblasti', self.coords)
        form.addRow('', self.active)
        meta_card.body_layout.addLayout(form)
        left_layout.addWidget(meta_card)

        map_card = Card('Mapování výstupních polí', 'Přidejte řádky pro pole a ukázkové hodnoty.')
        self.field_map_table = QTableWidget(0, 2)
        self.field_map_table.setHorizontalHeaderLabels(['Pole', 'Ukázková hodnota'])
        self.field_map_table.horizontalHeader().setStretchLastSection(True)
        self.field_map_table.verticalHeader().setVisible(False)
        map_card.body_layout.addWidget(self.field_map_table)
        map_actions = QWidget()
        map_actions_layout = QHBoxLayout(map_actions)
        map_actions_layout.setContentsMargins(0, 0, 0, 0)
        add_map_btn = QPushButton('Přidat mapování')
        remove_map_btn = QPushButton('Odstranit mapování')
        add_map_btn.clicked.connect(self._add_empty_field_map_row)
        remove_map_btn.clicked.connect(self._remove_selected_field_map_row)
        map_actions_layout.addWidget(add_map_btn)
        map_actions_layout.addWidget(remove_map_btn)
        map_actions_layout.addStretch(1)
        map_card.body_layout.addWidget(map_actions)
        left_layout.addWidget(map_card, 1)

        preview_card = Card('Předloha dokumentu', 'Vyberte stránku, přibližte náhled a označte oblast.')
        preview_controls = QWidget()
        preview_controls_layout = QHBoxLayout(preview_controls)
        preview_controls_layout.setContentsMargins(0, 0, 0, 0)
        preview_controls_layout.setSpacing(8)
        self.preview_zoom_out = QPushButton('-')
        self.preview_zoom_in = QPushButton('+')
        self.preview_zoom_slider = QSpinBox()
        self.preview_zoom_slider.setRange(25, 300)
        self.preview_zoom_slider.setValue(100)
        self.preview_page_info = QLabel('1 / 1')
        self.preview_hint = QLabel('PDF nebo obrázek můžete přiblížit a označit region pro čtení.')
        self.preview_hint.setWordWrap(True)
        self.preview_zoom_out.clicked.connect(self._zoom_out_preview)
        self.preview_zoom_in.clicked.connect(self._zoom_in_preview)
        self.preview_zoom_slider.valueChanged.connect(self._on_preview_zoom_changed)
        preview_controls_layout.addWidget(self.preview_zoom_out)
        preview_controls_layout.addWidget(self.preview_zoom_slider)
        preview_controls_layout.addWidget(self.preview_zoom_in)
        preview_controls_layout.addWidget(self.preview_page_info)
        preview_controls_layout.addStretch(1)
        preview_card.body_layout.addWidget(preview_controls)
        self.canvas_scroll = QScrollArea()
        self.canvas_scroll.setWidgetResizable(True)
        self.canvas = RegionSelectorWidget()
        self.canvas.regionChanged.connect(self._on_region_changed)
        self.canvas_scroll.setWidget(self.canvas)
        preview_card.body_layout.addWidget(self.canvas_scroll, 1)
        preview_card.body_layout.addWidget(self.preview_hint)
        self.shell_splitter.addWidget(preview_card)

        field_map = self._decode_json(self.pattern, 'field_map')
        for key, value in field_map.items() or {'supplier_ico': '', 'document_number': ''}.items():
            self._add_field_map_row({'field': key, 'value': value})
        if self.document_path.text().strip():
            self.canvas.set_preview_path(self.document_path.text().strip(), page_no=self.page_no.value())
            self._sync_preview_labels()
        self.root.addWidget(self._button_row())
        self._apply_breakpoint_layout(self.width())

    def resizeEvent(self, event: QResizeEvent) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._apply_breakpoint_layout(event.size().width())

    def _apply_breakpoint_layout(self, width: int) -> None:
        breakpoint_name = breakpoint_for_width(width)
        compact = breakpoint_name in {BreakpointName.SM, BreakpointName.MD}
        self.shell_splitter.setOrientation(Qt.Orientation.Vertical if compact else Qt.Orientation.Horizontal)
        self.shell_splitter.setSizes([420, 520] if compact else [460, 620])

    def _decode_json(self, pattern: dict, key: str) -> dict[str, Any]:
        raw = pattern.get(key)
        if isinstance(raw, dict):
            return raw
        try:
            return json.loads(raw or '{}')
        except Exception:
            return {}

    def _decode_json_field(self, pattern: dict, key: str, field: str) -> str:
        return str(self._decode_json(pattern, key).get(field, ''))

    def _browse_document(self) -> None:
        start = str(self.project_path) if self.project_path else str(Path.home())
        path, _ = QFileDialog.getOpenFileName(self, 'Vyberte dokument', start, 'Doklady (*.pdf *.png *.jpg *.jpeg *.bmp);;Všechny soubory (*)')
        if path:
            self.document_path.setText(path)
            self.canvas.set_preview_path(path, page_no=self.page_no.value(), zoom=self.preview_zoom_slider.value())
            self._sync_preview_labels()

    def _on_page_changed(self) -> None:
        if self.document_path.text().strip():
            self.canvas.set_page_no(self.page_no.value())
            self._sync_preview_labels()

    def _on_region_changed(self, rect: dict[str, Any]) -> None:
        self.coords.setText(json.dumps(rect, ensure_ascii=False))
        self._sync_preview_labels()

    def _add_empty_field_map_row(self) -> None:
        self._add_field_map_row({'field': '', 'value': ''})

    def _add_field_map_row(self, row: dict[str, Any]) -> None:
        index = self.field_map_table.rowCount()
        self.field_map_table.insertRow(index)
        self.field_map_table.setItem(index, 0, QTableWidgetItem(str(row.get('field', ''))))
        self.field_map_table.setItem(index, 1, QTableWidgetItem(str(row.get('value', ''))))

    def _remove_selected_field_map_row(self) -> None:
        row = self.field_map_table.currentRow()
        if row >= 0:
            self.field_map_table.removeRow(row)

    def _zoom_out_preview(self) -> None:
        self.preview_zoom_slider.setValue(max(25, self.preview_zoom_slider.value() - 25))

    def _zoom_in_preview(self) -> None:
        self.preview_zoom_slider.setValue(min(300, self.preview_zoom_slider.value() + 25))

    def _on_preview_zoom_changed(self, value: int) -> None:
        self.canvas.set_zoom(value)
        self._sync_preview_labels()

    def _sync_preview_labels(self) -> None:
        self.preview_page_info.setText(f'{self.canvas.page_no} / {self.canvas.page_count}')

    def validate(self) -> None:
        if not self.name.text().strip():
            raise ValueError('Název vzoru je povinný.')
        if not self.document_path.text().strip():
            raise ValueError('Vzorový dokument je povinný.')
        try:
            json.loads(self.coords.text().strip() or '{}')
        except json.JSONDecodeError as exc:
            raise ValueError('Souřadnice oblasti nejsou validní JSON.') from exc

    def values(self) -> dict[str, Any]:
        field_map: dict[str, str] = {}
        for row in range(self.field_map_table.rowCount()):
            field_item = self.field_map_table.item(row, 0)
            value_item = self.field_map_table.item(row, 1)
            field_name = field_item.text().strip() if field_item else ''
            if field_name:
                field_map[field_name] = value_item.text().strip() if value_item else ''
        return {
            'name': self.name.text().strip(),
            'document_path': self.document_path.text().strip(),
            'page_no': self.page_no.value(),
            'recognition_rules': {
                'ico': self.ico_rule.text().strip(),
                'anchor_text': self.anchor_rule.text().strip(),
            },
            'field_map': field_map,
            'is_active': self.active.isChecked(),
            'preview_state': json.loads(self.coords.text().strip() or '{}'),
        }


class GroupEditorDialog(BaseDialog):
    def __init__(self, parent: QWidget | None = None, *, group: dict | None = None) -> None:
        super().__init__('Skupina položek', parent)
        data = group or {}
        card = Card('Skupina položek')
        form = QFormLayout()
        self.name = QLineEdit(str(data.get('name', '')))
        self.description = QTextEdit(str(data.get('description', '')))
        self.is_active = QCheckBox('Aktivní skupina')
        self.is_active.setChecked(bool(int(data.get('is_active', 1) or 0)))
        form.addRow('Název', self.name)
        form.addRow('Popis', self.description)
        form.addRow('', self.is_active)
        card.body_layout.addLayout(form)
        self.root.addWidget(card)
        self.root.addWidget(self._button_row())

    def validate(self) -> None:
        if not self.name.text().strip():
            raise ValueError('Název skupiny je povinný.')

    def values(self) -> dict[str, Any]:
        return {
            'name': self.name.text().strip(),
            'description': self.description.toPlainText().strip(),
            'is_active': self.is_active.isChecked(),
        }


class CatalogEntryDialog(BaseDialog):
    def __init__(self, parent: QWidget | None = None, *, entry: dict | None = None, groups: list[dict] | None = None) -> None:
        super().__init__('Číselník položek', parent)
        data = entry or {}
        groups = groups or []
        card = Card('Číselníková položka')
        form = QFormLayout()
        self.name = QLineEdit(str(data.get('name', '')))
        self.vat_rate = QLineEdit(str(data.get('vat_rate', '')))
        self.group_id = QComboBox()
        self.group_id.addItem('Bez skupiny', None)
        for group in groups:
            self.group_id.addItem(str(group.get('name', '')), int(group.get('id')))
        selected_group = data.get('group_id')
        for index in range(self.group_id.count()):
            if self.group_id.itemData(index) == selected_group:
                self.group_id.setCurrentIndex(index)
                break
        self.notes = QTextEdit(str(data.get('notes', '')))
        self.is_active = QCheckBox('Aktivní položka')
        self.is_active.setChecked(bool(int(data.get('is_active', 1) or 0)))
        form.addRow('Název', self.name)
        form.addRow('DPH sazba', self.vat_rate)
        form.addRow('Skupina', self.group_id)
        form.addRow('Poznámky', self.notes)
        form.addRow('', self.is_active)
        card.body_layout.addLayout(form)
        self.root.addWidget(card)
        self.root.addWidget(self._button_row())

    def validate(self) -> None:
        if not self.name.text().strip():
            raise ValueError('Název číselníkové položky je povinný.')

    def values(self) -> dict[str, Any]:
        return {
            'name': self.name.text().strip(),
            'vat_rate': self.vat_rate.text().strip(),
            'group_id': self.group_id.currentData(),
            'notes': self.notes.toPlainText().strip(),
            'is_active': self.is_active.isChecked(),
        }


class ProjectSetupDialog(BaseDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__('Nový projekt', parent)
        card = Card('Projektové nastavení', 'Program založí project root, working DB, production DB a složky projektu automaticky.')
        form = QFormLayout()
        self.project_name = QLineEdit('')
        self.structure = QLabel('Vzniknou složky input, logs, backups a metadata kajovospend-project.json.')
        self.structure.setWordWrap(True)
        form.addRow('Název projektu', self.project_name)
        form.addRow('Struktura projektu', self.structure)
        card.body_layout.addLayout(form)
        self.root.addWidget(card)
        self.root.addWidget(self._button_row('Založit'))

    def validate(self) -> None:
        if not self.project_name.text().strip():
            raise ValueError('Název projektu je povinný.')

    def values(self) -> dict[str, str]:
        return {'project_name': self.project_name.text().strip()}


class OcrInspectorDialog(BaseDialog):
    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        detail: dict | None = None,
        pages: list[dict] | None = None,
        blocks: list[dict] | None = None,
        fields: list[dict] | None = None,
        lines: list[dict] | None = None,
    ) -> None:
        super().__init__('Forenzní OCR inspektor', parent)
        self.resize(980, 720)
        detail = detail or {}
        pages = pages or []
        blocks = blocks or []
        fields = fields or []
        lines = lines or []
        summary_card = Card('Souhrn OCR detailu')
        self.summary = KeyValueText()
        self.summary.set_mapping(detail)
        summary_card.body_layout.addWidget(self.summary)
        self.root.addWidget(summary_card)
        self.tabs = QTabWidget()
        self.root.addWidget(self.tabs, 1)

        self.pages_table = DataTable()
        self.pages_table.set_records(pages, [
            ('page_no', 'Strana'), ('width', 'Šířka'), ('height', 'Výška'), ('rotation_deg', 'Rotace'),
            ('text_layer_present', 'Text layer'), ('source_kind', 'Zdroj'), ('ocr_status', 'OCR'), ('confidence_avg', 'Confidence'),
        ], key_field='id')
        self.page_text = QTextEdit()
        self.page_text.setReadOnly(True)
        pages_tab = QWidget()
        pages_layout = QVBoxLayout(pages_tab)
        pages_layout.setContentsMargins(0, 0, 0, 0)
        self.pages_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.pages_splitter.setChildrenCollapsible(False)
        self.pages_splitter.addWidget(self.pages_table)
        self.pages_splitter.addWidget(self.page_text)
        pages_layout.addWidget(self.pages_splitter)
        self.tabs.addTab(pages_tab, 'Strany')

        self.blocks_table = DataTable()
        self.blocks_table.set_records(blocks, [
            ('page_no', 'Strana'), ('block_no', 'Blok'), ('source_engine', 'Engine'), ('source_kind', 'Zdroj'), ('confidence', 'Confidence'), ('normalized_text', 'Normalizace')
        ], key_field='id')
        self.block_text = QTextEdit()
        self.block_text.setReadOnly(True)
        blocks_tab = QWidget()
        blocks_layout = QVBoxLayout(blocks_tab)
        blocks_layout.setContentsMargins(0, 0, 0, 0)
        self.blocks_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.blocks_splitter.setChildrenCollapsible(False)
        self.blocks_splitter.addWidget(self.blocks_table)
        self.blocks_splitter.addWidget(self.block_text)
        blocks_layout.addWidget(self.blocks_splitter)
        self.tabs.addTab(blocks_tab, 'Text bloky')

        self.fields_table = DataTable()
        self.fields_table.set_records(fields, [
            ('field_name', 'Pole'), ('raw_value', 'Raw'), ('normalized_value', 'Normalized'), ('confidence', 'Confidence'), ('chosen', 'Chosen'), ('page_id', 'Page ID'), ('source_kind', 'Zdroj')
        ], key_field='id')
        fields_tab = QWidget()
        fields_layout = QVBoxLayout(fields_tab)
        fields_layout.addWidget(self.fields_table)
        self.tabs.addTab(fields_tab, 'Field candidates')

        self.lines_table = DataTable()
        self.lines_table.set_records(lines, [
            ('line_no', 'Line'), ('name_raw', 'Název'), ('qty_raw', 'Qty'), ('unit_price_raw', 'Unit'), ('total_price_raw', 'Total'), ('vat_raw', 'VAT'), ('confidence', 'Confidence'), ('chosen', 'Chosen')
        ], key_field='id')
        lines_tab = QWidget()
        lines_layout = QVBoxLayout(lines_tab)
        lines_layout.addWidget(self.lines_table)
        self.tabs.addTab(lines_tab, 'Line candidates')

        self.pages_table.selectionChangedSignal.connect(self._on_page_selected)
        self.blocks_table.selectionChangedSignal.connect(self._on_block_selected)
        self._on_page_selected()
        self._on_block_selected()
        self.root.addWidget(self._button_row('Zavřít', 'Zavřít'))
        self._apply_breakpoint_layout(self.width())

    def resizeEvent(self, event: QResizeEvent) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._apply_breakpoint_layout(event.size().width())

    def _apply_breakpoint_layout(self, width: int) -> None:
        breakpoint_name = breakpoint_for_width(width)
        compact = breakpoint_name in {BreakpointName.SM, BreakpointName.MD}
        orientation = Qt.Orientation.Vertical if compact else Qt.Orientation.Horizontal
        self.pages_splitter.setOrientation(orientation)
        self.blocks_splitter.setOrientation(orientation)
        sizes = [320, 300] if compact else [420, 560]
        self.pages_splitter.setSizes(sizes)
        self.blocks_splitter.setSizes(sizes)

    def _on_page_selected(self) -> None:
        record = self.pages_table.current_record() or {}
        self.page_text.setPlainText(str(record.get('extracted_text', 'Bez extrahovaného textu.')))

    def _on_block_selected(self) -> None:
        record = self.blocks_table.current_record() or {}
        self.block_text.setPlainText(str(record.get('raw_text', 'Bez raw textu.')))


class TaskProgressDialog(BaseDialog):
    cancelled = Signal()

    def __init__(self, parent: QWidget | None = None, *, title: str = 'Probíhá operace') -> None:
        super().__init__(title, parent)
        self.setModal(False)
        card = Card('Průběh úlohy', 'Dialog zobrazuje reálný stav pipeline, procenta i odhad zbývajícího času.')
        self.message_label = QLabel('Čekám na spuštění úlohy…')
        self.message_label.setWordWrap(True)
        self.step_label = QLabel('Krok: příprava')
        self.step_label.setWordWrap(True)
        self.detail_label = QLabel('')
        self.detail_label.setWordWrap(True)
        self.percent_label = QLabel('0 %')
        self.eta_label = QLabel('ETA: počítám…')
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        metrics_row = QWidget()
        metrics_layout = QHBoxLayout(metrics_row)
        metrics_layout.setContentsMargins(0, 0, 0, 0)
        metrics_layout.addWidget(self.percent_label)
        metrics_layout.addStretch(1)
        metrics_layout.addWidget(self.eta_label)
        self.cancel_button = QPushButton('Storno')
        self.cancel_button.clicked.connect(self._on_cancel_requested)
        card.body_layout.addWidget(self.message_label)
        card.body_layout.addWidget(self.step_label)
        card.body_layout.addWidget(self.detail_label)
        card.body_layout.addWidget(metrics_row)
        card.body_layout.addWidget(self.progress_bar)
        card.body_layout.addWidget(self.cancel_button, alignment=Qt.AlignmentFlag.AlignRight)
        self.root.addWidget(card)

    def update_progress(self, current: int, total: int, message: str) -> None:
        payload = {'label': message or 'Zpracovávám…', 'detail': '', 'step': '', 'percent': 0, 'eta_seconds': None}
        text = str(message or '')
        if text.startswith('{') and text.endswith('}'):
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, dict):
                payload.update(parsed)
        if total <= 0:
            self.progress_bar.setRange(0, 0)
        else:
            self.progress_bar.setRange(0, total)
            self.progress_bar.setValue(max(0, min(current, total)))
        percent = int(payload.get('percent') or (round((current / total) * 100) if total > 0 else 0))
        self.message_label.setText(str(payload.get('label') or 'Zpracovávám…'))
        self.step_label.setText(f"Krok: {str(payload.get('step') or 'běh').replace('_', ' ')}")
        self.detail_label.setText(str(payload.get('detail') or 'Bez detailního technického balastu.'))
        self.percent_label.setText(f'{percent} %')
        eta_seconds = payload.get('eta_seconds')
        if eta_seconds in (None, ''):
            self.eta_label.setText('ETA: heuristika čeká na více dat')
        else:
            eta_seconds = max(int(float(eta_seconds)), 0)
            minutes, seconds = divmod(eta_seconds, 60)
            self.eta_label.setText(f'ETA: {minutes:02d}:{seconds:02d}')

    def _on_cancel_requested(self) -> None:
        self.cancelled.emit()
        self.cancel_button.setEnabled(False)
        self.message_label.setText('Probíhá bezpečné zastavení úlohy…')
        self.detail_label.setText('Čekám na dokončení právě běžícího kroku, aby nedošlo ke ztrátě stavu.')
