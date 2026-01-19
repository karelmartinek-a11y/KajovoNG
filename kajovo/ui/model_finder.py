from __future__ import annotations

from typing import List, Optional

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QCheckBox, QPushButton,
    QListWidget, QListWidgetItem
)
from .theme import DARK_STYLESHEET

from ..core.model_capabilities import ModelCapabilitiesCache, ModelCapabilities


class ModelFinderDialog(QDialog):
    def __init__(self, cache: ModelCapabilitiesCache, models: List[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Find model")
        self.resize(740, 540)
        self.setStyleSheet(DARK_STYLESHEET)
        self.cache = cache
        self.models = sorted(models)
        self.selected_model: Optional[str] = None

        v = QVBoxLayout(self)

        row = QHBoxLayout()
        row.addWidget(QLabel("Search"))
        self.ed = QLineEdit()
        self.ed.setPlaceholderText("filter by model id")
        row.addWidget(self.ed, 1)
        v.addLayout(row)

        feats = QHBoxLayout()
        self.chk_prev = QCheckBox("needs cascade (previous_response_id)")
        self.chk_temp = QCheckBox("needs temperature")
        self.chk_fs = QCheckBox("needs file_search")
        self.chk_include_untested = QCheckBox("include untested")
        self.chk_include_untested.setChecked(False)

        feats.addWidget(self.chk_prev)
        feats.addWidget(self.chk_temp)
        feats.addWidget(self.chk_fs)
        feats.addStretch(1)
        feats.addWidget(self.chk_include_untested)
        v.addLayout(feats)

        self.lst = QListWidget()
        v.addWidget(self.lst, 1)

        btns = QHBoxLayout()
        self.btn_apply = QPushButton("Use selected")
        self.btn_close = QPushButton("Close")
        btns.addStretch(1)
        btns.addWidget(self.btn_apply)
        btns.addWidget(self.btn_close)
        v.addLayout(btns)

        self.btn_close.clicked.connect(self.close)
        self.btn_apply.clicked.connect(self.on_apply)

        self.ed.textChanged.connect(self.refresh)
        self.chk_prev.stateChanged.connect(self.refresh)
        self.chk_temp.stateChanged.connect(self.refresh)
        self.chk_fs.stateChanged.connect(self.refresh)
        self.chk_include_untested.stateChanged.connect(self.refresh)

        self.refresh()

    def _match_caps(self, caps: Optional[ModelCapabilities]) -> bool:
        need_prev = self.chk_prev.isChecked()
        need_temp = self.chk_temp.isChecked()
        need_fs = self.chk_fs.isChecked()
        include_untested = self.chk_include_untested.isChecked()

        if caps is None:
            return include_untested

        if need_prev and not caps.supports_previous_response_id:
            return False
        if need_temp and not caps.supports_temperature:
            return False
        if need_fs and not caps.supports_file_search:
            return False
        return True

    def refresh(self):
        q = (self.ed.text() or "").strip().lower()
        self.lst.clear()

        for m in self.models:
            if q and q not in m.lower():
                continue
            caps = self.cache.get(m)
            if not self._match_caps(caps):
                continue

            if caps is None:
                status = "untested"
            else:
                status = (
                    f"prev={int(caps.supports_previous_response_id)} "
                    f"temp={int(caps.supports_temperature)} "
                    f"fs={int(caps.supports_file_search)}"
                )
            it = QListWidgetItem(f"{m}   [{status}]")
            it.setData(32, m)
            self.lst.addItem(it)

    def on_apply(self):
        sel = self.lst.selectedItems()
        if not sel:
            return
        self.selected_model = sel[0].data(32)
        self.accept()
