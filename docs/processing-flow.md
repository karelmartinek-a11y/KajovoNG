# Tok dokumentu

## 1. Import a provenance

Import začíná reálným souborem z disku. `ProjectRepository.import_file()` uloží interní kopii do projektu, spočítá SHA-256, typ souboru, velikost, počet stran a založí korelační ID.

`DocumentLoader.load()` nyní zároveň vrací provenance:
- `source_path`
- `source_sha256`
- `source_bytes`
- `loader_branch`

To je zdroj pravdy pro pozdější audit, že se zpracovával skutečný soubor.

## 2. Načtení a OCR

- `.txt` a textové fallbacky se čtou v UTF-8 s `errors="replace"`.
- `.pdf` se nejprve čte přes `pypdf`. Pokud stránka nemá textovou vrstvu, provede se rasterizace přes `pypdfium2` a OCR přes Tesseract.
- obrázky se posílají přímo do OCR.

OCR používá `ces+eng`, `--oem 1`, `--psm 6` a vrací i průměrnou confidence, pokud ji Tesseract poskytne.

## 3. Offline extrakce a OpenAI

`ProcessingService` nejprve zkouší offline extrakci kandidátů. Pokud workflow dovolí OpenAI větev a je uložený API klíč, `OpenAIClient` posílá request na oficiální endpoint `https://api.openai.com/v1/responses`.

Audit OpenAI volání obsahuje:
- endpoint,
- model,
- request fingerprint,
- response_id,
- duration,
- status code,
- validation status,
- source SHA-256 a vybrané stránky.

## 4. Working DB a promotion

Vytěžená data se ukládají do working DB tabulek `processing_documents`, `processing_suppliers`, `processing_items` a auditních tabulek. Teprve když je dokument úplný a supplier je ověřený, `finalize_result(..., final)` provede promotion do production DB.

## 5. Logging a progress

Každý důležitý krok zapisuje auditní stopu do `logs/decisions.jsonl` a do DB tabulek `operational_log`, `errors`, `document_results`, `promotion_audit`.

Dlouhé úlohy běží přes background worker a UI ukazuje `TaskProgressDialog` s názvem operace, krokem, detailem, procenty a ETA.
