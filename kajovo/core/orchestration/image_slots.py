"""Fyzické obrazové sloty vznikají až po deduplikaci všech logických rolí."""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from importlib.resources import files
from types import MappingProxyType
from typing import Literal

from .contracts import canonical_sha256, parse_json_strict
from .errors import OrchestrationError

_HASH = re.compile(r"[0-9a-f]{64}\Z")
_ORDER = {"edit_base": 0, "identity_reference": 1, "style_reference": 2}


def image_policy() -> dict:
    """Každý volající dostane vlastní kopii nezměněné normativní politiky."""
    return parse_json_strict(files(__package__).joinpath("policies/images.json").read_text("utf-8"))


def normalization_policy_hash() -> str:
    """Otisk pouze skutečně aplikované transformace, ne nesouvisejícího review."""
    return canonical_sha256({
        "version": 1,
        "exif_normalization": image_policy()["exif_normalization"],
        "color": "RGB_or_RGBA_preserving_transparency",
        "output_format": "PNG",
        "metadata": "strip",
    })


@dataclass(frozen=True)
class ImageRole:
    role_id: str
    asset_id: str
    kind: Literal["edit_base", "identity_reference", "style_reference"]
    sort_id: str


@dataclass(frozen=True)
class FrozenImageAsset:
    asset_id: str
    storage_id: str
    normalized_sha256: str
    transform_policy_hash: str


@dataclass(frozen=True)
class FrozenAssetIndex:
    assets: tuple[FrozenImageAsset, ...]
    max_references: int


@dataclass(frozen=True)
class ReferenceSlot:
    index: int
    slot_id: str
    asset_id: str
    normalized_sha256: str
    transform_policy_hash: str


@dataclass(frozen=True)
class RoleSlot:
    role_id: str
    slot_id: str
    index: int


@dataclass(frozen=True)
class ReferenceSlots:
    slots: tuple[ReferenceSlot, ...]
    roles: tuple[RoleSlot, ...]

    @property
    def role_to_index(self) -> Mapping[str, int]:
        return MappingProxyType({role.role_id: role.index for role in self.roles})


def compile_slots(roles: Sequence[ImageRole], assets: FrozenAssetIndex) -> ReferenceSlots:
    """Integrace reference.compile_image_slots s deterministickým pořadím a limitem modelu."""
    maximum = assets.max_references
    if type(maximum) is not int or maximum < 0:
        raise OrchestrationError("IMAGE_REFERENCE_LIMIT", "Neplatný limit modelu.")
    maximum = min(maximum, image_policy()["max_references_policy"])
    by_id: dict[str, FrozenImageAsset] = {}
    for asset in assets.assets:
        if not asset.asset_id or not asset.storage_id or asset.asset_id in by_id:
            raise OrchestrationError("REFERENCE_UNKNOWN", "Nejednoznačná identita assetu.")
        if not _HASH.fullmatch(asset.normalized_sha256) or not _HASH.fullmatch(asset.transform_policy_hash):
            raise OrchestrationError("ARTIFACT_HASH_MISMATCH", "Neplatný otisk assetu.")
        by_id[asset.asset_id] = asset
    identifiers: set[str] = set()
    edit_count = 0
    for role in roles:
        if not role.role_id or role.role_id in identifiers:
            raise OrchestrationError("ROLE_DUPLICATE", "Role musí mít jedinečné ID.")
        if role.kind not in _ORDER or not role.sort_id:
            raise OrchestrationError("REFERENCE_UNKNOWN", "Neznámá obrazová role.")
        if role.asset_id not in by_id:
            raise OrchestrationError("REFERENCE_UNKNOWN", "Role nemá zmrazený asset.")
        identifiers.add(role.role_id)
        edit_count += role.kind == "edit_base"
    if edit_count > 1:
        raise OrchestrationError("ROLE_DUPLICATE", "Editace smí mít pouze jeden základ.")
    ordered = sorted(roles, key=lambda role: (_ORDER[role.kind], role.sort_id, role.role_id))
    by_key: dict[tuple[str, str], ReferenceSlot] = {}
    slots: list[ReferenceSlot] = []
    mapping: list[RoleSlot] = []
    for role in ordered:
        asset = by_id[role.asset_id]
        key = (asset.normalized_sha256, asset.transform_policy_hash)
        if key not in by_key:
            index = len(slots) + 1
            slot = ReferenceSlot(index, f"IMAGE-{index:03d}", asset.storage_id, *key)
            by_key[key] = slot
            slots.append(slot)
        slot = by_key[key]
        mapping.append(RoleSlot(role.role_id, slot.slot_id, slot.index))
    if len(slots) > maximum:
        raise OrchestrationError(
            "IMAGE_REFERENCE_LIMIT", f"Je nutných {len(slots)} referencí, model dovoluje {maximum}."
        )
    return ReferenceSlots(tuple(slots), tuple(mapping))
