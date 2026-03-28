# 11 Test report

## Spuštěné příkazy

- `QT_QPA_PLATFORM=offscreen pytest -q`
- `python /home/oai/skills/kajovospendng-zip-in-out/scripts/run_repo_checks.py . --continue`

## Výsledek

- `pytest`: PASS, `36 passed`
- `run_repo_checks.py`: PASS, detekován a spuštěn `pytest`

## Nové důkazní testy

- `tests/test_openai_client_truth.py`
- `tests/test_document_loader_truth.py`
- `tests/test_logging_schema.py`
- `tests/test_progress_dialog.py`
- `tests/test_runtime_workflow_smoke.py`
- `tests/test_encoding_regressions.py`

## Co testy pokrývají

- oficiální OpenAI endpointy a audit metadata,
- validaci OpenAI JSON výstupu,
- reálné načtení textového souboru,
- reálné OCR nad obrázkem,
- strukturované log schema,
- progress dialog s ETA a detailem,
- working → production persistence workflow,
- UTF-8 a diakritiku.
