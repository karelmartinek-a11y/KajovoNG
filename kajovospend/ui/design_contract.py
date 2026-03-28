from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class BreakpointName(StrEnum):
    SM = 'sm'
    MD = 'md'
    LG = 'lg'
    XL = 'xl'


class StateName(StrEnum):
    DEFAULT = 'default'
    LOADING = 'loading'
    EMPTY = 'empty'
    ERROR = 'error'
    OFFLINE = 'offline'
    MAINTENANCE = 'maintenance'
    FALLBACK = 'fallback'


@dataclass(frozen=True, slots=True)
class BreakpointSpec:
    name: BreakpointName
    minimum_width: int
    maximum_width: int | None = None

    def matches(self, width: int) -> bool:
        return width >= self.minimum_width and (self.maximum_width is None or width <= self.maximum_width)


@dataclass(frozen=True, slots=True)
class BrandHostRule:
    minimum_brand_elements: int = 1
    maximum_brand_elements: int = 2


BREAKPOINT_SPECS: tuple[BreakpointSpec, ...] = (
    BreakpointSpec(BreakpointName.SM, 0, 599),
    BreakpointSpec(BreakpointName.MD, 600, 1023),
    BreakpointSpec(BreakpointName.LG, 1024, 1439),
    BreakpointSpec(BreakpointName.XL, 1440, None),
)

PRIMARY_VIEW_STATES: tuple[StateName, ...] = (
    StateName.DEFAULT,
    StateName.LOADING,
    StateName.EMPTY,
    StateName.ERROR,
    StateName.OFFLINE,
    StateName.MAINTENANCE,
    StateName.FALLBACK,
)

BRAND_HOST_RULE = BrandHostRule()


def breakpoint_for_width(width: int) -> BreakpointName:
    for spec in BREAKPOINT_SPECS:
        if spec.matches(width):
            return spec.name
    return BreakpointName.XL
