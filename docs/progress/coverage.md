# Pokrytí kruhové mapy a zdrojové důkazy

Referenční seznam [182 variant](reference_inventory_182.json) je odvozen z přiloženého grafického návrhu. Obsahuje 1 284 kroků. U dvanácti kroků variant 040–043 je doložena dílčí hranice backendové události; ostatní kroky zůstávají neověřené. Záznam o události nepotvrzuje všechny části navrženého kroku. Referenční seznam není důkazem, že backend vydává událost.

## Společný začátek projektových běhů a odpovědi na otázku

| Krok v návrhu | Zdrojový průchod | Stav události |
| --- | --- | --- |
| 01 Kontrola zadání | `kajovo/studio/workbench.py::validate` a `::config` | Místní validace proběhne před vytvořením pracovníka; samostatná dokončená událost chybí. |
| 02 Zahájení práce | `workbench.py::start`, `operations.py::adopt` | Vytvoří logger, pracovníka a rezervaci; samostatná událost chybí. |
| 03 Kontrola rozpracované práce | `kajovo/core/runs/executor.py::RunExecutor.run` (zámek, načtení stavu, pokračování, publish journal) | Výjimky a předčasné návraty existují; samostatné potvrzení kroku chybí. |
| 04 Uložení nastavení a podkladů | `RunExecutor.run` (RUN_CONFIG_V2, SourcePack, autorizace, registrace) | Potvrzení musí být až po uložení a registraci; samostatná událost chybí. |
| 05 Příprava podkladů | `kajovo/core/runs/recovery.py::prepare_runtime` | Některé vnitřní úkony už hlásí dílčí zprávy, společné potvrzení kroku chybí. |

Tyto kroky jsou popsány u variant 001, 002 a 040–043; úplná platnost stejné mapy v ostatních variantách se musí ověřit samostatně. Předčasný návrat a chyba nesmějí posunout kruh.

## Odpověď na otázku, varianty 040–043

| Krok | Zdrojová funkce a potvrzovací hranice | Pokrytí |
| --- | --- | --- |
| Příprava podkladů k otázce | `kajovo/core/runs/qa.py::_run_qa`; po sestavení požadavku a jeho zápisu do evidence | Nová `QA_INPUT completed`; selhání při přípravě ji nevydá. |
| Získání odpovědi | `_create_response`, `validate_output` | Nová `QA_RESPONSE waiting` před voláním, `completed` teprve po validaci tvaru odpovědi. |
| Kontrola odpovědi | Kontrola `status`, `answer`, `claims`, `limitations`, `evidence_ids`; uložení manifestu | Nová `QA_VALIDATION active/completed`; chybné podklady nevydají `completed`. |
| Navázání na předchozí rozhovor (041) | Předchozí odpověď se přidává pouze při explicitní volbě a podpoře modelu | Samostatná událost dosud chybí. |
| Odpověď vyžaduje upřesnění (042) | `PreparationBlocked`, poté `RunExecutor.run` uloží `needs_clarification` | Samostatná událost kroku dosud chybí. |
| Příprava přiložených souborů (043) | `prepare_runtime` a přílohové funkce | Samostatná událost kroku dosud chybí. |

Terminální `RUN` znamená výsledek místního běhu; nesmí zpětně potvrdit nedokončené kroky. Současné okno stále zobrazuje jen ohlášené etapy, takže plný jmenovatel 9/10 kroků ještě není zaveden. Všech 182 variant zůstává otevřených do plného sémantického auditu, testů a vizuální kontroly; dílčí důkaz hranice pro dvanáct kroků uzavření varianty neznamená.
