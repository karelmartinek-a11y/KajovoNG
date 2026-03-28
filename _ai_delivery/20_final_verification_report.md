# 20 Final verification report

## Verdikt

**READY WITH KNOWN LIMITATIONS**

Projekt po finální verifikaci prochází kompletním test suite a kritické regresní scénáře byly důkazně ověřeny. V této fázi byla navíc opravena jedna reálná technická vada: SQLite spojení v `ProjectRepository` byla dříve používána přes context manager bez explicitního zavření; při coverage běhu to generovalo `ResourceWarning`. Oprava zavedla uzavíratelné context managery pro `work_connection()` a `prod_connection()`.

## Co bylo finálně ověřeno

1. **Kompletní test suite**
   - `QT_QPA_PLATFORM=offscreen pytest -q` → `36 passed`
   - Testy byly znovu spuštěny po opravě connection leak warningu a zůstaly zelené.

2. **Import reálného souboru a persistence**
   - Verifikační smoke běh nad skutečným `doc.txt` skončil stavem `final`.
   - Working DB i production DB existují jako dva fyzicky odlišné soubory.
   - Finální dokument v production DB odpovídá zadanému vstupu (`FA2025001`, `12345678`, `1210.0`).

3. **OCR nad reálným souborem**
   - Byl vytvořen skutečný obrázek na disk a `DocumentLoader.load()` nad ním provedl Tesseract OCR.
   - Výstup obsahuje skutečný text ze souboru, SHA-256 provenance a OCR confidence.

4. **OpenAI větev**
   - Bylo znovu ověřeno, že klient míří na oficiální `https://api.openai.com/v1/responses`.
   - Audit metadata nesou endpoint, model, fingerprint a validační status.
   - V této fázi nebyl proveden živý síťový call proti OpenAI, protože nebyl dodán produkční klíč a kontrolovaný externí sandbox. Kódová a boundary verifikace je ale doložená testy i manuální kontrolou requestu.

5. **Progress UI**
   - `TaskProgressDialog` zobrazuje název operace, krok, detail, procenta a ETA.
   - Ověřeno testem i code review: ETA je heuristika z reálného elapsed času, ne fake timer.

6. **Forenzní logy**
   - Ve smoke běhu vznikl čitelný `logs/decisions.jsonl` s řetězcem `import.created -> attempt.started -> attempt.finished -> document.finalized`.
   - Logy jsou použitelné pro rekonstrukci jednoho případu.

7. **Encoding a textová kontrola**
   - Nebyly nalezeny rozsypané znaky ani invalid UTF-8 decode chyby v auditovaných textových souborech.
   - Repo zůstává UTF-8 clean.

## Co bylo v této finální fázi ještě opraveno

- `kajovospend/persistence/repository.py`
  - `work_connection()` a `prod_connection()` nyní spojení po použití explicitně zavírají.
  - Důvod: coverage běh odhalil `ResourceWarning: unclosed database`.

## Zbylá omezení

- Živý OpenAI request proti produkční službě nebyl v této verifikaci puštěn; důkaz zůstává na úrovni boundary testu, oficiální URL a audit metadata.
- Celkové coverage je přibližně `48 %`; kritické nové cesty jsou ověřené, ale repo jako celek stále nemá plné test coverage.
- Část runtime češtiny zůstává bez diakritiky. Nejde o encoding chybu ani mojibake, ale o stylistickou nekonzistenci textové vrstvy.
