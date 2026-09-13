# Offline regrese kontextu ID92

Archiv SHA-256 `788270ac6cf5cf1f1efc5dfc00cce18396b3ee11b49988d8ae49672b6e3e0e8b`: ověřeno všech 258 souborů.
Zadání má 1,215,653 znaků. Struktura má 300 souborů; přesný lokálně dodaný SSOT se negeneruje. Vyhodnoceno všech 299 archivních souborových úloh.

**Bezpečně připravené FileContextV1: 0. Blokující nedostatečnost přípravy: 299 souborů.**
Finální A2Q neobsahuje přesné verzované implementační kontrakty ani rozhodnutí působnosti globálních povinností. Jejich domyšlení by nebylo deterministické zachování kvality. Archiv se neopravuje a celý SSOT se nepoužívá jako fallback.

| Veličina | Hodnota |
|---|---:|
| Archivní součet input tokenů 299 úloh | 112,887,221 |
| Součet lokálně odhadnutých kandidátů | 14,034,472 |
| Orientační redukce reprezentace | 87.57 % |
| Největší kandidát | 48,709 |
| Průměr | 46938.0 |
| Medián | 46907.0 |
| p90 / p95 / p99 | 47366.0 / 47573.7 / 47978.8 |
| Nad 100k / 150k / 200k | 0 / 0 / 0 |
| Nad doloženým cenovým prahem | 0 kandidátů; u Luny je práh 272k |

Legacy: archivní API input_tokens včetně přílohy. Kandidát: lokální odhad UTF-8 bajty / 3; bez API přílohy, instrukcí a schema. Nejde o ekvivalentní přesná měření.

**Tato čísla nejsou prokázanou úsporou při stejné kvalitě.** Kandidáti obsahují všechny nezacílené globální povinnosti; žádná z nich nebyla kvůli velikosti odstraněna. Individuální blokace a měření jsou ve [strojovém reportu](ID92_CONTEXT_OPTIMIZATION_REPORT.json). Kvalitativně ekvivalentní úspora zůstává neprokázaná, dokud příprava nedodá chybějící kontrakty a implementace neprojde integrací.

Přímé počítání výsledků potvrzuje 228 chyb mini `context_length_exceeded`, 38 dokončených odpovědí Luny a 33 `incomplete/max_output_tokens`. Hotová Response ani Batch nedokládá integraci projektu.

Reprodukce: `.venv/Scripts/python.exe scripts/analyze_id92_context.py`. Skript používá nový dočasný adresář, ověřuje SHA-256 archivu i obsahů, neposílá API volání a nevkládá původní zadání ani odpovědi do dokumentace.
