# Matice požadavků

Matice popisuje rozhraní implementované aplikací. Sdílené podmínky jsou v `kajovo/core/request_rules.py`; parametrické testy v `tests/test_request_rules.py` ověřují kartézské součiny kategorií hodnot. Dostupnost modelu pro účet je samostatná podmínka: seznam `/models` nedokládá podporu každého parametru ani nástroje.

## Pracovní postupy

[Strojově čitelná matice](REQUEST_COMBINATIONS.csv) obsahuje všech 1 024 kombinací čtyř režimů a osmi boolean voleb: Batch, návaznost, připojené úložiště, ověřený file search a čtyři druhy diagnostiky. Příkaz `python scripts/verify_request_matrix.py --output docs/REQUEST_COMBINATIONS.csv` porovná samostatný očekávaný predikát s validátorem a obnoví soubor. Matice ověřuje přípustnost konfigurace, nikoli dostupnost SSH nebo vzdáleného modelu. Kaskáda má vlastní kontrakt kroků a samostatné testy.

| Volba | GENERATE | MODIFY | QA | QFILE | KASKÁDA |
|---|---|---|---|---|---|
| Synchronní Responses | A1 → A2 → A3 | B1 → B2 → B3 | Text | Jeden soubor | Definované kroky |
| Batch | A1/A2 živě → N úloh A3_FILE | C_FILES_ALL | Nepovoleno | Nepovoleno | Nepovoleno |
| Návaznost | Mezi kroky, volitelný počátek | Mezi kroky, volitelný počátek | Volitelná | Volitelná | Výraz každého kroku |
| Modely dílčích kroků | A1/A2/A3 | Hlavní model | Hlavní model | Hlavní model | Model každého kroku |
| Výstupní soubory | Manifest | Manifest změn | Bez zápisu | Souborový kontrakt | JSON manifest a expected_out_files |
| Přílohy | Dokument/obrázek | Dokument/obrázek a IN | Dokument/obrázek | Dokument/obrázek | Strukturované části a lokální soubory |

GENERATE BATCH přijímá response_id, připojené vector stores a diagnostiku IN pro živou přípravu A1/A2. Dávkové A3 má samostatný společný kontext, bez návaznosti a nástrojů. MODIFY BATCH tyto volby nepřijímá. Diagnostika OUT není součástí odeslání žádné dávky. Připojený vector store vyžaduje ověřený file search u modelů přípravy. Automatické přepínání teploty se řídí skutečným modelem požadavku.

GENERATE BATCH ponechává aktivní modely A1/A2/A3, návaznost a diagnostiku IN; vypíná a odznačí diagnostiku OUT. MODIFY BATCH vypíná také návaznost a diagnostiku IN. Editor kaskády vypíná teplotu u modelů s výchozím reasoning. Externí Response ID lze zadat už v prvním kroku. Neplatný krok se neukládá ani částečně.

## Parametry Responses

| Osa | Podmínka |
|---|---|
| model | Neprázdný identifikátor; přístup ověřuje služba |
| temperature | Konečné číslo 0–2, boolean není číslo |
| top_p | Konečné číslo 0–1; běžný RUN jej nenastavuje |
| reasoning | GPT-4.1 a GPT-4o jej nepřijímají |
| GPT-5 / o-series | Běžný RUN ponechává výchozí reasoning a neposílá teplotu |
| GPT-5.1 / 5.2 / 5.4 | Lokální klient připouští sampling jen s explicitním reasoning.effort=none; výjimky pro varianty pro/codex/chat nejsou odvozovány z názvu rodiny |
| GPT-6 | Bez temperature/top_p; reasoning none/minimal odmítnuto |
| conversation + previous_response_id | Vzájemně výlučné |
| stream / background | Synchronní klient přijímá pouze vypnuté hodnoty |
| max_output_tokens | Celé číslo nejméně 16; boolean není číslo |
| Odpověď | Dokončená, bez error/refusal; textové operace vyžadují text |

Pravidla API vycházejí z [reference Responses](https://developers.openai.com/api/reference/python/resources/responses/methods/create), [průvodce GPT-5.2](https://developers.openai.com/api/docs/guides/latest-model?model=gpt-5.2) a [průvodce GPT-6 Astra](https://developers.openai.com/api/docs/guides/latest-model). Aplikace při výchozím reasoning konzervativně neposílá sampling ani u novějších modelů; nejde o tvrzení, že všechny jejich režimy sampling zakazují.

## Přílohy a vyhledávání

| Mechanismus | Validace |
|---|---|
| Přímý soubor | Jednotlivě méně než 50 000 000 bajtů, součet nejvýše 50 000 000 bajtů; neznámá velikost je chyba |
| Obrázek | PNG, JPG/JPEG, WEBP, GIF; příloha používá input_image |
| File search | Samostatný seznam přípon v compat.py; CSV/YAML nejsou povolené formáty indexace |
| Atributy vector store file | Nejvýše 16 položek; klíč do 64 znaků, textová hodnota do 512; jinak konečné číslo nebo boolean |
| Indexace | Připojení neznamená hotový index; před použitím se čeká na completed |
| Změna atributů | POST na soubor ve vector store; následné načtení může mít prodlevu |

Podmínky odpovídají [vstupním souborům](https://developers.openai.com/api/docs/guides/file-inputs), [file search](https://developers.openai.com/api/docs/guides/tools-file-search) a [aktualizaci atributů](https://developers.openai.com/api/reference/python/resources/vector_stores/subresources/files/methods/update). Přijetí souboru do Files API samo nedokládá jeho použitelnost pro indexaci nebo konkrétní model.

## Ověřování

`scripts/verify_generate_batch_live.py --live` ověřuje A1/A2 a skutečnou dávku tří provázaných souborů s integračním testem. Souborové úlohy mají jedinečné ID, společný snímek a pevnou cílovou cestu. Import testuje chybějící a promíchané výsledky, duplicity, odmítnutí, neúplnost a ochranu uživatelských změn. Opakování používá stejné rozhraní, ale nové ID dávky a úloh.

| Oblast rozhraní | Obsluha a skutečná operace |
|---|---|
| RUN | RunWorker / CascadeRunWorker → Responses, Files, případně Batch; kontrola výsledných souborů |
| FILES API | OpenAIClient → seznam, nahrání, odstranění; připojení ID do konfigurace běhu |
| VECTOR STORES | OpenAIClient → vytvoření, seznam, odstranění, přiřazení souborů, atributy; připojení do file_search |
| KASKÁDA | CascadeDefinition / CascadeStep → uložení definice, ověření schémat, navazující požadavky a soubory |
| SETTINGS | AppSettings / secret_store → uložení voleb, klíče a bezpečnostní politiky; neimplementované šifrování je neaktivní |
| SMTP | send_smtp_notification → ověřené SSL/STARTTLS a odeslání zprávy |
| MODELS | seznam API a ModelProbeWorker → ruční ověření schopností; vyřazení zjevně odlišných API modelů |
| BATCH | seznam na pracovním vlákně, časované sledování, stažení souborů a cancel |
| GITHUB | Git subprocess → stav, diff, commit, remote a synchronizace repozitáře |
| PRICING | PriceTable / ReceiptDB → načtení sazeb, přepočet, zobrazení a export evidence |
| REQUEST/RESPONSE | RunLogger a uložené požadavky → filtrování, zobrazení a výběr podkladů ReRun |
| HELP | Dokumentace a odkazy aplikace |

Strukturální test Qt kontroluje připojení všech aktivních akčních tlačítek. Funkční testy odděleně kontrolují pracovní postupy, soubory, databázi, síťové kontrakty a vybrané interakce; samotné připojení signálu není důkazem správnosti vzdálené služby.

Offline testy odděleně pokrývají kombinace režimu, Batch, návaznosti, úložiště a diagnostiky; sampling kombinuje rodinu modelu, reasoning a hraniční hodnoty teploty. Další testy ověřují formáty, velikosti a atributy. Jde o konečné kategorie podmínek, nikoli výčet nekonečně mnoha textových zadání a čísel.

`scripts/verify_openai_live.py --live` používá skutečný SDK/REST klient, návaznost, JSON Schema, přímou přílohu a file search včetně správy vlastních prostředků. `scripts/verify_workflows_live.py --live` provádí pracovní postupy nad dočasnými soubory. Tyto příkazy vytvářejí placená volání, nejsou součástí pytest a po ověření odstraňují vlastní vzdálené prostředky. Klíč čtou z prostředí nebo ignorovaného `.env.local` a nevypisují jej.

`scripts/verify_batch_live.py --live` ověřuje vlastní dávku `/v1/responses` a její výstup. Vzdálené okno je 24 hodin; krátké ověření má limit 90 sekund a při překročení požádá o zrušení. Na jeho konečný stav čeká nejvýše dalších 10 minut, aby mohl odstranit i případný částečný výstup. Neuzavřený úklid hlásí s ID prostředku a nenulovým návratovým kódem. API dávku nemaže, proto její záznam zůstává v seznamu i po odstranění vlastních souborů. Kontrakt vychází z [dokumentace Batch](https://developers.openai.com/api/docs/guides/batch).

Pro GPT-4.1 a aktuální aliasy GPT-4o/mini používají A2/B2 a A3/B3 striktní JSON Schema. Struktura omezuje typ kontraktu a povolené akce; souborový požadavek také konkrétní cestu a index části. U ostatních modelů se uplatňuje instrukční kontrakt a lokální kontrola. Podpora [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs) se nesmí odvozovat pouze z dostupnosti modelu v účtu.
