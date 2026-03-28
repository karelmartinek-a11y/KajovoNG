# 15 Encoding fixes

## Provedené opravy

- `README.md` byl přepsán do konzistentního UTF-8 s českou diakritikou.
- nové dokumentační soubory byly zapsány jako UTF-8.
- byl přidán regresní test, který hlídá známé mojibake vzory.
- `DocumentLoader` používá při textovém fallbacku `encoding='utf-8', errors='replace'`.
- logovací formatter serializuje JSON s `ensure_ascii=False`.

## Ověření

- `tests/test_encoding_regressions.py`
- stávající gate test `tests/test_kdgs_gate.py`
