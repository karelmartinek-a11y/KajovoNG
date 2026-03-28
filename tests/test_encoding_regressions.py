from __future__ import annotations

from pathlib import Path


def test_readme_contains_utf8_diacritics_without_mojibake() -> None:
    text = Path('README.md').read_text(encoding='utf-8')
    assert 'Desktop aplikace' in text
    assert 'fyzicky oddělenými' in text
    forbidden = [chr(0x00C3), chr(0x00C4), chr(0x00C5), chr(0x00E2) + chr(0x20AC), 'Aplikace pro zpracovani']
    for token in forbidden:
        assert token not in text
