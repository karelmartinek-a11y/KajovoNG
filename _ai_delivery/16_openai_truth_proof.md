# 16 OpenAI truth proof

## Co bylo opraveno

`kajovospend/integrations/openai_client.py` nyní explicitně pracuje s:
- `https://api.openai.com/v1/models`
- `https://api.openai.com/v1/responses`

## Jak je ověřeno, že nejde o simulaci

- request se sestavuje do skutečného HTTP POST na oficiální endpoint,
- test `tests/test_openai_client_truth.py` kontroluje URL, hlavičky a payload modelu,
- klient generuje audit metadata: endpoint, model, request fingerprint, duration, status code,
- při chybě sítě nebo při nevalidním JSON klient vrací explicitní chybu místo tichého fallbacku.

## Co se loguje bezpečně

- endpoint,
- model,
- request fingerprint,
- status code,
- duration,
- výsledek validace.

## Co se neloguje

- API klíč,
- plný citlivý payload requestu.
