# R20 — úplné pokrytí katalogu §41 a návrhová baseline

`policy_definitions.py` je explicitní návrhová baseline verze 1. `verify_policies.py` ji deterministicky kompiluje do `policy-catalog.json`: 130 policy records pokrývá všech 129 SYSTEM_MANAGED odrážek §41.1–41.9. Dalších 15 odrážek jsou OWNER vstupy, které se nesmějí převést na technické policy. Nejde o hodnoty naměřené na produkčním serveru ani o důkaz runtime PASS.

R20 konkretizuje dosud neurčené technické hodnoty; přebírá bezpečnostní omezení původního textu a R16/R17. Přednost má pouze ve vyplněných položkách. Věta §31.5 o OWNER správě technické retention se, stejně jako §19.3, nahrazuje R16: OWNER může zobrazit effective hodnoty a spustit explicitní archivaci/export, ale nemění safety retention. Required evidence, současné a rollback reference mají vždy přednost před časovým promazáním.

## Výpočet, validace a použití

Každý record obsahuje owner module, přesnou zdrojovou odrážku a její digest, input/value schema, algoritmus a digest, jednotku, default/bounds, trigger, effective version, failure/fallback, audit, requirements, test IDs a acceptance gates. Čísla jsou celá; výpočty nepoužívají floating point ani hodiny procesu. Počet CPU a allocatable RAM se čtou pouze z persistovaného capability snapshotu. Nevalidní, chybějící nebo nadlimitní vstup je chyba; schema validation probíhá před výpočtem, nikoli po tichém clampu vstupu.

Konstantní record je uzavřená struktura s přesným `const` schema. Označení `record` neznamená libovolný JSON či runtime volitelné vlastnosti. Číselné přípony `Ms`, `Bytes`, `Tokens`, `MicroUsd`, `Days`, `Percent` a `Count` určují jednotku jednotlivých members. Peníze jsou integer mikro-USD, nikoli float. Hodnota `null` u `browser.timeouts.unknownRetentionMs` znamená zákaz časového zániku unknown-effect evidence; není neomezeným výpočetním nebo síťovým limitem. Nula u swap/core znamená vypnuto, u SDK retries žádný opakovaný create; nikde neznamená neomezený resource budget.

Výpočty:

- `CPU_HALF_CLAMP_1_4_V1`: `max(1, min(4, floor(availableCpuCores / 2)))`.
- `BROWSER_SLOTS_V1`: `min(4, max(1, floor(availableCpuCores / 2)), floor(allocatableMemoryMiB / 4096))`; vstup vyžaduje alespoň 4096 MiB. Na host slot se rezervuje nejvýše 2048 MiB a nejméně polovina allocatable RAM tak zůstává mimo browser hosts. Před skutečným spuštěním musí projít také souhrnná rezervace všech služeb; samotný výpočet slotů nedokazuje volnou paměť.
- `CONSTANT_V1`: deep copy přesné konstanty; bez input/environment override.

Technické resource limity nejsou business oprávnění. Capacity exhaustion vyvolá bounded queue nebo stabilní kapacitní chybu; nesmí z katalogu odstranit capability ani ji potichu zaměnit. Hranice output/context modelu se před každým skutečným submit porovnají s pinned model capability a zůstatkem rozpočtu. Nevejde-li se požadovaný výstup a rezerva, submit se zastaví a úloha se musí explicitně rozdělit; nekrátí se zadání ani neoznačuje neúplný výstup za úspěch.

## Exaktní build a release vstupy

Odkazy na manifest, lockfile, provider descriptor, locator compiler a serializer v konstantních profiles nejsou oprávněním vymyslet runtime fallback. Jsou požadavkem na další konkrétní build artefakt:

| Vstup | Povinná validace před použitím |
| --- | --- |
| SDK/runtime/browser manifest | Exact package versions, lock integrity, binary/runtime/font/dependency digests; compatibility gates. Žádný `latest`, rozsah verzí ani síťový lookup za běhu. |
| Seccomp/namespace/socket/service profile | Exact generated profile bytes, signed release manifest a digest; povolené syscall/FD/paths a Linux negative tests podle §50. Nedostupný profil blokuje spuštění. |
| Model capability/pricing snapshot | Exact model a endpoint, podporované parametry a limity, platnost snapshotu, nenulové známé ceny; rezervace před create. |
| DNS/scanner/alert binding | Existující exact descriptor nebo business binding a jeho digest; secret reference bez plaintextu v policy. Nedostupnost nesmí spustit neověřený transfer, alternativního poskytovatele či smazání cizího DNS recordu. |
| Browser field/action/account/automation contract | Exact immutable revision, target/identity, preconditions, input/trigger/read-back; chybějící adaptér nepovoluje blind input. |

Compiler tyto budoucí artefakty nevydává za již existující. Jejich homes, schema, producer a runtime/BUILD gates musí být v implementační A2 a propojeném Contract Packu. Dokud reference nemají konkrétní implementační definici, samotné pokrytí 129 odrážek neuzavírá celý B-10.

## Ověření

`test_policies.py` ověřuje byte-stabilní kompilaci, úplnost topic mappingu, zákaz změny SYSTEM_MANAGED na OWNER input, duplicity a neznámé source refs, strict inputs, CPU/RAM výpočty, digest integrity, heartbeat/lease, MemoryHigh/Max, IPC hard ceilings, token součty a vyhrazenou recovery kapacitu. Runtime aplikace musí stejné records používat přes jeden policy evaluator; test tohoto návrhu není náhradou jeho runtime integračních testů.

Početní pokrytí samo nedokazuje, že deployment na konkrétním hostu projde: chybějící RAM, disk, credential, browser build či Linux capability zůstává explicitním preflight blockerem. Generátor nesmí tyto podmínky obejít označením PASS.
