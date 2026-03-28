# 18 DB persistence truth proof

## Co bylo ověřeno

- working DB a production DB jsou dva fyzicky odlišné soubory,
- zpracování reálného textového dokumentu vede k zápisu do working vrstvy a po validaci i do production vrstvy,
- smoke test jde od souboru přes import, offline extrakci, ARES validaci až po finalizaci.

## Důkaz

- `tests/test_runtime_workflow_smoke.py`
- po běhu testu existují oba soubory `work.sqlite3` a `production.sqlite3`,
- dokument končí ve stavu `final`,
- v production vrstvě existuje finální dokument,
- `get_processing_document_detail()` potvrzuje správný document number a IČO.
