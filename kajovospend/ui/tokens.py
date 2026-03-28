from __future__ import annotations

from dataclasses import dataclass

from kajovospend.ui.design_contract import BREAKPOINT_SPECS

@dataclass(frozen=True, slots=True)
class ColorTokens:
    brand_red: str = '#FF0000'
    white: str = '#FFFFFF'
    ink_900: str = '#111111'
    ink_700: str = '#333333'
    ink_500: str = '#666666'
    line_300: str = '#E0E0E0'
    surface_100: str = '#FFFFFF'
    surface_200: str = '#F5F5F5'
    surface_300: str = '#EEEEEE'
    surface_900: str = '#171A1F'
    success: str = '#1B5E20'
    warning: str = '#E65100'
    error: str = '#B71C1C'
    info: str = '#0D47A1'
    focus: str = '#0D47A1'
    ink_000: str = '#000000'
    metal: str = '#737578'
    subtle_metal: str = '#9AA0A6'
    surface_900: str = '#171A1F'
    surface_800: str = '#20242B'
    surface_700: str = '#252A31'
    line_500: str = '#CACFD6'
    line_700: str = '#2A2E36'
    disabled_text: str = '#9CA3AF'
    nav_text: str = '#D7DBE3'
    selection_bg: str = '#DBE8FF'
    table_alt: str = '#FBFBFB'
    brand_red_hover: str = '#D90000'
    surface_hover: str = '#FAFAFA'
    surface_press: str = '#EDEDED'
    success_surface: str = '#E7F5EA'
    success_line: str = '#B9DFC1'
    warning_surface: str = '#FFF1E7'
    warning_line: str = '#F4C3A1'
    error_surface: str = '#FBE9E9'
    error_line: str = '#E7B8B8'
    info_surface: str = '#E8F0FB'
    info_line: str = '#BCD0F0'
    stop_line: str = '#D5B5B5'


@dataclass(frozen=True, slots=True)
class RadiusTokens:
    r0: int = 0
    r8: int = 8
    r12: int = 12
    r16: int = 16


@dataclass(frozen=True, slots=True)
class SpacingTokens:
    xxs: int = 4
    xs: int = 8
    sm: int = 12
    md: int = 16
    lg: int = 24
    xl: int = 32
    xxl: int = 40


@dataclass(frozen=True, slots=True)
class ElevationTokens:
    e0: int = 0
    e1: int = 1
    e2: int = 2
    e3: int = 3


@dataclass(frozen=True, slots=True)
class TypographyTokens:
    h1: tuple[int, int, int] = (32, 40, 700)
    h2: tuple[int, int, int] = (24, 32, 700)
    h3: tuple[int, int, int] = (20, 28, 700)
    body: tuple[int, int, int] = (16, 24, 400)
    small: tuple[int, int, int] = (14, 20, 400)
    micro: tuple[int, int, int] = (12, 16, 400)
    button: tuple[int, int, int] = (14, 20, 700)
    dialog_title: tuple[int, int, int] = (24, 32, 700)
    state_title: tuple[int, int, int] = (20, 28, 700)


@dataclass(frozen=True, slots=True)
class MotionTokens:
    micro_ms: int = 140
    view_ms: int = 220
    overlay_ms: int = 180


COLORS = ColorTokens()
RADII = RadiusTokens()
SPACING = SpacingTokens()
ELEVATION = ElevationTokens()
TYPE = TypographyTokens()
MOTION = MotionTokens()

BREAKPOINTS = {
    spec.name.value: spec.minimum_width for spec in BREAKPOINT_SPECS
}

Z_INDEX = {
    'base': 0,
    'dropdown': 100,
    'overlay': 200,
    'modal': 300,
    'critical_modal': 400,
}
