# 13 Logging schema

## JSON schema verze 2

Každá strukturovaná událost má minimálně:
- `schema_version`
- `timestamp`
- `level`
- `logger`
- `message`
- `event_name`

Volitelně může nést:
- `correlation_id`
- `request_id`
- `document_id`
- `file_id`
- `attempt_id`
- `endpoint`
- `model`
- `request_fingerprint`
- `duration_ms`
- `status_code`
- `phase`
- `result_code`
- `payload`

## Interpretace přes tok

- `import.created` propojí soubor s `correlation_id`.
- `attempt.started` a `attempt.finished` dávají obrys pipeline.
- `openai.call_completed` a `openai.call_failed` nesou bezpečná metadata AI větve.
- `document.finalized` uzavírá výsledek případu.

## Bezpečnost

Do logů se nezapisuje plný API klíč ani plný citlivý payload requestu.
