# 05_logging_forensics_plan

## Požadované minimální pole pro každý pipeline event

- `timestamp`
- `level`
- `event_name`
- `correlation_id`
- `project_path`
- `file_id`
- `document_id`
- `attempt_id`
- `stage`
- `substage`
- `status`
- `duration_ms`
- `source_file_path`
- `source_file_sha256`
- `page_count`
- `selected_page_numbers`
- `processor_name`
- `model`
- `endpoint`
- `ocr_engine`
- `db_target` (`working` / `production`)
- `result_code`
- `message`
- `payload_fingerprint`

## Doporučené eventy

1. `import.file_discovered`
2. `document.load.started`
3. `document.load.finished`
4. `ocr.page.started`
5. `ocr.page.finished`
6. `ocr.page.failed`
7. `candidate.extraction.finished`
8. `openai.request.started`
9. `openai.request.finished`
10. `openai.request.failed`
11. `ares.validation.started`
12. `ares.validation.finished`
13. `db.working.persisted`
14. `db.production.promoted`
15. `document.finalized`
16. `batch.cancel_requested`
17. `batch.cancel_completed`

## Aktuální deficit proti plánu

- app logger a project logger nejsou sjednocené,
- chybí duration metrics,
- chybí explicitní stage/substage schema,
- chybí provenance/fingerprint vstupu,
- chybí OpenAI/OCR specific metadata,
- chybí dokumentovaný log contract.
