# Forenzni audit zdrojoveho kodu

## Stav: dokonceny staticky audit

Audit byl dokoncen nad pracovnim stromem ze dne 2026-09-24. Nejde o potvrzeni funkcnosti: nebyly spusteny testy, aplikace ani vzdalene API. Z puvodnich 11 bodu zustava v aktualni implementaci dolozeny nalez A3-010; ostatnich 10 bodu soucasny kod resi a jsou nize oznaceny jako uzavrene. V tomto statickem rozsahu nebyl potvrzen dalsi samostatny nalez.

Puvodni inventar obsahoval 204 souboru. Zmenene soubory byly znovu porovnany a tabulka otiskuje aktualni pracovni strom. Pocty radku vyjadruji rozsah inventare, nikoli miru semanticke kontroly.

## Metoda a hranice důkazů

- Důkazem je aktuální implementace: Python, spouštěcí a sestavovací skripty, provozní konfigurace, JSON masky a workflow. Testy, jejich výsledky, předchozí audity, dokumentace ani komentáře nebyly použity k dokazování funkčnosti nebo chyb.
- Proběhlo statické čtení a sledování producentů, spotřebitelů, podmínek a ukládání dat. Nebyly spuštěny testy, aplikace, generování ani placená API volání. Příklady níže jsou odvozené průchody kódem, nikoli tvrzení o provedených reprodukčních testech.
- Aktuální chování vzdáleného OpenAI API nebylo ověřováno. Zpráva nedokazuje dostupnost modelů ani kompatibilitu se současnými pravidly poskytovatele; nálezy se opírají o vnitřní chování implementace.
- Zahrnuty jsou zdrojové soubory a konfigurace v níže uvedeném inventáři. Vynechány jsou testy, dokumentace, prostředí, cache, runtime data a generované balíčky v Build/lib, Build/Kajovo, Build/bdist.* a dist. Inventář byl sestaven podle zdrojových přípon a vybraných kořenových konfiguračních souborů; není důkazem kontroly případných dalších neidentifikovaných formátů.
- Referenční HEAD: `6a277dd444d942288c9c8607310a0a04413971e1`. Pracovní strom obsahoval existující změny; audit se vztahuje k pracovním souborům identifikovaným jejich SHA-256, nikoli pouze ke commitu.
- Inventar obsahuje 204 souboru. SHA-256 a pocty radku byly pred zapsanim zpravy prepocitany; 22 souboru se lisi od otisku puvodni prubezne zpravy.
- Implementace nebyla upravena. Zápisem tohoto auditu se nemění scope, kontrakty ani SSOT. Nejsou navrhovány nové produktové testovací brány.

Závažnost: P1 = blokace zásadní podporované cesty; P2 = funkční, bezpečnostní nebo kontextová chyba za uvedených podmínek; P3 = menší chyba výstupu či evidence. Seřazení identifikátorů odpovídá evidenci nálezů, ne jejich závažnosti.

## Doložené nálezy

### A3-001 | UZAVRENO | Platný JSON výstup kaskády s polem type typu array/object shodí ukládání stavu

**Zdroj:** [kajovo/core/safe_config.py](kajovo/core/safe_config.py) · řádky 88-97; [kajovo/core/cascade_pipeline.py](kajovo/core/cascade_pipeline.py) · řádky 1406-1426,1502-1525,2066-2069; [kajovo/core/runlog.py](kajovo/core/runlog.py) · řádky 532-535.

Hodnota {'type':['a']} validní vůči explicitní uživatelské masce projde validací výstupu, uloží se do values/legacy_context; persist_evidence rekurzivně zkouší kind in set, list/dict nejsou hashovatelné. TypeError po úspěšné generaci, nelze uložit dokončený krok; chyba není chybný JSON.

### A3-002 | UZAVRENO | Ztrátová redakce mění cílové cesty již hashovaného WorkOrderu

**Zdroj:** [kajovo/core/runs/file_execution.py](kajovo/core/runs/file_execution.py) · řádky 182-212; [kajovo/core/orchestration/work_order.py](kajovo/core/orchestration/work_order.py) · řádky 120-122,209-235; [kajovo/core/runlog.py](kajovo/core/runlog.py) · řádky 653-659; [kajovo/core/safe_config.py](kajovo/core/safe_config.py) · řádky 31,126-134.

Legitimní cesta sk-abcdefgh123.txt prochází validate_relative_path. work_order.target_id a target_path jsou maskovány až po výpočtu order_hash; při work_order_from_mapping uložená struktura neodpovídá původnímu order_hash. Ostatní podobně pojmenované metadata a kaskádový mezistav podléhají téže ztrátové redakci.

### A3-003 | UZAVRENO | Maskování výjimky obchází nezačištěné human_message a technical_message

**Zdroj:** [kajovo/core/runlog.py](kajovo/core/runlog.py) · řádky 752-766; [kajovo/core/run_bundle.py](kajovo/core/run_bundle.py) · řádky 561-578,583.

Data pro error.exception se redigují, ale str(ex) a traceback.format_exc() se předají znovu do samostatných polí bez redakce; append_event je uloží doslova. Pokud výjimka obsahuje token/API klíč/heslo, v events.jsonl zůstává původní hodnota.

### A3-004 | UZAVRENO | Git filtr citlivých souborů obchází jiné velikosti písmen

**Zdroj:** [kajovo/core/project_git.py](kajovo/core/project_git.py) · řádky 19-37,107-110.

allowed_file('.ENV') a allowed_file('KAJOVO_SETTINGS.JSON') vrací True, ačkoli malé varianty odmítá. Windows používá běžně case-insensitive souborový systém. Stejné pro LOG/log, cache/Cache, Build/build; synchronizace push používá tento filtr.

### A3-005 | UZAVRENO | macOS ikona 512@2x má jen rozměry 512x512

**Zdroj:** [Build/generate_icons.py](Build/generate_icons.py) · řádky 44; [Build/build_macos.sh](Build/build_macos.sh) · řádky 27-29.

Výrobce vytváří 512x512; build soubor beze změny kopíruje pod název 512x512@2x, zatímco ostatní @2x mají dvojnásobné rozměry. Chybí skutečná 1024px varianta. Nelze bez spuštění tvrdit konkrétní reakci iconutil.

### A3-006 | UZAVRENO | Dohledání neurčitého prvního BATCH submitu neopraví databázovou identitu operací

**Zdroj:** [kajovo/core/runs/batch_execution.py](kajovo/core/runs/batch_execution.py) · řádky 140-146,180-208; [kajovo/core/batch_completion.py](kajovo/core/batch_completion.py) · řádky 221-247; [kajovo/core/generate_batch.py](kajovo/core/generate_batch.py) · řádky 1470-1497; [kajovo/core/orchestration/repository.py](kajovo/core/orchestration/repository.py) · řádky 927-965.

První submit ukládá generate_batch, nikoli pending_batch_submission. recover_unknown_submission volá recover_batch_identity a přechod V4 pouze ve větvi pending manifest. Nalezené batch_id tedy zapíše do run_state a zruší příznak unknown, ale DB zůstane submission_unknown s provider_id NULL. Při importu selže record_usage nebo mark_terminal na PROVIDER_OPERATION_NOT_CONFIRMED. Výsledek na serveru existuje, standardní dohledání neumožní dokončení.

### A3-007 | UZAVRENO | Nový upload při opakování nepotvrzené další vlny koliduje s původní fyzickou vazbou pokusu

**Zdroj:** [kajovo/core/generate_batch.py](kajovo/core/generate_batch.py) · řádky 1276-1296,1329-1349; [kajovo/core/orchestration/repository.py](kajovo/core/orchestration/repository.py) · řádky 655-671,725-734; [kajovo/core/orchestration/batch_recovery.py](kajovo/core/orchestration/batch_recovery.py) · řádky 15-25.

Po jednoznačném odmítnutí POST (např.429) je automatic followup attempt1 označen not_submitted. Opakované dokončení předchozí vlny vytvoří stejný WorkOrder/attempt1, prepare_provider_operation vrátí prepared bez vymazání physical_request_hash/remote_input_file_id. Další upload vrátí jiné Files ID; bind_physical_request odmítne nový fyzický hash či ID. Automatická další vlna zůstane zablokována po odeznění dočasného odmítnutí.

### A3-008 | UZAVRENO | Nečitelný legacy registr zahodí platný klíč z credential storage

**Zdroj:** [kajovo/core/secret_store.py](kajovo/core/secret_store.py) · řádky 23-35,153-162; [kajovo/app/main.py](kajovo/app/main.py) · řádky 78-88.

Po úspěšném keyring čtení se ještě povinně čte legacy registr mimo try pro cleanup. OSError či REG_EXPAND_SZ/nesprávnýtyp vyvolá APIKeyStoreError namísto vrácení platného klíče; app.main nastaví api_key na prázdný. Chyba starého nepoužívaného záznamu tak znemožní běžné použití API.

### A3-009 | UZAVRENO | IN se indexuje i při vypnutém file_search a vytvořený vector store se nepoužije

**Zdroj:** [kajovo/core/runs/attachments.py](kajovo/core/runs/attachments.py) · řádky 161-179,364-413; [kajovo/core/openai_client.py](kajovo/core/openai_client.py) · řádky 422-426.

_prepare_in_dir_upload při podporovaném vector store (LIVE nebo GENERATE) vždy provede create_vector_store, add_file a čekání. Neověřuje cfg.use_file_search. Teprve sestavení _fs_tools kontroluje use_file_search nebo diagnostiku. Pro běžný běh bez diagnostiky s use_file_search=False tak proběhne vzdálená indexace, která se do žádného pracovního toolu nezapojí; přibývají vzdálené operace, čekání a zůstává serverový prostředek bez expires_after.

### A3-010 | OTEVRENO | Příprava přenáší obsah IN současně inline i jako úplnou přímou přílohu

**Zdroj:** [kajovo/core/runs/attachments.py](kajovo/core/runs/attachments.py) · řádky 340-375,429-461; [kajovo/core/runs/generate.py](kajovo/core/runs/generate.py) · řádky 435-450; [kajovo/core/runs/modify.py](kajovo/core/runs/modify.py) · řádky 325-335; [kajovo/core/delivery_preparation.py](kajovo/core/delivery_preparation.py) · řádky 177-184; [kajovo/core/orchestration/preparation.py](kajovo/core/orchestration/preparation.py) · řádky 752-758,817-819,903-910,1315-1320,1338-1341,1373-1378,1386-1390.

Textový projekt je vždy nahrán jako in_dir_*.txt a _input_files_with_in_dir jej přidá k přímým input_file. GENERATE A0R/A1 současně zahrne celé source.segments; MODIFY B0R/B1 zahrne celé selected_originals. _compile_source_attachments pro všechny čtyři stage připojí runtime file_ids. Obsah projektu je tedy na těchto krocích předán dvakrát různými reprezentacemi, bez deduplikace podle původu. Zvětšuje se vstupní kontext a zmenšuje prostor pro skutečnou práci; konkrétní cenu ani přesný počet provider tokenů audit netvrdí.

### A3-011 | UZAVRENO | Chybové ukončení běhu ponechá lifecycle_status v běžící fázi

**Zdroj:** [kajovo/core/runs/context.py](kajovo/core/runs/context.py) · řádky 94-99; [kajovo/core/runs/executor.py](kajovo/core/runs/executor.py) · řádky 291-340; [kajovo/core/runs/contracts.py](kajovo/core/runs/contracts.py) · řádky 28-41,65-116.

transition ukládá lifecycle_status, ale exception větve pro běžné selhání, STOP_REQUESTED, ResponseCancelled i unknown mění pouze status a emitují finální signál. Například chyba API po přechodu REMOTE_WORK zanechá status=failed a lifecycle_status=remote_work, ač existují terminální enumy a povolené přechody. Rozporný forenzní stav, nikoli doložené další odeslání API.

## Kontrola JSON masek a navazujicich kroku

Spustil jsem `.venv\Scripts\python.exe tools\verify_contract_links.py --output %TEMP%\kajovong-contract-audit.json`. Skoncilo to kodem 0, bez chyb. Kontrola nasla a overila 14 provider masek, 16 lokalnich JSON schemat a 7 vazeb mezi fyzickymi schema soubory a runtime schematy. Staticky inventar mel 305 zdrojovych souboru. Zahrnute masky: A0R, A1, A2_SPINE, A2_DETAIL, A2Q, B0R, B1, B2_SPINE, B2_DETAIL, B2Q, FILE_CONTENT_V1, QA_ANSWER_V2, QFILE_PLAN_V1 a TEXT_RESPONSE.

To potvrzuje syntaktickou/struktur?ln? platnost techto schemat, jejich vazbu na runtime kontrakty a AST inventar mist s providerem, ulozistem, soubory a obnovou. Samo o sobe to ale neoveruje vsechny kombinace vystupu jedne masky jako vstupu nasledujiciho kroku. Nebyly generovany pro kazdou masku reprezentativni instance vsech variant (`ready`/`blocked`, volitelne vetve, GENERATE/MODIFY, quality gate, jednotlive akce souboru) ani overena jejich pruchodnost navazujicimi semantickymi validatory. Proto odpoved na pozadavek "vsechny varianty scenaru" je stale **ne**; tenhle rozsah zustava neproveden.

## Revalidace puvodnich nalezu v aktualnim stromu

A3-001 az A3-009 a A3-011 jsou uzavrene podle dnesni implementace. Puvodni texty pod nimi zachycuji historicky duvod, nikoli aktualni chyby:

- A3-001: `persist_evidence` kopiruje rozpoznana kanonicka pole a JSON Schema beze ztrat.
- A3-002: kanonicky WorkOrder odpovidajici schematu se pri ukladani kopiruje bez redakce.
- A3-003: `RunLogger.exception` rediguje data, text vyjimky i traceback.
- A3-004: Git filtr normalizuje velikost pismen cest.
- A3-005: macOS sestaveni pouziva 1024px zdroj pro variantu 512@2x.
- A3-006: obnova nalezene davky zapisuje identitu do orchestrace i pro puvodni cestu submitu.
- A3-007: priprava noveho pokusu po potvrzenem neodeslani obnovi fyzickou vazbu operace.
- A3-008: chyba uklidu legacy registru nezabrani vratit platny klic z keyringu.
- A3-009: vector store se vytvari jen pri zapnutem file search.
- A3-011: chybove vetve prechazeji do terminalniho lifecycle stavu.

A3-010 zustava otevreny. `preparation._request` serializuje `input_value` do textu pozadavku; `_compile_source_attachments` zaroven pridava file/image ID pro zdroje v `_provider_inputs`. U pripravy A0R/B0R/A1/B1 tak muze byt stejny zdroj v inline JSON (`source.segments` nebo `selected_originals`) i jako prime prilozene soubory. Dopadem je duplicitni kontext a vetsi pozadavek; audit neurcuje cenu ani chovani vzdalenych modelu.

## Upřesnění dopadů a omezení nálezů

### A3-001 — příklad legálního výstupu

Pro explicitní masku objektu s povinnou vlastností `type` typu pole řetězců je `{"type":["a"]}` platný výstup. Selhání vzniká až při rekurzivní klasifikaci ukládaných metadat, nikoli při parsování nebo validaci tohoto výstupu. Analogický problém má objektová hodnota `type`. Netýká se automaticky každé masky s union typem: rozpoznaný kořen masky může být zkopírován bez dalšího průchodu.

### A3-002 — stejná příčina zasahuje také obnovované payloady

Výsledkem je rozpor mezi již spočteným hashem a změněnými metadaty. Stejný mechanismus může změnit cesty či účel ve `generate_batch.snapshot.structure`, který není celý chráněn jako kanonické pole, a zneplatnit `snapshot_hash` kontrolovaný v `generate_batch.py:628–629`. Kaskádové hodnoty uložené pod `values` také nejsou obecně chráněné: doménové pole `token` nebo řetězec připomínající klíč se při uložení změní. Obnova `_runtime_evidence` v `cascade_pipeline.py:552–566` vychází z kanonického run_state, takže nezměněná pomocná cache tuto ztrátu nenapraví. Jde o společnou chybu klasifikace kanonických dat versus diagnostiky, nikoli o požadavek ukládat runtime přihlašovací údaje.

### A3-003 a A3-004 — podmínky bezpečnostního dopadu

Únik v A3-003 nastane, pokud text výjimky skutečně obsahuje tajný údaj rozpoznatelný redakcí; audit nečetl skutečné klíče a netvrdí, že již unikly. A3-004 je doložené obejití filtru názvem, nikoli tvrzení, že každý takový soubor obsahuje tajemství. Pravidla ignorování Gitu sama neochrání soubor, který je již sledovaný a vstoupí do kontroly před push.

### A3-005 — rozsah tvrzení

Z kódu plyne chybné rozlišení položky iconsetu. Bez provedení macOS sestavení nelze tvrdit, zda konkrétní verze iconutil odmítne celý build, nebo vznikne pouze nekvalitní či chybějící varianta ikony.

### A3-006 a A3-007 — dvě různé chyby BATCH obnovy

A3-006 vyžaduje neurčitý výsledek prvního POST a následné jednoznačné dohledání dávky podle vstupního souboru a endpointu. A3-007 vyžaduje jednoznačně odmítnuté odeslání automatické následující vlny a opakování dokončení předchozí vlny. Nejde o stejný případ ani o požadavek automaticky opakovat neurčitý placený POST. Ruční opakování s novým číslem pokusu se od druhé cesty liší.

### A3-009 a A3-010 — bez cenových odhadů

A3-009 dokládá zbytečné serverové vytvoření a indexaci při nepoužitém nástroji. A3-010 dokládá souběžné předání stejného obsahu v jednom pracovním požadavku. Netvrdí přesnou cenu, počet účtovaných tokenů ani chybné řetězení přes previous_response_id; takové údaje nebyly měřeny.


## Rozsah dokonceni

Pokracovani proverilo hlavni behove cesty, obnovu, ukladani, prilohy, foto a komiksove workflow, UI Studio a pomocne skripty a nastroje v inventari. Dvacet dva souboru melo oproti prubezne zprave zmeneny otisk a bylo znovu porovnano. Stav "neprecteno" v puvodni tabulce je historicky, nejde o otevreny ukol.

Audit z?st?v? statick?: neprokazuje chov?n? p?i b?hu, spr?vnost v?ech u?ivatelsk?ch sc?n???, dostupnost vzd?len?ch model? ani v?sledek sestaven?. Neprob?hly testy ani placen? vol?n? API. Implementace nebyla m?n?na; upravena byla pouze tato zpr?va.

## Inventář a skutečné pokrytí

Rozsah „celý“ znamená souvislé čtení všech fyzických řádků souboru; neznamená důkaz absence všech chyb. Hash identifikuje auditovaný obsah. Pouhá přítomnost souboru v inventáři neznamená jeho přečtení.

| Soubor | Řádků | Přečtený rozsah | SHA-256 |
| --- | ---: | --- | --- |
| [.editorconfig](.editorconfig) | 16 | celý | `f1998568ae43bea112851234b5ffd98817b776bcc5aa4657298f282745cc70c8` |
| [.gitattributes](.gitattributes) | 5 | celý | `a4b3d2ca3d79cdc5f0dadb628c97d6a4f65f7a43366b19f88346d2462c99fd2c` |
| [.github/workflows/ci.yml](.github/workflows/ci.yml) | 168 | celý | `14ad69408e141e06bbb2360ed86b80c0ec458a3be278af5803c103806692999c` |
| [.github/workflows/contract-links.yml](.github/workflows/contract-links.yml) | 76 | celý | `f2a60854f50ae68856ea00db2d1166cc3bdff0dec20850fca68087276f690e58` |
| [.github/workflows/finalize_nine_hardening.yml](.github/workflows/finalize_nine_hardening.yml) | 138 | celý | `c4c6b3b28e3fd77f57555f5855a744204c14bdecdbb1a57c4590836dd0b306e6` |
| [.github/workflows/r05c_finalize_studio.yml](.github/workflows/r05c_finalize_studio.yml) | 92 | celý | `677578d9774a3767775e8a249ea3a8770fcc3f2803acacd1d3e204f7705d5e06` |
| [.github/workflows/release.yml](.github/workflows/release.yml) | 508 | celý | `b20fdd0380260bc141eabe9a70413a2d0330bc152213e141d56d4ee73b467967` |
| [.gitignore](.gitignore) | 41 | celý | `a0b4c944fe3d5121ad8da0beaaa22c775ecd94518b495e489f3aa4450e89e5ab` |
| [.pre-commit-config.yaml](.pre-commit-config.yaml) | 33 | celý | `4679a511806d47f7ff81213b361b3bc2a5c3d3871aa22507bb67841b5689f9b6` |
| [Build/assets/.gitignore](Build/assets/.gitignore) | 3 | celý | `aae815b9313ef60fb99d51bec324f3de1cea5256d6bbf58a660578b3e2d5815c` |
| [Build/build_macos.sh](Build/build_macos.sh) | 54 | celý | `0499460779cf40fb9b528e832c55a324bd9b85951c7bbdfb5420baa3cfcaa6d4` |
| [Build/build_windows.ps1](Build/build_windows.ps1) | 46 | celý | `cc6e6e68bd070b4f0e2a8622398a35d75280d0b155a7318cabe5922a36398614` |
| [Build/generate_icons.py](Build/generate_icons.py) | 53 | celý | `b62437c21a146a87afa3a2fc5c52b57cad947bacca09b12b3233e66ca56cb958` |
| [coverage-critical.toml](coverage-critical.toml) | 83 | celý | `719861f59964f2ec400010a88e367d1b8c2915eae91f5422307bc2bb73c0b200` |
| [kajovo/__init__.py](kajovo/__init__.py) | 19 | celý | `a64605bf2170a09de6e4f9917ac26fe444609421074cd7e07e8057ebb2d90efd` |
| [kajovo/app/__init__.py](kajovo/app/__init__.py) | 0 | prázdný | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| [kajovo/app/__main__.py](kajovo/app/__main__.py) | 4 | celý | `4ac953d9d43e4ef3a1f1a054d0ceb385a87076d105a99db4166d79426a737ca4` |
| [kajovo/app/main.py](kajovo/app/main.py) | 93 | celý | `d8e40f6c47c156730db7291cebcf2b35b013e25bfc6deaf9033ad45f5d1b9644` |
| [kajovo/comic_layout.py](kajovo/comic_layout.py) | 125 | nepřečteno | `4827b10adc2a15ae884eb5eea94f870d71628047650922831d0ffce77620961d` |
| [kajovo/core/__init__.py](kajovo/core/__init__.py) | 0 | prázdný | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| [kajovo/core/batch_completion.py](kajovo/core/batch_completion.py) | 762 | celý | `99219bf14b88bf900ae1f677ada3ca284e5f40fb24abdfc076d6ffd543954748` |
| [kajovo/core/batch_result.py](kajovo/core/batch_result.py) | 17 | celý | `96a36099070a7f16e4d738739a482f245a791130b7ad59a728d595445fa70d9a` |
| [kajovo/core/batch_submit.py](kajovo/core/batch_submit.py) | 109 | celý | `39b64020558eddeb6cdc7255627933ef7dbd8d99edcdaae27c82fce2d6367c9c` |
| [kajovo/core/cascade_contract.py](kajovo/core/cascade_contract.py) | 538 | celý | `b98f3a14e27c7a541a41385b3e119b0837bee198e391dc56c378156c8c3fde46` |
| [kajovo/core/cascade_log.py](kajovo/core/cascade_log.py) | 14 | celý | `4d608302f57416229fa071b5bd25887de1e9f4f26b2a554697e61ce8a30d4a78` |
| [kajovo/core/cascade_pipeline.py](kajovo/core/cascade_pipeline.py) | 2344 | celý | `7fa6ba5fbb694b8c78ca139d4d3c6eb31d1a1b316514aa6a479408992ff46cc5` |
| [kajovo/core/cascade_production.py](kajovo/core/cascade_production.py) | 186 | celý | `a3df03e254a45080872e2efc8808a069576d8c96518384341ef3cabfb1c6ac5a` |
| [kajovo/core/cascade_types.py](kajovo/core/cascade_types.py) | 448 | celý | `caac328282c4e1e31d804f91ac58e986c866a513ee6c77880e8e2b0019b2910d` |
| [kajovo/core/comic_clone.py](kajovo/core/comic_clone.py) | 107 | nepřečteno | `e3ef16c074f62b71b92c2f35c8179ae471c8545310f9b09b5c879c8b3667fa38` |
| [kajovo/core/comic_service.py](kajovo/core/comic_service.py) | 1698 | nepřečteno | `9fbbe230b4a1e37d891e1ee2be9a51fa5adeb362e708a4de82a0f7fd9d67eebb` |
| [kajovo/core/comic_store.py](kajovo/core/comic_store.py) | 705 | nepřečteno | `e4bd64d0b7c37431d5f059b8fcca840381050d3c4d0330ee28fd4eb4a835dacb` |
| [kajovo/core/comic_types.py](kajovo/core/comic_types.py) | 370 | nepřečteno | `ac90353dd3d628adbe9a28c934681ec0ba45ad406ff30001a537a50496104bbc` |
| [kajovo/core/compat.py](kajovo/core/compat.py) | 41 | celý | `9d25dd50f1a540bb097644f0343394e3ce205c3c84fc6f7d9d8d3842eecb5224` |
| [kajovo/core/config.py](kajovo/core/config.py) | 175 | celý | `816edb768a59fec407bea3bf16960fe85ce31286008dc056784790c847cfc1d9` |
| [kajovo/core/context_compiler.py](kajovo/core/context_compiler.py) | 539 | celý | `d08abb46cedd10ba5415afeafe47d8ff8a0d2f33dc1286a12eda5f7b1080b95d` |
| [kajovo/core/context_limits.py](kajovo/core/context_limits.py) | 272 | celý | `460329cb765779aed39079c5ed3fde03721ea16f3c90958ef0e71418566c9c6c` |
| [kajovo/core/contracts.py](kajovo/core/contracts.py) | 209 | celý | `134e051ab9ff644c37f25ce9e16ecddf32f7eafb6d0f3a0cbdf0ca120d0306d8` |
| [kajovo/core/delivery_preparation.py](kajovo/core/delivery_preparation.py) | 184 | celý | `655cf96ac7dc0911ccf380da3898b9a1a9bf5e6e91394fe3f31d1a8f1c9929d1` |
| [kajovo/core/diagnostics/__init__.py](kajovo/core/diagnostics/__init__.py) | 1 | nepřečteno | `359eee834a628e867f9dbf7d34196d211475b093658f7395206e9f37c9f9bd93` |
| [kajovo/core/diagnostics/ssh.py](kajovo/core/diagnostics/ssh.py) | 116 | celý | `d041c1d1ea839ac48adac2cd9574f0b676935cbb16be989b4df84826a6b7af3e` |
| [kajovo/core/diagnostics/windows.py](kajovo/core/diagnostics/windows.py) | 97 | celý | `3e3bb443183438ee648f5511097ba2a2b34d0a82276cc2d464493ddc1854f04a` |
| [kajovo/core/diagnostics/windows_collect.ps1](kajovo/core/diagnostics/windows_collect.ps1) | 13 | celý | `33e19c3fe5de45f3001f78f9cbde515ede1848e93cf67d6bd413d2ed61a90605` |
| [kajovo/core/filescan.py](kajovo/core/filescan.py) | 155 | celý | `1570deae719d959173e2241a2a00e5cbeee3f7596bb3f08bbd66e3ef11532a5f` |
| [kajovo/core/generate_batch.py](kajovo/core/generate_batch.py) | 2318 | celý | `67b42659e0187d02dff7690b3106af76f97ec8f6360bdea7997389cfef330074` |
| [kajovo/core/image_runtime.py](kajovo/core/image_runtime.py) | 149 | celý | `f50382b6859422eb0ac9fcbb42b1f3fa684b5db303774bef018ada90a1f04682` |
| [kajovo/core/model_capabilities.py](kajovo/core/model_capabilities.py) | 84 | celý | `03206b00f5b12adf120a66d8eb4ef8b406cfa9ff05cf592df30bc0420806da28` |
| [kajovo/core/model_catalog.py](kajovo/core/model_catalog.py) | 101 | celý | `194ca10bc3e6bcbdefc3b9eec92b0ac00969484a26b322dd2199dcacb086376e` |
| [kajovo/core/model_registry.py](kajovo/core/model_registry.py) | 293 | celý | `71a920d9defb7d638473b627a8a0e2653a233d3406905474c66b9dcd558bf6b7` |
| [kajovo/core/notifications.py](kajovo/core/notifications.py) | 52 | celý | `9cfa4023f02ca483a14f9e036efc65afd657e33e06349eae6a21503d631f4bb5` |
| [kajovo/core/openai_client.py](kajovo/core/openai_client.py) | 585 | celý | `8b11b7fedfc602b0ec35e5b5589ddb986925aca75926994e2265789d992b76f8` |
| [kajovo/core/openai_model_matrix.json](kajovo/core/openai_model_matrix.json) | 9318 | nepřečteno | `9b7e6e4a9354b1443717974f88b63b20682a3c962574f385a91fb44c3d9aedc4` |
| [kajovo/core/openai_transport.py](kajovo/core/openai_transport.py) | 425 | celý | `d347b27b602ab0f1cbfc79abdc32f061d0b3a2f196c1127bac5470082fd07a62` |
| [kajovo/core/orchestration/__init__.py](kajovo/core/orchestration/__init__.py) | 1 | nepřečteno | `d5d7c778724efffd6aa05ba415bfab76cdc407cbb492853a0d79b5d0da5f5f4a` |
| [kajovo/core/orchestration/authorization.py](kajovo/core/orchestration/authorization.py) | 168 | celý | `9f527866532c25d35dd80d2811ae3870123ddff1ebb7b0d9a0e28f0c3b64d9ae` |
| [kajovo/core/orchestration/batch_manifest.py](kajovo/core/orchestration/batch_manifest.py) | 185 | celý | `6398051f2f64c76e9bd9dfd9262e6563c4596b6b59d61a19d7cb6104c9555af8` |
| [kajovo/core/orchestration/batch_recovery.py](kajovo/core/orchestration/batch_recovery.py) | 31 | celý | `071014aa6b7975c884e2dc50b7f62a374dcb55935e3dce7888f238c0f9dfd008` |
| [kajovo/core/orchestration/contracts.py](kajovo/core/orchestration/contracts.py) | 76 | celý | `0d828c253359e26238e26cf5ed91b6ebc74ded7d2d95614bdf92325aaa3434a6` |
| [kajovo/core/orchestration/errors.py](kajovo/core/orchestration/errors.py) | 10 | celý | `f3c818de1257a2b0d5db3511b8cb3e6cbd885c9bacad90229b493d5e9449961a` |
| [kajovo/core/orchestration/executor.py](kajovo/core/orchestration/executor.py) | 86 | celý | `8539802b0835b44875c99a09516e715f6641532b9c7a24ea6c7d9941b12c026f` |
| [kajovo/core/orchestration/image_slots.py](kajovo/core/orchestration/image_slots.py) | 127 | celý | `9bae1630fdc2ef7771942832083abce7aa6605544997777f552dcc98842877a0` |
| [kajovo/core/orchestration/manual_resources.py](kajovo/core/orchestration/manual_resources.py) | 136 | celý | `7cd340334f74313feae1d23960b6129aa2ca3c765119ba9b0aa28056ff5f3e49` |
| [kajovo/core/orchestration/policies/images.json](kajovo/core/orchestration/policies/images.json) | 41 | celý | `a0fd2c7143262e44c63edc3fff98a0b1bc97d369ee8258a974e8d72e61fdc3b2` |
| [kajovo/core/orchestration/preparation.py](kajovo/core/orchestration/preparation.py) | 1672 | celý | `e9a836ff0676b10b3cbf69a38424342c31a4f205d56d7d0eb8c75e75a81da3d9` |
| [kajovo/core/orchestration/projection.py](kajovo/core/orchestration/projection.py) | 149 | celý | `557c34d1a6d050466709280349c516722d7790f0e66d4badaf3e00971a24bd4a` |
| [kajovo/core/orchestration/provider_operations.py](kajovo/core/orchestration/provider_operations.py) | 229 | celý | `f24ae050d18e2068e566906b70919c1f9abde5d62ff569c06f44317ad767780e` |
| [kajovo/core/orchestration/publish.py](kajovo/core/orchestration/publish.py) | 608 | celý | `d32ba7aaa400298bb3ee2183083f8e1eb812b429165f474b322481f3bf03597e` |
| [kajovo/core/orchestration/repository.py](kajovo/core/orchestration/repository.py) | 1071 | celý | `5fdcd25fb5d55b1de634d02ff9c0b7b4e465280a3fa6fa3ccc8b0faa7c3537bd` |
| [kajovo/core/orchestration/request_binding.py](kajovo/core/orchestration/request_binding.py) | 36 | celý | `b8a2905ece5567143b44d541cb88f03c8f9e73360dd5c0021d89f31dd8eb98c5` |
| [kajovo/core/orchestration/resource_delivery.py](kajovo/core/orchestration/resource_delivery.py) | 993 | celý | `b5de2a615c3e4c5ee3d7cd909ccfc7df70b77a7fb1fb2162cb19e9f3505a391c` |
| [kajovo/core/orchestration/run_config.py](kajovo/core/orchestration/run_config.py) | 138 | celý | `43cab0f1e5f53ee6f9b39ea0f4040b93d5eda9b4201264198487fbbc7339878b` |
| [kajovo/core/orchestration/source_pack.py](kajovo/core/orchestration/source_pack.py) | 559 | celý | `f6624e51eb641c20ef8e2e14826af8c808cd847c20de23410a687f2a7fe03279` |
| [kajovo/core/orchestration/verification.py](kajovo/core/orchestration/verification.py) | 714 | celý | `ae12ddd8aab5db5f21f8cb5f241506d7346d26267d2a0739f0e649080f3ff774` |
| [kajovo/core/orchestration/waves.py](kajovo/core/orchestration/waves.py) | 99 | celý | `0286043acbef26d2188546e280f317d99be44d9525d28ae86fc3a4f198cd90b8` |
| [kajovo/core/orchestration/work_order.py](kajovo/core/orchestration/work_order.py) | 330 | celý | `b9d5d1944ffcc6b7cf32aa4e50ff0a6de837b6917d2449d83d99bdcc52c10ea7` |
| [kajovo/core/photo_batch.py](kajovo/core/photo_batch.py) | 1212 | nepřečteno | `48ccf36f98343b52ed3d84181b1e00a5edd9257d98fe489cfedcb04d91d8fcd2` |
| [kajovo/core/photo_prompt.py](kajovo/core/photo_prompt.py) | 273 | nepřečteno | `15a9953139925001f7615358d53266574128e21e6dc825b8e36334c59fcc90bf` |
| [kajovo/core/photo_templates.py](kajovo/core/photo_templates.py) | 232 | nepřečteno | `fc78c66bbe50236e04fccc6d03e4c58b190d429343a9953eab81b69e57ad5c95` |
| [kajovo/core/pipeline.py](kajovo/core/pipeline.py) | 6 | celý | `f33aebf82925ba77c312fd3f923c9bf0e51dade3704bc0862c27e756a264f468` |
| [kajovo/core/progress.py](kajovo/core/progress.py) | 113 | nepřečteno | `3888e49ab2269d8f2d40d76bd10d436fdc43f47f85d89f407406a228dbb4d133` |
| [kajovo/core/progress_display.py](kajovo/core/progress_display.py) | 174 | nepřečteno | `63c5babae58e43e6cfe93a8a3117f19d092c51032185d340abb864d3017e36d8` |
| [kajovo/core/project_git.py](kajovo/core/project_git.py) | 360 | celý | `6ddbd1c8285a944a5adab099f4040279df219dc0988d9ec83f0642d994ad8a3e` |
| [kajovo/core/recoverable_artifacts.py](kajovo/core/recoverable_artifacts.py) | 100 | celý | `f341305884c435ad5dba937527e77f321dd9072ef45ad8e1dd11d0fa230ed31a` |
| [kajovo/core/recovery.py](kajovo/core/recovery.py) | 168 | nepřečteno | `a6ce2f577fba47af33754e0d4e49a89adf63dc9a7bbed0c613c2392208a4795d` |
| [kajovo/core/repair_execution.py](kajovo/core/repair_execution.py) | 119 | nepřečteno | `86ad267d28baf653cf4c282ba5bdc1fdbc5aaf54c155c281e0b81bedefce8484` |
| [kajovo/core/request_rules.py](kajovo/core/request_rules.py) | 253 | celý | `19a1d7dc8c9545499990af2d97ed961c70f4e2ef0c8ad4316889a086e854b912` |
| [kajovo/core/requirements.py](kajovo/core/requirements.py) | 477 | nepřečteno | `0c3177f916d3facda269f2302715d065ca34f9a72cb3b4860ede54bd9330d7c7` |
| [kajovo/core/resources.py](kajovo/core/resources.py) | 12 | celý | `6f0f235866b7b0ec245637ca05abfd5ba032393112d366e9b969eb6b9d948907` |
| [kajovo/core/response_journal.py](kajovo/core/response_journal.py) | 365 | celý | `ec356004e259b62918b72ebc0b54744fcfb5966e57efe63db2d5b9cd3a2a4780` |
| [kajovo/core/response_policy.py](kajovo/core/response_policy.py) | 67 | celý | `817b0aa4bbaa3eef334f7fe73c3ce2780414209e791547f6929bbc815a2983a1` |
| [kajovo/core/retry.py](kajovo/core/retry.py) | 67 | nepřečteno | `1bebad932ba20e38310a9ade4570972765cd796346c5f93306dfc749e5adc284` |
| [kajovo/core/run_bundle.py](kajovo/core/run_bundle.py) | 1587 | celý | `42f01b33400251e22f15e2f8754876eb8f89df3d841c339612bfe2cc000a4e59` |
| [kajovo/core/runlog.py](kajovo/core/runlog.py) | 839 | celý | `8178eedb4d9be76b7c3065b7a36cea66ac52fde261fbaad3d6cf25774228d15f` |
| [kajovo/core/runs/__init__.py](kajovo/core/runs/__init__.py) | 37 | celý | `40afce9c7a0df4d41f0c928e8216255863a5de5965554aa1fca31864c1996892` |
| [kajovo/core/runs/attachments.py](kajovo/core/runs/attachments.py) | 605 | celý | `8bd26e50108a663925be189cc48563b211884c8fd9a9f66fe7673013a279de00` |
| [kajovo/core/runs/batch_execution.py](kajovo/core/runs/batch_execution.py) | 265 | celý | `2f6618aab762d9cc65fa9d79281ea81256bfaf9689a6fa837eae12857b9c6061` |
| [kajovo/core/runs/cancellation.py](kajovo/core/runs/cancellation.py) | 24 | celý | `c061fb6ef5494c91653e5077371099f6d2e7ac1cc21c06a4792997a9f0fb490a` |
| [kajovo/core/runs/config.py](kajovo/core/runs/config.py) | 81 | celý | `4fb7b48adae99437d1985b4378e14933b891f21fd2c61c8c5134d17ecd4dbf5a` |
| [kajovo/core/runs/context.py](kajovo/core/runs/context.py) | 229 | celý | `5f49fa15400fe38eeffbee3aaedc39ac70dfad330f3e9bd62985cd56872bea4c` |
| [kajovo/core/runs/contracts.py](kajovo/core/runs/contracts.py) | 190 | celý | `7b9730db3901df76744c873d9b828bd39d3ee4e0dc91616894141a5cfff534cc` |
| [kajovo/core/runs/delivery.py](kajovo/core/runs/delivery.py) | 295 | celý | `3043f695d514efb2ee8162ee1d7f28d32064426d6e850a8c7f6a8e6673e0ad71` |
| [kajovo/core/runs/delivery_execution.py](kajovo/core/runs/delivery_execution.py) | 151 | celý | `f74a4902fa816a92c8aab65963abd51100b29d26f95277ddaef957ec2f5a3aab` |
| [kajovo/core/runs/diagnostics.py](kajovo/core/runs/diagnostics.py) | 211 | celý | `5e36679886b61217454119fb640a8e9d03aa73c962092fdd747d49b82c17fbf2` |
| [kajovo/core/runs/executor.py](kajovo/core/runs/executor.py) | 362 | celý | `bcf64d64b651a947532f880d93c9a568c6249a6cf6f1d54b632e59731b3d9a22` |
| [kajovo/core/runs/file_execution.py](kajovo/core/runs/file_execution.py) | 427 | celý | `4132a095db747902d3a2bb58d5e536074f9d66b5b7287b89d34fa551f7dc914f` |
| [kajovo/core/runs/generate.py](kajovo/core/runs/generate.py) | 497 | celý | `075638df06b9c00aa95bcc742bd525a76510e8ae78a9749c384e6130323ca50e` |
| [kajovo/core/runs/live_continuation.py](kajovo/core/runs/live_continuation.py) | 347 | celý | `ac5f011fb2953a0670c19fad0668af523c7644fa6d22a9d450e9965d25b80ee7` |
| [kajovo/core/runs/locking.py](kajovo/core/runs/locking.py) | 80 | celý | `d0a8d4925521c2f446888667f0a799b7d7447c7dd8b266f3aa089c2890638a9c` |
| [kajovo/core/runs/modify.py](kajovo/core/runs/modify.py) | 392 | celý | `0186a217f7341167ff4af67daa7eaea73768125b1cff8db08aaa86b92a19bae4` |
| [kajovo/core/runs/observability.py](kajovo/core/runs/observability.py) | 47 | celý | `b0cc4c7c04854c1188128122c47f2500e7c39c8314c77ac10a514b9bd9dff18b` |
| [kajovo/core/runs/polling.py](kajovo/core/runs/polling.py) | 139 | celý | `4888191bf9c9e7e4768bdf7188ceb083c8ccb72c0575c4567029a8153236ca3c` |
| [kajovo/core/runs/ports.py](kajovo/core/runs/ports.py) | 28 | celý | `89bc26a3558aa1465d33c1b552038b291c8184a10bd8427c7b12f558e26218ca` |
| [kajovo/core/runs/qa.py](kajovo/core/runs/qa.py) | 171 | celý | `8674a84f2854bfbf69cd5295e90bac1cc2828033dfe58a349a9397e42c354a5f` |
| [kajovo/core/runs/qfile.py](kajovo/core/runs/qfile.py) | 353 | celý | `5f244b610737fff83f11a0469e1bdfc5d5f23e1424f76d0ed97ca391641c3bcf` |
| [kajovo/core/runs/recovery.py](kajovo/core/runs/recovery.py) | 176 | celý | `3fb898f9e8a06ef871fb61500ac11946a42841b54c985591b0ea54c8d4d26c5e` |
| [kajovo/core/runs/response_execution.py](kajovo/core/runs/response_execution.py) | 374 | celý | `5e9ade29362be114c38bc5596ebf6992a40dda127276a12e015c966c2cd5afbd` |
| [kajovo/core/safe_config.py](kajovo/core/safe_config.py) | 143 | celý | `108499fb77d0669c3e71e40ebf972d4765839aeea20258ade39c57042c3faf22` |
| [kajovo/core/secret_store.py](kajovo/core/secret_store.py) | 258 | celý | `dcd2ab4e468ed8cf79557fb31e9c09f9157183a5cefa44d08dd74d83116c0145` |
| [kajovo/core/structured_output.py](kajovo/core/structured_output.py) | 376 | celý | `7cfaea53ddb5e09bbb6798c0af3225843f159fb3aa7937de1a72fdb20d8c6991` |
| [kajovo/core/user_errors.py](kajovo/core/user_errors.py) | 170 | nepřečteno | `54996fc8f889129e7a257d0d42840b30793525b051a5beb172d2113518cd5e2e` |
| [kajovo/core/utils.py](kajovo/core/utils.py) | 92 | celý | `7a74d977df71239e8cb2009a33004139dde0a588df8d7ed7bb2e3fcd23d101fa` |
| [kajovo/progress_ui.py](kajovo/progress_ui.py) | 706 | nepřečteno | `5be3b3edbd6e3f8f3b4632083bed808d9491c3a463d7cc7f672e0bd30fee242d` |
| [kajovo/studio/__init__.py](kajovo/studio/__init__.py) | 1 | nepřečteno | `10d8c7e3c3ef9d7d0342afccbf097786db941c375af57f09a654895a605129bc` |
| [kajovo/studio/application.py](kajovo/studio/application.py) | 357 | nepřečteno | `b4e409aa58463090ca22f87b2cbc67d28262b0c4f57528995f2b4b8864313fe5` |
| [kajovo/studio/batches.py](kajovo/studio/batches.py) | 222 | nepřečteno | `33beca8d80217bbef74eb5702de73e25e164002b3d2d495b335c8d1109b5e7ae` |
| [kajovo/studio/cascade_items.py](kajovo/studio/cascade_items.py) | 109 | nepřečteno | `622c771250a6d99dafde2e52a2b9dbc98a7515df25f13abb769c490f68662fe6` |
| [kajovo/studio/cascades.py](kajovo/studio/cascades.py) | 386 | nepřečteno | `1e458686e5d499bed1cad65434edea5b8d458308932025fbdc8de9c0a07f98be` |
| [kajovo/studio/comic_editor.py](kajovo/studio/comic_editor.py) | 330 | nepřečteno | `d163f8e5d5bb091490c67ae293c4ed2c79b6ba897d5f43d11edbeb567a673671` |
| [kajovo/studio/comics.py](kajovo/studio/comics.py) | 983 | nepřečteno | `aac6278b930f656e53e1c9a5ba641b6fde63093dd9b6363030c05054710579db` |
| [kajovo/studio/components.py](kajovo/studio/components.py) | 351 | nepřečteno | `30960cb9305458771f8f4a6a3e43a9058dc8476e18709cc4ff2e71dbd58bbd42` |
| [kajovo/studio/context.py](kajovo/studio/context.py) | 113 | nepřečteno | `a60cd1a402f9171b2ea7f969d54060041ce75ee684808cfd1e37c887a621d233` |
| [kajovo/studio/converter.py](kajovo/studio/converter.py) | 147 | celý | `42b6d55acce31e18572f820f0f3424d1357b1ce7f4fbe075cdc91603bf3bb0b1` |
| [kajovo/studio/evidence.py](kajovo/studio/evidence.py) | 120 | nepřečteno | `f61750c4139e91a1d29a0292aad593f439dd47dbd963d4102f6ee08fe00a2304` |
| [kajovo/studio/history.py](kajovo/studio/history.py) | 668 | nepřečteno | `c0fb296ffdaed45de92096b38e825d831d4322477551f36811eab95bec768bde` |
| [kajovo/studio/history_artifacts.py](kajovo/studio/history_artifacts.py) | 527 | nepřečteno | `4024b1efbe6c630b8008e05c0dfb582e8e39bc954cc8631858eda1294f99a777` |
| [kajovo/studio/history_cascade.py](kajovo/studio/history_cascade.py) | 103 | nepřečteno | `9a6afa01338e3270a16656a65c6d95784ab1a1f2a08a9fa60d607795ebef1408` |
| [kajovo/studio/history_composer.py](kajovo/studio/history_composer.py) | 148 | nepřečteno | `2575dbfd873e7ae5b9c2ef5078aee364b78656bfecc531e77662ccfbfb6e81cb` |
| [kajovo/studio/history_data.py](kajovo/studio/history_data.py) | 113 | nepřečteno | `13c4bd8c1c0095b9c808ceec353ff22679fd233b813373bda0177cb9185cdb48` |
| [kajovo/studio/history_details.py](kajovo/studio/history_details.py) | 535 | nepřečteno | `ea386ce04174b05e7981b8272ccf35500bcf9bd5f78c419240641022a62f218f` |
| [kajovo/studio/history_launcher.py](kajovo/studio/history_launcher.py) | 365 | nepřečteno | `204039634118c9dd6b0fe12fd771a9cc61d303ace9a8ff88853e8965a50094ec` |
| [kajovo/studio/history_models.py](kajovo/studio/history_models.py) | 299 | nepřečteno | `cdd0e659acbc98c4da9438cd3deb6cdbd412e705232981559e7975ec2834df3c` |
| [kajovo/studio/history_overview.py](kajovo/studio/history_overview.py) | 136 | nepřečteno | `83234214c65cad265defe9d20f3072d7a896b877780dfa1356f6f6f16427973f` |
| [kajovo/studio/history_policy.py](kajovo/studio/history_policy.py) | 165 | nepřečteno | `cccb493fc06f8881a534491224bad2249f8759baa0285a2249560ab7b44ce5bb` |
| [kajovo/studio/history_state.py](kajovo/studio/history_state.py) | 72 | nepřečteno | `be0221df45a67ee9c018020cc4bf77665f0b2617768df54a63270cc77434f499` |
| [kajovo/studio/history_timeline.py](kajovo/studio/history_timeline.py) | 284 | nepřečteno | `82eea99b7ed257098fbabf9f997e1de380310cfce96b7bff69d1e5c8d027c8ae` |
| [kajovo/studio/model_selection.py](kajovo/studio/model_selection.py) | 20 | nepřečteno | `ccb81c683ed0c00ad81e82ea48f99f6274c47648facb0be18b2e7ed513387b07` |
| [kajovo/studio/operations.py](kajovo/studio/operations.py) | 541 | nepřečteno | `d07d83884f27866ea6b634170e53af54bf2ae554e5b1cc81cabdabcfe0e9d485` |
| [kajovo/studio/photos.py](kajovo/studio/photos.py) | 497 | nepřečteno | `5a2c3d20554292169921a5b417bbbad24b8fc8bb44efc5ef1d4b2b29afd3a34b` |
| [kajovo/studio/resources.py](kajovo/studio/resources.py) | 309 | nepřečteno | `2ee8a50e600e1e745b0c6d3531b5a496c9c46da45bbeda68c5470bfadb4afcde` |
| [kajovo/studio/settings.py](kajovo/studio/settings.py) | 197 | nepřečteno | `69ed27a064a9acc411eaca5e22ade7c636ccf8d261352a9a03b9b3e9bf75d2e0` |
| [kajovo/studio/ui_audit.py](kajovo/studio/ui_audit.py) | 485 | nepřečteno | `82a18bab8266b184f1470a56f7cfd8b8b95c58dc25a49d18d858379275854a28` |
| [kajovo/studio/versions.py](kajovo/studio/versions.py) | 187 | nepřečteno | `914999b048e7adb503a94ba12e2d0c87dcf6f7b963324757205bc37a43a7c263` |
| [kajovo/studio/workbench.py](kajovo/studio/workbench.py) | 524 | nepřečteno | `da31ca1c0ee131dcf224b442b73dc34a63cea630a5aef8668dc70107fa70a7c5` |
| [kajovo/studio/workers/__init__.py](kajovo/studio/workers/__init__.py) | 5 | nepřečteno | `b1f5573829cd73abb2240b39384538ec4617bb2f751574621f671ac59e7e640e` |
| [kajovo/studio/workers/cascade_worker.py](kajovo/studio/workers/cascade_worker.py) | 63 | nepřečteno | `93a451411c9bc90a86f40a1f0e95e358806d7284bb254de29b99d9a1f39de802` |
| [kajovo/studio/workers/run_worker.py](kajovo/studio/workers/run_worker.py) | 73 | nepřečteno | `13be9a8ca93762b0e0112a412f52a8df1f963cc55ae98e7f9d43d66daffdaefa` |
| [kajovo_settings.example.json](kajovo_settings.example.json) | 61 | celý | `c63a765ced55477ad94fc39554129bc48929898a9522d35ba0e716bcdf6bf386` |
| [kajovong/__init__.py](kajovong/__init__.py) | 4 | celý | `1c3aee02d865f149623b0aedc3b4a17a1b6e45d9e023195e1e222b07c62c7bd4` |
| [kajovong/__main__.py](kajovong/__main__.py) | 9 | celý | `bf3cfbe46e81f32afe6e82f05aa77257214a54c31a39237a2c28fe3b1a0d0db6` |
| [pyproject.toml](pyproject.toml) | 73 | celý | `ffca293fa53003c83b77780bd72c45860715a3e5f99f3920951b4adf470d5dbc` |
| [requirements.txt](requirements.txt) | 1 | celý | `0cac0e472eba3359aa79e9674ff11a5fd0484b9c465fc1cb774c30938edcc5d7` |
| [requirements/constraints.txt](requirements/constraints.txt) | 86 | celý | `cf9ee2fda19c36ec41ec8802ff9eca34834ee08e1da65e4077f0de438f45d8a4` |
| [requirements-dev.txt](requirements-dev.txt) | 1 | celý | `9d6d2c2e18451cd8eb86a739cbcb1664e4342ef4ae478266a3c333dd4f73674a` |
| [resources/orchestration/contracts/local/BATCH_MANIFEST_V4.schema.json](resources/orchestration/contracts/local/BATCH_MANIFEST_V4.schema.json) | 148 | celý | `08a6c9173270e4b4bae4cd2044ebf86a4c8b98750b58021d0f652a85ab1a0bec` |
| [resources/orchestration/contracts/local/MANUAL_RESOURCE_BINDINGS_V1.schema.json](resources/orchestration/contracts/local/MANUAL_RESOURCE_BINDINGS_V1.schema.json) | 24 | celý | `f76484077566ee6cd217f2a2d82a200b81e8a7367b4f32158ffa5aa96e33b8fc` |
| [resources/orchestration/contracts/local/RUN_CONFIG_V2.schema.json](resources/orchestration/contracts/local/RUN_CONFIG_V2.schema.json) | 28 | celý | `b5e5c992d273956c2e5e49df4ec4cfc671d86dd6aa7945935ae3263178fc15e4` |
| [resources/orchestration/contracts/local/VERIFICATION_REPORT_V3.schema.json](resources/orchestration/contracts/local/VERIFICATION_REPORT_V3.schema.json) | 192 | celý | `9b67f41cc4f76e01e971d3d72276e96f3bb33a64819ad9ba20d6afd230500bb6` |
| [resources/orchestration/contracts/local/WORK_ORDER_V2.schema.json](resources/orchestration/contracts/local/WORK_ORDER_V2.schema.json) | 123 | celý | `ffb735667173df57186929874217d0110798c369bf08668a767e70d7a00f5fe1` |
| [resources/orchestration/contracts/local/WORK_ORDER_V3.schema.json](resources/orchestration/contracts/local/WORK_ORDER_V3.schema.json) | 130 | celý | `456af3c08f75fb1f7713e3f430136511e0d59f35a46250525a5acb47643ddfd0` |
| [resources/orchestration/contracts/wire/FILE_CONTENT_V1.schema.json](resources/orchestration/contracts/wire/FILE_CONTENT_V1.schema.json) | 8 | celý | `f539c75af081036ba4ba54fb044b1ae714596ca8348e1879e8b96ef98bb64edc` |
| [scripts/accept_comic.py](scripts/accept_comic.py) | 102 | nepřečteno | `92fdb9ef2946f4d2f12117ecf20bce15aaba356c23ea1b15517b50d35cfd170b` |
| [scripts/analyze_id92_context.py](scripts/analyze_id92_context.py) | 164 | nepřečteno | `4598fb13f898cf7c2a5893c4aa4f16e6f3f332af63c48e14c7c0cad9d01760ac` |
| [scripts/audit_studio.py](scripts/audit_studio.py) | 57 | nepřečteno | `83a244cfeedf8b5269daac4ec69a13d1f1979664b7477d4bd5a076cd6b34bda3` |
| [scripts/audit_ui.py](scripts/audit_ui.py) | 42 | nepřečteno | `8053c276c72c5cdb53810517cb55347d37015c26e5625bb0c0a9f7181f4bd719` |
| [scripts/benchmark_history.py](scripts/benchmark_history.py) | 54 | nepřečteno | `f4ea9b9a790c8102f4fd1fdf096f988437e6879ee8209293b54b7d71c07097f5` |
| [scripts/bootstrap_windows.ps1](scripts/bootstrap_windows.ps1) | 60 | celý | `12fb2a2902a847d885322f2c2dd3c0c6be1a8066e67a22f5e5f9eb45e8441711` |
| [scripts/build_ui_gallery.py](scripts/build_ui_gallery.py) | 47 | nepřečteno | `2dc975c64d7231c6f59b0473d13cf2bd81b505a4947e960769f3e6e044943dc9` |
| [scripts/export_model_matrix.py](scripts/export_model_matrix.py) | 68 | nepřečteno | `2fdc8f1086f4dd0218ccf3edfc91a0bd7ca2429984f3f88454b28bf42a31def4` |
| [scripts/export_ui_validation.py](scripts/export_ui_validation.py) | 42 | nepřečteno | `8a6fd3a5d34718469e61b919f0e8ef68d13c6eadc404970d3aa649f179b12514` |
| [scripts/install.bat](scripts/install.bat) | 4 | celý | `520cce72d552af9cb50b0ba40bf7401d41c561c43b48c1780d4934e78b6b5013` |
| [scripts/install.ps1](scripts/install.ps1) | 2 | celý | `3d15292360ff68362d899dad71d4df1c33298dbb41e8f0029c94f9bd37847044` |
| [scripts/live_acceptance.py](scripts/live_acceptance.py) | 770 | nepřečteno | `29b5c4807bef5c572beda996aa14a60e610c825cd9924363a0471cd02c217ef4` |
| [scripts/measure_request_context.py](scripts/measure_request_context.py) | 33 | nepřečteno | `fd42cce33d414a95222a5aef8932417d24aa0f07f86e493862dac5078ec8112d` |
| [scripts/render_studio.py](scripts/render_studio.py) | 490 | nepřečteno | `b7192e6bf08c735276cfef833703d13e2eb656276994ff0d34c0e25821c61cd7` |
| [scripts/render_ui.py](scripts/render_ui.py) | 16 | nepřečteno | `f8eab889129bedf14f41acd44ce92341ba93ad5351768c871fc477faec9ceac5` |
| [scripts/run.bat](scripts/run.bat) | 10 | celý | `e333526e2b35c45f7c995e5cc125a1b55442fed9187b3b4ea9ca3e727a809655` |
| [scripts/run.ps1](scripts/run.ps1) | 5 | celý | `5da73cf21ef81702f670e7976b57f34eff4cbb6ffc871da77dc6600625423b9d` |
| [scripts/start.ps1](scripts/start.ps1) | 51 | celý | `d14baa824c219e17c68ae1e17883e65bcefd8fdbc1c921adde7b8badd0dd64e3` |
| [scripts/start_app.py](scripts/start_app.py) | 71 | celý | `1a79f204b7cf0d2fe3d520d1521ef2f701de6ab44748d55154c0312a0b4050f4` |
| [scripts/verify_request_matrix.py](scripts/verify_request_matrix.py) | 59 | nepřečteno | `147b441437b5c8e027873c8b40e82f5288dd79ab82258ed3b22a94e04e194934` |
| [start.bat](start.bat) | 12 | celý | `5c30abb0e147b171687d18ee3631d1ad4ada7556111e414833b93139caf892f6` |
| [tools/_finalize_lfs_checkout.py](tools/_finalize_lfs_checkout.py) | 55 | nepřečteno | `50ed1c2f155a33c6448f1799d61838476e767178fe0ce70a51aa9ee1bc540110` |
| [tools/_finalize_nine_hardening.py](tools/_finalize_nine_hardening.py) | 326 | nepřečteno | `cf3a14a69982727ac28fe6661b1c2db49b504a9e76a770255314fe9e6065a9fa` |
| [tools/_finalize_nine_postfix.py](tools/_finalize_nine_postfix.py) | 33 | nepřečteno | `822da1f3ef6732dcb23ee589efe92f8994705bc5742c067d6bb016330c0386d7` |
| [tools/_fix_cascade_type_shadow.py](tools/_fix_cascade_type_shadow.py) | 32 | nepřečteno | `f36d17f2da6d5e31d13d80cd68f8cf9294853bb0e2069be252634b8a077201df` |
| [tools/_r05c_finalize_studio.py](tools/_r05c_finalize_studio.py) | 642 | nepřečteno | `fc56c9c6eecb884e1280bc240dfeab1cac843b83883edf4ed8f1f67f2871f144` |
| [tools/_r05c_postfix.py](tools/_r05c_postfix.py) | 52 | nepřečteno | `b3b45e576fcd15831a1e75de2294a66a6551eb99ab1de259d91dc85dd1ccd849` |
| [tools/compatibility_smoke.py](tools/compatibility_smoke.py) | 29 | nepřečteno | `3140aebaf64b272e7fc40813cee18e8dadaf42486a4887a9cf1640d95d6b9ac8` |
| [tools/check_critical_coverage.py](tools/check_critical_coverage.py) | 89 | nepřečteno | `07a778cd09b333e2331a41bfb4977a34900cde4b533629928598717dfe7337e2` |
| [tools/verify_contract_links.py](tools/verify_contract_links.py) | 306 | nepřečteno | `cb7031a96190c00539c0409ff4a9148434f9da54c99116afe1beb82e3a05bc41` |
| [tools/verify_dependency_contract.py](tools/verify_dependency_contract.py) | 92 | celý | `14b7e07a7a9a5971d412f50b8eec9cdd6bf8a96a28b45b651f0e2a5a2b9d2c5e` |
| [tools/write_build_metadata.py](tools/write_build_metadata.py) | 63 | celý | `e3da52c60d83a827b8015b37bf2231e69c9c527921d451e9db2beefbf4f00ea4` |
| [utf8nobom/__init__.py](utf8nobom/__init__.py) | 5 | celý | `ba81e66f4e75c06cd7c53022498e8bd0d63734b23ec25daa614ba3231a498159` |
| [utf8nobom/app.py](utf8nobom/app.py) | 538 | celý | `4a7c44d024d4aa577fe2ed485fcbededf4a95245127ca674c5e7c2a6b79976a8` |
| [utf8nobom/py.py](utf8nobom/py.py) | 7 | celý | `99c50dec10f71336df0800b215bc3662775dee840e5cdc4a421b1cb992789fe2` |
