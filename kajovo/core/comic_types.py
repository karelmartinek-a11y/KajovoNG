"""Kontrakty komiksu, nezávislé na Qt a poskytovatelském transportu."""
from __future__ import annotations

import copy
import json
import math
import re
from dataclasses import dataclass

from .structured_output import obj

IMAGE_MODEL = "gpt-image-2.5-sunburst-2026-09-08"
TEXT_MODEL = "gpt-6-astra"
BIBLE_FIELDS = (
    "art_direction", "linework_rules", "color_rules", "lighting_rules", "materials",
    "character_consistency", "environment_consistency", "camera", "typography",
    "speech_balloons", "captions", "sfx", "negative_constraints", "identity_rules",
    "style_consistency",
)
BIBLE_SCHEMA = obj({name: {"type": "string"} for name in BIBLE_FIELDS})
DESCRIPTOR_SCHEMA = obj({"descriptor": {"type": "string"}})
DEFAULT_STYLE = {
    "description": "", "line": "standardní", "color": "barevná", "palette": [],
    "palette_description": "", "balloon": "dialogová", "sfx": False,
    "typography": "Čitelné komiksové písmo s českou diakritikou", "extra": "",
}


class ComicError(ValueError):
    """Doménová chyba s bezpečným českým sdělením a možností opakování."""
    def __init__(self, code, message, retryable=False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def checked_text(value, label, maximum=30000, required=False):
    if not isinstance(value, str) or len(value) > maximum or (required and not value.strip()):
        raise ComicError("invalid_input", f"{label}: vyplňte text do {maximum} znaků.")
    return value.strip()


def validate_style(value):
    if not isinstance(value, dict) or set(value) - set(DEFAULT_STYLE):
        raise ComicError("invalid_input", "Neplatné nastavení stylu.")
    style = copy.deepcopy(DEFAULT_STYLE)
    style.update(value)
    for field in ("description", "palette_description", "typography", "extra"):
        checked_text(style[field], field, 4000)
    for field, options in {"line": ("jemná", "standardní", "silná"),
                           "color": ("barevná", "černobílá", "vlastní paleta"),
                           "balloon": ("dialogová", "myšlenková", "narativní")}.items():
        if style[field] not in options:
            raise ComicError("invalid_input", f"Neplatná volba {field}.")
    if type(style["sfx"]) is not bool or not isinstance(style["palette"], list):
        raise ComicError("invalid_input", "Neplatné nastavení SFX nebo palety.")
    if len(style["palette"]) > 16 or any(not isinstance(c, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", c) for c in style["palette"]):
        raise ComicError("invalid_input", "Paleta má nejvýše 16 barev ve formátu #RRGGBB.")
    return style


def normalize_bible(result, style):
    """Volby uživatele mají samostatnou autoritativní část, model je nenahrazuje."""
    from jsonschema import validate
    validate(result, BIBLE_SCHEMA)
    if any(not result[key].strip() for key in BIBLE_FIELDS):
        raise ComicError("invalid_output", "Bible obsahuje prázdnou kapitolu.")
    output = dict(result)
    output.update(linework_rules=style["line"], typography=style["typography"],
                  speech_balloons=style["balloon"],
                  color_rules=canonical({k: style[k] for k in ("color", "palette", "palette_description")}))
    if not style["sfx"]:
        output["sfx"] = "SFX jsou zakázány. Nekresli ani nepřidávej zvukové nápisy."
    return {"rules": output, "explicit_options": copy.deepcopy(style)}


def validate_document(value):
    if not isinstance(value, dict) or set(value) != {"version", "nodes"} or value["version"] != 1:
        raise ComicError("invalid_document", "Nepodporovaná verze zadání panelu.")
    if not isinstance(value["nodes"], list) or len(value["nodes"]) > 2000:
        raise ComicError("invalid_document", "Zadání má příliš mnoho částí.")
    for node in value["nodes"]:
        if not isinstance(node, dict):
            raise ComicError("invalid_document", "Neplatná část zadání.")
        if node.get("type") == "text" and set(node) == {"type", "text"}:
            checked_text(node["text"], "Zadání")
        elif node.get("type") in ("character_ref", "environment_ref") and set(node) == {"type", "entity_id"}:
            if not isinstance(node["entity_id"], str) or not re.fullmatch(r"[a-f0-9]{32}", node["entity_id"]):
                raise ComicError("broken_reference", "Zadání obsahuje neplatný odkaz na entitu.")
        else:
            raise ComicError("invalid_document", "Neznámá část zadání.")
    if len(canonical(value)) > 40000:
        raise ComicError("invalid_document", "Zadání je příliš dlouhé.")
    return copy.deepcopy(value)


@dataclass(frozen=True)
class PanelFormat:
    width: int = 2048
    height: int = 2048
    dpi: int = 300
    fit: str = "pad"
    experimental: bool = False

    def __post_init__(self):
        if any(type(v) is not int for v in (self.width, self.height, self.dpi)) or not (
            1 <= self.width <= 16384 and 1 <= self.height <= 16384
            and self.width * self.height <= 64000000 and 150 <= self.dpi <= 600
        ) or self.fit not in ("pad", "crop") or type(self.experimental) is not bool:
            raise ComicError("invalid_format", "Cíl musí mít 1–16 384 px na hranu, nejvýše 64 MP a 150–600 DPI.")

    @classmethod
    def paper(cls, name, landscape=False, dpi=300):
        sizes = {"A4": (210, 297), "A5": (148, 210), "A6": (105, 148), "DL": (110, 220)}
        if name not in sizes:
            raise ComicError("invalid_format", "Neznámý papírový formát.")
        w, h = sizes[name]
        if landscape:
            w, h = h, w
        return cls(round(w * dpi / 25.4), round(h * dpi / 25.4), dpi)

    def native_size(self, capability):
        maximum = capability["max_pixels"] if self.experimental else capability["standard_pixels"]
        multiple, max_ratio = capability["multiple"], capability["max_ratio"]
        area = min(self.width * self.height, maximum)
        ratio = min(max_ratio, max(1 / max_ratio, self.width / self.height))
        target_w = math.sqrt(max(area, capability["min_pixels"]) * ratio)
        target_h = target_w / ratio
        candidates = []
        for width in range(multiple, capability["max_edge"] + 1, multiple):
            for height in {max(multiple, int(width / ratio / multiple) * multiple), max(multiple, math.ceil(width / ratio / multiple) * multiple)}:
                pixels = width * height
                if (height <= capability["max_edge"] and max(width, height) <= max_ratio * min(width, height)
                        and capability["min_pixels"] <= pixels <= maximum):
                    score = abs(math.log(width / height / ratio)) * 5 + abs(math.log(width / target_w)) + abs(math.log(height / target_h))
                    candidates.append((score, width, height))
        _, w, h = min(candidates)
        return f"{w}x{h}"


def validate_overlays(value, sfx=True):
    if not isinstance(value, list) or len(value) > 40:
        raise ComicError("invalid_overlay", "Panel dovoluje nejvýše 40 textových prvků.")
    for layer in value:
        if not isinstance(layer, dict) or set(layer) - {"bold"} != {"kind", "text", "x", "y", "w", "h", "tail_x", "tail_y", "font_size"}:
            raise ComicError("invalid_overlay", "Neplatný textový prvek.")
        if "bold" in layer and type(layer["bold"]) is not bool:
            raise ComicError("invalid_overlay", "Neplatná typografie textového prvku.")
        if layer["kind"] not in ("dialog", "thought", "caption", "sfx") or (layer["kind"] == "sfx" and not sfx):
            raise ComicError("sfx_disabled", "SFX nejsou povoleny stylem komiksu.")
        checked_text(layer["text"], "Text bubliny", 3000, True)
        for key in ("x", "y", "w", "h", "tail_x", "tail_y", "font_size"):
            v = layer[key]
            if type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 1:
                raise ComicError("invalid_overlay", "Pozice a velikosti musí ležet uvnitř panelu.")
        if layer["w"] <= 0 or layer["h"] <= 0 or layer["font_size"] <= 0 or layer["x"] + layer["w"] > 1.001 or layer["y"] + layer["h"] > 1.001:
            raise ComicError("invalid_overlay", "Textový prvek přesahuje panel.")
    return copy.deepcopy(value)
