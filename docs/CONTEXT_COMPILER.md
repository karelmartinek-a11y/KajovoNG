# Pracovní kontext souboru

`context_compiler.py` sestavuje FileContext deterministicky z kanonické přípravy. Nevolá model, nevyhledává podle slov ani přípon a neořezává text. Příprava nových A2/B2 a A2Q/B2Q vyžaduje `implementation.version: 1`. Starý kontrakt zůstává validní pro čtení původní evidence, ale nemůže vytvořit nový souborový request bez implementačních údajů.

## Kontrakty a původ

`implementation.scopes` přiřazuje každé globální povinnosti konkrétní cesty a důvod. Atomem je položka seznamu nebo neprázdný objekt/skalar v requirements, plánu či pravidlech struktury. Requirement objekty s ID a položky architektury mají samostatné vazby souborů. Prázdná působnost, chybějící položka nebo neznámá cesta blokují přípravu; compiler působnost nevymýšlí.

`implementation.interfaces` obsahuje stabilní ID, verzi, přesnou signaturu, chybovou sémantiku a lifecycle. Každý provider i consumer se váže na tutéž verzi. Příprava má v signatuře zachytit typy, generika, nullability a veřejné exporty. Chybějící symbol nebo provider blokuje dodání. Lokální kontrola ověřuje shodu kanonických kontraktů; sama není typovým překladačem libovolného jazyka ani důkazem správnosti slovní definice.

`implementation.files` obsahuje jen relevantní facets, například persistence, transakce, serializaci, concurrency, security či framework. Facet uvádí definici a zdrojové odkazy. `required_facets` stanovuje příprava podle rizik souboru; chybějící deklarovaný detail je blokující. Akceptace, ověřovací scénáře, očekávaný viditelný výstup a explicitní `allow_empty` jsou povinné. Kritická nevyřešená otázka blokuje A3/B3 i ve Standard režimu. Maximum Quality navíc zachovává nezávislý modelový quality gate.

FileContext obsahuje cílový soubor, jeho implementační kontrakt, relevantní requirements, architekturu, povinnosti s působností, přesná sdílená rozhraní, přímé dependency kontrakty a případné původní zdroje. B3 zachovává úplný původní cíl a přímé zdrojové závislosti; neposílá celý IN. Každá složka má hash a dohledatelný selector ve `context_provenance`. Detailní důvod globální působnosti zůstává přímo u povinnosti.

## Hashování a obnova

Obálka odděluje `source_snapshot_hash`, `file_context_hash`, `contract_hash` a `dependency_hashes`. Hash pracovního kontextu nezahrnuje auditní hash, takže změna nesouvisejícího zdroje nemění pracovní identitu consumeru. `ContextCompiler.invalidated` porovnává pracovní kontexty a rozšiřuje změny na tranzitivní konzumenty. U MODIFY přijímá také přesné původní obsahy obou verzí.

Nový manifest má verzi 3. Auditní snapshot se ukládá jednou; řádky obsahují FileContext bez globálního snapshotu, bez tools a bez návaznosti. `encode_requests` znovu kompiluje kontext proti snapshotu a ověřuje hashe původních obsahů. Verze 1 a 2 se interpretují pouze podle svých původních kontraktů. Jejich import zůstává dostupný; opakované odeslání vyžaduje novou implementační přípravu. Žádná tichá migrace nebo full-snapshot fallback neexistuje.

`recoverable_artifacts.py` ukládá přesný serializovaný obsah jako obsahově adresovanou obnovitelnou evidenci. Současný `RunLogger` a Run Bundle uchovávají důkazní obsah rovněž bezeztrátově; nová evidence se obsahově nerediguje ani nemaskuje. Obsahově adresované soubory vznikají výhradně exkluzivním vytvořením, při čtení se ověřuje SHA-256 a poškozený obsah se nenahrazuje jiným logem. Index vybírá aktuální artefakt, staré objekty se nemažou. Na POSIX se používají režimy 0700/0600; na Windows zůstává přístup závislý na ACL zvoleného LOG adresáře. Nejde o šifrování ani bezpečnostní hranici vůči uživateli s přístupem k tomuto adresáři.

`RunLogger.find_json` preferuje přesný artefakt. Obnova přípravy a Batch načítá kanonická stavová pole přes `load_run_state`. Historické legacy běhy, jejichž starší verze obsah dříve redigovala a nemají přesný artefakt, se zpětně neopravují odhadem. Response journal zachovává request hash, response ID i neznámý submit; import failure nevyvolává generování.

## Technické limity a směrování

Stejná měřicí vrstva chrání globální přípravu i souborové requesty. `context_limits.py` měří instrukce, input, schema a tools odděleně; FileContext má další rozpad po složkách. Lokální odhad je UTF-8 bajty / 3 doplněný konzervativní bajtovou horní mezí. Není to přesný tokenizer a neslouží k finančnímu rozhodování.

`preparation_measurement` a `checked_measurement` validují skutečné technické capabilities z modelové registry: `context_window`, případný samostatný `max_input_tokens`, provider `max_output_tokens`, podporované parametry a zákaz truncation. Pro přílohy, vnější historii a jiné lokálně neurčitelné vstupy se používá [ne-generativní počítání tokenů](https://developers.openai.com/api/docs/guides/token-counting). Výsledek musí odpovídat hashi přesného payloadu; neplatné nebo nedostupné měření blokuje generativní submit. Budoucí retrieval zůstává explicitně nejistý.

U souborů se reasoning effort volí podle složitosti kontraktu v rámci podporovaných capabilities modelu. Maximum Quality zachovává nejvyšší podporovanou úroveň. Model zvolený uživatelem se automaticky nesnižuje. Výstupní limit je odvozen z očekávaného viditelného výstupu, reasoning rezervy a obálky, ale nikdy nesmí překročit provider capability. Pokud se request nevejde do technického okna modelu, runtime jej neposílá.

KájovoNG neimplementuje cenový engine ani runtime finanční budget. Hospodárnost je řešena návrhem workflow a provozním rozhodnutím uživatele. Runtime validuje pouze technické a kontraktní limity API. Levnější model, menší prompty, Batch nebo minimální testovací workload jsou vývojová/provozní rozhodnutí mimo runtime kontrakty.

LIVE pokračování a opravy používají stejnou technickou kontrolu. Raw provider `usage` se archivuje beze změny jako telemetrie. Neprovádí se nad ním peněžní výpočet, model se nefiltruje podle lokálního ceníku a neexistuje finanční gate.

## Závislosti, pokračování a hranice ověření

Compiler vytváří SCC a topologické vlny kondenzovaného DAG iterativním algoritmem. Cyklická skupina je explicitní součást kontextu; soubory používají přesná společná rozhraní. Manifest ukládá `dependency_waves`. Současné Batch úlohy pracují proti kontraktům, nikoli proti dosud neověřenému kódu providerů. Uložený plán vln zatím není automatický vícebatchový scheduler čekající na integrační validaci každé vlny.

Rozhraní compileru přijímá verified dependency artefakty pouze se stavem `verified`, shodným kontraktem a SHA-256 obsahu. Runtime automaticky nepovyšuje stažený nebo syntakticky validní soubor na takový artefakt. To vyžaduje skutečnou integrační validaci příslušného projektu, která v aplikaci není univerzálně implementována.

LIVE začíná každý soubor bez historie přípravy. Další chunky navazují pouze uvnitř stejného souboru, posílají hash kontextu a prefixu namísto opakovaného FileContextu. Indexy, počet, konec a návaznost se nadále validují. Konečný obsah a hashe chunků se ukládají přesně. Protokol neprovádí slepé spojování neúplných JSON; při `incomplete` se běh zastaví. Dávkový soubor musí být celý v jedné odpovědi.

## Evidence a UI

GENERATE/MODIFY uchovává identitu requestu, fázi, model, effort, technické input/output limity, varování, blokace a raw provider usage v běžné orchestration/Run Bundle evidenci. Samostatný cenový report se nevytváří. Opakovaný import téže provider položky je idempotentní podle provider identity.

Progress UI může ukázat velikost vstupu, model, reasoning, technický výstupní limit a počet položek Batch. Nenabízí finanční limit, ceníkovou politiku ani zacházení s neznámou cenou.
