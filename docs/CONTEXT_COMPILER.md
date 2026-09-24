# Pracovní kontext souboru

`context_compiler.py` sestavuje FileContext deterministicky z kanonické přípravy V2 a grafu V3 (SPINE a FILE_SPEC). Nevolá model, nevyhledává podle slov ani přípon a neořezává text. Legacy kontrakty slouží pro čtení původní evidence; nový request vyžaduje úplnou přípravu.

## Kontrakty a původ

`spine.obligation_owners` přiřazuje globálním povinnostem konkrétní vlastníky. Requirements a komponenty plánu mají explicitní ID a vazby souborů. Chybějící vlastník nebo neznámá cesta blokují přípravu; compiler působnost nevymýšlí.

`spine.interfaces` obsahuje stabilní identity a přesný vstupní, výstupní, chybový a životní kontrakt. Provider i consumer se vážou na stejnou definici. Chybějící rozhraní nebo provider blokuje přípravu. Kontrola vazeb není typovým překladačem libovolného jazyka ani důkazem správnosti slovní definice.

`file_specs` obsahují relevantní facets, například persistence, transakce, serializaci, concurrency, security či framework. Facet uvádí definici a zdrojové odkazy. `required_facets` stanovuje SPINE; chybějící deklarovaný detail blokuje přípravu. Kritická otázka z DETAIL se předá uživateli před A3/B3 i ve Standard režimu. Maximum Quality navíc zachovává modelovou kontrolu přípravy.

FileContext obsahuje cílový soubor, jeho implementační kontrakt, relevantní requirements, architekturu, povinnosti s působností, přesná sdílená rozhraní, přímé dependency kontrakty a případné původní zdroje. B3 zachovává úplný původní cíl a přímé zdrojové závislosti; neposílá celý IN. Každá složka má hash a dohledatelný selector ve `context_provenance`. Detailní důvod globální působnosti zůstává přímo u povinnosti.

## Hashování a obnova

Obálka odděluje `source_snapshot_hash`, `file_context_hash`, `contract_hash` a `dependency_hashes`. Hash pracovního kontextu nezahrnuje auditní hash, takže změna nesouvisejícího zdroje nemění pracovní identitu consumeru. `ContextCompiler.invalidated` porovnává pracovní kontexty a rozšiřuje změny na tranzitivní konzumenty. U MODIFY přijímá také přesné původní obsahy obou verzí.

Nový manifest má verzi 3 a `compiler_revision: 2`. Snapshot se ukládá jednou; řádky obsahují FileContext bez globálního snapshotu, tools a návaznosti. Nové requesty se překompilují proti snapshotu. Archivovaný import kontroluje zmrazený kontext, request, WorkOrder a hashe, ale nepřepisuje je současným compilerem. Původní evidence se nemigruje změnou obsahu. Snapshot zahrnuje přesné zdrojové segmenty a MODIFY závazky zachování a migrací; zachované obsahové závislosti pocházejí z hashově ověřeného archivu.

`recoverable_artifacts.py` ukládá přesný serializovaný obsah jako obsahově adresovanou obnovitelnou evidenci. Současný `RunLogger` a Run Bundle uchovávají důkazní obsah rovněž bezeztrátově; nová evidence se obsahově nerediguje ani nemaskuje. Obsahově adresované soubory vznikají výhradně exkluzivním vytvořením, při čtení se ověřuje SHA-256 a poškozený obsah se nenahrazuje jiným logem. Index vybírá aktuální artefakt, staré objekty se nemažou. Na POSIX se používají režimy 0700/0600; na Windows zůstává přístup závislý na ACL zvoleného LOG adresáře. Nejde o šifrování ani bezpečnostní hranici vůči uživateli s přístupem k tomuto adresáři.

`RunLogger.find_json` preferuje přesný artefakt. Obnova přípravy a Batch načítá kanonická stavová pole přes `load_run_state`. Historické legacy běhy, jejichž starší verze obsah dříve redigovala a nemají přesný artefakt, se zpětně neopravují odhadem. Response journal zachovává request hash, response ID i neznámý submit; import failure nevyvolává generování.

## Technické limity a směrování

Stejná měřicí vrstva chrání globální přípravu i souborové requesty. `context_limits.py` měří instrukce, input, schema a tools odděleně; FileContext má další rozpad po složkách. Lokální odhad je UTF-8 bajty / 3 doplněný konzervativní bajtovou horní mezí. Není to přesný tokenizer a neslouží k finančnímu rozhodování.

`preparation_measurement` a `checked_measurement` validují skutečné technické capabilities z modelové registry: `context_window`, případný samostatný `max_input_tokens`, provider `max_output_tokens`, podporované parametry a zákaz truncation. Pro přílohy, vnější historii a jiné lokálně neurčitelné vstupy se používá [ne-generativní počítání tokenů](https://developers.openai.com/api/docs/guides/token-counting). Výsledek musí odpovídat hashi přesného payloadu; neplatné nebo nedostupné měření blokuje generativní submit. Budoucí retrieval zůstává explicitně nejistý.

U souborů se reasoning effort volí podle složitosti kontraktu v rámci podporovaných capabilities modelu. Maximum Quality zachovává nejvyšší podporovanou úroveň. Model zvolený uživatelem se automaticky nesnižuje. Výstupní limit je odvozen z očekávaného viditelného výstupu, reasoning rezervy a obálky, ale nikdy nesmí překročit provider capability. Pokud se request nevejde do technického okna modelu, runtime jej neposílá.

KájovoNG neimplementuje cenový engine ani runtime finanční budget. Hospodárnost je řešena návrhem workflow a provozním rozhodnutím uživatele. Runtime validuje pouze technické a kontraktní limity API. Levnější model, menší prompty, Batch nebo minimální testovací workload jsou vývojová/provozní rozhodnutí mimo runtime kontrakty.

LIVE pokračování a opravy používají stejnou technickou kontrolu. Raw provider `usage` se archivuje beze změny jako telemetrie. Neprovádí se nad ním peněžní výpočet, model se nefiltruje podle lokálního ceníku a neexistuje finanční gate.

## Závislosti, pokračování a hranice ověření

Závislosti typu `contract` sdílejí přesná rozhraní; `verified_content` čekají na obsah providera. BATCH scheduler posílá připravenou dependency-wave a po jejím importu může sestavit další. Cyklická závislost požadující dosud nevytvořený obsah je neproveditelná a příprava ji odmítne. Číslo vlny se přebírá ze stejného zdrojového manifestu do transportní evidence V4.

Compiler přijímá dependency artefakt se shodným kontraktem, SHA-256 obsahu a stavem `verified`. Tento stav značí ověřenou integritu a návaznost evidence, nikoli funkční či integrační test produktu. Dodaný produkt zůstává `files_complete_unverified`; runtime nezavádí nové produktové testovací brány.

LIVE i BATCH předává každý soubor v samostatném requestu bez historie přípravy a bez návaznosti na předchozí soubor. `FILE_CONTENT_V1` vrací celý obsah. Neúplná odpověď se nespojuje naslepo ani nevydává za hotový soubor. Explicitní návaznost uživatelské konverzace je jiný kontrakt než automatické řetězení výrobních kroků.

## Evidence a UI

GENERATE/MODIFY uchovává identitu requestu, fázi, model, effort, technické input/output limity, varování, blokace a raw provider usage v běžné orchestration/Run Bundle evidenci. Samostatný cenový report se nevytváří. Opakovaný import téže provider položky je idempotentní podle provider identity.

Progress UI může ukázat velikost vstupu, model, reasoning, technický výstupní limit a počet položek Batch. Nenabízí finanční limit, ceníkovou politiku ani zacházení s neznámou cenou.
