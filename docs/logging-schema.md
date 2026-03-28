# Logging schema

Strukturované logování používá JSON řádky se `schema_version = 2`. Totéž platí i pro projektový `logs/decisions.jsonl`.

## Základní pole

- `timestamp`
- `level`
- `logger`
- `message`
- `event_name`

## Forenzní pole podle potřeby

- `correlation_id`
- `request_id`
- `project_id`
- `project_path`
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

## Interpretace

- `correlation_id` propojuje import, extrakci, chyby i promotion stejného případu.
- `request_fingerprint` je bezpečný hash requestu na OpenAI bez citlivého obsahu.
- `payload` nese provozní metadata, ale nesmí obsahovat tajné klíče ani plné citlivé payloady.
- pro rychlou lidskou orientaci zůstává i `runtime.log`.
