"""Kontrakty komiksu, nezávislé na Qt a poskytovatelském transportu."""
from __future__ import annotations

import copy
import math
import re
from dataclasses import dataclass

from .structured_output import array, obj
from .orchestration.contracts import canonical_bytes

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

_COMIC_TEXT = {"type": "string"}
_COMIC_STRINGS = array(_COMIC_TEXT)
_DIALOGUE_SCHEMA = obj({
    "speaker": _COMIC_TEXT,
    "text": _COMIC_TEXT,
})
STORY_SCHEMA = obj({
    "title": _COMIC_TEXT,
    "premise": _COMIC_TEXT,
    "synopsis": _COMIC_TEXT,
    "beats": array(obj({
        "id": _COMIC_TEXT,
        "summary": _COMIC_TEXT,
        "purpose": _COMIC_TEXT,
    })),
})
SCRIPT_SCHEMA = obj({
    "scenes": array(obj({
        "id": _COMIC_TEXT,
        "beat_id": _COMIC_TEXT,
        "location": _COMIC_TEXT,
        "time": _COMIC_TEXT,
        "action": _COMIC_TEXT,
        "dialogue": array(_DIALOGUE_SCHEMA),
        "entity_ids": _COMIC_STRINGS,
    })),
})
STORYBOARD_SCHEMA = obj({
    "panels": array(obj({
        "id": _COMIC_TEXT,
        "scene_id": _COMIC_TEXT,
        "position": {"type": "integer"},
        "shot": _COMIC_TEXT,
        "visual": _COMIC_TEXT,
        "entity_ids": _COMIC_STRINGS,
        "dialogue": array(_DIALOGUE_SCHEMA),
        "caption": _COMIC_TEXT,
    })),
})
CONTINUITY_SCHEMA = obj({
    "status": {"type": "string", "enum": ["pass", "needs_changes"]},
    "issues": array(obj({
        "id": _COMIC_TEXT,
        "severity": {"type": "string", "enum": ["blocking", "major", "minor"]},
        "scope": _COMIC_TEXT,
        "description": _COMIC_TEXT,
        "resolution": _COMIC_TEXT,
    })),
    "approved_panel_ids": _COMIC_STRINGS,
})

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


def _json_value(value):
    """Explicitně převede interní neměnné sekvence komiksu na JSON arrays."""
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    return value


def canonical(value):
    return canonical_bytes(_json_value(value)).decode("utf-8")


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



def validate_story(value):
    from jsonschema import validate

    validate(value, STORY_SCHEMA)
    for key in ("title", "premise", "synopsis"):
        checked_text(value[key], key, 30000, True)
    beats = value["beats"]
    if not beats:
        raise ComicError("invalid_output", "Příběh musí obsahovat alespoň jeden beat.")
    ids = [row["id"] for row in beats]
    if any(not item.strip() for item in ids) or len(ids) != len(set(ids)):
        raise ComicError("invalid_output", "Story beat ID musí být jedinečná a neprázdná.")
    for row in beats:
        checked_text(row["summary"], "Story beat", 12000, True)
        checked_text(row["purpose"], "Účel beatu", 4000, True)
    return copy.deepcopy(value)


def validate_script(value, story):
    from jsonschema import validate

    validate(value, SCRIPT_SCHEMA)
    beat_ids = {row["id"] for row in story["beats"]}
    scenes = value["scenes"]
    if not scenes:
        raise ComicError("invalid_output", "Scénář musí obsahovat alespoň jednu scénu.")
    scene_ids = [row["id"] for row in scenes]
    if any(not item.strip() for item in scene_ids) or len(scene_ids) != len(set(scene_ids)):
        raise ComicError("invalid_output", "Scene ID musí být jedinečná a neprázdná.")
    used_beats = set()
    for scene in scenes:
        if scene["beat_id"] not in beat_ids:
            raise ComicError("invalid_output", "Scéna odkazuje na neznámý story beat.")
        used_beats.add(scene["beat_id"])
        for key in ("location", "time", "action"):
            checked_text(scene[key], f"Scéna {key}", 20000, True)
        for line in scene["dialogue"]:
            checked_text(line["speaker"], "Mluvčí", 200, True)
            checked_text(line["text"], "Dialog", 5000, True)
    if used_beats != beat_ids:
        raise ComicError(
            "invalid_output",
            "Scénář musí pokrýt všechny story beaty alespoň jednou.",
        )
    return copy.deepcopy(value)


def validate_storyboard(value, script, entity_ids):
    from jsonschema import validate

    validate(value, STORYBOARD_SCHEMA)
    scene_ids = {row["id"] for row in script["scenes"]}
    panels = value["panels"]
    if not panels:
        raise ComicError("invalid_output", "Storyboard musí obsahovat alespoň jeden panel.")
    ids = [row["id"] for row in panels]
    if any(not item.strip() for item in ids) or len(ids) != len(set(ids)):
        raise ComicError("invalid_output", "Storyboard panel ID musí být jedinečná a neprázdná.")
    positions = sorted(row["position"] for row in panels)
    if positions != list(range(1, len(panels) + 1)):
        raise ComicError(
            "invalid_output",
            "Storyboard pozice musí tvořit souvislou řadu od 1.",
        )
    used_scenes = set()
    known_entities = set(entity_ids)
    for panel in panels:
        if panel["scene_id"] not in scene_ids:
            raise ComicError("invalid_output", "Storyboard panel odkazuje na neznámou scénu.")
        used_scenes.add(panel["scene_id"])
        if not set(panel["entity_ids"]) <= known_entities:
            raise ComicError("invalid_output", "Storyboard panel odkazuje na neznámou entitu.")
        checked_text(panel["shot"], "Typ záběru", 2000, True)
        checked_text(panel["visual"], "Vizuální obsah panelu", 12000, True)
        checked_text(panel["caption"], "Titulek", 5000)
        for line in panel["dialogue"]:
            checked_text(line["speaker"], "Mluvčí", 200, True)
            checked_text(line["text"], "Dialog", 5000, True)
    if used_scenes != scene_ids:
        raise ComicError(
            "invalid_output",
            "Storyboard musí pokrýt všechny scénáře alespoň jedním panelem.",
        )
    return copy.deepcopy(value)


def validate_continuity(value, storyboard):
    from jsonschema import validate

    validate(value, CONTINUITY_SCHEMA)
    panel_ids = {row["id"] for row in storyboard["panels"]}
    if not set(value["approved_panel_ids"]) <= panel_ids:
        raise ComicError("invalid_output", "Continuity odkazuje na neznámý storyboard panel.")
    blocking = [row for row in value["issues"] if row["severity"] == "blocking"]
    if value["status"] == "pass":
        if blocking or set(value["approved_panel_ids"]) != panel_ids:
            raise ComicError(
                "invalid_output",
                "Continuity PASS vyžaduje všechny panely schválené a bez blocking nálezu.",
            )
    elif not value["issues"]:
        raise ComicError(
            "invalid_output",
            "Continuity needs_changes musí obsahovat alespoň jeden konkrétní nález.",
        )
    for issue in value["issues"]:
        checked_text(issue["id"], "Continuity issue ID", 200, True)
        checked_text(issue["scope"], "Continuity scope", 1000, True)
        checked_text(issue["description"], "Continuity popis", 8000, True)
        checked_text(issue["resolution"], "Continuity oprava", 8000, True)
    return copy.deepcopy(value)
