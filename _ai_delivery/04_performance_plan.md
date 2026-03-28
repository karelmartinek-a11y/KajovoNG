# 04_performance_plan

## Quick wins

1. Přesunout `on_load_openai_models()` do `_run_background_task()`.
2. Rozšířit progress reporting na fáze: load file -> OCR -> candidate extraction -> OpenAI -> ARES -> DB finalize.
3. Přidat timing metriky pro každou fázi do logů.
4. Omezit redundantní serializaci OpenAI kontextu a zvážit limitaci payloadu podle relevance.
5. Profilovat počet SQLite connect/commit operací na dokument a sloučit části zápisů do větších transakcí tam, kde to nenaruší audit stopu.

## Hlubší zásahy

1. Zvážit cache/reuse rasterovaných PDF stránek mezi OCR a OpenAI image input branch.
2. Rozdělit `processing/service.py` na menší orchestration kroky se samostatnými timers.
3. Rozdělit `finalize_result()` na menší, testovatelnější transaction služby.
4. Zavést explicitní performance benchmark pro:
   - text PDF,
   - scan PDF,
   - image,
   - OpenAI branch,
   - promotion.

## Co přesně měřit

- `document_loader.load()` duration
- OCR per page duration
- OpenAI request duration
- ARES request duration
- DB write duration per stage
- total per file / per document duration
- selected pages count / image payload count
- retry counts

## Očekávané přínosy

- nižší pocit „sekání“ díky lepšímu background behavior a progress semantice,
- rychlejší troubleshooting díky časovým metrikám,
- identifikace skutečných bottlenecků místo odhadů.
