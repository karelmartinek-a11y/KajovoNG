from __future__ import annotations

import re
from dataclasses import fields
from pathlib import Path

from kajovospend.ui.design_contract import PRIMARY_VIEW_STATES, StateName
from kajovospend.ui.tokens import BREAKPOINTS, COLORS, MOTION, RADII, TYPE


REPO_ROOT = Path(__file__).resolve().parents[1]
MAIN_WINDOW = (REPO_ROOT / 'kajovospend' / 'ui' / 'main_window.py').read_text(encoding='utf-8')
DIALOGS = (REPO_ROOT / 'kajovospend' / 'ui' / 'dialogs' / 'forms.py').read_text(encoding='utf-8')
THEME = (REPO_ROOT / 'kajovospend' / 'branding' / 'theme.py').read_text(encoding='utf-8')
PRIMITIVES = (REPO_ROOT / 'kajovospend' / 'ui' / 'widgets' / 'primitives.py').read_text(encoding='utf-8')


def test_design_tokens_match_governance_palette() -> None:
    assert COLORS.brand_red == '#FF0000'
    assert COLORS.white == '#FFFFFF'
    assert COLORS.ink_900 == '#111111'
    assert COLORS.success == '#1B5E20'
    assert COLORS.warning == '#E65100'
    assert COLORS.error == '#B71C1C'
    assert COLORS.info == '#0D47A1'
    assert COLORS.metal == '#737578'
    assert RADII.r0 == 0
    assert RADII.r8 == 8
    assert RADII.r12 == 12
    assert RADII.r16 == 16
    assert TYPE.h1 == (32, 40, 700)
    assert TYPE.h2 == (24, 32, 700)
    assert TYPE.h3 == (20, 28, 700)
    assert TYPE.body == (16, 24, 400)
    assert TYPE.small == (14, 20, 400)
    assert TYPE.micro == (12, 16, 400)
    assert TYPE.button == (14, 20, 700)
    assert MOTION.micro_ms == 140
    assert MOTION.view_ms == 220
    assert MOTION.overlay_ms == 180


def test_breakpoints_and_state_contract_exist() -> None:
    assert BREAKPOINTS == {'sm': 0, 'md': 600, 'lg': 1024, 'xl': 1440}
    assert PRIMARY_VIEW_STATES == (
        StateName.DEFAULT,
        StateName.LOADING,
        StateName.EMPTY,
        StateName.ERROR,
        StateName.OFFLINE,
        StateName.MAINTENANCE,
        StateName.FALLBACK,
    )


def test_main_window_registers_state_hosts_for_all_primary_views() -> None:
    assert 'def _register_state_page' in MAIN_WINDOW
    for key in ['dashboard', 'expenses', 'accounts', 'suppliers', 'operations', 'quarantine', 'unrecognized', 'settings']:
        assert f"_register_state_page('{key}'" in MAIN_WINDOW


def test_main_window_sets_runtime_states_for_primary_views() -> None:
    for key in ['dashboard', 'expenses', 'accounts', 'suppliers', 'operations', 'quarantine', 'unrecognized', 'settings']:
        assert f"_set_page_state('{key}'" in MAIN_WINDOW


def test_dialog_suite_is_brand_hosted() -> None:
    assert 'class BaseDialog' in DIALOGS
    assert "BrandLockup('KajovoSpendNG')" in DIALOGS
    for name in ['ConfirmDialog', 'InfoDialog', 'WarningDialog', 'ErrorDialog']:
        assert f'class {name}' in DIALOGS


def test_theme_is_tokenized_and_qmessagebox_is_forbidden() -> None:
    assert 'font-family: Montserrat, sans-serif' in THEME
    assert 'font-size: 16px;' in THEME
    assert 'border: 2px solid' in THEME
    assert 'border: 2px solid {COLORS.focus};' in THEME
    forbidden_dialog_api = 'QMessage' + 'Box'
    assert forbidden_dialog_api not in MAIN_WINDOW
    assert forbidden_dialog_api not in DIALOGS
    lower = THEME.lower()
    assert 'gradient' not in lower
    assert 'blur' not in lower
    assert 'pattern' not in lower
    hex_values = set(re.findall(r'#[0-9A-Fa-f]{6}', THEME))
    allowed = {getattr(COLORS, item.name).upper() for item in fields(COLORS) if isinstance(getattr(COLORS, item.name), str)}
    assert {value.upper() for value in hex_values}.issubset(allowed)


def test_theme_respects_reduced_motion_mode() -> None:
    assert 'reduced_motion' in (REPO_ROOT / 'kajovospend' / 'app' / 'settings.py').read_text(encoding='utf-8')
    assert 'subtle_hover = COLORS.surface_100 if reduced else COLORS.surface_hover' in THEME
    assert 'subtle_press = COLORS.surface_100 if reduced else COLORS.surface_press' in THEME
    assert 'primary_hover = COLORS.brand_red if reduced else COLORS.brand_red_hover' in THEME


def test_state_primitives_exist() -> None:
    for name in ['StateHost', 'LoadingState', 'EmptyState', 'ErrorState', 'OfflineState', 'MaintenanceState', 'FallbackState']:
        assert f'class {name}' in PRIMITIVES


def test_text_integrity_has_no_known_mojibake_patterns() -> None:
    corpus = '\n'.join([MAIN_WINDOW, DIALOGS, THEME, PRIMITIVES])
    forbidden = [
        chr(0x00C3),
        chr(0x0102),
        chr(0x0139),
        chr(0x00C4),
        chr(0x00E2) + chr(0x20AC),
        chr(0x00E2) + chr(0x20AC) + chr(0x201D),
        chr(0x00C2) + chr(0x00B7),
    ]
    for token in forbidden:
        assert token not in corpus
