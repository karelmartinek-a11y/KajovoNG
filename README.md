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

API klíč nastavte v aplikaci v sekci Nastavení → Přístup nebo v proměnné prostředí `OPENAI_API_KEY`. Klíč se nevkládá do repozitáře. Běhy zahrnují placené zkušební volání před pracovním odesláním.

Na Windows volba „Uložit“ zachová klíč i pro další spuštění. Uložený klíč má přednost před proměnnou prostředí terminálu; restart Windows není nutný. Volba smazání zabrání i opětovnému načtení starého klíče z prostředí.

Program před pracovním během automaticky ověřuje potřebné možnosti modelu; i tato ověření mohou být zpoplatněná. Výsledky uchovává 24 hodin pro stejný přístup. JSON Schema zajišťuje program včetně textových odpovědí a kaskád; uživatel je nemusí sestavovat. V Nastavení lze změnit čekání na odpověď API, výchozí hodnota je 300 sekund. Po timeoutu program požadavek automaticky znovu neodesílá.

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

V levé navigaci jsou **Zadání, Kaskády, Zdroje, Dávky, Historie, Verze projektu, Modely, Nastavení a Nápověda**. [Úplný inventář a návrh](docs/UI_DESIGN.md) obsahuje mapu funkcí, parametrů a validačních pravidel.

V **Zadání** vyplňte projekt, režim, model a prompt. GENERATE vytváří soubory, MODIFY upravuje existující IN, QA vrací text a QFILE jeden úplný soubor. Adresáře a pokročilé volby mají vlastní kartu; diagnostika a výsledek také. Připojené zdroje jsou vidět v souhrnu zadání. **Spustit**, **Zastavit** a **Aktivní běhy** zůstávají pod pracovní plochou.

Vlastní postup sestavte v **Kaskádách**. Soubory API a vector stores spravujte ve **Zdrojích**. **Modely** ukazují dostupnost a pevnou validační matici. **Dávky** oddělují stav API od stažení a ověření souborů. **Historie** umožňuje hledání, detail, export, tisk, dokončení BATCH a ReRun ostatních běhů. **Verze projektu** obsahují Git, milníky a editor s porovnáním. Nastavení a Dávky lze otevřít také samostatně.

Po odeslání **GENERATE BATCH** jsou A1/A2 hotové a soubory zpracovává OpenAI. V **Historii** klikněte u běhu na **Dokončit**, případně použijte stejné tlačítko v **Dávkách**. Aplikace ověří stav a převezme dostupné výsledky do původního OUT bez opakování generování. Pokud dávka stále běží, dokončete ji později. Přehled ukazuje projekt, datum odeslání a zvlášť stav uložení souborů. Částečný výsledek lze znovu převzít; ručně změněné soubory zůstanou zachované. Stejná akce dokončí také MODIFY BATCH. Akce **Zrušit zpracování** zastaví aktivní dávku; samotný záznam na OpenAI smazat nelze.

Před pracovní dávkou může čekat **zkušební dávka**. V Historii i Dávkách u ní použijte **Pokračovat**: program převezme ověření a při úspěchu odešle pracovní dávku; u uloženého GENERATE BATCH neopakuje A1/A2. Dokončení zkoušky na serveru ještě nepotvrzuje platnost jejích výsledků. Zkušební dávka se neimportuje do OUT. Dávky ukazují také typ a navázané běhy; pokud zkoušku sdílí více běhů, tlačítko nabídne jejich výběr. **Obnovit stav** aktualizuje poslední známý stav také v Historii, bez automatického pokračování. Po restartu jsou dostupné uložené stavy; aktuální stav ověřte obnovením.

U hlavní volby modelu nebo v **Modelech** použijte **Nastavit jako výchozí**. Model lze také vybrat v **Nastavení → Provoz** a uložit nastavení. Předvolba platí pro nová zadání včetně dalšího spuštění aplikace; model rozpracovaného nebo načteného zadání se nezmění.

Průběh ukazuje dokončené jednotky, fázi, trvání, ETA a stáří poslední události. U čekání na API může být ETA neznámá. Skrytý průběh znovu otevřete přes Aktivní běhy. Zavření okna nezničí běžící worker; Stop čeká na bezpečné přerušení mezi operacemi. Průběh uploadu a potvrzení mazání mají samostatná nová okna.

Izolované snímky pořídí `.venv\Scripts\python.exe scripts/render_ui.py --output C:\Temp\kajovo-ui --size 1366,900 --scale 1`. Pro malou logickou plochu použijte `--size 911,480 --scale 1.5`. Skript vykresluje skutečné Qt rozhraní s označenými ukázkovými daty v dočasném pracovním adresáři, bez API volání. Parametr `--native` volí vykreslování Windows; systémový tiskový dialog vyžaduje samostatnou kontrolu na Windows.

V GENERATE + SEND AS BATCH proběhnou A1 (plán) a A2 (společná specifikace rozhraní) živě. A3 vytvoří jeden úkol dávky na jeden kompletní textový soubor. Modely A1/A2/A3 lze vybrat samostatně. Návaznost, přílohy, diagnostika IN a ověřený file search slouží živé přípravě; diagnostika OUT je při odesílání vypnutá. MODIFY BATCH používá samostatný souhrnný kontrakt.

V sekci Dávky zvolte dokončenou dávku a stáhněte výsledek. Import kontroluje ID, cesty, úplnost a změny existujících souborů. „Soubory kompletní, funkčnost neověřena“ znamená, že je ještě nutné ručně spustit sestavení a integrační testy podle `build_run` plánu A1 v `LOG/<run>/run_state.json`. Aplikace vygenerovaný kód automaticky nespouští. „Opakovat soubory…“ odešle zvolené cesty znovu bez A1/A2; „Opravit podle připomínky…“ přidá aktuální obsah a popis chyby. Obě akce vytvářejí placenou dávku. Cesty zadejte jako JSON pole, např. `["main.py", "maths.py"]`. Evidence, specifikace a výsledky zůstávají dostupné po restartu aplikace.

Samostatný test `.venv\Scripts\python.exe scripts/verify_generate_batch_live.py --live` ověřuje živou přípravu, skutečnou dávku tří provázaných Python souborů, import a jejich integrační test. Spouští pouze vlastní testovací projekt v dočasném adresáři; běžné testy síť nevyužívají.

IN se odesílá jako filtrovaný textový balíček. Ruční přílohy a očekávané soubory kaskády se také nahrávají do API. Vzdálené soubory a úložiště spravujte v sekci Zdroje; dokončení běhu je automaticky nemaže.

Zastavit přerušuje běh mezi operacemi a může čekat na dokončení právě probíhajícího požadavku. Snapshot vytváří snapshot v OUT. Nastavení je v `kajovo_settings.json` a běhy v LOG; relativní cesty se vztahují k pracovnímu adresáři aplikace.

Cena tokenů = (běžný vstup × vstupní sazba + cache × její sazba + výstup × výstupní sazba) / 1 000 000. Reasoning se nepřičítá podruhé. Skutečný file search přidá poplatek za volání; průběžné úložiště je oddělené. Přesné sazby se liší podle modelu, režimu a délky kontextu.

Logy obsahují zadání, odpovědi a případně zdrojový kód. Redakce známých tajných polí není šifrování ani úplná anonymizace. Adresář LOG, databázi a výstupy chraňte před nepovolaným přístupem.

Licence projektu: [MIT](LICENSE). Licence závislostí a prostředků: [oznámení třetích stran](THIRD_PARTY_NOTICES.md).

### Kompatibilita OpenAI

Volby modelů omezuje [pevná matice](docs/MODEL_MATRIX.md); [kompletní parametry a pracovní kombinace](docs/REQUEST_MATRIX.md) rozlišují LIVE a BATCH. Před pracovním požadavkem proběhne skutečné zkušební volání se stejnými parametry. Zkušební Batch může čekat až 24 hodin: aplikace po minutě zobrazí ID a pracovní dávku neodešle. Opakované spuštění stejného zadání převezme výsledek již vytvořené zkoušky. Chyba ukáže odmítnutý parametr, pokud jej OpenAI uvede. Zkoušky jsou placené.
