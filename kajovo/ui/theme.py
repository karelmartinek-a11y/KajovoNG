from __future__ import annotations

DARK_STYLESHEET = """QWidget {
  background: #111111;
  color: #EAEAEA;
  font-family: Montserrat, Segoe UI, Arial;
}
QLabel { background: transparent; }
QLineEdit, QTextEdit, QPlainTextEdit, QTextBrowser, QComboBox, QListWidget, QTableWidget, QSpinBox, QDoubleSpinBox, QDateEdit {
  background: #1b1b1b;
  border: 1px solid #707070;
  border-radius: 8px;
  padding: 6px;
}
QComboBox::drop-down { border: 0px; }
QAbstractItemView { selection-background-color: #2699E8; selection-color: #0b0b0b; }
QComboBox QAbstractItemView { background: #1b1b1b; selection-background-color: #2699E8; selection-color: #0b0b0b; }
QPushButton {
  background: #1b1b1b;
  border: 1px solid #707070;
  border-radius: 10px;
  padding: 5px 10px;
}
QPushButton:hover { border-color: #2699E8; }
QPushButton:pressed { background: #161616; }
QPushButton#PrimaryButton {
  background: #2699E8;
  border: 1px solid #2699E8;
  color: #0b0b0b;
  font-weight: 600;
}
QPushButton#PrimaryButton:hover { background: #2FA0FF; }
QGroupBox { border: 1px solid #2b2b2b; border-radius: 10px; margin-top: 8px; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 6px 0 6px; }
QTabBar::tab { background: #1b1b1b; padding: 8px 14px; border-top-left-radius: 10px; border-top-right-radius: 10px; margin-right: 4px; }
QTabBar::tab:selected { background: #161616; border: 1px solid #2699E8; }
QProgressBar { color: #FFFFFF; border: 1px solid #707070; border-radius: 6px; background: #1b1b1b; text-align: center; }
QProgressBar::chunk { background: #17649A; border-radius: 5px; }
QHeaderView::section { background: #242424; color: #EAEAEA; padding: 5px; border: 1px solid #707070; }
QWidget:disabled { color: #A0A0A0; }
QPushButton:disabled { background: #242424; color: #A0A0A0; border-color: #555555; }
QPushButton:focus, QLineEdit:focus, QComboBox:focus, QAbstractSpinBox:focus, QPlainTextEdit:focus, QTextEdit:focus { border: 2px solid #65BFFF; }
QCheckBox:focus, QRadioButton:focus { color: #FFFFFF; background: #303030; }
QToolTip { color: #FFFFFF; background: #242424; border: 1px solid #707070; }
QCheckBox::indicator {
  width: 16px;
  height: 16px;
  border-radius: 4px;
  border: 1px solid #2699E8;
  background: #1b1b1b;
}
QCheckBox::indicator:checked {
  border: 1px solid #2699E8;
  background: #2699E8;
}
QCheckBox::indicator:unchecked:hover { border: 1px solid #2FA0FF; }
QCheckBox::indicator:checked:hover { border: 1px solid #2FA0FF; background: #2FA0FF; }
"""
