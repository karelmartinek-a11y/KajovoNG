from __future__ import annotations

from kajovospend.app.settings import AppSettings
from kajovospend.ui.tokens import COLORS, RADII


def stylesheet(settings: AppSettings | None = None) -> str:
    reduced = bool(settings.reduced_motion) if settings else False
    subtle_hover = COLORS.surface_100 if reduced else COLORS.surface_hover
    subtle_press = COLORS.surface_100 if reduced else COLORS.surface_press
    primary_hover = COLORS.brand_red if reduced else COLORS.brand_red_hover
    return f'''
    * {{
        font-family: Montserrat, sans-serif;
        color: {COLORS.ink_900};
        outline: none;
    }}
    QMainWindow, QWidget {{
        background: {COLORS.surface_200};
        font-size: 16px;
    }}
    QWidget#RootShell {{
        background: {COLORS.surface_200};
    }}
    QWidget#AppHeader {{
        background: {COLORS.surface_900};
        border-bottom: 1px solid {COLORS.line_700};
    }}
    QWidget#AppSubHeader {{
        background: {COLORS.surface_800};
        border-bottom: 1px solid {COLORS.line_700};
    }}
    QWidget#FooterBar {{
        background: {COLORS.surface_100};
        border-top: 1px solid {COLORS.line_300};
    }}
    QLabel#BrandWordmark {{
        color: {COLORS.white};
        font-size: 24px;
        font-weight: 700;
        letter-spacing: 0.3px;
    }}
    QLabel#BrandSignageImage {{
        background: transparent;
    }}
    QLabel#SectionTitle {{
        font-size: 20px;
        line-height: 28px;
        font-weight: 700;
    }}
    QLabel#StatValue {{
        font-size: 32px;
        line-height: 40px;
        font-weight: 700;
    }}
    QLabel#EmptyStateTitle {{
        font-size: 20px;
        line-height: 28px;
        font-weight: 700;
    }}
    QLabel#EmptyStateIcon, QLabel#StateIcon {{
        color: {COLORS.brand_red};
        font-size: 28px;
    }}
    QLabel[muted="true"] {{
        color: {COLORS.ink_500};
    }}
    QLabel[signalLight="true"] {{
        background: transparent;
        font-size: 14px;
        font-weight: 700;
        min-width: 18px;
        max-width: 18px;
        qproperty-alignment: 'AlignCenter';
    }}
    QLabel[signalState="ok"] {{ color: {COLORS.success}; }}
    QLabel[signalState="warning"] {{ color: {COLORS.warning}; }}
    QLabel[signalState="error"] {{ color: {COLORS.error}; }}
    QLabel[signalState="neutral"] {{ color: {COLORS.line_300}; }}
    QLabel[pill="true"] {{
        border-radius: {RADII.r12}px;
        background: {COLORS.surface_300};
        color: {COLORS.ink_700};
        padding: 6px 10px;
        font-size: 12px;
        font-weight: 600;
    }}
    QLabel[pill="true"][tone="ok"], QLabel[statusType="ok"] {{
        background: {COLORS.success_surface};
        color: {COLORS.success};
        border: 1px solid {COLORS.success_line};
        border-radius: {RADII.r12}px;
        padding: 6px 10px;
    }}
    QLabel[pill="true"][tone="warning"], QLabel[statusType="warning"] {{
        background: {COLORS.warning_surface};
        color: {COLORS.warning};
        border: 1px solid {COLORS.warning_line};
        border-radius: {RADII.r12}px;
        padding: 6px 10px;
    }}
    QLabel[pill="true"][tone="error"], QLabel[statusType="error"] {{
        background: {COLORS.error_surface};
        color: {COLORS.error};
        border: 1px solid {COLORS.error_line};
        border-radius: {RADII.r12}px;
        padding: 6px 10px;
    }}
    QLabel[pill="true"][tone="info"], QLabel[statusType="info"] {{
        background: {COLORS.info_surface};
        color: {COLORS.info};
        border: 1px solid {COLORS.info_line};
        border-radius: {RADII.r12}px;
        padding: 6px 10px;
    }}
    QLabel[statusType="neutral"] {{
        background: {COLORS.surface_300};
        color: {COLORS.ink_700};
        border: 1px solid {COLORS.line_300};
        border-radius: {RADII.r12}px;
        padding: 6px 10px;
    }}
    QFrame[card="true"] {{
        background: {COLORS.surface_100};
        border: 1px solid {COLORS.line_300};
        border-radius: {RADII.r16}px;
    }}
    QFrame[card="true"][tone="danger"] {{
        border-color: {COLORS.error_line};
    }}
    QPushButton {{
        min-height: 36px;
        min-width: 36px;
        border-radius: {RADII.r12}px;
        padding: 8px 14px;
        border: 1px solid {COLORS.line_300};
        background: {COLORS.surface_100};
        font-size: 14px;
        font-weight: 700;
    }}
    QPushButton:hover {{
        background: {subtle_hover};
    }}
    QPushButton:pressed {{
        background: {subtle_press};
    }}
    QPushButton:disabled {{
        color: {COLORS.disabled_text};
        background: {COLORS.surface_300};
        border-color: {COLORS.line_300};
    }}
    QPushButton#PrimaryButton, QPushButton#importButton {{
        background: {COLORS.brand_red};
        color: {COLORS.white};
        border-color: {COLORS.brand_red};
    }}
    QPushButton#PrimaryButton:hover, QPushButton#importButton:hover {{
        background: {primary_hover};
        border-color: {primary_hover};
    }}
    QPushButton#stopButton {{
        background: {COLORS.surface_100};
        color: {COLORS.error};
        border-color: {COLORS.stop_line};
    }}
    QPushButton[navButton="true"] {{
        min-height: 44px;
        border-radius: {RADII.r0}px;
        background: transparent;
        border: none;
        color: {COLORS.nav_text};
        padding: 10px 14px;
        text-align: center;
    }}
    QPushButton[navButton="true"]:hover {{
        color: {COLORS.white};
        background: {COLORS.surface_700};
    }}
    QPushButton[navButton="true"]:checked {{
        color: {COLORS.white};
        background: {COLORS.surface_700};
        border-bottom: 3px solid {COLORS.brand_red};
    }}
    QPushButton[navButton="true"][compact="true"] {{
        border-radius: {RADII.r12}px;
        border: 1px solid transparent;
        margin-right: 4px;
        padding: 8px 14px;
    }}
    QPushButton[navButton="true"][compact="true"]:checked {{
        border: 1px solid {COLORS.line_300};
        background: {COLORS.surface_100};
        color: {COLORS.ink_900};
        border-bottom: 1px solid {COLORS.line_300};
    }}
    QPushButton[navButton="true"][compact="true"]:hover {{
        background: {COLORS.surface_300};
        color: {COLORS.ink_900};
    }}
    QLineEdit, QComboBox, QSpinBox, QTextEdit, QListWidget, QTableWidget, QScrollArea {{
        background: {COLORS.surface_100};
        border: 1px solid {COLORS.line_500};
        border-radius: {RADII.r12}px;
        selection-background-color: {COLORS.selection_bg};
        selection-color: {COLORS.ink_900};
    }}
    QLineEdit, QComboBox, QSpinBox {{
        min-height: 36px;
        padding: 6px 10px;
    }}
    QTextEdit {{
        padding: 10px 12px;
    }}
    QComboBox::drop-down {{
        width: 26px;
        border: none;
        background: transparent;
    }}
    QTableWidget {{
        border-radius: {RADII.r12}px;
        gridline-color: transparent;
        alternate-background-color: {COLORS.table_alt};
    }}
    QHeaderView::section {{
        background: {COLORS.surface_100};
        border: none;
        border-bottom: 1px solid {COLORS.line_300};
        padding: 10px 8px;
        font-size: 12px;
        font-weight: 700;
        color: {COLORS.ink_700};
    }}
    QLabel#Signage {{
        background: transparent;
        color: {COLORS.brand_red};
        font-size: 12px;
        font-weight: 800;
        letter-spacing: 0.4px;
    }}
    QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QTextEdit:focus, QListWidget:focus, QTableWidget:focus, QPushButton:focus {{
        border: 2px solid {COLORS.focus};
    }}
    QProgressBar {{
        border: 1px solid {COLORS.line_300};
        border-radius: {RADII.r12}px;
        text-align: center;
        background: {COLORS.surface_300};
        min-height: 20px;
    }}
    QProgressBar::chunk {{
        background: {COLORS.brand_red};
        border-radius: {RADII.r12}px;
    }}
    QLabel[overlayTitle="true"] {{
        font-size: 24px;
        line-height: 32px;
        font-weight: 700;
    }}
    '''
