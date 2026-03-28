from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from kajovospend.ocr.document_loader import DocumentLoader


def test_text_loader_tracks_real_file_provenance(tmp_path: Path) -> None:
    path = tmp_path / 'sample.txt'
    path.write_text('Dodavatel\nIČO 12345678\nČíslo dokladu FA-2025-001\nDatum 2025-01-10\nCelkem 1210 Kč', encoding='utf-8')
    loaded = DocumentLoader().load(path)
    assert loaded.source_path == str(path.resolve())
    assert loaded.source_sha256
    assert loaded.source_bytes == path.stat().st_size
    assert loaded.pages[0].text.startswith('Dodavatel')
    assert loaded.provenance['source_suffix'] == '.txt'


def test_image_loader_runs_real_tesseract_on_file(tmp_path: Path) -> None:
    path = tmp_path / 'ocr.png'
    image = Image.new('RGB', (900, 200), 'white')
    draw = ImageDraw.Draw(image)
    draw.text((20, 60), 'FA 2025 001 12345678 CELKEM 1210', fill='black')
    image.save(path)
    loaded = DocumentLoader().load(path)
    assert loaded.file_type == 'image'
    assert loaded.source_sha256
    assert loaded.pages[0].metadata['source_branch'] == 'image_ocr'
    assert any(token in loaded.pages[0].text for token in ['12345678', '1210', '2025'])
