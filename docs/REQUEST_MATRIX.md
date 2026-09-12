# Matice požadavků

Matice popisuje rozhraní implementované aplikací. Sdílené podmínky jsou v `kajovo/core/request_rules.py`; parametrické testy v `tests/test_request_rules.py` ověřují kartézské součiny kategorií hodnot. Dostupnost modelu pro účet je samostatná podmínka: seznam `/models` nedokládá podporu každého parametru ani nástroje. Aplikace však kvůli této nejistotě neposílá samostatný placený generativní probe; definitivní odmítnutí poskytovatele se projeví až na skutečném pracovním požadavku.

## Pracovní postupy

[Strojově čitelná matice](REQUEST_COMBINATIONS.csv) obsahuje všech 1 024 kombinací čtyř režimů a osmi boolean voleb: Batch, návaznost, připojené úložiště, file search a čtyři druhy diagnostiky. Příkaz `python scripts/verify_request_matrix.py --output docs/REQUEST_COMBINATIONS.csv` porovná samostatný očekávaný predikát s validátorem a obnoví soubor. Jde o lokální, neplacenou kontrolu; neposílá generativní API požadavky. Matice ověřuje přípustnost konfigurace, nikoli dostupnost SSH nebo budoucí přijetí konkrétního pracovního požadavku vzdálenou službou. Kaskáda má vlastní kontrakt kroků a samostatné testy.

| Volba | GENERATE | MODIFY | QA | QFILE | KASKÁDA |
|---|---|---|---|---|---|
| Synchronní Responses | A1 → A2 → A3 | B1 → B2 → B3 | Text | Jeden soubor | Definované kroky |
| Batch | A1/A2 živě → N úloh A3_FILE | C_FILES_ALL | Nepovoleno | Nepovoleno | Nepovoleno |
| Návaznost | Mezi kroky, volitelný počátek | Mezi kroky, volitelný počátek | Volitelná | Volitelná | Výraz každého kroku |
| Modely dílčích kroků | A1/A2/A3 | Hlavní model | Hlavní model | Hlavní model | Model každého kroku |
| Výstupní soubory | Manifest | Manifest změn | Bez zápisu | Souborový kontrakt | JSON manifest a expected_out_files |
| Přílohy | Dokument/obrázek | Dokument/obrázek a IN | Dokument/obrázek | Dokument/obrázek | Strukturované části a lokální soubory |

GENERATE BATCH přijímá response_id, připojené vector stores a diagnostiku IN pro živou přípravu A1/A2. Dávkové A3 má samostatný společný kontext, bez návaznosti a nástrojů. MODIFY BATCH tyto volby nepřijímá. Diagnostika OUT není součástí odeslání žádné dávky. Připojený vector store vyžaduje file search podporovaný pevnou maticí u modelů přípravy. Automatické přepínání teploty se řídí skutečným modelem požadavku.

GENERATE BATCH ponechává aktivní modely A1/A2/A3, návaznost a diagnostiku IN; vypíná a odznačí diagnostiku OUT. MODIFY BATCH vypíná také návaznost a diagnostiku IN. Editor kaskády vypíná teplotu u modelů s výchozím reasoning. Externí Response ID lze zadat už v prvním kroku. Neplatný krok se neukládá ani částečně.

## Úplná matice parametrů aplikace

Každá volba musí současně projít lokálním kontraktem, řádkem konkrétního modelu v [MODEL_MATRIX.csv](MODEL_MATRIX.csv), pravidly pracovního postupu a případným ne-generativním ověřením existence použitých vzdálených prostředků. Neznámé parametry a nedoložené modely se odmítají. Hodnoty jsou konečné kategorie a intervaly; nekonečné množství textových zadání a reálných čísel se nevyjmenovává po jednotlivých hodnotách.

| Parametr nebo volba | LIVE | BATCH | Vztahy a validace |
|---|---|---|---|
| model | Povolený přesný identifikátor matice i účtu | Přesný identifikátor matice i účtu | Jediný model v celém JSONL; neznámé snapshoty se nedědí podle názvu; definitivní přijetí konkrétní kombinace potvrdí až pracovní požadavek |
| input | Text nebo seznam zpráv | Stejné | role=user/assistant/system/developer; části níže |
| instructions | Text | Stejné | Zachovávají se beze změny v pracovním požadavku |
| input_text | Text | Stejné | Žádná další pole kromě type/text |
| input_file.file_id | Existující ID souboru | Stejné | Model, přípona a metadata velikosti; PDF navíc vision |
| input_file.file_url | Neprázdná HTTP(S) URL | Stejné | PDF podle známé přípony vyžaduje vision; obsah URL ověří služba při pracovním požadavku |
| input_file.file_data + filename | Platné Base64 a podporovaná přípona | Stejné | Právě jeden zdroj; jednotlivě méně než 50 MB, součet známých přímých souborů nejvýše 50 MB |
| input_file.detail | auto/low/high | Stejné | Podrobnost zpracování PDF, original nepovoleno |
| input_image.file_id / image_url | Právě jeden zdroj | Stejné | Model musí přijímat obrázky; image_url je HTTP(S) nebo data:image |
| input_image.detail | auto/low/high; original podle matice modelu | Stejné | Nepřenášet original automaticky na starší modely |
| temperature | 0–2 podle sloupce sampling | Stejné | Bool, NaN, nekonečno odmítnuto; explicit_none vyžaduje reasoning.effort=none |
| top_p | 0–1 podle sloupce sampling | Stejné | Stejné podmínky jako temperature; aplikace nevymýšlí zákaz současného nastavení |
| reasoning.effort | Výčet konkrétního modelu | Stejné | none/minimal/low/medium/high/xhigh/max nejsou univerzální |
| reasoning.summary | Výčet konkrétního modelu | Stejné | Non-reasoning model parametr nepřijímá |
| reasoning.mode | standard/pro jen doložené novější modely | Stejné | Samostatná osa vůči effort; žádné odvozování z prefixu |
| max_output_tokens | Celé číslo 16 až maximum modelu | Stejné | Vstup + rezervovaný výstup nepřekročí kontext; kontroluje se i samostatný vstupní limit |
| text.format | json_schema, strict=true | Stejné, povinně již v JSONL | Název 1–64 znaků; podporovaná uzavřená podmnožina schémat; pracovní payload se kvůli validaci neposílá dvakrát |
| tools | Seznam implementovaných file_search nástrojů | Stejná modelová pravidla | Pracovní GENERATE A3 a MODIFY Batch mají navíc vlastní omezení pracovního postupu |
| tools[].vector_store_ids | Neprázdný seznam platných ID | Stejné | Model musí umět file_search; úložiště musí být completed, bez chybné či nedokončené indexace |
| tools[].max_num_results | Celé číslo 1–50 | Stejné | Bool nepovolen |
| tool_choice | auto/none/required nebo {type:file_search} | Stejné | Vynucení vyžaduje tools; validační vrstva hodnotu nepřepisuje |
| max_tool_calls | Kladné celé číslo | Stejné | Bool nepovolen |
| parallel_tool_calls | Boolean | Stejné | Zachová se v pracovním payloadu |
| previous_response_id | Existující dokončená odpověď | Aplikace nepovoluje pro nezávislé dávkové položky | Skutečné ID lze ověřit ne-generativním GET; žádné kontrolní zástupné ID se neposílá |
| conversation | Aplikace nepovoluje | Aplikace nepovoluje | Program používá previous_response_id |
| stream / background | Pouze false nebo vynechat | Stejné | Desktopový transport přijímá synchronní dokončenou odpověď |
| store | Boolean | Stejné | Navazující kroky potřebují dostupnou uloženou odpověď |
| metadata | Nejvýše 16 dvojic text:text | Stejné | Klíč do 64, hodnota do 512 znaků |
| service_tier | auto/default + flex/priority podle matice a regionu | Aplikace povoluje auto/default | Astra priority se blokuje pro EU endpoint; účet může mít další omezení |
| truncation | auto/disabled | Stejné | Hodnota se zachová v pracovním payloadu |
| include | file_search_call.results, message.input_image.image_url, reasoning.encrypted_content | Stejné | encrypted_content jen reasoning modely; jiné funkce aplikace neimplementuje |
| user / safety_identifier / prompt_cache_key | Neprázdný text | Stejné | Samostatné parametry; žádné automatické přepisování |
| prompt_cache_retention | in_memory/24h podle matice | Stejné | GPT-5.5 pouze 24h; novější modely používají options podle matice |
| prompt_cache_options | mode=implicit/explicit, ttl=30m podle matice | Stejné | Bez automatické konverze ze starší retention |
| Batch endpoint / metoda | — | /v1/responses / POST | Další API endpointy katalog uvádí informativně, aplikace je negeneruje |
| completion_window | — | 24h | Nejvýše 50 000 řádků a 200 MB; unikátní neprázdné custom_id |
| Vector file attributes | Nejvýše 16 hodnot | Správa úložiště mimo dávku | Klíč do 64 znaků; text do 512, jinak konečné číslo či boolean |
| GENERATE A1/A2/A3 | Každý krok vlastní model | A1/A2 live, A3 Batch | Tentýž model může mít současně oba kontexty; dávkové A3 nedědí nástroje a návaznost A1/A2 |
| MODIFY, QA, QFILE, KASKÁDA | Dle workflow matice | MODIFY podporován, QA/QFILE/KASKÁDA ne | Diagnostika a IN/OUT se řídí existujícími pracovními kontrakty |

Zdroje pravidel: [Responses](https://developers.openai.com/api/reference/resources/responses/methods/create), [Batch](https://developers.openai.com/api/docs/guides/batch), [File inputs](https://developers.openai.com/api/docs/guides/file-inputs), [File search](https://developers.openai.com/api/docs/guides/tools-file-search), [Reasoning](https://developers.openai.com/api/docs/guides/reasoning), [Prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching), [modelové stránky](MODEL_MATRIX.md). Pevný JSON nese verzi, zdroje i SHA-256 načtených dokumentů. Změna dokumentace za běhu aplikace pravidla nezmění.

## Neplacená validace před pracovním odesláním

Validační vrstva nesmí sama vytvářet generativní práci. Před LIVE požadavkem smí provést pouze lokální přípravu/validaci payloadu, kontrolu pevné matice a katalogu modelů a ne-generativní načtení metadat již existujících vzdálených prostředků. Nesmí poslat samostatný `POST /responses` jen proto, aby ověřila budoucí pracovní `POST /responses`.

BATCH se před odesláním kompletně lokálně validuje. Validace zahrnuje každý řádek JSONL, jednotný model, velikost a počet řádků, strict schema a známé reference. Potom se nahrává přímo pracovní JSONL s `purpose=batch` a následuje jediný pracovní pokus o `POST /batches`. Nesmí se vytvářet pomocný JSONL, pomocný Files upload ani zkušební dávka. Pokud je výsledek pracovního `POST /batches` neurčitý, další submit je blokován do přesného dohledání podle `input_file_id`.

Historická pole nebo logy ze starších verzí označené jako preflight/probe jsou pouze zpětně čitelné. Aktuální runtime je nevytváří a nesmí podle nich automaticky zahájit placenou zkušební operaci.

## Přílohy a vyhledávání

| Mechanismus | Validace |
|---|---|
| Přímý soubor | Jednotlivě méně než 50 000 000 bajtů, součet nejvýše 50 000 000 bajtů; neznámá velikost je chyba |
| Obrázek | PNG, JPG/JPEG, WEBP, GIF; příloha používá input_image |
| File search | Samostatný seznam přípon v compat.py; CSV/YAML nejsou povolené formáty indexace |
| Atributy vector store file | Nejvýše 16 položek; klíč do 64 znaků, textová hodnota do 512; jinak konečné číslo nebo boolean |
| Indexace | Připojení neznamená hotový index; před použitím se ne-generativně ověří stav completed |
| Změna atributů | POST na soubor ve vector store; následné načtení může mít prodlevu |

Podmínky odpovídají [vstupním souborům](https://developers.openai.com/api/docs/guides/file-inputs), [file search](https://developers.openai.com/api/docs/guides/tools-file-search) a [aktualizaci atributů](https://developers.openai.com/api/reference/python/resources/vector_stores/subresources/files/methods/update). Přijetí souboru do Files API samo nedokládá jeho použitelnost pro indexaci nebo konkrétní model.

## Ověřování

Standardní ověření repozitáře je offline vůči placeným generativním endpointům. Pytest používá mocky a lokální kontrakty. Repozitář neobsahuje `verify_*_live.py` skripty, jejichž účelem by bylo posílat placené generativní probe požadavky nebo zkušební BATCH dávky. Lokální `scripts/verify_request_matrix.py` pouze porovnává očekávanou matici s validátorem.

| Oblast rozhraní | Obsluha a skutečná operace |
|---|---|
| RUN | RunWorker / CascadeRunWorker → skutečné pracovní Responses, Files, případně Batch; kontrola výsledných souborů |
| FILES API | OpenAIClient → seznam, nahrání, odstranění; připojení ID do konfigurace běhu |
| VECTOR STORES | OpenAIClient → vytvoření, seznam, odstranění, přiřazení souborů, atributy; připojení do file_search |
| KASKÁDA | CascadeDefinition / CascadeStep → uložení definice, lokální ověření schémat, navazující pracovní požadavky a soubory |
| SETTINGS | AppSettings / secret_store → uložení voleb, klíče a bezpečnostní politiky; neimplementované šifrování je neaktivní |
| SMTP | send_smtp_notification → ověřené SSL/STARTTLS a odeslání zprávy |
| MODELS | seznam API a pevná matice → dostupné pracovní modely; bez generativního probe |
| BATCH | seznam na pracovním vlákně, časované sledování, stažení souborů a cancel |
| GITHUB | Git subprocess → stav, diff, commit, remote a synchronizace repozitáře |
| REQUEST/RESPONSE | RunLogger a uložené požadavky → filtrování, zobrazení a výběr podkladů ReRun |
| HELP | Dokumentace a odkazy aplikace |

Strukturální test Qt kontroluje připojení všech aktivních akčních tlačítek. Funkční testy odděleně kontrolují pracovní postupy, soubory, databázi, síťové kontrakty a vybrané interakce; samotné připojení signálu není důkazem správnosti vzdálené služby.

Offline testy odděleně pokrývají kombinace režimu, Batch, návaznosti, úložiště a diagnostiky; sampling kombinuje rodinu modelu, reasoning a hraniční hodnoty teploty. Další testy ověřují formáty, velikosti a atributy. Jde o konečné kategorie podmínek, nikoli výčet nekonečně mnoha textových zadání a čísel.
