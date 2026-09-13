# Forenzní audit nákladů a kontextu

## Metoda a důkazní hranice

Výchozí audit před změnou orchestrace vychází ze zdrojového kódu, nikoli z předpokladu správnosti `spec.txt`. Důkazní archiv má SHA-256 `788270ac6cf5cf1f1efc5dfc00cce18396b3ee11b49988d8ae49672b6e3e0e8b`. Rozbalení proběhlo do nového systémového dočasného adresáře s bezpečným filtrem tar `data`. Multiset délek a SHA-256 všech 258 souborů souhlasí s archivním manifestem. Původní archiv ani provozní data se nemění. Názvy některých položek manifestu obsahují náhradní znaky; kontrola obsahu proto používá také multiset hashů a délek.

`original_prompt.txt` obsahuje 1 215 653 znaků. Mini error JSONL obsahuje přesně 228 chyb `context_length_exceeded`. Luna output JSONL má 71 odpovědí: 38 `completed`, 33 `incomplete` s důvodem `max_output_tokens`. Součet usage Luny je 26 804 428 input a 963 092 output tokenů; output zahrnuje reasoning. To není součet všech přípravných volání ani účetní cena celého incidentu. Formální dokončení 38 odpovědí nedokládá jejich sestavení, integraci ani splnění zadání. Automaticky importovaný výsledek nelze odvodit z `completed` dávky.

## Matice pracovních cest před optimalizací

Všechny generativní operace používají POST `/v1/responses`, strict `text.format` a model z pevné matice. `temperature` se posílá jen při kompatibilním reasoning, `top_p` běžná pipeline nepřidává. Maximum Quality volí maximum podporovaného effort; Standard ponechává výchozí politiku. Neuvedený output budget znamená delegaci na `prepare_payload`, nikoli neomezený výstup. ID návaznosti přenáší historii na straně poskytovatele; není důkazem bezplatného kontextu.

| Cesta / call site | Vstup, přílohy a historie | Počet a opakování | Klasifikace a zásah |
|---|---|---|---|
| GENERATE/MODIFY technický A0, `pipeline._ingest_prompt_if_needed` | Nad 150 000 znaků části po 20 000; návaznost, jen potvrzení přijetí | ceil(znaky/20 000) generací | Zbytečný náklad technických potvrzení; kumulace historie. Nahradit lokálním uložením a jednorázovým pracovním příjmem, bez ztráty textu. |
| A0R/B0R, `delivery_preparation.prepare_delivery` | Zdroj, přímé dokumenty/obrázky, IN, diagnostika, případně file_search a vstupní response ID | 1 pracovní syntéza | Nutný náklad. Zachovat plnost povinností a vysoký reasoning podle složitosti. |
| A1/B1, stejný modul | Zdroj + requirements + historie; MODIFY znovu přílohy | 1 plán | Nutný náklad analýzy; riziková duplicita explicitních dat a historie. |
| A2/B2, stejný modul | Zdroj + requirements + plán + historie; JSON schema struktury | 1 + nejvýše 2 opravy | Nutný implementační kontrakt. Opravy opakují plný přípravný kontext. Deterministická dostatečnost dosud nekontroluje signatury, typy ani lifecycle. |
| A2Q/B2Q, stejný modul | Totéž + celá struktura, jen Maximum Quality | 1 + nejvýše 2 opravy | Podmíněně nutná hloubková kontrola, nekomprimovat analytickou kvalitu. Lokální gate je převážně traceability. |
| A3 LIVE, `pipeline._gen_file_chunks` | Celý `_delivery_snapshot` znovu na každý soubor i chunk; návaznost na přípravu/předchozí soubor, diagnostický text, případně tools | Soubory × chunky × nejvýše 3 pokusy | Riziková duplicita a zbytečný náklad. Samostatný FileContext, návaznost pouze uvnitř jednoho souboru. |
| B3 LIVE, stejná funkce | Navíc `_delivery_originals` celé, přímé přílohy a IN odkazy | Stejné | Významné riziko nerelevantních zdrojů. Povinný původní měněný text zachovat; závislosti vybírat explicitně. |
| A3/B3 Batch, `generate_batch.build_manifest` | Každý řádek `specification=snapshot`, cílový file; B3 originál a dependency originals; bez tools a historie | N řádků, jedna dávka | Ověřená produkční full-snapshot duplicita, nezávislá na ručním recovery. Manifest v3 musí oddělit audit a pracovní kontext. |
| Batch transport, `pipeline._submit_generate_batch`, `batch_submit.submit_verified_batch` | Pracovní JSONL → Files `purpose=batch` → POST `/batches` | Jeden create pokus; neznámý submit blokován | Nutný náklad. Zachovat zákaz zkušebního Batch. |
| Batch repeat/repair, `generate_batch.repeat_saved_batch` | Kopie starého řádku, u repair přidán feedback a aktuální text | Jen vybrané cesty, nová placená dávka | Podmíněně nutný; legacy řádky znovu přenášejí snapshot. Vyžaduje explicitní migraci, ochranu dokončených souborů a blokaci neurčitého submitu. |
| Batch hybrid import, `process_saved_batch`, `import_results` | GET Batch a Files content, uložený manifest, validace schématu/cesty/chunku/hashů | Opakovatelný download/import | Nutný lokální náklad; nesmí vyvolat generaci. Již rozlišuje `files_complete_unverified`. Prázdný obsah dosud nemá file-specific gate. |
| ReRun, `delivery_preparation.validate_preparation_snapshot`, `runlog.verified_output_evidence` | Hashované checkpointy a doložené výstupy | Přeskakuje doloženou přípravu/soubory | Nutná ochrana investice. Poškozený hash nesmí vést k tiché regeneraci. |
| Response recovery, `response_journal.ResponseJournal` | Hash celého payloadu, response ID, GET polling, uložená odpověď | Jeden create; polling a nejvýše 4 selhání GET | Správná ochrana unknown submission. Redakce uloženého journalu může zničit hash. |
| QA, `pipeline._run_qa` | Zadání, přílohy, diagnostika, tools, volitelná historie | Jedna odpověď | Nutný náklad dle dotazu. Nesnižovat kontext bez znalosti otázky. |
| QFILE, `pipeline._run_qfile` | Zadání, přílohy a file schema, návaznost | Jedna pracovní odpověď | Podmíněně nutný. Nezaměňovat s A3 implementací z připravené struktury. |
| Kaskády, `cascade_pipeline._prepare_step`, `.run` | Substituce textu/JSON/předchozích výsledků, lokální uploady, explicitní návaznost | Definice kroků včetně smyček a resume cache | Uživatelsky definovaný nutný/podmíněný náklad. Opakování velkých výstupů lze měřit; automatické vynechání by měnilo kontrakt. |
| Příprava schema, `structured_output.resolve_schema` | Instrukce, původní schema, downstream kontext a chyba | Nejvýše 3 skutečné návrhy | Podmíněně nutný pracovní artefakt; lokálně kompilovatelné schema nevolá model. |
| IN, `pipeline` upload a mirror | Manifest, JSONL obsah nebo jednotlivé soubory, indexace vector store | Uploady podle skenu + GET indexace | Riziková duplicita direct file a stejného retrieval obsahu. Kritický obsah nesmí být pouze retrieval. |
| Diagnostika IN/OUT, `pipeline` diagnostické metody | Textové výsledky, JSON přílohy, vector store | Podle zapnutých voleb, polling indexace | Podmíněně nutné; celý diagnostický materiál není potřebný ke každému souboru. OUT při Batch zakázán. |
| Files/vector stores, `openai_client` a desktop správa | POST upload/create/index, GET seznam/metadata/content, DELETE | REST retry a někdy vnější `with_retry` | Nejde o generativní tokeny; upload/indexace/úložiště mohou mít vlastní náklad a dvojí retry může násobit operace. |
| SDK/REST, `openai_client._req`, `_send_response`, `retry.with_retry` | Přesný pracovní payload | SDK max_retries=0; Responses/Batch create 1; ostatní REST nejvýše 4, nad tím aplikační retry | Síťový retry nesmí být zaměněn za repair. Mutace SDK se neopakuje REST fallbackem. |
| Provozní evidence, `runlog.save_json`, `_write_state` | Rekurzivní redakce včetně řetězců obsahujících bearer | Každý zápis | Riziková destrukce obnovitelných dat: redigovaný log není kanonický artefakt. |

## Parametry, které je nutné měřit odděleně

Instrukce, text input, response schema, requirements, plán, structure, dependency contracts, source excerpts a soubory tvoří rozdílné kategorie. Délka lokálního JSON není přesný počet tokenů přímé přílohy, obrázku, retrieval ani serverové historie. Neznámé složky nesmějí být vykazovány jako nula. Aktuální matice modelů je lokální zdroj limitů; cenové sazby a long-context prahy bez doloženého zdroje nejsou odhadovány z názvu modelu.

Oficiální [Responses create](https://developers.openai.com/api/reference/python/resources/responses/methods/create) uvádí, že output limit zahrnuje viditelný výstup i reasoning. Samostatný [input token count](https://developers.openai.com/api/reference/python/resources/responses/subresources/input_tokens/methods/count) je ne-generativní měření. Offline audit používá archivované počty a jasně označený lokální odhad; neposílá měřicí generaci.

## Rozhodnutí před implementací

Bezpečné zúžení vyžaduje explicitní vazby na všechny relevantní povinnosti, nejen requirement IDs. Bohatý kontrakt musí popsat veřejné symboly, verze, chyby, lifecycle a akceptaci tam, kde se použijí. Chybějící detail se nesmí vymyslet deterministickou heuristikou. Legacy ID92 lze lokálně rozdělit a změřit jako kandidátní kontexty, ale bez doplnění kontraktů nelze tvrdit připravenost ani prokázanou kvalitativně ekvivalentní úsporu.

Zakázané optimalizace: plošné snížení modelu/reasoning, ořez povinností, implicitní návrat k plnému snapshotu, generativní preflight, slepý retry incomplete a označení dokončeného Batch za ověřený projekt.

## Kontrola implementovaných cest

Nové `build_manifest` vytváří výhradně v3 a vyžaduje implementační přípravu. `specification` se vytváří pouze při lokálním měření legacy srovnání; tato varianta se nevkládá do odesílaného řádku. `encode_requests` větví v1/v2 čtení a v3 recompilaci explicitně. Repeat v1/v2 odmítá nové odeslání. LIVE `_gen_file_chunks` nepřidává `_delivery_snapshot`, celé `_delivery_originals`, diagnostiku ani tools do requestu; přijímá je compiler lokálně. První souborový request nenavazuje na přípravu a další chunky používají pouze historii stejného souboru. Technický A0 ukládá přesné zadání bez modelových potvrzení.

Přípravné analytické fáze nadále mohou obsahovat plné zdroje a návaznost. Jejich automatické zúžení by bez důkazu úplné syntézy měnilo kvalitu. QA, QFILE, kaskády a příprava schema zachovávají pracovní kontrakty; jejich obecný uložený payload lze změřit přes `scripts/measure_request_context.py`. Report běhu je zapojen pro GENERATE/MODIFY a Batch import, nikoli univerzálně pro všechny kaskádové kroky.

Přesné artefakty a journal jsou oddělené od provozní redakce; obnovení ověřuje hash. Import stále rozlišuje jednotlivé `completed` Responses od `files_complete_unverified`. Neprovádí generovaný kód. Planner detekuje SCC a vlny, ale automatický vícebatchový executor s integrační validací providerů není implementován. Automatické přepínání na levnější model se neprovádí. Tyto hranice nejsou vydávány za kompletní splnění celého požadavku na orchestraci.

Offline ID92 má všech 299 úloh individuálně změřených a blokovaných pro nedostatečné archivní kontrakty. Kandidátní redukce reprezentace se nesmí zaměňovat za ověřenou úsporu ekvivalentní implementace. Výsledky a reprodukovatelná metoda jsou v [ID92_CONTEXT_OPTIMIZATION_REPORT.md](ID92_CONTEXT_OPTIMIZATION_REPORT.md).
