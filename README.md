# KájovoNG

Desktopová aplikace v Pythonu a PySide6 pro práci s OpenAI Responses API, soubory, vlastními kaskádami a dávkami.

Kanonická specifikace systému: [SSOT](docs/SSOT.md).

Kombinace režimů, modelů, parametrů a příloh: [matice požadavků](docs/REQUEST_MATRIX.md).

## Spuštění na Windows

Vyžaduje Python 3.12 nebo novější a Git LFS pro fonty.

```powershell
git lfs pull
.\start.bat
```

Pro běžné spuštění stačí dvojklik na kořenový `start.bat`. Vytvoří chybějící projektové `.venv`, zkontroluje potřebné balíčky a jejich verze, podle potřeby je doinstaluje a otevře aplikaci. Doplnění závislostí může vyžadovat internet; vyhovující prostředí se kontroluje bez sítě. Python 3.12+ musí být již nainstalovaný. Při chybě se program nespustí a okno zobrazí důvod. Samotnou přípravu bez otevření aplikace lze spustit příkazem `.\start.bat -CheckOnly`.

API klíč nastavte v aplikaci v sekci API-KEY na záložce SETTINGS nebo v proměnné prostředí `OPENAI_API_KEY`. Klíč se nevkládá do repozitáře. Běhy a ruční Probe mohou být zpoplatněné.

Na Windows volba „Uložit“ zachová klíč i pro další spuštění. Uložený klíč má přednost před proměnnou prostředí terminálu; restart Windows není nutný. Volba smazání zabrání i opětovnému načtení starého klíče z prostředí.

Program před pracovním během automaticky ověřuje potřebné možnosti modelu; i tato ověření mohou být zpoplatněná. Výsledky uchovává 24 hodin pro stejný přístup. JSON Schema zajišťuje program včetně textových odpovědí a kaskád; uživatel je nemusí sestavovat. V SETTINGS lze změnit čekání na odpověď API, výchozí hodnota je 300 sekund. Po timeoutu program požadavek automaticky znovu neodesílá.

## Vývoj a ověření

```powershell
.venv\Scripts\python.exe -m pip install -e ".[dev]"
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m ruff check --select F,B,E9 kajovo kajovong utf8nobom tests Build
.venv\Scripts\python.exe -m pip check
```

Testy používají náhrady API a nevyžadují skutečný klíč. Spouštějte je v projektovém prostředí; systémový Python může mít jinou verzi nebo závislosti. Sestavení: [Build](Build/README.md).

Samostatné placené ověření lze spustit příkazy `.venv\Scripts\python.exe scripts/verify_openai_live.py --live`, `scripts/verify_workflows_live.py --live` a `scripts/verify_batch_live.py --live` se stejným interpretem. Čtou `OPENAI_API_KEY`, případně ignorovaný `.env.local`, používají vlastní testovací data a uklízejí vlastní prostředky.

## Práce s aplikací

RUN nabízí GENERATE pro vytvoření souborů, MODIFY pro úpravy, QA pro textovou odpověď a QFILE pro jeden úplný soubor. SEND AS BATCH vytvoří vzdálenou dávku; výsledky stáhnete v BATCH. Vlastní kroky se definují v KASKÁDA. MODELS nabízí ruční ověření schopností modelů.

V RUN vyplňte projekt, režim a model a otevřete sekci **Zadání a výsledek**. IN/OUT, Batch a další volby jsou v **Parametry a adresáře**; přílohy, diagnostika a provozní detail mají vlastní sekce. **Spustit** a **Zastavit** zůstávají pod pracovní plochou. Na menší obrazovce se panely KASKÁDA a VECTOR STORES přepínají pomocí záložek.

Průběh ukazuje dokončené jednotky, trvání a stáří poslední události. Zbývající čas se odhaduje až z několika dokončených jednotek stejné etapy. U čekání na API nebo ve frontě Batch může být neznámý. Předání dávky do API neznamená hotové soubory. PRICING nabízí samostatný ceník, účtenky a přehled rozpočtů; podrobné podklady lze rozbalit.

Úplné hodnoty v seznamech a tabulkách zobrazí nápověda po najetí myší. Vybrané položky zkopírujete pomocí Ctrl+C nebo položkou **Kopírovat výběr** v místní nabídce.

Izolované kontrolní rendery vytvoří `.venv\Scripts\python.exe scripts/render_ui.py --size 1366,768 --scale 1.5`. Skript používá testovací data a dočasný adresář, jehož cestu vypíše. Parametr `--native` používá vykreslování Windows. Systémový tiskový dialog vyžaduje samostatnou kontrolu při jeho otevření; běžný Qt snímek nezachytí jeho nativní obsah.

V GENERATE + SEND AS BATCH proběhnou A1 (plán) a A2 (společná specifikace rozhraní) živě. A3 vytvoří jeden úkol dávky na jeden kompletní textový soubor. Modely A1/A2/A3 lze vybrat samostatně. Návaznost, přílohy, diagnostika IN a ověřený file search slouží živé přípravě; diagnostika OUT je při odesílání vypnutá. MODIFY BATCH používá samostatný souhrnný kontrakt.

V panelu BATCH zvolte dokončenou dávku a Download. Import kontroluje ID, cesty, úplnost a změny existujících souborů. „Soubory kompletní, funkčnost neověřena“ znamená, že je ještě nutné ručně spustit sestavení a integrační testy podle `build_run` plánu A1 v `LOG/<run>/run_state.json`. Aplikace vygenerovaný kód automaticky nespouští. „Opakovat soubory…“ odešle zvolené cesty znovu bez A1/A2; „Opravit podle připomínky…“ přidá aktuální obsah a popis chyby. Obě akce vytvářejí placenou dávku. Cesty zadejte jako JSON pole, např. `["main.py", "maths.py"]`. Evidence, specifikace a výsledky zůstávají dostupné po restartu aplikace.

Samostatný test `.venv\Scripts\python.exe scripts/verify_generate_batch_live.py --live` ověřuje živou přípravu, skutečnou dávku tří provázaných Python souborů, import a jejich integrační test. Spouští pouze vlastní testovací projekt v dočasném adresáři; běžné testy síť nevyužívají.

IN se odesílá jako filtrovaný textový balíček. Ruční přílohy a očekávané soubory kaskády se také nahrávají do API. Vzdálené soubory a úložiště spravujte v FILES API a VECTOR STORES; dokončení běhu je automaticky nemaže.

STOP zastavuje běh mezi operacemi a může čekat na dokončení právě probíhajícího požadavku. VERSING vytváří snapshot v OUT. Nastavení je v `kajovo_settings.json`, účtenky ve výchozím `kajovo.sqlite` a běhy v LOG; relativní cesty se vztahují k pracovnímu adresáři aplikace.

Před první generující operací se otevře **Odhad nákladů**. Obsahuje počet vstupních tokenů konkrétního požadavku, sazby, scénáře výstupu a dostupné maximum ceny. Budoucí kroky závislé na odpovědi zatím nemají známou cenu. Můžete nastavit tvrdý limit USD a maximum výstupních tokenů; změna výstupu vyvolá nový odhad a potvrzení. Příliš nízké maximum může přerušit generovaný soubor. Zrušení nebo zavření okna požadavek neodešle.

GENERATE BATCH vyžaduje další potvrzení po živých A1/A2 před odesláním A3. Při nedostatečném rozpočtu se další odesílání pozastaví. Limit nelze bezpečně použít s nedoloženými sazbami, neurčeným maximem výstupu nebo dynamickými nástroji. Rozpočty a nevyřešené rezervace jsou dostupné v PRICING → Rozpočty a rezervace. Opakování souborů sdílí rozpočet původního běhu.

Po živém běhu se zobrazí samostatná **Výsledná účtenka**. U dávky se zobrazí po dokončení a načtení spotřeby na pozadí, nezávisle na importu do OUT. Jde o výpočet podle spotřeby API, nikoli fakturu poskytovatele. USD je rozhodující; CZK používá uložený orientační kurz ČNB s datem. Neznámé poplatky se nevydávají za nulu. Průběžné úložiště se do limitu jednorázových požadavků nezapočítává.

PRICING nabízí oficiální obnovení ceníku, neověřený ruční import JSON, filtry projektu/běhu/modelu, stránkování, detail a úplný export filtrovaných účtenek do JSON nebo CSV. Archivace uchovává finanční historii. Ceník při neúspěšném obnovení zůstává zachován. Samostatný placený test cen spustíte `.venv\Scripts\python.exe scripts/verify_costs_live.py --live`.

Logy obsahují zadání, odpovědi a případně zdrojový kód. Redakce známých tajných polí není šifrování ani úplná anonymizace. Adresář LOG, databázi a výstupy chraňte před nepovolaným přístupem.

Licence projektu: [MIT](LICENSE). Licence závislostí a prostředků: [oznámení třetích stran](THIRD_PARTY_NOTICES.md).
