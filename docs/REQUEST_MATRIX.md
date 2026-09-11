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

## Úplná matice parametrů aplikace

Každá volba musí současně projít tímto kontraktem, řádkem konkrétního modelu v [MODEL_MATRIX.csv](MODEL_MATRIX.csv), pravidly pracovního postupu a ověřením přístupu. Neznámé parametry a nedoložené modely se odmítají. Hodnoty jsou konečné kategorie a intervaly; nekonečné množství textových zadání a reálných čísel se nevyjmenovává po jednotlivých hodnotách.

| Parametr nebo volba | LIVE | BATCH | Vztahy a validace |
|---|---|---|---|
| model | Povolený přesný identifikátor matice i účtu | Navíc potvrzená podpora Batch | Jediný model v celém JSONL; neznámé snapshoty se nedědí podle názvu |
| input | Text nebo seznam zpráv | Stejné | role=user/assistant/system/developer; části níže |
| instructions | Text | Stejné | Zkušební požadavek zachová skutečné instrukce |
| input_text | Text | Stejné | Žádná další pole kromě type/text |
| input_file.file_id | Existující ID souboru | Stejné | Model, přípona a metadata velikosti; PDF navíc vision |
| input_file.file_url | Neprázdná HTTP(S) URL | Stejné | PDF podle známé přípony vyžaduje vision; obsah URL ověří služba |
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
| text.format | json_schema, strict=true | Stejné, povinně již v JSONL | Název 1–64 znaků; podporovaná uzavřená podmnožina schémat; skutečné schéma zachová i zkouška |
| tools | Seznam implementovaných file_search nástrojů | Stejná modelová pravidla | Pracovní GENERATE A3 a MODIFY Batch mají navíc vlastní omezení pracovního postupu |
| tools[].vector_store_ids | Neprázdný seznam platných ID | Stejné | Model musí umět file_search; úložiště musí být completed, bez chybné či nedokončené indexace |
| tools[].max_num_results | Celé číslo 1–50 | Stejné | Bool nepovolen |
| tool_choice | auto/none/required nebo {type:file_search} | Stejné | Vynucení vyžaduje tools; zkouška volbu nikdy nepřepisuje na required |
| max_tool_calls | Kladné celé číslo | Stejné | Bool nepovolen |
| parallel_tool_calls | Boolean | Stejné | Zachová se v identitě i zkušebním volání |
| previous_response_id | Existující dokončená odpověď | Aplikace nepovoluje | Skutečné ID se ověřuje a zkouší; kontrolní zástupné ID se nikdy neposílá |
| conversation | Aplikace nepovoluje | Aplikace nepovoluje | Sdílená conversation by zkušebním voláním změnila historii; program používá previous_response_id |
| stream / background | Pouze false nebo vynechat | Stejné | Desktopový transport přijímá synchronní dokončenou odpověď |
| store | Boolean | Stejné | Zkouška zachová nastavení; navazující kroky potřebují dostupnou uloženou odpověď |
| metadata | Nejvýše 16 dvojic text:text | Stejné | Klíč do 64, hodnota do 512 znaků |
| service_tier | auto/default + flex/priority podle matice a regionu | Aplikace povoluje auto/default | Astra priority se blokuje pro EU endpoint; účet může mít další omezení |
| truncation | auto/disabled | Stejné | Zachová se v identitě i zkoušce |
| include | file_search_call.results, message.input_image.image_url, reasoning.encrypted_content | Stejné | encrypted_content jen reasoning modely; jiné funkce aplikace neimplementuje |
| user / safety_identifier / prompt_cache_key | Neprázdný text | Stejné | Samostatné parametry; žádné přepisování před zkouškou |
| prompt_cache_retention | in_memory/24h podle matice | Stejné | in-memory je neplatné; GPT-5.5 pouze 24h; novější modely používají options |
| prompt_cache_options | mode=implicit/explicit, ttl=30m podle matice | Stejné | Bez automatické konverze ze starší retention |
| Batch endpoint / metoda | — | /v1/responses / POST | Další API endpointy katalog uvádí informativně, aplikace je negeneruje |
| completion_window | — | 24h | Nejvýše 50 000 řádků a 200 MB; unikátní neprázdné custom_id |
| Vector file attributes | Nejvýše 16 hodnot | Správa úložiště mimo dávku | Klíč do 64 znaků; text do 512, jinak konečné číslo či boolean |
| GENERATE A1/A2/A3 | Každý krok vlastní model | A1/A2 live, A3 Batch | Tentýž model může mít současně oba kontexty; dávkové A3 nedědí nástroje a návaznost A1/A2 |
| MODIFY, QA, QFILE, KASKÁDA | Dle workflow matice | MODIFY podporován, QA/QFILE/KASKÁDA ne | Diagnostika a IN/OUT se řídí existujícími pracovními kontrakty |

Zdroje pravidel: [Responses](https://developers.openai.com/api/reference/resources/responses/methods/create), [Batch](https://developers.openai.com/api/docs/guides/batch), [File inputs](https://developers.openai.com/api/docs/guides/file-inputs), [File search](https://developers.openai.com/api/docs/guides/tools-file-search), [Reasoning](https://developers.openai.com/api/docs/guides/reasoning), [Prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching), [modelové stránky](MODEL_MATRIX.md). Pevný JSON nese verzi, zdroje i SHA-256 načtených dokumentů. Změna dokumentace za běhu aplikace pravidla nezmění.

## Zkušební volání před odesláním

Zkušební LIVE volání zachová celý skutečný payload včetně souborů, nástrojů, schématu, historie, výstupního limitu a ostatních voleb. Výstup se ověří stejným kontraktem, ale nezapisuje pracovní soubory. Teprve poté se odešle pracovní požadavek; každé další pracovní odeslání vyžaduje novou zkoušku. Zkouška je skutečné placené volání, tedy může mít náklady a délku srovnatelnou s pracovním požadavkem.

BATCH se ověřuje skutečnou samostatnou dávkou `/v1/responses`, se všemi dosud neověřenými přesnými payloady. Lokální kontrola všech řádků předchází prvnímu uploadu. Aplikace čeká nejvýše 60 sekund; pokud zkouška ještě běží, pracovní dávku neodešle, oznámí ID a stav a uloží podklady pro další spuštění. Opakované odeslání stejného zadání převezme dokončený výsledek stejné zkušební dávky; nevytváří znovu již běžící zkoušku. Úspěch vyžaduje completed, HTTP 200 a platný obsah každého řádku. Výsledek live nikdy nenahrazuje důkaz Batch.

Identita zahrnuje celý payload, transport, verzi matice a kontext klíče/endpointu (uložený pouze jako SHA-256). Staré probe cache se nepřebírají. Úspěch Batch je použitelný do pracovního odeslání, nejdéle hodinu po převzetí výsledku; při změně parametrů se provede nová zkouška. Při neurčitém výsledku vytvoření Batch bez obdrženého ID aplikace sama zkoušku znovu nevytváří; ID souboru a chyba jsou v cache a je nutné nejprve dohledat stav v panelu Batch/API. Chyba nese model, LIVE/BATCH, parametr a kód, pokud je API poskytlo. Pokud API parametr neuvede, aplikace to výslovně uvede. Síťové chyby, oprávnění, soubory a odmítnutý obsah nejsou důkazem nepodporované schopnosti modelu.

Po převzetí a evidenci výsledků se odstraňují pouze vlastní zkušební dávkové soubory; protokol zůstává. Neúspěšný úklid je zaznamenaný. Platná lokální kombinace nemůže zaručit budoucí dostupnost účtu, souborů, kvóty ani neměnnost API mezi zkouškou a pracovním voláním.

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
| MODELS | seznam API a pevná matice → dostupné pracovní modely; bez ručního probe |
| BATCH | seznam na pracovním vlákně, časované sledování, stažení souborů a cancel |
| GITHUB | Git subprocess → stav, diff, commit, remote a synchronizace repozitáře |
| REQUEST/RESPONSE | RunLogger a uložené požadavky → filtrování, zobrazení a výběr podkladů ReRun |
| HELP | Dokumentace a odkazy aplikace |

Strukturální test Qt kontroluje připojení všech aktivních akčních tlačítek. Funkční testy odděleně kontrolují pracovní postupy, soubory, databázi, síťové kontrakty a vybrané interakce; samotné připojení signálu není důkazem správnosti vzdálené služby.

Offline testy odděleně pokrývají kombinace režimu, Batch, návaznosti, úložiště a diagnostiky; sampling kombinuje rodinu modelu, reasoning a hraniční hodnoty teploty. Další testy ověřují formáty, velikosti a atributy. Jde o konečné kategorie podmínek, nikoli výčet nekonečně mnoha textových zadání a čísel.

`scripts/verify_openai_live.py --live` používá skutečný SDK/REST klient, návaznost, JSON Schema, přímou přílohu a file search včetně správy vlastních prostředků. `scripts/verify_workflows_live.py --live` provádí pracovní postupy nad dočasnými soubory. Tyto příkazy vytvářejí placená volání, nejsou součástí pytest a po ověření odstraňují vlastní vzdálené prostředky. Klíč čtou z prostředí nebo ignorovaného `.env.local` a nevypisují jej.

`scripts/verify_batch_live.py --live` ověřuje vlastní dávku `/v1/responses` a její výstup. Vzdálené okno je 24 hodin; krátké ověření má limit 90 sekund a při překročení požádá o zrušení. Na jeho konečný stav čeká nejvýše dalších 10 minut, aby mohl odstranit i případný částečný výstup. Neuzavřený úklid hlásí s ID prostředku a nenulovým návratovým kódem. API dávku nemaže, proto její záznam zůstává v seznamu i po odstranění vlastních souborů. Kontrakt vychází z [dokumentace Batch](https://developers.openai.com/api/docs/guides/batch).
