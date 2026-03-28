# 07_documentation_gap_report

## Co je v dokumentaci zastaralé / neúplné / chybné

1. **README nepopisuje AI extraction flow.**
   - Chybí popis `openai_client`, `processing/service`, validace výsledku a fallback větví.

2. **README nepopisuje OCR flow.**
   - Chybí text layer vs raster OCR, Tesseract branch, image branch, warning states.

3. **README ani docs nepokrývají forenzní logging pipeline.**
   - Není popsán vztah `app log dir` vs `project logs/runtime.log` vs `decisions.jsonl` vs `operational_log`.

4. **README ani docs nepokrývají progress reporting a storno model.**
   - Není popsáno, co znamená progress bar a kdy běží background thread.

5. **KDGS docs jsou zaměřené na UI/brand governance, ne na core processing důkaznost.**
   - To samo o sobě není chyba, ale vůči uživatelskému cíli „forenzní pravda“ jde o zásadní mezeru.

6. **Dokumentace nepopsala testovací strategii pro OpenAI/OCR/DB pravdivost.**
   - Chybí integrační a end-to-end důkazní scénáře.

## Doporučené nové/rozšířené dokumenty

- `docs/ai_extraction_flow.md`
- `docs/ocr_flow.md`
- `docs/logging_schema.md`
- `docs/progress_reporting.md`
- `docs/test_strategy.md`
- `docs/troubleshooting_pipeline.md`
