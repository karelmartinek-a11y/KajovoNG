# KájovoHotel Security

## Threat model
Assets: API key, prompts/responses, generated files, logs and receipts DB.
Entrypoints: Desktop UI inputs (paths/payloads), OpenAI API responses, local filesystem.
Trust boundaries: user-provided paths/content vs application output roots.

## Security guidelines
- Never hard-code secrets; use environment/keyring.
- Keep timeouts on all outbound HTTP calls.
- Reject path traversal when writing files from model output.
- Avoid logging secret values and full bearer tokens.

## Dependency policy
- Prefer stdlib where possible.
- Keep runtime deps reviewed and pinned by CI checks before release.
