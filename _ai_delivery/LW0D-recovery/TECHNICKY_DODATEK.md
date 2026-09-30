# Technický dodatek k obnově LW0D

Stav: rozpracováno; není potvrzením architecture readiness ani povolením odeslat neúplnou dávku.

Autorita změn: uživatel výslovně povolil technické opravy zadání pro obnovu generování. Původní zadání zůstává beze změny v `source/SSOT.original.md`. Tento dodatek má přednost pouze v konkrétně uvedených ustanoveních. Produktový rozsah, bezpečnostní hranice, povinné testy a skutečné runtime důkazy se neomezují.

## Rozhodnutí R01: fáze důkazů (§55.1, §55.19; B-12, B-13)

Rozlišují se tři nezaměnitelné druhy ověření:

1. DESIGN ověřuje úplnost specifikace, konzistenci kontraktů, plán vlastnictví souborů a dohledatelnost požadavků. U dosud nevytvořených souborů používá plánovanou cestu, specifikaci rozhraní a akceptační test; nevyžaduje existenci implementace. Plánovaný artefakt není důkaz splnění požadavku.
2. BUILD ověřuje skutečné soubory, jejich otisky, kompilaci a provedené lokální testy. Plánované artefakty nemohou nahradit chybějící výstupy.
3. RUNTIME ověřuje konkrétní nasazení, aktuální identitu, oprávnění, účinky a cleanup. BUILD ani DESIGN nejsou náhradou runtime evidence.

Contract Pack odděluje čistý návrhový kontrakt od build manifestu a runtime evidence. Návrhový digest nesmí záviset na budoucím runtime UUID nebo na svém vlastním digestu. Build manifest váže skutečné artefakty na návrhový digest, runtime evidence váže události na konkrétní build a deployment. Digest vstupu každé fáze je určen před vznikem výstupu.

§55.19 vyžaduje existující aktivní artefakty při aktivaci, nikoli před zahájením jejich výroby. Žádný implementační ani akceptační požadavek tím nezaniká. Test: projekt s úplným návrhem a bez implementace může projít DESIGN, ale musí selhat v BUILD i RUNTIME; chybějící návrhový kontrakt blokuje všechny tři fáze.

## Rozhodnutí R02: idempotency (§49.4; část B-03)

Doplňují se chybějící explicitní hrany:

| Z | Do | Guard |
| --- | --- | --- |
| EXECUTING | SUCCEEDED | Current fenced writer atomicky ukládá známý úspěšný výsledek, digest a jediný terminal event. |
| EXECUTING | FAILED_FINAL | Známá finální chyba podle operation contractu; žádný nerozhodnutý účinek nesmí být skryt. |
| RESERVED | CANCELLED_FINAL | Current cancel vyhrál CAS ještě před claimem a neexistuje dispatch ani účinek. |
| EXECUTING | CANCELLED_FINAL | Cancellation potvrzena; všechny možné účinky mají známý výsledek slučitelný s cancellation. |
| WAITING_FOR_INPUT | EXECUTING | Přijat current vstup pro stejnou logical operation; nový fenced attempt, znovu ověřené guards. |
| WAITING_FOR_RECONCILIATION | EXECUTING | Read-back prokázal neprovedení účinku a operation contract dovoluje další attempt. |
| WAITING_FOR_RECONCILIATION | SUCCEEDED, FAILED_FINAL, CANCELLED_FINAL | Current reconciliation prokázala odpovídající známý výsledek všech účinků. |

UNKNOWN nesmí projít žádnou terminální hranou. MANUAL_REVIEW nadále vyžaduje exact OWNER resolution. Business terminal outcome neuvolňuje recovery claims, dokud nejsou splněny samostatné closure podmínky. Všechny neuvedené hrany zůstávají zakázané. Toto rozhodnutí neuzavírá dosud nezpracované automaty supporting entities ani queued discussion cancellation.

## Rozhodnutí R03: bezpečné potlačení outboxu (§51.14, §51.37; část B-05)

`SUPPRESSED` se doplňuje do běžného i authority outbox automatu. Běžný outbox povoluje `READY → SUPPRESSED` a `RETRY_WAIT → SUPPRESSED`; authority outbox pouze `READY → SUPPRESSED`. Přechody vyžadují current root/claim guards a CAS, pokud neexistuje claim, dispatch authorization ani možný externí účinek. SUPPRESSED je neměnný koncový stav se zaznamenanou příčinou a odkazem na steer/cancel intent. Není úspěšným doručením a nezvyšuje počítadlo doručených zpráv.

Autoritativní outbox po dispatch authorization ani CLAIMED řádek tímto přechodem potlačit nelze. Vyžadují dosavadní reconciliation. Závod claim/steer musí povolit právě jednoho vítěze; prohra steeru nepovoluje smazat existující effect evidence. Chybějící enum RESETTING_INPUT a jeho guarded přechody doplňuje R14 v RESENI_KONKRETNI.md.

## Rozhodnutí R04: klíč externího účinku (§51.31; B-08)

Platforma vždy přidělí nenulový stabilní identifikátor/logical idempotency key účinku. Provider idempotency key je samostatná volitelná položka: je povinný právě tehdy, když zmrazený target contract deklaruje podporu provider idempotence. Absence podpory se explicitně ukládá jako capability, nikoli jako prázdný řetězec či vymyšlený provider key.

Klíč platformy bez provider podpory nedokazuje exactly-once external execution. Po nejednoznačném dispatchi je další mutující dispatch zakázán do read-back rozhodnutí; při nemožnosti rozhodnout zůstává UNKNOWN/MANUAL_REVIEW. Testy musí pokrýt provider s klíčem, bez klíče, timeout po účinku a duplicitní callback.

## Rozhodnutí R05: kanonické JSON (§6.3, §51.4; část B-11)

KCML-CANONICAL-JSON/1 používá JCS podle [RFC 8785](https://www.rfc-editor.org/rfc/rfc8785.html): UTF-8 bez BOM, rekurzivní řazení názvů podle UTF-16 code units, zachování pořadí polí a Unicode bez normalizace. Parser odmítá duplicitní dekódované názvy, osamocené surrogate a neplatná čísla. Serializace konečných binary64 čísel odpovídá JCS, včetně převodu záporné nuly na nulu.

Dodatečné pravidlo KCML: přesná celá čísla mimo bezpečný interval ±9007199254740991 a přesná desetinná množství přenáší pole s explicitním schématem jako kanonický desetinný string, nikdy tichým zaokrouhlením na Number. U integer stringu je povolen tvar `0` nebo `-?[1-9][0-9]*`; `-0`, plus a úvodní nuly se odmítají. Použití tohoto typu musí být jednotné v API, DB adaptérech i native rozhraní.

Explicitní audit preimage a referenční vectors doplňuje `AUDIT_FORMAT.md`. Byte-level hash je ověřen v Pythonu a Node.js; produkční DB/native implementace zůstává povinností výsledného projektu.

## Navazující konkrétní přílohy

`RESENI_KONKRETNI.md` doplňuje R06–R14 pro lifecycle, locks, effect/admission, recovery, closure, Linux socket, bootstrap, symboly a supporting states. `KONFIGURACE.md` obsahuje field-level allowlist a rozpracovanou tabulku policy. Aktuální uzavřenost jednotlivých nálezů eviduje `resolution-status.json`; následující seznam je seznam kontrolovaných oblastí, nikoli tvrzení, že už existuje validní A2.

## Kontroly před dávkou

- B-01, B-02, B-17: úplný registr operací, chyb a opravených referencí; ne pouze obecné pravidlo jejich tvorby.
- B-03, B-04, B-05: ostatní stavové hrany, kompozitní lifecycle/activation přechody a control transfer enum.
- B-06, B-07, B-14, B-18: úplný lock plan, recovery admission, vlastnictví a closure všech účinků.
- B-09, B-10: pole konfigurace a konkrétní deterministické policy s bounds, fallbackem a testy.
- B-11: audit preimage a golden vectors.
- B-15, B-16: realizovatelný důkaz identity systemd socketu a bootstrap DNS/TLS bez kruhové závislosti.
- Provázat rozhodnutí do A0/A1; staré blokující texty se nesmějí tiše ponechat jako současně platná autorita.
- Vytvořit skutečnou A2 strukturu aplikace s přesnými exporty/importy, migračními a runtime kontrakty, relevantními výňatky a testy každého souboru.
- Ověřit traceability, implementační kontexty, velikost a úplnost všech Batch requestů. Teprve poté odeslat dávku a doložit skutečné batch ID.

Žádný z těchto zbývajících bodů není označen za vyřešený pouze normalizací JSON.
