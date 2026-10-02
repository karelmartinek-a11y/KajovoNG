# Kontrakty odpovědí a důkazy úplnosti

`response-contract-inventory.json` je udržovaný index kandidátů hranic. `response-runtime-schemas.json` obsahuje skutečné masky sestavené produkčními továrnami a oficiálními SDK modely. Inventář je **PARTIAL**. Počty kandidátů, šablon nebo úspěšných testů nepotvrzují úplnost dosažitelných volání ani jejich příjemců.

## Kontroly

`python tools/verify_response_contracts.py` kontroluje všechny evidované doménové masky, nativní masky management/image/token-count, AST místa volání, oba dispatch registry transportu a otisky všech vlastních Python zdrojů. Neznámá maska, reference, nové volání, změněný volající, nový či změněný zdroj nebo změněná runtime varianta kontrolu zastaví. Otisky zdrojů normalizují CRLF na LF pro společný kontrakt Windows/Linux. Distribuční kopie Build/lib, Build/Kajovo, Build/bdist.* a dist jsou oddělené a nepatří do inventáře vlastních zdrojů.

`python tools/verify_response_contracts.py --require-complete` navíc vyžaduje důkaz každého volání a předávky. Při pending položkách selhává. CI provádí obě kontroly; přísnou kontrolu spouští po ostatních quality kontrolách. Běžný průchod není náhradou přísného průchodu.

AST index odvozuje názvy fasády z implementace OpenAIClient a zahrnuje transport, wrappery, SMTP, SSH a kandidáty dynamického getattr. Kandidát nemusí být síťové volání. Stabilní ID tvoří cesta, rozsah, jméno volání a pořadí uvnitř rozsahu. `call_sha256` a `caller_sha256` zachytí také změnu argumentů nebo větvení bez změny ID. Přímé endpointy a regex endpointy se odvozují z operation_spec, ne z opakovaného ručního seznamu. Index všech zdrojů zachytí i novou síťovou cestu mimo sledovaná jména. Neprovádí úplnou mezifunkční analýzu datového toku.

Samostatný index hlídá také síťové skripty mimo Python, fyzické JSON kontrakty, workflow a závislosti. Není to důkaz jejich úplného sémantického auditu.

Při změně aktualizujte pouze doložené údaje, regenerujte skutečné masky přes `runtime_catalog()` a index přes `response_call_sites()` / `transport_registry()`. Nedoplňujte status verified bez úplného čtení, všech kontraktových variant, příjemců, mapování a relevantních regresí. Předchozí výsledek ani samotný nový otisk nejsou důkazem aktuálního stavu.

## Pokrytý rozsah testů

| Oblast | Důkaz | Meze |
| --- | --- | --- |
| Pevné doménové továrny | 74 sestavených masek; negativní změna každého datového uzlu | Počet šablon není počet všech konkrétních dynamických masek |
| Nativní obálky | 20 operací; úspěch, chybějící pole, typ, null, nepopsané pole, identita, counts a mapy | Nezahrnuje celé provider Responses ani HTTP error obálky |
| Kaskádové masky | Všechny podporované souborové typy a všech 15 neprázdných podmnožin čtyř druhů výstupu | Neprokazuje každou kombinaci počtu výstupů, vlastních schémat, zdrojů a obnovy |
| Návaznosti kaskády | Všech 315 dopředných grafů s pěti kroky; typy a role všech testovaných substitucí; obnova a smíšená legacy/typed návaznost | Grafy s více kroky a úplný nekonečný prostor dat nemají generativní důkaz |
| QFILE a Batch | Typ obnoveného plánu, kanonická maska i po přepočtu hashů, předání validovaných dat | Není úplný audit všech archivních verzí a navazujících úložišť/UI |
| SMTP | Přesné protokolové dvojice a mapa odmítnutí konkrétních příjemců; částečné odmítnutí | Neprokazuje skutečné doručení adresátovi |
| Souborové kódování | UTF-8 pro chybějící volitelné encoding; odmítnutí null, prázdného řetězce, jiného typu a neznámého kódování; preflight původní masky | Další binární formáty mají vlastní neuzavřený audit |
| Spojení stránek | Unikátní ID na stránce i mezi stránkami; soulad cursoru s pořadím a counts se součtem | Neprokazuje neměnnost vzdáleného seznamu během stránkování |
| Konfigurace běhů | 1 024 kombinací osmi boolean voleb ve čtyřech režimech | Matice validate_run_options neprokazuje všechny response masky a předávky |

## Otevřené položky, které blokují úplný PASS

- Úplně přečíst zbývající relevantní vlastní zdroje a příjemce; source review explicitně rozlišuje úplné čtení od AST indexace.
- Rozlišit všechny dynamické kandidáty od skutečných volání, doložit dosažitelnost a porovnat s procesními registry včetně semantic_runtime_inventory/progress_catalog.
- Dodat přesný kontrakt celé Responses provider obálky, všech nástrojových položek, HTTP error obálek, všech variant poll/cancel/background a jejich návazností. Doménový strict JSON tyto vrstvy nenahrazuje.
- Odstranit zbývající neurčité doménové typy/předávky v původních modulech včetně úložišť a UI; nové masky netvoří důkaz těchto vrstev.
- Doložit sémantiku automaticky připravených schémat a všech vlastních kontraktů vůči skutečným dalším příjemcům. Typovaný návrh sám neprokazuje věcnou kompatibilitu zadání.
- Dokončit varianty archivních Batch/Photo/Comic obálek, SSH a všechny transformace binárního obsahu; byte transport sám neprokazuje následný formát ani stav procesu.
- Dodat úplnou matici kontraktově odlišných kombinací a evidence mapování všech polí do všech příjemců. Reprezentativní katalog ani párové pokrytí tuto matici nenahrazují.

Tyto položky nesmějí být odstraněny pouhým přeznačením inventáře nebo rozvolněním kontraktů. Každá má mít vazbu nález → oprava → regresní test a aktuální ověřovaný commit.
