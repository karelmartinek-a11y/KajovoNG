from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_svg_assets_follow_kdgs_rules() -> None:
    svg_paths = list((REPO_ROOT / 'brand').rglob('*.svg')) + list((REPO_ROOT / 'kajovospend' / 'branding').rglob('*.svg')) + list((REPO_ROOT / 'signace').rglob('*.svg'))
    assert svg_paths
    for path in svg_paths:
        text = path.read_text(encoding='utf-8')
        lower = text.lower()
        assert '<text' not in lower, path.as_posix()
        assert '<lineargradient' not in lower, path.as_posix()
        assert '<radialgradient' not in lower, path.as_posix()
        assert '<filter' not in lower and 'filter=' not in lower, path.as_posix()
        assert not re.search(r'opacity=\"0(\\.\\d+)?\"', lower), path.as_posix()
        assert not re.search(r'fill-opacity=\"0(\\.\\d+)?\"', lower), path.as_posix()
        if path.name != 'app_icon.svg':
            assert 'stroke=' not in lower, path.as_posix()
        for value in re.findall(r'#[0-9a-f]{6}', lower):
            assert len(value) == 7, path.as_posix()


def test_required_brand_exports_exist() -> None:
    slug = 'kajovo-spend'
    for variant in ['full', 'mark', 'wordmark', 'signace']:
        assert (REPO_ROOT / 'brand' / 'logo' / 'exports' / variant / 'svg' / f'{slug}_{variant}.svg').exists()
        assert (REPO_ROOT / 'brand' / 'logo' / 'exports' / variant / 'pdf' / f'{slug}_{variant}.pdf').exists()
        for size in ['64', '128', '256', '512', '1024', '2048']:
            assert (REPO_ROOT / 'brand' / 'logo' / 'exports' / variant / 'png' / f'{slug}_{variant}_{size}.png').exists()


def test_kdgs_documents_exist() -> None:
    for rel in [
        'docs/kdgs_auditni_matice.md',
        'docs/kdgs_implementacni_standard.md',
        'docs/kdgs_release_checklist.md',
        'docs/kdgs_finalni_forenzni_report.md',
        ]:
        assert (REPO_ROOT / rel).exists(), rel


def test_palette_registry_documents_secondary_ui_tokens() -> None:
    palette_path = REPO_ROOT / 'brand' / 'palette' / 'palette.json'
    payload = __import__('json').loads(palette_path.read_text(encoding='utf-8'))
    assert payload['version'] >= 2
    assert payload['primary']['red'] == '#FF0000'
    assert payload['neutral']['metal'] == '#737578'
    assert payload['state']['error'] == '#B71C1C'
    assert 'purpose' in payload['productSecondary']
    for key in ['surface800', 'surface700', 'line500', 'line700', 'selectionBg', 'successSurface', 'warningSurface', 'errorSurface', 'infoSurface']:
        assert key in payload['productSecondary'], key


def test_brand_metadata_follow_required_schema() -> None:
    payload = __import__('json').loads((REPO_ROOT / 'brand' / 'brand.json').read_text(encoding='utf-8'))
    for key in ['appSlug', 'appName', 'wordmarkLine2', 'usesLegacyOutlinePackV1', 'lockupH', 'gapG1', 'gapG2', 'safeZone', 'signaceViewBox']:
        assert key in payload, key
    assert payload['appSlug'] == 'kajovo-spend'
    assert payload['lockupH'] == 202.0
    assert payload['gapG1'] == 10.0
    assert payload['gapG2'] == 30.0
    assert payload['signaceViewBox'] == '0 0 59 202'


def test_repo_text_integrity_and_forbidden_ui_apis() -> None:
    text_extensions = {'.py', '.md', '.txt', '.json', '.svg', '.toml'}
    forbidden = [
        chr(0x00C3),
        chr(0x0102),
        chr(0x0139),
        chr(0x00E2) + chr(0x20AC),
        chr(0x00E2) + chr(0x20AC) + chr(0x201D),
        'QMessage' + 'Box',
    ]
    for root in [REPO_ROOT / 'kajovospend', REPO_ROOT / 'tests', REPO_ROOT / 'docs']:
        for path in root.rglob('*'):
            if path.is_file() and path.suffix.lower() in text_extensions:
                text = path.read_text(encoding='utf-8')
                for token in forbidden:
                    assert token not in text, path.as_posix()
