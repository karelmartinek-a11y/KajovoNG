# 17 OCR truth proof

## Co bylo opraveno

`kajovospend/ocr/document_loader.py` teď vrací provenance skutečného souboru:
- absolutní cestu,
- SHA-256,
- velikost v bytech,
- použitou větev loaderu.

OCR pipeline:
- PDF bez textové vrstvy rasterizuje přes `pypdfium2`,
- obrázky a vyrenderované stránky zpracuje Tesseract,
- OCR používá `ces+eng`, `--oem 1`, `--psm 6`,
- vrací i průměrnou confidence, pokud je k dispozici.

## Jak je ověřeno, že se bere skutečný obsah souboru

- `tests/test_document_loader_truth.py` zapisuje reálný dočasný soubor a ověřuje jeho provenance,
- druhý test generuje skutečný obrázek, uloží ho na disk a ověří, že OCR vrátí text z tohoto souboru.
