# 06_progress_ui_plan

## Místa, kde má být progress dialog / detailní čekací stav

1. Načtení OpenAI modelů
2. Import dávky souborů
3. Zpracování čekajících pokusů
4. Export CSV/XLSX
5. Obnova diagnostik / případné snapshot operace
6. Dlouhé OCR/AI retry akce z detailu dokumentu

## Minimální obsah dialogu

- Název operace
- Hlavní věta „co se právě děje“
- Fáze pipeline (např. `2/6 OCR strany 3/5`)
- Procento
- ETA
- Elapsed time
- Detailní message (např. název souboru / číslo strany)
- Bezpečné storno

## Návrh progress modelu

```text
Batch progress:
- files_completed / files_total
- current_file_name
- current_phase

Document progress:
- page_completed / page_total
- selected_pages_for_ai / total_pages
- db_steps_completed / db_steps_total
```

## ETA heuristika

- pro batch: rolling average času na soubor * zbývající soubory
- pro OCR: rolling average času na stránku * zbývající stránky
- pro OpenAI: průměr předchozích AI pokusů v rámci běhu nebo fallback „čas se počítá…"

## Aktuální deficit

- `TaskProgressDialog` drží jen label + progress bar + storno,
- controller nese jen `current/total/label/mode`,
- processing service nereportuje detailní pipeline kroky,
- nejsou procenta a ETA s významem pro uživatele.
