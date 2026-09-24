"""Šablony promptů pro Photo Studio."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import uuid

from .utils import atomic_write_text

SCHEMA_VERSION = 1

HOTEL_BOOKING_PROMPT = """Edit the existing hotel/property photograph into a realistic, professional hospitality presentation image suitable for Booking.com and similar OTA platforms. Preserve factual accuracy and the identity of the real property. Do not invent, add, remove, relocate, or materially redesign furniture, amenities, architecture, views, decorations, people, or other scene content unless the user explicitly asks for that exact change. Improve exposure, white balance, natural dynamic range, clarity, sharpness, and color balance while keeping materials and colors believable. Correct tilted horizons, lens distortion, converging verticals, and perspective where appropriate. Keep lighting bright, warm, natural, and professionally balanced; visible existing lamps may appear switched on when physically plausible. Remove only minor transient distractions such as obvious loose cables or small clutter when this does not misrepresent the property. Keep rooms tidy, beds naturally well presented, mirrors and glass clean, bathrooms spotless, towels neat, floors and carpets clean, and exterior/common-area photographs truthful to the location. Blur recognizable faces and vehicle license plates when present. Avoid excessive HDR, oversaturation, artificial contrast, fake depth of field, stylization, watermarks, collages, or an AI-generated look. Preserve a photorealistic editorial hotel-photography result and do not crop more tightly than necessary."""


@dataclass(frozen=True)
class PhotoPromptTemplate:
    template_id: str
    name: str
    description: str
    category: str
    prompt: str
    builtin: bool
    created_at: str
    updated_at: str


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def builtin_templates() -> list[PhotoPromptTemplate]:
    stamp = "builtin-v1"
    return [
        PhotoPromptTemplate(
            "builtin-hotel-booking",
            "Hotel / Booking – profesionální",
            "Realistická univerzální úprava hotelových a ubytovacích fotografií.",
            "Hotel",
            HOTEL_BOOKING_PROMPT,
            True,
            stamp,
            stamp,
        ),
        PhotoPromptTemplate(
            "builtin-room",
            "Hotelový pokoj",
            "Světlo, perspektiva, čistota a přirozená prezentace pokoje.",
            "Pokoj",
            HOTEL_BOOKING_PROMPT
            + " Pay special attention to a tidy guest room: keep the exact room layout and furniture, correct verticals, balance window and interior light, present existing bedding naturally smooth and freshly made, keep surfaces uncluttered, and preserve the true room proportions.",
            True,
            stamp,
            stamp,
        ),
        PhotoPromptTemplate(
            "builtin-bathroom",
            "Koupelna",
            "Čistá realistická prezentace koupelny bez vymýšlení vybavení.",
            "Koupelna",
            HOTEL_BOOKING_PROMPT
            + " Pay special attention to the bathroom: retain the exact fixtures and layout, correct perspective, keep mirrors and glass clean, tiles and sanitary fixtures spotless, towels neat, and lighting natural and bright. Do not invent toiletries or amenities.",
            True,
            stamp,
            stamp,
        ),
        PhotoPromptTemplate(
            "builtin-exterior",
            "Exteriér",
            "Fasáda, vstup a okolí s věrným světlem a geometrií.",
            "Exteriér",
            HOTEL_BOOKING_PROMPT
            + " For the exterior, preserve the real facade, entrance, surrounding buildings, street context, vegetation, weather cues, and view. Correct geometry and exposure while keeping the scene truthful. Do not create a different sky, landscaping, signs, or architecture unless explicitly requested.",
            True,
            stamp,
            stamp,
        ),
        PhotoPromptTemplate(
            "builtin-light-color",
            "Pouze světlo a barvy",
            "Konzervativní korekce expozice, bílé a barev bez obsahových změn.",
            "Technická úprava",
            "Edit the existing photograph conservatively. Change only photographic rendering: improve exposure, white balance, natural contrast, highlight/shadow balance, color fidelity, noise, clarity, and sharpness. Preserve every object, person, architectural element, crop, perspective, geometry, material, texture, and scene detail. Do not add or remove content. Keep the result photorealistic and natural, without excessive HDR, oversaturation, or stylization.",
            True,
            stamp,
            stamp,
        ),
        PhotoPromptTemplate(
            "builtin-perspective",
            "Perspektiva a geometrie",
            "Srovnání horizontu, svislic a optického zkreslení.",
            "Technická úprava",
            "Correct the existing photograph's horizon, vertical alignment, perspective convergence, and realistic lens distortion while preserving the exact scene content, framing as much as possible, true proportions, colors, lighting, textures, architecture, furniture, and objects. Do not add or remove anything. Keep a natural professional photographic appearance.",
            True,
            stamp,
            stamp,
        ),
    ]


class PhotoTemplateStore:
    """Verzované uživatelské šablony; vestavěné šablony jsou neměnné."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def _load_custom(self) -> list[PhotoPromptTemplate]:
        if not self.path.exists():
            return []
        try:
            from .orchestration.contracts import parse_json_strict
            payload = parse_json_strict(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError(f"Soubor šablon nelze načíst: {exc}") from exc
        if (not isinstance(payload, dict) or set(payload) != {"schema_version", "templates"}
                or type(payload.get("schema_version")) is not int
                or payload["schema_version"] != SCHEMA_VERSION):
            raise ValueError("Soubor šablon má nepodporovanou verzi.")
        rows = payload.get("templates")
        if not isinstance(rows, list):
            raise ValueError("Soubor šablon neobsahuje seznam templates.")
        result = []
        seen = set()
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("Neplatný záznam šablony.")
            required = {"template_id", "name", "description", "category", "prompt", "builtin", "created_at", "updated_at"}
            if set(row) != required:
                raise ValueError("Záznam šablony má neplatná pole.")
            if row["builtin"] is not False or any(
                not isinstance(row[key], str) for key in required - {"builtin"}
            ):
                raise ValueError("Záznam šablony má neplatné typy polí.")
            if row["template_id"].startswith("builtin-"):
                raise ValueError("Identifikátor vestavěné šablony je vyhrazený.")
            for key in ("created_at", "updated_at"):
                try:
                    stamp = datetime.fromisoformat(row[key])
                    if stamp.tzinfo is None:
                        raise ValueError("Chybí časové pásmo.")
                except ValueError as exc:
                    raise ValueError(f"Šablona má neplatný čas {key}.") from exc
            item = PhotoPromptTemplate(**row)
            if item.builtin:
                raise ValueError("Uživatelský soubor nesmí předefinovat vestavěnou šablonu.")
            if not item.template_id or item.template_id in seen or not item.name.strip() or not item.prompt.strip():
                raise ValueError("Šablona má neplatné nebo duplicitní údaje.")
            seen.add(item.template_id)
            result.append(item)
        return result

    def list(self) -> list[PhotoPromptTemplate]:
        return [*builtin_templates(), *self._load_custom()]

    def get(self, template_id: str) -> PhotoPromptTemplate:
        for item in self.list():
            if item.template_id == template_id:
                return item
        raise KeyError(template_id)

    def _write_custom(self, items: list[PhotoPromptTemplate]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"schema_version": SCHEMA_VERSION, "templates": [asdict(item) for item in items]}
        atomic_write_text(str(self.path), json.dumps(payload, ensure_ascii=False, indent=2) + "\n")

    def create(self, name: str, prompt: str, description: str = "", category: str = "Vlastní") -> PhotoPromptTemplate:
        name, prompt = name.strip(), prompt.strip()
        if not name or not prompt:
            raise ValueError("Název a prompt šablony jsou povinné.")
        stamp = _now()
        item = PhotoPromptTemplate(
            "tpl_" + uuid.uuid4().hex,
            name,
            description.strip(),
            category.strip() or "Vlastní",
            prompt,
            False,
            stamp,
            stamp,
        )
        items = self._load_custom()
        items.append(item)
        self._write_custom(items)
        return item

    def update(self, template_id: str, *, name: str, prompt: str, description: str = "", category: str = "Vlastní") -> PhotoPromptTemplate:
        if template_id.startswith("builtin-"):
            raise ValueError("Vestavěnou šablonu nelze přepsat; nejprve ji duplikujte.")
        name, prompt = name.strip(), prompt.strip()
        if not name or not prompt:
            raise ValueError("Název a prompt šablony jsou povinné.")
        items = self._load_custom()
        updated = None
        output = []
        for item in items:
            if item.template_id == template_id:
                updated = PhotoPromptTemplate(
                    item.template_id,
                    name,
                    description.strip(),
                    category.strip() or "Vlastní",
                    prompt,
                    False,
                    item.created_at,
                    _now(),
                )
                output.append(updated)
            else:
                output.append(item)
        if updated is None:
            raise KeyError(template_id)
        self._write_custom(output)
        return updated

    def delete(self, template_id: str) -> None:
        if template_id.startswith("builtin-"):
            raise ValueError("Vestavěnou šablonu nelze smazat.")
        items = self._load_custom()
        output = [item for item in items if item.template_id != template_id]
        if len(output) == len(items):
            raise KeyError(template_id)
        self._write_custom(output)

    def duplicate(self, template_id: str) -> PhotoPromptTemplate:
        source = self.get(template_id)
        return self.create(
            source.name + " – kopie",
            source.prompt,
            source.description,
            source.category,
        )
