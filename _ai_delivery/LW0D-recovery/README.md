# Obnova LW0D

Tato složka obsahuje soukromé pracovní podklady; je ignorovaná Gitem. Není novým Run Bundle a nic v ní nesmí přepsat původní LOG/RUN_160920260039_LW0D. Žádný skript zde nevolá API ani nezakládá Batch.

## Aktuální stav

- Zachováno celé původní zadání, A0, A1 a odmítnutá A2. Původní A2 má jen 13 handoff/dokumentačních souborů, není strukturou aplikace.
- Odděleno 70 non-requirement references bez ztráty; 200 requirements a 66 architecture items zachováno. Normalizovaný A1 není účinný opravený plán.
- `resolution-status.json` eviduje 15 DECIDED návrhových rozhodnutí a 3 PARTIAL nálezy. DECIDED neznamená implementační či runtime PASS.
- `operation-map.expanded.json`: explicitní mapování 503/503 API cest na operace, writer homes a parity identifikátory. Neobsahuje ještě všechny úplné operation contracts §55.6.
- `SSOT.recovery-review.md` je konsolidovaný REVIEW_ONLY text, nikoli schválený Batch vstup. Jeho přesné zdroje a otisky jsou v `review-bundle.manifest.json`.
- Neexistují A0.effective.json, A1.effective.json ani A2.implementation.json. Neexistuje Batch manifest, nahraný input file ani batch ID. Nebyl znovu spuštěn A1/A2 model.
- Poslední lokální kontrola potvrzuje, že podklady pomocných agentů nejsou uzavřené: lifecycle registr má 227 entit, ale 144 strojů, z nichž 28 nemá explicitní transitions a 64 entit nemá přiřazený stroj; operation registr má 12 konkrétních záznamů proti 661 požadovaným. `verify_recovery_artifacts.py` proto zůstává fail-closed.

## Zbývající práce

1. B-01: dokončit operation records podle §55.6 včetně ne-API akcí; explicitní mapa již existuje a nemá se znovu vymýšlet.
2. B-02 je rozhodnut v ERROR_NAMESPACE.md a error-symbols.json. Všechny 33 error-like symboly mimo §32 mají explicitní roli; při výsledném BUILD ještě ověřit každé skutečné errorCode/stableCode pole proti uzavřenému namespace.
3. B-03: dokončit coverage mutable supporting entities. Kapitola 25 obsahuje další entity nad rámec dosud opravených hran; immutable payload musí být oddělen od mutable lifecycle projection. R14 není univerzální fallback automat.
4. B-10: doplnit všechny zbývající konkrétní policy entries; R17 obsahuje pouze základní tabulku. Žádné skryté runtime defaults.
5. Teprve potom vytvořit účinné requirements/plan, skutečnou implementační A2 s přesnými rozhraními, source excerpts, acceptance a test scenarios. Původní blocking texty nesmějí zůstat druhou protichůdnou autoritou.
6. Spustit stávající aplikací používané traceability/context/quality/budget validátory a sestavit Batch manifest. Zachovat maximum_quality=true, model souborové fáze podle původního nastavení gpt-5.2, cílový adresář D:/CML D. Před submit ověřit duplicity, známé existující batches a náklady; nepřepisovat historické checkpointy.

Nový `policy-catalog.v2.json` je pouze nová lokální revize po opravě IPC/resource/stream limitů; starší `policy-catalog.json` zůstává nedotčený. Ani jeden katalog sám neprokazuje BUILD/RUNTIME implementaci.

## Lokální ověření

Z kořene projektu používat `.venv/Scripts/python.exe` a pro čitelný výstup `PYTHONUTF8=1`.

```powershell
& '_ai_delivery/LW0D-recovery/export.ps1'
& '.venv/Scripts/python.exe' '_ai_delivery/LW0D-recovery/verify_recovery.py'
node '_ai_delivery/LW0D-recovery/audit-reference.mjs'
& '.venv/Scripts/python.exe' '_ai_delivery/LW0D-recovery/compile_operation_map.py'
& '.venv/Scripts/python.exe' -m pytest -q '_ai_delivery/LW0D-recovery/test_recovery.py'
& '.venv/Scripts/python.exe' '_ai_delivery/LW0D-recovery/check_submission.py'
```

Poslední příkaz při neúplných podkladech správně vrací exit code 2. Exporty a sestavení odmítají přepsat odlišný existující artefakt. Další konsolidovaný návrh proto musí dostat explicitně novou revizi, zatímco zdrojový export zůstává neměnný.

Audit vectors ověřují byte-level formát odděleně od plného audit event schema. Node referenční canonicalizer kontroluje accepted vectors, není strict produkční JSON parser. Linux/systemd/DB/runtime acceptance nebyly na Windows provedeny.
