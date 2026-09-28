# Pokrytí kruhové mapy a zdrojové důkazy

Referenční seznam [182 variant](reference_inventory_182.json) obsahuje 1 284 navržených podkroků. Původní položky `verification` v tomto historickém návrhovém inventáři se nepřepisují, protože popisují stav původního návrhu. Aktuální produkční pokrytí je samostatně a strojově závazně definováno v `core/progress_catalog.py` a ověřováno `tools/verify_progress_coverage.py`.

Katalog pokrývá bez mezery varianty 1–182. Každý z 1 284 referenčních kroků je v auditním výstupu přiřazen právě jednomu runtime vlastníku a strategii instrumentace. GENERATE/MODIFY, QA/QFILE, BATCH, PHOTO, COMIC a KASKÁDA mají specializované backendové události. Ostatní asynchronní operace používají společný lifecycle `Příprava → skutečná operace → Převzetí výsledku` ve `studio/operations.py`; krátká čtení nevyskakují automaticky, ale jejich události jsou po dobu běhu součástí stejného modelu a lze je otevřít z přehledu operací.

Kruh nadále nezobrazuje odhadovaný čas jako procenta. Referenční návrhový podkrok, který nemá samostatný pozorovatelný backendový přechod, je důkazně vlastněn nejbližší skutečnou runtime hranicí; UI si kvůli shodě s PDF nevymýšlí falešné dokončení. Nové skutečné hranice byly rozděleny tam, kde je lze potvrdit: zahájení a konfigurace běhu, výběr cíle, resource producer, kontrola výsledků, jednotlivé fáze BATCH submit/importu, PHOTO upload/submit/recovery/download, COMIC příprava/submit/retrieval a u KASKÁDY příprava/provedení/validace/checkpoint každého kroku.

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

Úplnost mapování hlídá Windows CI job `circular-progress-complete-windows`. Kontrola vyžaduje přesně 182 variant a 1 284 kroků, existenci všech deklarovaných runtime vlastníků, povinné progress markery, jediný produkční `MultiProgressDialog` a zákaz návratu `QProgressDialog`. Synchronní okamžité změny formuláře nevytvářejí umělý pracovní běh; jakmile je práce asynchronní nebo může čekat na I/O/službu, je vlastněna `Operations` nebo specializovaným executorem a používá kruhový progress.
