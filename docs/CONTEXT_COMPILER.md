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

## Rozpočet a směrování

Stejná měřicí vrstva chrání také globální přípravu GENERATE/MODIFY. A1/B1 a následující fáze předávají úplné původní zadání, přílohy a aktuální podklady bez připojené historie předchozích fází. Opravy nahrazují kandidáta, místo aby řetězily neplatné odpovědi. Starý checkpoint A1/B1 se obnoví z uložených podkladů bez opakování requirements/plánu. Explicitní vnější response návaznost se uplatní pouze v první syntéze A0R/B0R.

`preparation_measurement` rezervuje maximální výstup modelu a 10 % okna. Pevná hranice globální přípravy je zbývající kapacita modelu se zohledněním samostatného vstupního limitu; 200k souborový limit se na ni nevztahuje. Obsah se nezkracuje a model ani Maximum Quality se nesnižují. Pro přílohy, vnější historii a lokálně nevyhovující horní mez se povinně používá [ne-generativní počítání tokenů](https://developers.openai.com/api/docs/guides/token-counting). Výsledek musí odpovídat hashi požadavku; selhání měření blokuje generování. Budoucí retrieval zůstává nejistý, ani přesné vstupní měření nezaručuje dokončení celé generace.

LIVE souborové pokračování a opravy používají stejnou kontrolu nejistých vstupů. Skutečné měření se přenáší do `cost_context_report` pod identitou pracovního requestu včetně background/store a metadata opravy; polling aktualizuje tutéž položku. Ověření pokrývají `test_preparation_budget.py`, `test_preparation_snapshot.py`, `test_delivery_pipeline.py` a `test_context_compiler.py`.

`context_budget.py` měří instrukce, input, schema a tools odděleně; FileContext má další rozpad po složkách. Lokální odhad je UTF-8 bajty / 3, doplněný konzervativní bajtovou horní mezí pro kontrolu kontextového okna. Není to přesný tokenizer. Soubor, obrázek, retrieval a serverová historie jsou explicitně neznámé složky, dokud není dostupné přesné měření. `OpenAIClient.count_input_tokens` používá samostatný ne-generativní endpoint a váže výsledek k hashi payloadu. LIVE pokračování souboru jej používá pro započítání historie.

Měkký vstupní limit je 40k/80k/120k podle transparentního skóre. Skóre závisí na počtu dependency kontraktů, symbolů, scénářů, očekávaném výstupu, cyklech a rizikových facets. Přípona souboru se nepoužívá. Pevný provozní strop je 200k, nad 150k je nutné zdůvodnění; rezerva modelového kontextu je 10 %. Kontextový limit respektuje také rezervovaný výstup a samostatný vstupní limit modelu. Zablokovaný vstup se neořezává. Desktop zatím nemá editor rozpočtových override; blokující úloha vyžaduje změnu přípravy nebo kapacity modelu.

U souborů se volí medium/high/xhigh podle složitosti v rámci podporovaných effort. Maximum Quality zachovává nejvyšší podporovanou úroveň. Model zvolený uživatelem se automaticky nesnižuje; kapacitně nevyhovující model způsobí blokaci. Výstupní limit je 1,5násobek očekávaného viditelného výstupu plus reasoning rezerva a 1 024 tokenů obálky. Nad maximum modelu se požadavek neposílá. Není to garance, že model nikdy vyčerpá výstup; incomplete zůstává neúspěchem.

Verzovaný ceník pokrývá explicitně doložené GPT-5.4 mini a GPT-5.6 Luna. Luna má cenový práh nad 272k input tokenů, násobky 2× input a 1,5× output. Odhad Batch používá 50% základní sazbu. Neznámé modely nemají vymyšlenou cenu. Regionální příplatky, cache, retrieval a úložiště nejsou zahrnuty. Zdroje: [Luna](https://developers.openai.com/api/docs/models/gpt-5.6-luna), [mini](https://developers.openai.com/api/docs/models/gpt-5.4-mini), [Batch](https://developers.openai.com/api/docs/guides/batch).

## Závislosti, pokračování a hranice ověření

Compiler vytváří SCC a topologické vlny kondenzovaného DAG iterativním algoritmem. Cyklická skupina je explicitní součást kontextu; soubory používají přesná společná rozhraní. Manifest ukládá `dependency_waves`. Současné Batch úlohy pracují proti kontraktům, nikoli proti dosud neověřenému kódu providerů. Uložený plán vln zatím není automatický vícebatchový scheduler čekající na integrační validaci každé vlny.

Rozhraní compileru přijímá verified dependency artefakty pouze se stavem `verified`, shodným kontraktem a SHA-256 obsahu. Runtime automaticky nepovyšuje stažený nebo syntakticky validní soubor na takový artefakt. To vyžaduje skutečnou integrační validaci příslušného projektu, která v aplikaci není univerzálně implementována.

LIVE začíná každý soubor bez historie přípravy. Další chunky navazují pouze uvnitř stejného souboru, posílají hash kontextu a prefixu namísto opakovaného FileContextu. Indexy, počet, konec a návaznost se nadále validují. Konečný obsah a hashe chunků se ukládají přesně. Protokol neprovádí slepé spojování neúplných JSON; při `incomplete` se běh zastaví. Dávkový soubor musí být celý v jedné odpovědi.

## Report a UI

GENERATE/MODIFY ukládá `cost_context_report.json`: identitu requestu, fázi, model, effort, odhad/přesný vstup, output budget, rezervu, cenový práh, varování, blokace, usage a incomplete důvod. Response journal doplňuje skutečnou usage i při neúplném výsledku. Batch import aktualizuje podle `custom_id`; opakovaný import nezapočítává usage dvakrát. Cena není označována za skutečné vyúčtování.

Progress UI ukazuje velikost souborového vstupu, model, reasoning, výstupní rozpočet a varování. Batch příprava uvádí počet úloh a součet odhadů. Podrobný rozpad zůstává lokálně v reportu. Samostatný desktopový přehled cen, plné vykazování kaskád a automatický integrační scheduler nejsou touto vrstvou dodány.
