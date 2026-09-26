# Pokrytí kruhové mapy a zdrojové důkazy

Referenční seznam [182 variant](reference_inventory_182.json) obsahuje 1 284 navržených podkroků. Je to návrhová inventura, nikoli protokol o úspěšném provedení. Runtime plán používá etapy odpovídající současným potvrzovacím hranicím backendu; některé referenční podkroky jsou sloučené. Záznamy `unverified` v inventuře se nesmějí vydávat za dokončený audit všech variant.

## Hranice vydávané backendem

| Oblast | Potvrzení | Zdroj a význam |
| --- | --- | --- |
| Plán projektového běhu | `PLAN` | `runs/progress_plan.py`, `runs/executor.py`: režim, kvalita, zastavení po plánu a způsob zpracování; žádná etapa tím není hotová. |
| Rozpracovaný běh | `RUN_CHECK completed` | `RunExecutor.run`: po získání zámku, načtení stavu, kontrole obnovy a nepotvrzeného odeslání. Předčasné návraty potvrzení nevydávají. |
| Zmrazené vstupy | `RUN_INPUT completed` | Až po SourcePacku, autorizaci, uložení stavu a registraci běhu. |
| Model a parametry | `Lokální validace completed` | Až po kontrolách parametrů, účtu, režimu a explicitní návaznosti. |
| Provozní podklady | `RUN_RUNTIME completed` | Až po návratu `prepare_runtime`, včetně obnovení nebo uložení runtime podkladů. |
| Přípravné kontrakty | A0R/B0R, A1/B1, SPINE, DETAIL, A2/B2, quality gate | `orchestration/preparation.py`: vlastní validace a uložení výsledku. Interní transportní aliasy nejsou další kroky kruhu. |
| Výroba | `A3` / `B3` | `runs/generate.py`, `runs/modify.py`: počet vzroste jen po skutečně získaném cíli. Čekání na ruční prostředek a závislosti není dokončení. |
| Bezpečné uložení | `Ukládání completed` | `runs/delivery.py`: až po stagingu, manifestu, aktualizaci stavu a potvrzení delivery. Neznamená publikaci do OUT ani funkční ověření. |
| Odeslání dávky | `BATCH_SUBMIT completed` | `runs/batch_execution.py`: až po potvrzené identitě poskytovatele a uložení manifestu i stavu. Vzdálené zpracování dál zůstává `batch_pending`. |
| Odpověď na otázku | `QA_INPUT`, `QA_RESPONSE`, `QA_VALIDATION` | `runs/qa.py`: příprava a evidence požadavku; kontrola formátu odpovědi; kontrola struktury a vazeb na dostupná evidence ID. Nejde o nezávislý důkaz pravdivosti tvrzení. |
| Jeden soubor | `QFILE_PLAN completed` / `QFILE completed` | `runs/qfile.py`: uložený validní návrh cesty / převzatý a technicky validovaný obsah. Výroba stále vrací `files_complete_unverified`. |
| Indexace | `Indexace completed` | `runs/polling.py`: až po potvrzení všech sledovaných souborů. Výjimka v pollingu tuto událost nevydá. |
| Kaskáda | Pojmenovaný plán a `completed` konkrétního kroku | `cascade_pipeline.py`: až po uložení jeho výsledků a checkpointu; samostatně potvrzuje závěrečné uložení a publikaci posloupnosti. |
| Ukončení běhu | `RUN_FINALIZE completed` | `runs/executor.py`: po uložení doménového konečného stavu; následující `RUN` zachovává konkrétní výsledek. |
| Ostatní asynchronní operace | `OPERATION` | `studio/operations.py::Task.run`: volání a návrat konkrétní funkce, navíc její vlastní průběžné události. Vrácený chybový nebo neúplný stav se neoznačuje jako dokončení. Nepředstírá vnitřní kroky funkce. |

## Převod výsledků do UI

Správce operací předává doménový návratový stav nebo vlastní koncovou událost až po `QThread.finished` a převzetí výsledku. Výjimka neznamená automaticky bezpečné opakování. `ResponsePending`, nepotvrzené odeslání a potvrzené zrušení mají oddělený stav. Případná chyba při převzetí výsledku uživatelským rozhraním znamená selhání tohoto převzetí.

Fotografický `completed` znamená připraveno k převzetí; `downloaded` znamená dokončené místní stažení. `submission_unknown` je zachováno i bez `batch_id`. Nepojmenovaný výsledek bez potvrzení pracovníkem není označen jako úspěch.

## Důkazy a omezení

Regrese jsou v `tests/test_circular_progress_semantics.py`, `test_progress_completion.py`, `test_progress_stage_identity.py`, `test_progress_terminal_map.py`, `test_qa_evidence_links.py`, `test_remediation_resources.py` a testech kaskád. `scripts/render_progress.py` vykresluje produkční komponenty nad skutečnými událostmi izolovaných executorů; koncové chybové stavy doplňuje samostatnými řízenými scénáři.

Společný dialog pokrývá operace spravované `Operations`. To není důkaz samostatné instrumentace každého z 1 284 podkroků PDF. Synchronní akce, například pouhé připojení vybraného prostředku k formuláři, nevytvářejí umělý dlouhý běh. U obslužných funkcí bez jemnějších událostí ukazuje mapa pouze doloženou hranici celé funkce. Nepozorované podkroky se neoznačují jako hotové.
