# Zákaz placeného preflightu

Tento dokument je závazný doplněk SSOT pro review změn v oblasti OpenAI transportu. Pokud je v rozporu s `docs/SSOT.md`, platí SSOT.

## Invariant

Kájovo NG **nesmí před skutečnou pracovní generativní operací odesílat samostatný placený test, probe, preflight Response ani zkušební BATCH**.

Před pracovní operací jsou povoleny pouze:

- lokální validace konfigurace, schématu, modelové matice, cest a JSONL;
- ne-generativní čtení katalogu modelů;
- ne-generativní ověření již existujících Response/File/Vector Store/BATCH prostředků, pokud jsou skutečně součástí pracovního workflow;
- upload skutečných pracovních vstupů, které pracovní operace potřebuje.

Povoleny nejsou pomocné generativní požadavky vytvořené pouze proto, aby předem zkusily, zda následný pracovní požadavek projde.

## LIVE

První `POST /responses` dané generativní etapy je její skutečný pracovní požadavek. Validační vrstva nesmí volat generativní transport. Pokud poskytovatel odmítne kombinaci parametrů, chyba patří pracovnímu požadavku a program ji nesmí maskovat předchozím placeným testem.

## BATCH

Program nejprve lokálně ověří celý skutečný pracovní JSONL. Potom nahraje právě tento pracovní vstup s `purpose=batch` a provede jediný pracovní pokus o `POST /batches`.

Zakázáno je vytvářet pomocný validační JSONL, pomocný Files upload nebo zkušební BATCH. `submission_unknown` smí popisovat pouze neurčitý výsledek skutečného pracovního `POST /batches`; před dalším submittem se dohledává přes přesný `input_file_id`.

## Historická kompatibilita

Starší LOGy mohou obsahovat `preflight_batches`, staré preflight ID nebo historický stav preflightu. Tyto údaje smějí být pouze pasivně zobrazeny jako historické. Aktuální aplikace z nich nesmí nabídnout ani spustit pokračování, opakování, stažení, zrušení nebo jinou vzdálenou placenou operaci.

## CI guard

Repozitářové testy musí blokovat návrat aktivních symbolů a transportních cest placeného preflightu. Samostatné `verify_*_live.py` skripty pro placené generativní probe požadavky nejsou součástí repozitáře. Standardní CI používá mocky a lokální kontrakty a nesmí vyžadovat skutečný OpenAI API klíč.
