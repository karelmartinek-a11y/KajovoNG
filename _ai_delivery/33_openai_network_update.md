# 33 OpenAI network update

## Co bylo doplněno

- OpenAI klient nyní posílá do Responses API odděleně `developer` instrukci a `user` obsah se strukturovaným zdrojovým payloadem.
- Zdrojový payload nese:
  - jméno souboru,
  - SHA-256 vstupu,
  - cestu ke zdroji,
  - počet stran,
  - vybrané stránky pro image vstupy.
- Audit metadata nově zahrnují `response_id`.
- Parser výstupu zvládá jak `output_text`, tak vnořené tvary z `output[*].content[*].text.value`.
- Processing vrstva nyní loguje `response_id`, `selected_page_numbers` a při chybě i `validation_errors`.
- Segmentace dokumentu už neztrácí provenance při dělení více-stránkového vstupu.

## Důkazní testy

- `tests/test_openai_client_truth.py`
- `tests/test_openai_pipeline_source_flow.py`

## Ověření

- `python -m pytest tests/test_openai_client_truth.py tests/test_openai_pipeline_source_flow.py -q` → `5 passed`
- non-GUI suite včetně nových OpenAI testů → `27 passed`
- GUI dílčí suite `tests/test_kdgs_widgets.py tests/test_progress_dialog.py` → `3 passed`

## Poznámka k oficiální dokumentaci

Aktualizace míří na oficiální Responses API endpoint `POST https://api.openai.com/v1/responses`, který podle OpenAI API reference slouží k vytvoření modelové odpovědi a podporuje textové i obrazové vstupy.
