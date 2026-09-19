"""CHANGE COM-03: stabilni logicke role a fyzicke indexy po deduplikaci."""
from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from kajovo.core.orchestration.errors import OrchestrationError
from kajovo.core.orchestration.image_slots import (
    FrozenAssetIndex, FrozenImageAsset, ImageRole, compile_slots, image_policy,
)


def asset(name, digest='a', transform='c'):
    return FrozenImageAsset(name, 'stored-' + name, digest * 64, transform * 64)


def test_edit_entity_style_share_physical_reference_without_role_loss(record_property):
    inputs = (
        asset('base'), asset('a'), asset('b', 'b'), asset('style'),
    )
    roles = [
        ImageRole('STYLE', 'style', 'style_reference', 'style'),
        ImageRole('ENTITY_B', 'b', 'identity_reference', 'b'),
        ImageRole('ENTITY_A', 'a', 'identity_reference', 'a'),
        ImageRole('BASE', 'base', 'edit_base', 'base'),
    ]
    result = compile_slots(roles, FrozenAssetIndex(inputs, 16))
    assert len(result.slots) == 2
    assert dict(result.role_to_index) == {'BASE': 1, 'ENTITY_A': 1, 'ENTITY_B': 2, 'STYLE': 1}
    assert [slot.slot_id for slot in result.slots] == ['IMAGE-001', 'IMAGE-002']
    assert [slot.asset_id for slot in result.slots] == ['stored-base', 'stored-b']
    assert len(result.roles) == len(roles)
    assert compile_slots(list(reversed(roles)), FrozenAssetIndex(inputs, 16)) == result
    with pytest.raises(TypeError):
        result.role_to_index['BASE'] = 3
    with pytest.raises(FrozenInstanceError):
        result.slots[0].index = 2
    record_property('observed_transport_call_count', 0)


def test_transform_policy_is_part_of_physical_identity():
    assets = FrozenAssetIndex((asset('a'), asset('b', transform='d')), 16)
    roles = [ImageRole('A', 'a', 'identity_reference', 'a'), ImageRole('B', 'b', 'identity_reference', 'b')]
    assert len(compile_slots(roles, assets).slots) == 2


def test_limit_counts_unique_images_not_roles():
    roles = [ImageRole('R' + str(i), 'a', 'identity_reference', str(i)) for i in range(40)]
    assert len(compile_slots(roles, FrozenAssetIndex((asset('a'),), 1)).slots) == 1
    with pytest.raises(OrchestrationError, match='IMAGE_REFERENCE_LIMIT'):
        compile_slots(roles, FrozenAssetIndex((asset('a'),), 0))


@pytest.mark.parametrize('limit', [-1, True, 1.5])
def test_invalid_capability_does_not_enable_references(limit):
    with pytest.raises(OrchestrationError, match='IMAGE_REFERENCE_LIMIT'):
        compile_slots([], FrozenAssetIndex((), limit))


def test_policy_caps_more_permissive_model():
    assets = tuple(FrozenImageAsset(str(i), str(i), f'{i:064x}', 'c' * 64) for i in range(17))
    roles = [ImageRole(str(i), str(i), 'identity_reference', str(i)) for i in range(17)]
    with pytest.raises(OrchestrationError, match='IMAGE_REFERENCE_LIMIT'):
        compile_slots(roles, FrozenAssetIndex(assets, 100))


@pytest.mark.parametrize('roles,assets,code', [
    ([ImageRole('A', 'missing', 'identity_reference', 'a')], (asset('a'),), 'REFERENCE_UNKNOWN'),
    ([ImageRole('A', 'a', 'wrong', 'a')], (asset('a'),), 'REFERENCE_UNKNOWN'),
    ([ImageRole('A', 'a', 'identity_reference', '')], (asset('a'),), 'REFERENCE_UNKNOWN'),
    ([ImageRole('A', 'a', 'identity_reference', 'a')] * 2, (asset('a'),), 'ROLE_DUPLICATE'),
    ([ImageRole('A', 'a', 'edit_base', 'a'), ImageRole('B', 'a', 'edit_base', 'b')], (asset('a'),), 'ROLE_DUPLICATE'),
    ([], (asset('a'), asset('a')), 'REFERENCE_UNKNOWN'),
    ([], (FrozenImageAsset('a', 'a', 'bad', 'c' * 64),), 'ARTIFACT_HASH_MISMATCH'),
])
def test_invalid_reference_graph_never_silently_drops_role(roles, assets, code):
    with pytest.raises(OrchestrationError, match=code):
        compile_slots(roles, FrozenAssetIndex(assets, 16))


def test_policy_copy_cannot_change_subsequent_calls():
    first = image_policy()
    first['max_references_policy'] = 0
    assert image_policy()['max_references_policy'] == 16
    assert compile_slots([], FrozenAssetIndex((), 0)).roles == ()
