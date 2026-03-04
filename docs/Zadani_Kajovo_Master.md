# MASTER ZADÁNÍ: Desktopový program „Kája“ (Windows) pro automatizaci requestů na OpenAI (Responses API) + automatické zpracování odpovědí

Jsi senior programátor. Všechny programy, které píšeš, jsou funkční a plně provozuschopné. Programy jsou robustní natolik, že jsou ošetřeny pro většinu situací, kdy by mohly spadnout nebo zamrznout. Program musí tvořit detailní logování natolik detailní, že při eventuální chybě nebo problému je ihned v kombinaci s projevem chyby identifikovatelné, co je za problém. Program musí být stavěn tak, že pokud nejsou nutné vstupy k dispozici, uživatel je na to upozorněn. Všechny možnosti a komponenty v programu jsou realizovány tak, aby uživatel nemohl zadat ani takové kombinace, které jsou neplatné nebo nebezpečné. Pokud nějaká operace trvá více než 3–5 sekund, program zobrazí Pop Up okno, ve kterém informuje uživatele o tom, co se děje, a zabrání tomu, aby program vytuhnul nebo se tak tvářil.

Vygeneruj desktopový program s názvem „Kája“, který bude automatizovat requesty na OpenAI (Responses API) a zpracovávat odpovědi podle tohoto zadání. Program funguje jako detailní ovládací panel, kde uživatel nastaví očekávání, vstupy, režim, model a zpracování odpovědí. Po spuštění requestu se provede automatická sada kroků definovaná volbami v UI.

Důležité: V tomto zadání je záměrně redundance. Nesmíš vynechat žádný detail ani když je redundantní.

---

## 1) Precedence a determinismus

1. Vše, co je označeno „MUSÍ“ nebo „NESMÍ“, je závazné.
2. Pokud se dvě pravidla dostanou do konfliktu, platí toto pořadí precedence (od nejsilnějšího):
   a) TOKENS a KONTRAKTY stavů (sekce 20 a 24)
   b) Specifikace komponent (sekce 25)
   c) Layout aplikace (sekce 23)
   d) Obecná pravidla (sekce 21–22)
   e) Všechny ostatní části tohoto MASTER zadání (funkční logika, pipeline, logování, robustnost, pricing, batch, diagnostika).
3. Pokud je nějaká hodnota odvozená (vzorec), musí se použít přesně, včetně zaokrouhlení.
4. Jednotky: všechny rozměry jsou v logických pixelech (px).
5. Zaokrouhlování: `round(x)` = matematické zaokrouhlení na nejbližší celé číslo (0.5 nahoru).
6. Nesmí vzniknout překryv prvků (výjimka: overlay vrstvy tooltip/roletka/dialog/scrollbar).

---

## 2) Nezbytné vlastnosti a zásady

### 2.1 Robustnost

* Program musí být robustní a nikdy nesmí spadnout.
* Veškeré procesy musí být detailně zobrazovány ve vyskakovacím dialogu:

  * průběžné logování kroků,
  * odhad zbývajícího času (ETA),
  * procentuální průběh (0–100 %) pro celý krok i sub-kroky.
* UI se nesmí blokovat: síťové operace, uploady, pollingy, kopírování stromu adresářů, zipování bundle apod. poběží na pozadí.

### 2.2 Logování

* V adresáři spuštění programu vytvoř složku LOG/ (pokud neexistuje).
* Každý běh („run“) má vlastní Run ID, např. `RUN_<DDMMRRRRHHMM>_<random4>`.
* Každý request a response se uloží jako samostatný soubor do LOG/.
* Název log souboru musí obsahovat:

  * název projektu (pokud je vyplněn),
  * ResponseID (nebo BatchID / C_RUN_ID / RUN_ID),
  * časový kód DDMMRRRHHMM (čas) – přesně v názvu souboru.

Povinné artefakty v LOG (per run):

* kompletní request JSON (odeslaný) – samostatný soubor,
* kompletní response JSON (přijatý) – samostatný soubor,
* kompletní „UI state“ (co bylo vyplněno a zvoleno) – samostatný soubor a zároveň embed do request logu, aby šel použít pro LOAD REQUEST,
* manifesty:

  * mirror manifest (IN mirror),
  * diagnostické manifesty (Windows/SSH snapshot),
  * výstupní mapa uložených souborů (co se uložilo kam),
  * stavové logy kroků: uploady, pollingy, validace, retry, backoff, cancel.

Logovat se musí navíc detailně:

* seznam všech souborů, které program:

  * přidal (nově vytvořil),
  * přepsal,
  * smazal (lokálně nebo na File API),
  * přesunul / zkopíroval,
  * vytvořil adresář,
* u každé změny:

  * časové razítko,
  * původní cesta + cílová cesta,
  * velikost před/po (pokud existuje),
  * hash (např. SHA256) před/po, pokud je to rozumné (u velkých souborů volitelně),
* u uploadů na Files API:

  * lokální path,
  * file_id,
  * purpose,
  * velikost,
  * timestamp uploadu,
* u mazání na Files API:

  * file_id,
  * původní jméno (pokud dostupné),
  * timestamp.

Batch logování:

* vstupní JSONL,
* výstupní JSONL,
* error JSONL,
* mapování `custom_id → path` (pokud je použito),
* stavový průběh jobu (polling snapshots).

Skripty (pouze pokud jsou vyžádány diagnostikou OUT a přijdou v response):

* `readmerepair.txt`,
* skripty,
* log spuštění skriptu (stdout/stderr),
* návratové kódy,
* záznam o potvrzení uživatele před spuštěním.

### 2.3 Kontrola logiky kombinací

Program musí kontrolovat, že zvolené kombinace nastavení jsou přípustné. Nesmí odeslat request v neplatné kombinaci. Chyba se musí zobrazit uživateli jasně a s návrhem opravy.

### 2.4 Cenová evidence a nacenění (důraz na přesnost)

Program musí implementovat:

* přesné sledování cen za jednotlivé requesty i batch joby,
* přesná evidence nákladů na:

  * input tokens,
  * output tokens,
  * Batch (výsledná cena musí reflektovat Batch pricing),
  * file_search a Vector store náklady (tool + storage),
* důraz na přesnost a aktuálnost ceníku.

V UI musí existovat samostatná obrazovka pro ceny pod tlačítkem „$“ (v záhlaví vedle tlačítka API-KEY). Na hlavní ploše programu se ceny nezobrazují a neovlivňují workflow; ceny se řeší pouze v této samostatné obrazovce.

Elektronická účtenka:

* pro každý běh vzniká „účtenka“ (detailní rozpad položek) a ukládá se do lokální databáze (SQLite) + exportovatelný JSON v LOG,
* účtenky musí být filtrovatelné podle data, projektu, modelu, režimu, typu (A/B/QA/C), obsahu zadání (fulltext), ResponseID/BatchID,
* musí být možné mazat, exportovat, sumarizovat za období.

Aktualizace ceníku:

* program musí podporovat lokální cache „price table“ + ruční refresh z oficiálních podkladů (konfigurovatelné v SETTINGS),
* program musí podporovat i automatický refresh při startu (pokud je v SETTINGS zapnuto),
* pokud program nemá aktuální ceny, musí:

  * jasně upozornit (banner/popup),
  * umožnit pokračovat, ale „účtenka“ musí být označena jako „odhad / neověřeno“ (explicitně).

---

## 3) Technologie a runtime požadavky (implementuj stabilně)

* Program musí být připraven pro provoz na Windows 10 a Windows 11.
* Program vždy poběží jen na Windows a lokálním PC.
* UI musí být stabilní, s podporou dlouhých běhů a background operací (neblokovat UI).
* Použij standardní OpenAI SDK knihovnu (Python nebo JS). Implementace musí být plně funkční.

Komunikace s OpenAI:

* Responses API pro generování (s previous_response_id pro řetězení) – pro A/B/QA.
* Files API pro upload/list/delete.
* Models endpoint pro list modelů.
* Vector store + file_search (pokud je zvolen IN režim a model to podporuje).
* Batch API – pro režim C a pro sledování batch jobů.

Důležité:

* Program musí umět detekovat capabilities modelu a podle toho:

  * nepoužít nepodporované tooly (zejména file_search),
  * přepnout se do fallback režimu dle pravidel v sekci 11.3.1 pouze tam, kde je fallback definovaný a bezpečný.

---

## 4) Assets a povinné soubory

### 4.1 Fonty (TTF) – kritický kontrakt

* resources/montserrat_bold.ttf (Montserrat Bold)
* resources/montserrat_regular.ttf (Montserrat Regular)

Při startu aplikace MUSÍ dojít k načtení/registraci obou fontů z lokálních assets a font MUSÍ být použit pro celé UI. Aplikace NESMÍ spoléhat na systémovou instalaci fontů. Jiné fonty/řezy jsou zakázané. Pokud font nelze načíst/registrovat, aplikace MUSÍ zobrazit chybový dialog ve stylu „Kájovo“ a korektně se ukončit. Build proces MĚL BY kontrolovat hash souborů fontu (např. SHA-256), aby se nezměnila verze.

### 4.2 Degradovaný režim pro chybějící soubory (mimo fonty)

Pokud některý jiný povinný soubor chybí, uživatel musí dostat jasné hlášení a program musí pokračovat v degradovaném režimu, pokud to dává bezpečný smysl. Vše se zaloguje včetně toho, co přesně chybí, dopadu a aktivovaných omezení.

---

## 5) Start aplikace, okno a automatická inicializace

### 5.1 Hlavní okno

* Program se musí spustit v maximalizovaném okně na hlavním monitoru.
* Program nesmí znemožnit ovládání (žádné chování, které by blokovalo systémové ovládání; dialogy a overlaye jsou v rámci aplikace a jsou ovladatelné, program nezůstane ve stavu „zamrzlo“).

### 5.2 Jedna pracovní plocha, responsivní rozvržení do 1–3 sloupců

* Hlavní plocha je jedna scrollovatelná pracovní plocha („workspace“).
* Všechny sekce existují jako karty/sekce na této ploše.
* Layout je responsivní:

  * při velké šířce okna se sekce automaticky skládají do tří sloupců,
  * při menší šířce do dvou sloupců,
  * při malé šířce do jednoho sloupce.
* Nesmí existovat tvrdé fixní rozdělení na konkrétní sloupce.
* Horizontální scroll na úrovni celé plochy se nepoužívá; horizontální scroll pouze v konkrétních multiline polích, kde je explicitně požadován.

### 5.3 Automatická inicializace po startu

Před spuštěním UI program automaticky (na pozadí, s progress dialogem) provede:

* načtení aktuálního seznamu OpenAI modelů (pro uložený API key),
* načtení aktuálního seznamu souborů na Files API,
* načtení aktuálního seznamu existujících Vector Stores,
* načtení stavu všech rozpracovaných Batch jobů a jejich zobrazení v Batch monitoru.

Pokud API key chybí, místo toho zobrazí jasnou informaci, že automatická inicializace není možná, a nabídne uživateli otevřít dialog API-KEY.

---

## 6) Záhlaví (toolbar) – tlačítka a chování

V záhlaví obrazovky budou tlačítka:

Vlevo:

1. API-KEY
2. $ (CENY / ÚČTENKY / CENÍK)
3. SETTINGS
4. SAVE
5. LOAD
6. LOAD REQUEST

Vpravo:
7) EXIT

---

## 7) Dialog „API-KEY“

Po stisknutí API-KEY se otevře vyskakovací dialog s dlouhou řádkou (jednořádkové pole), kam lze napsat API key pro OpenAI.

Pod řádkou budou volby (tlačítka):

### 7.1 Uložit

* Uloží API key do systémových proměnných dat (system environment variables).
* Po uložení musí být program schopen key používat bez restartu (aktuální běh).

### 7.2 Zobraz

* Ukáže viditelně v řádku API key, pokud je uloženo v systémových proměnných.

### 7.3 Smazat

* Smaže uložené API key ze systémových proměnných.

### 7.4 STORNO

* Zavře okno beze změn.

---

## 8) SETTINGS

Tlačítko SETTINGS otevře samostatné okno (mimo hlavní pracovní plochu). SETTINGS je určeno pouze pro nastavení chodu programu, nikoliv pro plnění funkce requestu a jejich automatického zpracování.

SETTINGS musí obsahovat minimálně:

* lokace lokální databáze (default v adresáři programu),
* limity logování (rotace / max velikost / max počet runů),
* politika retry/backoff (limity, jitter, max pokusů, circuit breaker),
* ceník: zdroj (URL / lokální), refresh, cache TTL, auto-refresh při startu,
* bezpečnost:

  * volitelné maskování tajemství v logu (default: OFF, ale varování musí existovat),
  * volitelné šifrování logů (default OFF),
  * panic wipe lokální cache (tlačítko),
* Batch polling interval a timeouty,
* výchozí model a defaultní teploty dle politiky,
* povolení/konfigurace post-run hooks (lokálně/SSH),
* allow/deny list přípon a cest (glob patterns) odděleně pro IN mirror a diagnostické snapshoty,
* volitelný dry-run režim pro MODIFY.

---

## 9) $ (CENY) – samostatná obrazovka

Tlačítko $ otevře samostatnou obrazovku s:

### 9.1 Přehled ceníku

* tabulka cen per model:

  * input token,
  * output token,
  * batch pricing (pokud relevantní),
  * tool náklady (file_search),
  * storage náklady (vector store),
* datum poslední aktualizace,
* tlačítko „REFRESH CENÍK“,
* možnost nastavit zdroj ceníku a TTL (přes SETTINGS).

### 9.2 Účtenky (evidence)

* filtrování:

  * datum od–do,
  * projekt,
  * model,
  * režim (GENERATE/MODIFY/QA/C),
  * ResponseID / BatchID,
  * textový fulltext (v zadání / notes),
* detail účtenky:

  * rozpad tokenů a ceny,
  * rozpad tool/storage nákladů,
  * čas běhu, počty requestů, počty souborů,
  * příznak „odhad / neověřeno“ pokud nebyl ceník aktuální,
* akce:

  * export (JSON / CSV),
  * smazat vybrané,
  * sumarizace za období.

### 9.3 Vazba na LOG

* každá účtenka musí obsahovat přímý odkaz na příslušné LOG soubory (na disk, lokální path).

---

## 10) EXIT – potvrzení ukončení

Po stisknutí EXIT:

* zobrazí se potvrzovací dialog s upozorněním, že uživatel přijde o neuloženou práci, pokud nedal SAVE,
* pokud uživatel potvrdí, program se ukončí,
* pokud nepotvrdí, vrátí se do programu.

---

## 11) SAVE / LOAD / LOAD REQUEST (persistování stavu)

### 11.1 SAVE

* Uloží rozpracovanou mapu nastavení tak, jak je v danou chvíli zvolena.
* Po stisknutí se zobrazí výběr adresáře a jména souboru.
* Formát musí být JSON.
* Strukturu JSON určí program.
* Musí být uloženy všechny volby a stav UI.

### 11.2 LOAD

* Umožní vybrat uložený JSON soubor a nahrát nastavení obrazovky.
* Po načtení se UI přepne do stavu odpovídajícího uloženému souboru.

### 11.3 LOAD REQUEST

* Umožní vybrat request uložený v LOG/.
* Po načtení se v programu nastaví všechna zadání a dialogová okna explicitně identicky, jak byla nastavena a vyplněná při odeslání requestu.
* Uživatel může provést drobné úpravy a odeslat znovu.

Pravidla:

* Funkčně je LOAD REQUEST stejné jako LOAD, rozdíl je pouze v tom, odkud se stav bere:

  * LOAD = soubor uložený tlačítkem SAVE,
  * LOAD REQUEST = request log z LOG, který obsahuje embednutý UI state a technické vazby.
* LOAD REQUEST musí obnovit i technické vazby (model, režim, IN/OUT, attached file-id listy, vector_store_id, batch_id, response_id chain), ale pro nový běh se musí správně oddělit „historické ID“ od „nových volání“.

---

## 12) Sekce na hlavní ploše (karty/sekce)

Všechny níže uvedené sekce musí existovat. Jejich vizuální rozmístění je dynamické (responsivní) podle šířky okna.

### 12.1 Sekce „Zadání“

Obsah:

* Textový řádek (jednořádkové pole) s názvem „Název projektu:“ (není povinné). Je-li zadán, použije se ve všech requestech jako identifikátor.
* Dialogové okno pro zadání (multiline):

  * zobrazení cca 6 řádků,
  * při více řádcích posuvníky horizontálně i vertikálně.

### 12.2 Sekce „Připojené soubory“

* Tabulka souborů / id-files souborů.
* Tabulka by měla být vysoká tak, aby byly standardně vidět zhruba 2 soubory; při více souborech vertikální posuvný válec.
* Do tabulky lze přesunout soubory ze sekce FILE API.
* Tyto soubory:

  * lze odkazovat v promptu přes file-id,
  * objeví se v instructions jako jednotlivé soubory (každý zvlášť),
  * zároveň se objeví automaticky v promptu jako input,
  * budou označeny jako „pro informaci“.
* U každého souboru v tabulce bude ikonka koše, kterou lze z této tabulky soubor odstranit.

### 12.3 Sekce „IN“

Obsah:

* Tlačítko „VSTUP“,
* vedle něj textové pole (zobrazí kompletní PATH zvoleného adresáře).

Chování:

* Po stisku „VSTUP“ uživatel vybere libovolný adresář.
* Do textového pole se uloží kompletní PATH.

Funkce při spuštění requestu (až při GO):

* Pokud je vybrán IN adresář:

  * rekurzivně načti všechny soubory včetně podadresářů,
  * vyjmi adresáře: venv, .venv, LOG,
  * navíc vyjmi versing snapshot adresáře:

    * adresář, jehož název odpovídá patternu (končí 12 číslicemi a začíná shodně názvem root adresáře),
  * všechny kompatibilní soubory pro Files API uploaduj na Files API,
  * vytvoř mirror manifest (soubor) se všemi soubory + memo o neuploadovaných.

#### 12.3.1 Model capability check + fallback

Před použitím file_search + vector store musí program ověřit, zda zvolený model podporuje:

* file_search tool,
* práci s vector store (vector_store_ids).

Kontrola musí být robustní a může být provedena kombinací:

* metadata z Models endpointu (pokud dostupná),
* bezpečný „probe“ request a detekce chyb typu „tool not supported / invalid tool“.

Pokud model podporuje file_search + vector store:

* vytvoř vector store,
* připoj všechny uploadované soubory do vector store s atributem jejich plné původní PATH,
* připoj mirror manifest do vector store (aby ve store byla i existence neuploadovaných).

Pokud model nepodporuje file_search + vector store (fallback):

* nepoužívej vector store ani file_search tool,
* použij jen Files API:

  * uploadni kompatibilní soubory na Files API,
  * uploadni manifest na Files API,
* v requestech se pak mirror řeší takto:

  * v instructions uveď:

    * seznam všech input souborů včetně jejich původních PATH,
    * file_id pro každý uploadovaný soubor,
    * explicitně uveď manifest jako soubor (s file_id),
  * v input přilož všechny tyto soubory jako input_file (včetně manifestu),
  * bez tools a bez vector_store_ids.

Důležitá redundance (platí vždy):

* I když je zapnuté file_search + vector store, program musí redundantně poslat:

  * manifest i jako upload na Files API (aby měl file_id),
  * file_id manifestu (a všech input souborů) vypsat v instructions,
  * a současně tyto soubory přiložit jako input_file v input.

Označení (název) vector store:

* použij název projektu + čas ve formátu DDMMRRRRHHMM.

### 12.4 Sekce „OUT“

Obsah:

* Tlačítko „IN=OUT“,
* tlačítko „Výstup“,
* tlačítko „VERSING“,
* textové pole (výstupní PATH).

Chování:

* „Výstup“: vybere se adresář pro ukládání souborů přijatých v Response.
* „IN=OUT“:

  * nastaví výstupní adresář na stejný jako vstupní,
  * aktivuje možnost stisknutí tlačítka VERSING.
* Ukládání souborů:

  * do zvoleného OUT adresáře se uloží soubory přijaté v Response,
  * pokud ve stejném PATH existuje soubor stejného jména, bez upozornění se přepíše.

VERSING – funkce:

* Pokud je aktivní VERSING a přijde response alespoň s jedním souborem, který se má uložit do OUT:

  * před prvním zápisem program vytvoří ve zvoleném pracovním adresáři snapshot kopii aktuálního stavu,
  * snapshot adresář se vytvoří uvnitř pracovního adresáře,
  * název snapshot adresáře je: `<ROOT_NAME><DDMMRRRRHHMM>` (tj. root název + 12 číslic času).

Příklad:

* Projekt ABC je v adresáři `D:\PROJEKTY\ABC\`.
* V adresáři `D:\PROJEKTY\ABC\` jsou např. `.venv`, `LOG`, `app`, `data`, ...
* Při vytvoření nové verze se snapshot uloží do `D:\PROJEKTY\ABC\ABC171220251634\`.

Pravidla kopírování při VERSING:

* kopíruje se kompletní struktura pracovního adresáře, kromě:

  * `venv`, `.venv`, `LOG`,
  * a kromě všech adresářů, které odpovídají versing konvenci,
  * a samozřejmě kromě právě vytvářeného snapshot adresáře.
* Snapshot adresáře se považují za versing a proto se nikdy nezahrnují do IN mirroru ani do manifestu.

### 12.5 Sekce „DIAGNOSTICS (WINDOWS / SSH)“

V této sekci jsou volby:

* WINDOWS IN (checkbox),
* WINDOWS OUT (checkbox),
* SSH IN (checkbox),
* SSH OUT (checkbox).

Zakázané kombinace:

* Jakákoliv kombinace WINDOWS s SSH je zakázána (tj. nelze mít současně žádný WINDOWS checkbox a žádný SSH checkbox).

Povolené kombinace v rámci jedné skupiny:

* WINDOWS IN může být společně zvolena s WINDOWS OUT,
* SSH IN může být společně zvolena s SSH OUT.

Vynucení závislostí (tiše):

* pokud je zvolen WINDOWS OUT, musí být zvolen WINDOWS IN (program automaticky zaškrtne WINDOWS IN),
* pokud je zvolen SSH OUT, musí být zvolen SSH IN (program automaticky zaškrtne SSH IN).

Závislosti na výběru adresářů:

* WINDOWS IN nebo SSH IN lze použít jedině pokud je zvolen IN adresář,
* WINDOWS OUT nebo SSH OUT lze použít jedině pokud je zvolen OUT adresář.

Pokud uživatel zaškrtne volbu, která je v rozporu s výběrem adresářů, program:

* jasně zobrazí, co chybí (např. „Pro WINDOWS IN vyber IN adresář“),
* a zablokuje GO, dokud nebude stav validní.

Funkce:

* WINDOWS IN / SSH IN:

  * při spuštění „KÁJO GO'“:

    * WINDOWS IN: provede snímek kompletního nastavení Windows (diagnostický balík dle sekce 28: Full Windows Diagnostics),
    * SSH IN: po zadání IP adresy a autentizace, popřípadě jsou-li uloženy v sekci SETTINGS, automaticky stáhne diagnostický snímek vzdáleného systému (diagnostický balík dle sekce 27: Full Ubuntu (Web Server) Diagnostics),
  * diagnostický balík:

    * nahraje na Files API,
    * zaznamená do manifestu (včetně popisu obsahu a timestampů),
    * file_id se zapíše do instructions,
    * a zároveň se přiloží do input jako input_file části,
    * pokud je aktivní vector store, diagnostické soubory se navíc připojí do vector store s atributy (např. `source=diagnostics`, `scope=windows|ssh`, `captured_at=...`).

* WINDOWS OUT / SSH OUT:

  * při spuštění „KÁJO GO'“ program do instructions i do input redundantně zapíše očekávání, že výstup musí obsahovat:

    * vlastní skript,
    * soubor readmerepair.txt s popisem změny systému a návodem k ručnímu použití,
  * pro SSH OUT je skript spustitelný na Windows PC (skript je připraven tak, aby z Windows provedl změny na vzdáleném SSH pomocí stejného typu přístupu, který byl použit při SSH IN).

Bezpečnostní upozornění:

* Diagnostické snapshoty mohou obsahovat citlivá data; program musí před prvním použitím WINDOWS/SSH diagnostiky zobrazit varování a vyžádat potvrzení.
* Pokud je v SETTINGS maskování tajemství OFF, program upozorní, že citlivé údaje mohou skončit v LOG.

SSH UI (pokud je zvoleno SSH IN nebo SSH OUT):

* program se zeptá na:

  * uživatelské jméno (výchozí root),
  * IP adresu,
  * autentizace: SSH key (povinně podporovat; heslo volitelně jako fallback),
  * sudo se nepoužívá, jen root.

### 12.6 Sekce „MODE“

Obsah:

* Tlačítko „GENERATE“,
* Tlačítko „MODIFY“,
* Tlačítko „QA“,
* Pole RESPONSE ID (textové pole).

Pravidla:

* Může být zvoleno vždy jen jedno z tlačítek (GENERATE/MODIFY/QA).
* Alespoň jedno musí být zvoleno vždy.

Režimy:

* GENERATE:

  * nesmí být zvolen IN adresář,
  * musí být zvolen OUT adresář.
* MODIFY:

  * musí být zvolen IN i OUT adresář.
* QA:

  * nesmí být zvolen ani IN ani OUT adresář,
  * do instructions i do input se dá instrukce, že se čeká textová odpověď, žádný soubor.

RESPONSE ID:

* Umožňuje zadat Response-ID ručně.
* Je-li vyplněno:

  * použije se v requestu jako řetězení (previous_response_id).
* Není-li vyplněno:

  * request začne jako nový (bez previous_response_id).

### 12.7 Sekce „Model OpenAI“

Obsah:

* Tlačítko „GET MODELS“,
* roletka (dropdown) pro výběr OpenAI modelu.

Chování:

* Po stisknutí „GET MODELS“ se načtou aktuálně dostupné OpenAI modely pro uložený API key.
* Aktualizuje se seznam modelů ve dropdown.
* Vybraný model zůstává viditelný v roletce.

### 12.8 Sekce „BATCH MONITOR“

Funkce:

* seznam batch jobů (minimálně: „otevřené“ = vše kromě completed/failed/cancelled/expired),
* tlačítko „REFRESH“,
* zobrazení stavu a času vytvoření,
* tlačítko „DOWNLOAD RESULT“ (pokud je hotovo),
* tlačítko „OPEN LOG“ (link do LOG),
* „CANCEL“ (pokud API dovolí a uživatel chce).

### 12.9 Sekce „GO“

Obsah:

* Tlačítko „KÁJO GO'“,
* přepínač „SEND AS C (BATCH)“.

Chování:

* Spustí kroky v pořadí podle nastavení a provede plně automatické zpracování všeho, co je definováno na panelu.
* Během běhu se zobrazuje progress dialog (detailní kroky, %, ETA).
* Vše se loguje do LOG/.

### 12.10 Sekce „ANSWARE“

Obsah:

* Dialogové okno cca 6 řádků.
* Nadpis dialogového okna bude zobrazeno Response ID.
* V dialogovém okně se zobrazí odpověď z response.

Pod oknem tlačítka:

* CTRL+C,
* RESPONSE,
* SCRIPT.

CTRL+C:

* zkopíruje obsah dialogového okna včetně Response ID do schránky,
* vložení musí být jako neformátovaný text.

RESPONSE:

* zkopíruje přijaté Response ID do pole MODE/RESPONSE ID.

SCRIPT:

* je aktivní pouze pokud poslední response obsahovala skript + `readmerepair.txt`,
* po stisknutí:

  * program zobrazí potvrzení s varováním o nevratnosti,
  * po potvrzení spustí skript bez dry runu,
  * u SSH skriptu použije stejné IP/uživatelské jméno/klíč nebo heslo, které byly zadány při odesílání requestu (program je drží jako součást run state),
  * uživatel je informován o výsledku ve vyskakovacím okně,
  * do LOG se uloží stdout/stderr, návratový kód a audit provedených změn (pokud je ze skriptu dostupný).

### 12.11 Sekce „LOCAL FILES“

Obsah:

* tabulka souborů (cca 3–5 souborů, jinak scroll),
* tlačítka pod tabulkou:

  * VLOŽ,
  * UPLOAD.

Chování:

* „VLOŽ“:

  * uživatel vybere soubor,
  * soubor se vloží do tabulky,
  * lze vybrat více souborů,
  * každý řádek má ikonu pro smazání z tabulky.
* „UPLOAD“:

  * všechny soubory z tabulky uploaduj na Files API s purpose „user data“,
  * po uploadu:

    * automaticky aktualizuj přehled souborů v sekci FILE API,
    * tabulka LOCAL FILES se vyprázdní.

### 12.12 Sekce „FILE API“

Obsah:

* tabulka souborů (cca 4 souborů, jinak scroll),
* tlačítka pod tabulkou:

  * PŘIPOJ,
  * SMAŽ,
  * DEL ALL.

Chování:

* Tabulka:

  * kliknutím lze vybrat více souborů (multi-select).
* „PŘIPOJ“:

  * vybrané soubory se přesunou do sekce „Připojené soubory“.
* „SMAŽ“:

  * vybrané soubory smaž na Files API.
* „DEL ALL“:

  * smaž na Files API všechny soubory.

### 12.13 Sekce „VECTOR STORES“

Funkce:

* možnost se podívat, jaké aktuální vector store jsou založeny,
* co v nich je,
* prohlížet si jejich obsah,
* nastavovat souborům ručně atributy,
* jednotlivé soubory z vector store vyřazovat nebo zařazovat,
* nastavovat expiraci.

Povinné:

* list vector store,
* detail vybraného store:

  * expirace (view + update),
  * list souborů + jejich attributes,
  * odstranit soubor ze store,
  * přidat soubor do store (z Files API),
  * zobrazení „usage / velikost“ (pokud API poskytuje),
* vše logovat (co bylo změněno a kdy).

---

## 13) OpenAI request pipeline – logika A/B variant + tok C

### 13.1 Základní pravidla request builderu (platí pro A/B/QA)

Temperature policy:

* Jakmile je cílem vracet obsah výstupního souboru (kroky A3_FILE a B3_FILE), nastav temperature na 0.0.
* Pro všechny ostatní requesty používej temperature v rozsahu 0.0–0.2 (defaultně 0.2, pokud není důvod snížit).

Redundance výstupu:

* Vždy implementuj redundantní zadání očekávaného výstupu:

  * v instructions je kontrakt,
  * v input je redundantně zopakovaný kontrakt.

Projekt:

* Pokud je vyplněn „Název projektu“, použij ho jako identifikátor v každém requestu (např. v instructions a v log souborech).

Připojené soubory:

* Všechny soubory v sekci „Připojené soubory“ musí být v requestu:

  1. vyjmenované v instructions jako „pro informaci“ (každý zvlášť),
  2. a zároveň přiložené v input jako input_file části.

Řetězení:

* Pokud je vyplněn RESPONSE ID:

  * první request v daném běhu použije previous_response_id = hodnota pole RESPONSE ID.
* Pokud není vyplněn:

  * nezačínej řetězení.

Diagnostika (WINDOWS/SSH) – redundance:

* Pokud je aktivní WINDOWS IN / SSH IN:

  * diagnostické soubory se posílají redundantně:

    * existence + popis + file_id v instructions,
    * a zároveň jako input_file v inputu,
    * a případně i ve vector store, pokud je aktivní.
* Pokud je aktivní WINDOWS OUT / SSH OUT:

  * očekávání skriptu + `readmerepair.txt` se píše redundantně:

    * do instructions,
    * i do input.

### 13.2 Volba varianty podle MODE

* GENERATE → použij sérii A1 → A2 → A3-X
* MODIFY → použij sérii B1 → B2 → B3-X (protože IN vytváří mirror; primárně vector store + file_search, fallback dle 12.3.1)
* QA → pošli 1 request, který v instructions i v input říká, že se čeká jen textová odpověď, žádný soubor, a nepoužije IN/OUT

### 13.3 Chunking pro soubory delší než 500 řádků (A/B)

Pro A3/B3:

* Pokud má výstup souboru více než 500 řádků:

  * musí se vracet po chuncech,
  * program musí iterovat requesty pro stejný path, dokud soubor nebude kompletní,
  * v requestu pro file content se bude měnit jen path (a případně chunk_index).

---

## 14) Tok C (Batch-only, bez pipeline návazností, bez vstupních souborů, vše v jedné odpovědi)

Požadavek:

* C je vlastní nadefinovaný request, kde budou instructions stejné obsahově jako když se posílá prompt pro generování programu, ale bude naprosto separátně v programu sestavován a validován.
* C je jen přes BATCH (nikdy synchronně).
* C nevyužívá A1/A2/A3 ani B1/B2/B3, nemá řetězení, nemá previous_response_id.
* C nepoužívá žádný vstupní soubor:

  * žádný IN,
  * žádné attached files,
  * žádné file_search,
  * žádné vector_store_ids.
* C vrací v jedné odpovědi najednou všechny soubory.
* Jediné, co se využije ze stávající logiky, je zpracování přijatých souborů (uložení do OUT, VERSING, logování, evidence cen).

### 14.1 UI a validace pro C

Pokud uživatel zvolí „SEND AS C (BATCH)“, je to samostatný tok.

Validace:

* OUT musí být vybrán,
* IN nesmí být vybrán,
* připojené soubory musí být prázdné,
* RESPONSE ID se nepoužívá,
* model musí být zvolen.

Poznámka k diagnostice:

* diagnostika IN je v C zakázaná (protože C nemá IN),
* diagnostika OUT může být v C použita pouze tehdy, pokud je zvolena a validace je splněna (a pak C musí vrátit i skript + readmerepair).

### 14.2 C request kontrakt

C musí vyžadovat jediný výstupní JSON dokument:

* žádné markdown code-fences,
* žádné komentáře,
* žádné dodatečné vysvětlování mimo JSON.

KONTRAKT C_FILES_ALL:

```json
{
  "contract": "C_FILES_ALL",
  "project": {
    "name": "string",
    "target_os": "Windows 10/11",
    "runtime": "string",
    "language": "string"
  },
  "root": "string",
  "files": [
    {
      "path": "relative/path/file.ext",
      "purpose": "string",
      "content": "string"
    }
  ],
  "build_run": {
    "prerequisites": ["string"],
    "commands": ["string"],
    "verification": ["string"]
  },
  "notes": ["string"]
}
```

C request instructions musí obsahově odpovídat tomu, co jinak posíláš jako prompt pro generování programu (včetně požadavků na robustnost, logování, UI styl, atd.), ale C request builder je sestavuje samostatně.

Pokud je aktivní diagnostika OUT (WINDOWS OUT nebo SSH OUT), C request navíc explicitně vyžaduje, aby `files[]` obsahoval:

* `readmerepair.txt`,
* skript(y).

### 14.3 Batch implementace pro C

* vytvoř JSONL, který obsahuje právě 1 request (jedna řádka = jeden request na /responses),
* upload JSONL,
* vytvoř batch job,
* sleduj stav přes BATCH MONITOR,
* po dokončení stáhni výsledek.

Validace výsledku:

* `json.loads()`,
* validace schématu C_FILES_ALL,
* validace path pravidel:

  * relativní,
  * nesmí začínat `/`,
  * nesmí obsahovat `..`,
  * nesmí obsahovat `\\`,
  * žádné duplicity.

Pokud validace selže:

* výstup se uloží do karantény `OUT/_invalid/` a jasně se označí (v UI popup + log),
* soubory se nezapíší do cílových path.

### 14.4 Uložení souborů z C

* uložit všechny `files[]` do OUT,
* pokud existuje stejný soubor, bez upozornění přepsat,
* pokud je zapnutý VERSING, provést VERSING copy před prvním zápisem.

---

## 15) Explicitní kontrakty (A/B) – zachovat přesně (instructions + redundantní input)

Důležité: Kontrakty jsou postavené na instructions (bez text.format). Program musí odpovědi parsovat přes json.loads() a validovat.

### 15.1 Politika výstupů souborů (kritické)

* Pokud model generuje nebo modifikuje soubory, nikdy nesmí vracet DIFF/patch/změny.
* Očekává se kompletní výsledné znění souboru:

  * buď celé v jednom content,
  * nebo (u chunkingu) po částech, které se po spojení stanou kompletním souborem.
* Žádné markdown code-fences, žádné komentáře, žádné dodatečné vysvětlování mimo JSON.

### 15.2 A-varianta: neposílám jako vstup žádný soubor

A1) Request JSON – „PLAN (manifest / návrh projektu)“

* Bez file_search
* Bez vector store

```json
{
  "model": "<MODEL_FROM_UI>",
  "temperature": 0.2,
  "instructions": "Jsi senior software architekt a implementátor. MASTER: žádné externí soubory. OUTPUT: VRAŤ POUZE validní JSON. ŽÁDNÝ markdown, žádné komentáře, žádný další text. KONTRAKT A1_PLAN: {\"contract\":\"A1_PLAN\",\"project\":{\"name\":string,\"one_liner\":string,\"target_os\":string,\"language\":string,\"runtime\":string},\"assumptions\":[string],\"requirements\":{\"functional\":[string],\"non_functional\":[string],\"constraints\":[string]},\"architecture\":{\"modules\":[{\"name\":string,\"responsibility\":string}],\"data_flow\":[string],\"error_handling\":[string],\"security_notes\":[string]},\"build_run\":{\"prerequisites\":[string],\"commands\":[string],\"verification\":[string]},\"deliverable_policy\":{\"file_generation_strategy\":\"PLAN->STRUCTURE->FILE_CONTENT\",\"max_lines_per_chunk\":500}}",
  "input": "ZADÁNÍ PROGRAMU: <USER_SPEC>. REDUNDANTNÍ KONTRAKT: vrať pouze JSON dle A1_PLAN (bez md, bez textu navíc)."
}
```

A2) Request JSON – „STRUCTURE (výstup vlastní souborové struktury)“

* Navazuje přes previous_response_id

```json
{
  "model": "<MODEL_FROM_UI>",
  "previous_response_id": "<RESP_ID_FROM_A1_OR_USER_FIELD>",
  "temperature": 0.2,
  "instructions": "Jsi generátor projektové struktury podle schváleného plánu. OUTPUT: VRAŤ POUZE validní JSON. ŽÁDNÝ markdown ani další text. RULES: path musí být relativní, nesmí začínat '/', nesmí obsahovat '..' ani '\\\\', žádné duplicity. KONTRAKT A2_STRUCTURE: {\"contract\":\"A2_STRUCTURE\",\"root\":string,\"files\":[{\"path\":string,\"purpose\":string,\"language\":string,\"generated_in_phase\":\"A3\"}]}",
  "input": "VYGENERUJ FILE STRUCTURE pro projekt dle předchozího plánu. REDUNDANTNÍ KONTRAKT: vrať pouze JSON dle A2_STRUCTURE."
}
```

A3) Request JSON – „FILE CONTENT (obsah 1 konkrétního souboru)“

* Opakované volání pro každý path
* Chunking > 500 řádků

```json
{
  "model": "<MODEL_FROM_UI>",
  "previous_response_id": "<RESP_ID_FROM_A2_OR_USER_FIELD>",
  "temperature": 0.0,
  "instructions": "Jsi generátor obsahu jednoho konkrétního souboru podle A2_STRUCTURE. OUTPUT: VRAŤ POUZE validní JSON. ŽÁDNÝ markdown ani další text. KRITICKÉ: content je vždy čistý obsah souboru (ne DIFF, ne patch). U chunkingu posílej po částech, které se spojí do kompletního souboru. CHUNK: max 500 řádků v jednom chunku, dlouhé soubory vrať po částech. KONTRAKT A3_FILE: {\"contract\":\"A3_FILE\",\"path\":string,\"chunking\":{\"max_lines\":500,\"chunk_index\":integer,\"chunk_count\":integer,\"has_more\":boolean,\"next_chunk_index\":integer|null},\"content\":string}",
  "input": "Vrať obsah souboru PATH=<PATH_FROM_A2>. Pokud je dlouhý, použij chunking. Volitelně: CHUNK_INDEX=<N>. REDUNDANTNÍ KONTRAKT: vrať pouze JSON dle A3_FILE. KRITICKÉ: žádné DIFF/patch, jen čistý obsah souboru (nebo jeho chunk)."
}
```

### 15.3 B-varianta: posílám zrcadlo souborů do vector store a zapnu file_search

Pokud model nepodporuje file_search / vector store, v B-variantě se vynechá tools a místo toho se použijí soubory přes Files API + manifest dle 12.3.1.

B1) Request JSON – „PLAN (na základě mirroru)“

* file_search ON (pokud podporováno)
* vector_store_ids použij z IN kroku

```json
{
  "model": "<MODEL_FROM_UI>",
  "temperature": 0.2,
  "tools": [
    { "type": "file_search", "vector_store_ids": ["<VS_ID_FROM_IN_STEP>"] }
  ],
  "instructions": "Jsi senior debug/maintenance inženýr. MASTER SOURCE OF TRUTH: existující soubory/config jsou pouze ve vector store přes file_search. Nic si nevymýšlej. KRITICKÉ: i když máš file_search, ber v úvahu i přiložený manifest + input file-id jako redundantní zdroj. OUTPUT: VRAŤ POUZE validní JSON. ŽÁDNÝ markdown ani další text. WORKFLOW: vždy nejdřív použij file_search, pokud něco chybí uveď missing_inputs. KONTRAKT B1_PLAN: {\"contract\":\"B1_PLAN\",\"context\":{\"vector_store_ids\":[string],\"assumed_root\":string},\"diagnosis\":{\"summary\":string,\"evidence\":[{\"path\":string,\"reason\":string}],\"likely_root_causes\":[string]},\"change_plan\":{\"goals\":[string],\"files_to_modify\":[{\"path\":string,\"intent\":string}],\"files_to_add\":[{\"path\":string,\"intent\":string}],\"verification_steps\":[string]},\"missing_inputs\":[string]}",
  "input": "MÁŠ PŘÍSTUP K MIRRORU V VECTOR STORE (file_search je zapnutý, pokud model podporuje). ÚKOL: <USER_TASK>. REDUNDANTNÍ KONTRAKT: vrať pouze JSON dle B1_PLAN."
}
```

B2) Request JSON – „STRUCTURE (touched files)“

* Navazuje přes previous_response_id

```json
{
  "model": "<MODEL_FROM_UI>",
  "previous_response_id": "<RESP_ID_FROM_B1_OR_USER_FIELD>",
  "temperature": 0.2,
  "tools": [
    { "type": "file_search", "vector_store_ids": ["<VS_ID_FROM_IN_STEP>"] }
  ],
  "instructions": "Jsi implementátor změn nad existujícím systémem. MASTER: existující soubory a jejich aktuální obsah jsou pouze z vector store (file_search). KRITICKÉ: i když máš file_search, ber v úvahu i přiložený manifest + input file-id jako redundantní zdroj. OUTPUT: VRAŤ POUZE validní JSON. ŽÁDNÝ markdown ani další text. RULES: do touched_files nedávej nic, co neexistuje ve store (pokud to není nové). KONTRAKT B2_STRUCTURE: {\"contract\":\"B2_STRUCTURE\",\"touched_files\":[{\"path\":string,\"action\":\"modify\"|\"add\",\"intent\":string}],\"invariants\":[string]}",
  "input": "Na základě předchozího plánu a mirroru ve vector store vrať seznam souborů, které se budou měnit/přidávat. REDUNDANTNÍ KONTRAKT: vrať pouze JSON dle B2_STRUCTURE."
}
```

B3) Request JSON – „FILE CONTENT (1 soubor)“

* Opakované volání pro každý path
* Chunking > 500 řádků
* Pro modify musí vycházet z aktuální verze ve store (file_search)

```json
{
  "model": "<MODEL_FROM_UI>",
  "previous_response_id": "<RESP_ID_FROM_B2_OR_USER_FIELD>",
  "temperature": 0.0,
  "tools": [
    { "type": "file_search", "vector_store_ids": ["<VS_ID_FROM_IN_STEP>"] }
  ],
  "instructions": "Jsi implementátor obsahu jednoho konkrétního souboru. MASTER: pro modify vždy načti aktuální obsah souboru přes file_search a aplikuj změny. KRITICKÉ: content je vždy kompletní výsledné znění souboru (ne DIFF, ne patch). U chunkingu posílej po částech, které se spojí do kompletního souboru. OUTPUT: VRAŤ POUZE validní JSON. ŽÁDNÝ markdown ani další text. CHUNK: max 500 řádků v chunku. KONTRAKT B3_FILE: {\"contract\":\"B3_FILE\",\"path\":string,\"action\":\"modify\"|\"add\",\"chunking\":{\"max_lines\":500,\"chunk_index\":integer,\"chunk_count\":integer,\"has_more\":boolean,\"next_chunk_index\":integer|null},\"content\":string,\"notes\":[string]}",
  "input": "Vrať výsledný obsah souboru PATH=<PATH_FROM_B2> (ACTION=<modify|add>). Použij mirror ve vector store jako jediný zdroj pravdy pro existující verzi (pokud je dostupné). Pokud je dlouhý, vrať po chuncech. Volitelně: CHUNK_INDEX=<N>. REDUNDANTNÍ KONTRAKT: vrať pouze JSON dle B3_FILE. KRITICKÉ: žádné DIFF/patch, jen čistý obsah souboru (nebo jeho chunk)."
}
```

---

## 16) GO – exekuce kroků podle UI

Po stisku „KÁJO GO'“ proveď:

### 16.1 Validace nastavení

* MODE vybrán přesně jeden,
* GENERATE: IN nesmí být vybrán, OUT musí být vybrán,
* MODIFY: IN i OUT musí být vybrán,
* QA: IN ani OUT nesmí být vybrán,
* C: OUT musí být vybrán, IN nesmí být vybrán, attached files musí být prázdné, běží jen přes Batch,
* API key musí být dostupný (z uložených systémových proměnných nebo z dialogu),
* Diagnostics:

  * nelze kombinovat WINDOWS a SSH,
  * IN volby vyžadují vybraný IN adresář,
  * OUT volby vyžadují vybraný OUT adresář,
  * OUT volby si automaticky vynutí příslušnou IN volbu.

### 16.2 Připojené soubory (informational) – pouze A/B/QA

* Sestav seznam „Připojených souborů“ pro instructions + input,
* tyto soubory přidej do každého requestu jako:

  * textový blok v instructions (pro informaci),
  * content parts input_file v inputu.

### 16.3 Diagnostika IN (WINDOWS IN / SSH IN) – pokud je zvoleno

* Vygeneruj diagnostický balík dle specifikace v sekci 28 (Windows) nebo 27 (Ubuntu),
* uploadni diagnostické soubory na Files API,
* zapiš je do diagnostického manifestu + do mirror manifestu (jako externí artefakty s popisem),
* přilož je redundantně:

  * v instructions vypiš file_id + popis,
  * v input je přilož jako input_file,
  * pokud existuje vector store, připoj je i do store.

### 16.4 IN krok (pouze když je IN zvolen) – pouze MODIFY

* Rekurzivně projdi vstupní adresář (mimo venv, .venv, LOG a mimo versing snapshot adresáře dle 12.3),
* uploadni kompatibilní soubory na Files API,
* vytvoř mirror manifest se všemi soubory + memo o neuploadovaných,
* ověř podporu file_search + vector store pro zvolený model,
* pokud podporuje:

  * vytvoř vector store pojmenovaný „<NÁZEV_PROJEKTU><DDMMRRRRHHMM>“,
  * připoj všechny uploadované soubory do vector store s atributem jejich plné původní PATH,
  * připoj mirror manifest do vector store,
  * pokud jsou k dispozici diagnostické soubory, připoj i je,
* pokud nepodporuje:

  * nepoužívej vector store, nepoužívej file_search,
  * používej jen Files API a manifest (viz 12.3.1).

### 16.5 Očekávání skriptu (WINDOWS OUT / SSH OUT) – pokud je zvoleno

* Do instructions i do input přidej povinné očekávání:

  * response musí obsahovat skript(y),
  * response musí obsahovat `readmerepair.txt`.
* Po zpracování response:

  * `readmerepair.txt` se zobrazí v ANSWARE okně,
  * aktivuje se tlačítko SCRIPT.

### 16.6 Request pipeline podle režimu

* Pokud je zvoleno „SEND AS C (BATCH)“: tok dle sekce 14.
* Jinak:

  * GENERATE: A1 → A2 → pro každý file v A2: A3 (chunk loop) → ukládat do OUT,
  * MODIFY: B1 → B2 → pro každý touched file v B2: B3 (chunk loop) → ukládat do OUT,
  * QA: 1 request: text-only (v instructions i input explicitně), bez ukládání souborů.

### 16.7 OUT ukládání

* Pokud je aktivní VERSING a přijde alespoň jeden soubor k uložení:

  * udělej snapshot kopii dle pravidel v 12.4.
* Ukládej soubory:

  * bez upozornění přepisuj existující stejné soubory.

### 16.8 ANSWARE panel

* Zobraz Response ID v titulku,
* zobraz odpověď (raw nebo extrahovaný text/JSON) v okně,
* CTRL+C kopíruje Response ID + text,
* RESPONSE přenese Response ID do pole MODE/RESPONSE ID,
* pokud je přítomen `readmerepair.txt`, zobraz jeho obsah a zpřístupni SCRIPT.

---

## 17) File API panel – synchronizace

* Po každém uploadu a delete:

  * refresh seznamu FILE API.
* Po uploadu z LOCAL FILES:

  * vyprázdni LOCAL FILES tabulku.

---

## 18) Kompatibilní soubory pro Files API

Implementuj výběr souborů pro upload tak, aby:

* běžné textové konfigy a zdrojáky byly uploadovány,
* binární soubory a extrémně velké soubory byly detekovány jako nekompatibilní a zaznamenány do manifestu,
* `.env` a jiné citlivé soubory: preferuj neuploadovat a zaznamenat do manifestu (a případně generovat bezpečný seznam klíčů bez hodnot).

Existence souborů musí být v mirroru zachycena.

---

## 19) Další povinná rozšíření (detailně)

### 19.1 Spolehlivost a řízení běhu

A1) Cancel/Stop běhu

* V UI musí být vždy dostupné tlačítko „STOP“ (součást progress dialogu).
* STOP provede korektní ukončení:

  * zrušení čekání na batch (polling),
  * zrušení uploadů (pokud SDK umožní přerušení) nebo jejich bezpečné dokončení s jasným stavem,
  * zrušení dalších requestů v pipeline (nezahajovat nové).
* STOP nikdy nesmí nechat aplikaci ve stavu „zamrzlo“; progress dialog se přepne do režimu „Stopping...“ a po dokončení se zavře.
* Po STOP musí existovat možnost:

  * buď bezpečně „Resume“,
  * nebo „Close run“ (ukončit run).

A2) Resume běhu po pádu / restartu

* Program musí umět z LOG rekonstruovat poslední stav runu:

  * jaké kroky proběhly,
  * jaké file_id byly uploadnuté,
  * jaké response_id/batch_id už existují,
  * jaké soubory už byly uloženy.
* Při startu program nabídne „RESUME LAST RUN“, pokud najde nedokončený run.
* Resume musí být idempotentní:

  * pokud se krok už provedl, přeskočí se,
  * pokud není jistota, krok se provede znovu bezpečným způsobem (např. znovu stáhnout batch result, znovu validovat, znovu zapsat do karantény místo přepsání).

A3) Rate-limit & retry politika

* Implementuj retry s exponenciálním backoff + jitter.
* Circuit breaker:

  * pokud se opakovaně vrací rate-limit nebo 5xx, na čas se zastaví nové requesty a UI ukáže „Cooling down“.
* Retry pravidla:

  * retryovat transient chyby (429, 5xx, timeouts),
  * nikdy nere-tryovat chyby validace kontraktů (to je logická chyba výstupu).
* Veškeré retry pokusy se logují.

### 19.2 Bezpečnost práce se soubory a snapshoty

B1) Secret scanner + redakce

* Před uploadem do Files API (mirror i diagnostika) program projde soubory a detekuje:

  * `.env`,
  * klíče/tokeny (heuristiky a regexy),
  * privátní klíče/certy.
* Default chování:

  * citlivé soubory preferuj neuploadovat,
  * místo toho zapiš do manifestu, že existují, a uveď bezpečný popis (např. seznam klíčů bez hodnot).
* Pokud uživatel v SETTINGS vypne bezpečnostní omezení, program umožní upload i citlivých souborů, ale vždy zobrazí varování.

B2) Allow/Deny list přípon i cest

* V SETTINGS existuje konfigurace:

  * allow/deny list přípon,
  * allow/deny list cest (glob patterns),
* odděleně pro:

  * IN mirror,
  * diagnostické snapshoty.
* V manifestu musí být vždy vidět, co bylo vynecháno a proč.

B3) Šifrování logů + panic wipe

* Volitelné šifrování lokálních logů (minimálně: symetrické šifrování celé LOG složky nebo per-file, klíč uložen dle Windows bezpečného úložiště, pokud dostupné).
* „Panic wipe“:

  * smaže lokální cache (dočasné soubory, stažené batch výsledky, price cache),
  * volitelně smaže i nešifrované logy,
  * vždy vyžádá potvrzení.

### 19.3 Vývojářské workflow

C1) Dry-run režim pro MODIFY

* Volitelně v SETTINGS.
* V dry-run režimu pro MODIFY:

  * AI nejdřív vrátí seznam změn + rizika + touched files (bez generování obsahu),
  * uživatel musí potvrdit pokračování, teprve pak se spustí B3 generování obsahů.
* Dry-run výstup se loguje a je součástí run bundle.

C2) Post-run hooks

* Po uložení souborů program může spustit:

  * testy,
  * lint,
  * format.
* Hooky lze spustit:

  * lokálně,
  * nebo přes SSH (pokud je k dispozici SSH konfigurace pro hooky).
* Výstup hooků (stdout/stderr, návratové kódy) se loguje.

C3) Diff viewer

* V UI existuje diff viewer pouze pro přehled uživatele.
* Do AI se vždy posílá full content dle kontraktů.

### 19.4 Observabilita (kromě LOG souborů)

D1) Run timeline

* Program vede interní timeline:

  * krok,
  * start/end timestamp,
  * výsledek,
  * související IDs (file_id, response_id, batch_id, vector_store_id).
* Timeline je viditelná v UI a exportovatelná do LOG.

D2) Export „Run bundle“ (zip)

* Program umí vytvořit zip balík:

  * requesty,
  * response,
  * manifesty,
  * snapshoty (pokud uživatel povolí; jinak jen odkazy),
  * skripty,
  * `readmerepair.txt`,
  * timeline.
* Export je dostupný z UI (např. z progress dialogu po dokončení).

### 19.5 Pricing

* Program podporuje automatickou aktualizaci ceníku z oficiálních zdrojů (konfigurovatelný endpoint/URL v SETTINGS).
* Pro každý běh se ukládá účtenka:

  * usage (input/output tokens),
  * batch discount/pricing,
  * tool calls (file_search),
  * storage-days odhad (z usage_bytes a času držení).
* Pokud ceny nelze ověřit (offline, endpoint nedostupný):

  * běh může pokračovat,
  * účtenka je označena „odhad / neověřeno“.

---

## 20) Co musí výstupní program „Kája“ dodat

* Plně funkční aplikaci dle UI a logiky výše,
* kompletní zdrojové soubory projektu,
* jasné instrukce pro spuštění (např. `pip install -r requirements.txt` + `python ui_main.py` nebo ekvivalent),
* implementaci kroků, kontraktů A1/A2/A3 a B1/B2/B3, logování, progress dialogy, SAVE/LOAD/LOAD REQUEST, API-KEY správu, FILE API management,
* Windows/SSH diagnostics (IN snapshot + OUT očekávání skriptu),
* Vector store management,
* Batch monitor a tok C (Batch-only),
* Pricing screen „$“ a účtenky,
* VERSING dle pravidel v sekci 12.4.

---

## 21) KÁJOVO UI DESIGN STANDARD (MASTER v1.0)

title: KÁJOVO UI DESIGN STANDARD
designation: MASTER
version: MASTER v1.0
date: 2026-01-07
status: ZÁVAZNÉ
audience: OpenAI generování programů
language: cs-CZ

Tento standard je normativní specifikace použitelná přímo jako vstup pro generování UI. Klíčová slova MUSÍ / NESMÍ / MĚL BY se vykládají striktně.

---

## 22) TOKENS (jediný zdroj pravdy)

### 22.1 Barvy (HEX)

* BÍLÁ: `#FFFFFF`
* ČERNÁ: `#000000`
* ČERVENÁ: `#FF0000`
* ŠEDIVÁ: `#808080`

### 22.2 Vrstvy prvku (pořadí a tloušťky)

Každý prvek je složen ze čtyř vizuálních vrstev (zvenku dovnitř):

1. LEM: 2 px
2. OKRAJ: 1 px
3. POZADÍ: vnitřní výplň
4. OBSAH: text / piktogram / grafika

„Obsahová plocha“ = plocha uvnitř okraje (uvnitř lemu 2 px a okraje 1 px).

### 22.3 UI SCALE (deterministicky)

* Referenční okno: `1280 × 720` při `UI_scale = 1.0`.
* Výpočet: `UI_scale = min(W/1280, H/720)` kde `W` a `H` jsou aktuální rozměry okna.

Co se škáluje:

* typografie, paddingy, výšky řádků, rozměry ikon, rozměry modulů času/datu, tloušťky ikon čar.

Co se neskáluje:

* tloušťka lemu (2 px) a okraje (1 px),
* minimální mezera mezi sibling prvky (2 px).

### 22.4 Typografie (font kontrakt)

Povolený font:

* Rodina: Montserrat
* Řezy: Regular, Bold
* Jiné fonty/řezy jsou zakázané.

Distribuce fontu (determinismus):

* Fonty MUSÍ být součástí aplikace (bundled assets/resources).
* Aplikace NESMÍ spoléhat na systémovou instalaci fontů.
* Aplikace MUSÍ fonty načíst/registrovat při startu.
* Pokud font nelze načíst, aplikace MUSÍ zobrazit chybový dialog ve stylu Kájovo a ukončit se.
* Build proces MĚL BY kontrolovat hash souborů fontu (např. SHA-256).

Velikosti:

* Základní velikost písma při `UI_scale = 1.0`: `FS_base = 16 px`
* Výsledná velikost písma: `FS = round(FS_base × UI_scale)`
* Line-height: `LH = round(1.25 × FS)`

Použití:

* Nadpisy / názvy sekcí / hlavičky tabulek: Montserrat Bold, VŠE VELKÝMI PÍSMENY
* Běžný obsah a hodnoty: Montserrat Regular

### 22.5 Radius (zaoblení)

* Radius všech prvků: `R = max(1, round(0.35 × FS))`
* Ostré rohy jsou zakázány.

### 22.6 Rozestupy a standardní rozměry (tokeny)

* Minimální mezera mezi dvěma sibling prvky (vnější hrana lemu → vnější hrana lemu): `GAP_MIN = 2 px`
* Standardní vnitřní odsazení (padding) pro prvky s textem:

  * `PAD_X = round(0.8 × FS)`
  * `PAD_Y = round(0.4 × FS)`
* Standardní výška řádku seznamů/roletek/tabulek: `ROW_H = round(1.4 × LH)`
* Výška stavového řádku: `STATUS_H = round(1.6 × LH)`

### 22.7 Ikony (obecný styl)

* Základní velikost ikony: `ICON = round(1.5 × FS)` (čtverec ICON×ICON)
* Ikony jsou outline (bez výplně), pokud není u komponenty výslovně řečeno jinak.
* Tloušťka čáry ikony: `STROKE = max(1, round(0.12 × FS))`
* Barva ikony je vždy jedna z token barev.

---

## 23) Obecná pravidla vzhledu

1. UI je striktně černobílé. Šedivá pouze pro neaktivní text/symboly. Červená pouze pro ukončení/kritické akce a stavy „nebezpečí/nedostupné“.
2. Žádné stíny, gradienty, textury, průhlednosti (mimo overlay vrstvy), ani dekorativní efekty.
3. Všechny komponenty MUSÍ používat vrstvy LEM/OKRAJ/POZADÍ/OBSAH.
4. Překryv je zakázán (výjimky: tooltip, roletka, dialog, overlay scrollbar).
5. Změna stavu prvku NESMÍ změnit layout/rozměry – mění se pouze barvy (a u textu případně velikost v rámci pravidel fit/scale).

---

## 24) Interakce a input (globální kontrakty)

### 24.1 Hover

* Běžné prvky: žádný hover efekt.
* Tooltipy:

  * tabulky: tooltip jen při overflow a po 2 s setrvání,
  * dropdown/combobox položky: tooltip jen při overflow a po 2 s setrvání.

Tooltip (vzhled):

* lem: černý 2 px
* okraj: bílý 1 px
* pozadí: bílá
* text: černý (Regular)

### 24.2 Focus a klávesnice

* TAB → další fokusovatelný prvek
* SHIFT+TAB → předchozí fokusovatelný prvek
* ENTER nebo MEZERNÍK aktivuje fokusovaný prvek (výjimka: textová pole)

Vizuální indikace focusu:

* Focus je overlay stav: LEM = bílý (#FFFFFF) 2 px.
* Ostatní vrstvy zůstávají dle aktuálního stavu prvku.
* Focus nesmí měnit rozměry prvku ani rozložení.

### 24.3 ESC (globální)

* ESC zavře otevřený overlay prvek v pořadí:

  1. tooltip,
  2. roletka dropdown/combobox,
  3. dialog.
* ESC odpovídá akci CLOSE v dialogu.

---

## 25) Layout aplikace (záhlaví, sekce, status)

### 25.1 Záhlaví programu

Záhlaví musí obsahovat:

* název programu a verzi,
* ovládací prvky,
* ukončovací prvek (EXIT/QUIT/CLOSE) vpravo.

Ukončovací prvek je vždy kritický (červený kontrakt).

### 25.2 Sekce

Sekce je rámovaná oblast (LEM/OKRAJ/POZADÍ) pro logickou skupinu prvků.

Název sekce:

* je součást horního lemu (přeruší horní lem),
* typografie: Bold, VŠE VELKÝMI PÍSMENY.

### 25.3 Stavový řádek (STATUS)

Výška: `STATUS_H`.

Status vždy zobrazuje:

* vlevo: text aktuálního kroku (Regular) + volitelně progress „teploměr“
* vpravo: čas (PRAGOTRON) a datum (kalendářová kresba)

Čas (PRAGOTRON / flap):

* formát: `HH:MM:SS`,
* každý znak je samostatný modul (obdélník se zaoblením R),
* modul: lem bílý 2 px, okraj černý 1 px, pozadí černé, znak bílý Bold,
* uprostřed modulu je vodorovná bílá linka 1 px („spára“),
* výška modulu: `TIME_H = round(1.2 × LH)`.

Datum (kalendářová kresba):

* modul se zaoblením R, lem/okraj jako ostatní moduly,
* uvnitř horní lišta oddělená bílou linkou 1 px,
* uvnitř text `DD.MM.YYYY` (Bold, bílý) vycentrovaný.

Progress „teploměr“:

* rám: lem bílý 2 px, okraj černý 1 px,
* pozadí: černé,
* výplň: bílá,
* odhadnutelný proces: výplň odpovídá 0–100 %,
* neodhadnutelný proces: bílý segment se pohybuje tam a zpět.

---

## 26) Kontrakty barevných stavů (globální)

### 26.1 Nečervené interaktivní prvky (3 stavy)

NORMAL:

* lem: bílý
* okraj: černý
* pozadí: černé
* obsah: bílý

ACTIVE:

* lem: bílý
* okraj: černý
* pozadí: bílý
* obsah: černý

DISABLED:

* lem: šedivý
* okraj: černý
* pozadí: černý
* obsah: šedivý

### 26.2 Červené (kritické) prvky (2 stavy)

NORMAL:

* lem: červený
* okraj: černý
* pozadí: červené
* obsah: černý

ACTIVE:

* lem: červený
* okraj: černý
* pozadí: černé
* obsah: červený

Pozn.: Focus overlay (sekce 24.2) mění jen lem na bílý i u červeného prvku.

---

## 27) Komponenty (specifikace)

### 27.1 Tlačítko (button)

Vzhled:

* používá kontrakt stavů dle sekce 26,
* padding: `PAD_X`, `PAD_Y`,
* radius: `R`,
* text: Bold (pokud je primární volba) nebo Regular (sekundární; pokud není jasné, použij Bold).

Chování:

* Klik = aktivace.
* Klávesnice: focus + ENTER/MEZERNÍK = aktivace.

Šířka vs délka textu:

* Preferovaná šířka: `W_pref = textWidth(FS) + 2×PAD_X + 2×(LEM+OKRAJ)`,
* skupina tlačítek může být roztažená proporcionalně podle `W_pref`,
* pokud dostupný prostor nestačí, použije se algoritmus text fit/scale.

### 27.2 Přepínač / Toggle, Radio, Checkbox, Klikatelná ikona

* používají stejné vrstvy a stejný stavový kontrakt,
* nesmí měnit layout mezi stavy,
* musí být fokusovatelné a ovladatelné klávesnicí,
* ikony outline dle tokenů, `STROKE`, barva dle stavu.

### 27.3 Tabulka / seznam

* sloupce měnitelná šířka,
* řádky neměnitelná výška: `ROW_H`,
* výběr řádku: invert barvy (ACTIVE kontrakt na celý řádek),
* kopírování: CTRL+C kopíruje vybraný text/buňku/řádek,
* tooltip v tabulce po 2 s jen při overflow.

### 27.4 Dialog

Geometrie:

* obdélník se zaoblením R, lem bílý 2 px, okraj černý 1 px, pozadí černé.

Název dialogu:

* vlevo nahoře, přeruší horní lem,
* Bold, VŠE VELKÝMI PÍSMENY.

Obsah:

* standardně 5 řádků: `5×LH`,
* pokud je obsahu více, použije se vertikální overlay scrollbar.

Akce (vždy přítomné):

* OK / CANCEL / CLOSE (vždy).

Klávesy:

* ENTER = OK (pokud focus není v zadávacím textovém poli),
* ESC = CLOSE,
* TAB/SHIFT+TAB cyklují mezi fokusovatelnými prvky.

### 27.5 Textové pole

Typ:

* Zadávací (editable),
* Zobrazovací (read-only) – vždy umožňuje selection a kopírování.

Vzhled:

* lem: bílý 2 px,
* okraj: černý 1 px,
* pozadí: černé,
* text: bílý (Regular),
* placeholder: šedivý (Regular).

Placeholder řetězce:

* Zadávací pole: `Zde pište...`
* Zobrazovací pole: `Zde se zobrazí text...`

Caret:

* pouze u zadávacího pole s focusem,
* blikající svislá čára střídající bílou a černou.

Selection:

* vybraný text: černý,
* pozadí selection: bílá.

### 27.6 Text fit / scale-to-fit / fallback scroll (globální algoritmus)

1. Aplikuj UI_scale.
2. Prvek může zvětšit šířku podle `W_pref`, pokud to layout dovolí.
3. Pokud overflow, zmenši obsah scale-to-fit tak, aby se vešel.
4. Pokud stále overflow, povol overlay scrollbar pouze na ose overflow.
5. Scroll se nesmí aktivovat preventivně.

### 27.7 Scrollbar (posuvný válec)

* overlay (neubírá místo),
* track: černý, track okraj: bílý, thumb: bílý,
* šířka: `max(6, round(0.8 × FS))`,
* pokud overlay scrollbar nelze, je povolena výjimka (standardní scrollbar), ale barvy musí odpovídat paletě.

### 27.8 Dropdown (needitovatelný)

* needitovatelný box dle nečerveného kontraktu,
* vpravo šipka dolů (ikona),
* roletka jako overlay, 5 položek, více = overlay scrollbar,
* pozadí černé, okraj bílý, oddělovače 1 px bílé,
* text bílý (Regular),
* hover uvnitř roletky: invert řádek,
* klávesnice: šipka dolů otevře a posouvá; mezerník potvrdí; ESC zavře; tooltip po 2 s jen při overflow.

### 27.9 Combobox (editovatelný)

* textové pole + roletka,
* filtrování během psaní: case-insensitive prefix match,
* diakritika se porovnává přesně,
* klávesnice: šipka dolů otevře; ENTER vloží položku a zavře; mezerník vkládá znak mezery mimo režim výběru; ESC zavře,
* tooltip po 2 s jen při overflow.

### 27.10 Referenční tvary ikon (SVG, viewBox 0 0 24 24)

Šipka dolů (dropdown):

```svg
<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
  <path d="M6 9 L12 15 L18 9" />
</svg>
```

CLOSE (X):

```svg
<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
  <path d="M7 7 L17 17" />
  <path d="M17 7 L7 17" />
</svg>
```

Kalendář (datum ve status):

```svg
<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
  <rect x="5" y="6" width="14" height="14" rx="2" ry="2" />
  <path d="M5 10 H19" />
  <path d="M8 4 V8" />
  <path d="M16 4 V8" />
</svg>
```

INFO (volitelně):

```svg
<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
  <path d="M12 10 V17" />
  <path d="M12 7 H12.01" />
</svg>
```

---

## 28) FULL Ubuntu (Web Server) Diagnostics

Tento manuál definuje FULL diagnostický balík pro Ubuntu server, typicky hostující webový stack (např. nginx, databáze, Python aplikace, webhooky, certifikáty). Cílem je poskytnout konzistentní adresář se soubory (bez ZIP) tak, aby bylo možné rychle určit příčinu chyb (konfigurace, procesy, síť, certifikáty, permissions, venv, systémové limity).

Výstup je adresář se soubory na Ubuntu (a volitelně stažený na Windows). Neprovádí se kopie databází ani tajných klíčů – jen konfigurace, metadatové přehledy a logy.

### 28.1 Zásady bezpečnosti

* Neexportovat privátní klíče (TLS, SSH), tokeny a hesla.
* Konfigurační soubory, které mohou obsahovat tajemství, exportovat buď:

  * se základním maskováním (např. `password=***`), nebo
  * jako metadata (cesta, vlastník, práva, hash) + ruční poskytnutí redigované verze.
* Logy mohou obsahovat osobní údaje; sdílet selektivně.

### 28.2 Struktura kořenového adresáře

```
Diag_YYYYMMDD-HHMMSS/
│
├─ MANIFEST.md
├─ README_problem.md
├─ CHANGELOG_last_actions.txt
│
├─ system/
├─ hardware/
├─ storage/
├─ network/
├─ firewall/
├─ users_permissions/
├─ processes_services/
├─ packages/
├─ web/
├─ nginx/
├─ app/
├─ python/
├─ database/
├─ certs/
├─ cron_webhooks/
├─ logs/
└─ virtualization_containers/
```

### 28.3 README_problem.md (ručně doplnit)

Minimální šablona:

* Symptom (co nefunguje)
* Repro kroky
* Očekávání vs realita
* Kdy to začalo (konkrétní datum/čas)
* Poslední změny (deploy, update, cert renewal, firewall)
* Výpis chyby (kopie výstupu)

### 28.4 system/

* `os_release.txt` – `/etc/os-release`, kernel
* `uname_a.txt` – `uname -a`
* `uptime.txt` – `uptime`, `who -b`
* `locale_timezone.txt` – `locale`, `timedatectl`
* `hostname_hosts.txt` – `hostnamectl`, `/etc/hosts`, `/etc/hostname`
* `sysctl_all.txt` – `sysctl -a`
* `limits_summary.txt` – `ulimit -a`, `/etc/security/limits.conf` + `limits.d` listing
* `systemd_failed_units.txt` – `systemctl --failed`
* `systemd_running_units.txt` – `systemctl list-units --type=service --state=running`
* `journal_boot_errors.txt` – `journalctl -b -p err..alert --no-pager`
* `env_root_sanitized.txt` – environment relevantní pro služby (bez tajemství)

### 28.5 hardware/

* `cpuinfo.txt` – `/proc/cpuinfo` (souhrn)
* `meminfo.txt` – `/proc/meminfo` + `free -h`
* `load_ps_top.txt` – `ps aux --sort=-%cpu`, `ps aux --sort=-%mem` + `top -b -n 1`
* `dmesg_tail.txt` – `dmesg -T | tail -n 400`

### 28.6 storage/

* `df_h.txt` – `df -hT`
* `lsblk.txt` – `lsblk -f`
* `mounts.txt` – `mount`, `/etc/fstab`
* `inode_usage.txt` – `df -ih`
* `disk_health_hint.txt` – (pokud dostupné) `smartctl -H` pro hlavní disk (jen status)
* `largest_dirs_root.csv` – top složky v `/` (1–2 úrovně) podle velikosti
* `largest_dirs_var.csv` – top složky v `/var` podle velikosti

### 28.7 network/

* `ip_addr.txt` – `ip a`
* `ip_route.txt` – `ip r`
* `resolv_conf.txt` – `/etc/resolv.conf` + `systemd-resolve --status` (pokud je)
* `ss_listen.txt` – `ss -lntup`
* `ss_all.txt` – `ss -antup`
* `netstat_fallback.txt` – pokud `net-tools` existuje
* `dns_check.txt` – `getent hosts` pro klíčové domény (pokud definováno)
* `proxy_env.txt` – HTTP(S)_PROXY env (pokud existuje)

### 28.8 firewall/

* `ufw_status.txt` – `ufw status verbose` (pokud je ufw)
* `iptables_rules.txt` – `iptables -S` + `iptables -L -n -v`
* `nft_ruleset.txt` – `nft list ruleset` (pokud nft)

### 28.9 users_permissions/

* `users_groups.txt` – `getent passwd`, `getent group` (bez hashů)
* `sudoers_listing.txt` – listing `/etc/sudoers*` (bez obsahu, nebo redigovaně)
* `umask.txt` – `umask`
* `important_paths_permissions.txt` – vlastníci/práva u `/etc/nginx`, `/var/www`, `/srv`, `/opt`, app dirs

### 28.10 processes_services/

* `ps_full.txt` – `ps auxfww`
* `systemctl_status_nginx.txt` – `systemctl status nginx --no-pager`
* `systemctl_status_app.txt` – status aplikace (gunicorn/uvicorn/systemd unit) pokud existuje
* `systemctl_status_db.txt` – status DB služby pokud existuje
* `open_files_limits.txt` – `cat /proc/sys/fs/file-max` + `lsof` souhrn (pokud dostupné)

### 28.11 packages/

* `dpkg_list.txt` – `dpkg -l`
* `apt_policy_key_pkgs.txt` – verze balíků: nginx, openssl, python3, certbot, postgres/mysql, redis
* `snap_list.txt` – `snap list` (pokud)
* `pip_global_list.txt` – `python3 -m pip list` (pokud pip)

### 28.12 web/

* `web_root_overview.txt` – přehled document rootů (např. `/var/www`, `/srv/www`) – strom do hloubky 3
* `static_assets_sizes.csv` – top složky podle velikosti v dokument rootech
* `web_server_headers_hint.txt` – (volitelně) lokální `curl -I` na `http://127.0.0.1` a vybrané vhosty

### 28.13 nginx/

* `nginx_version.txt` – `nginx -V` (včetně configure arguments)
* `nginx_test.txt` – `nginx -t` output
* `nginx_conf_tree.txt` – strom `/etc/nginx` (hloubka 4)
* `nginx_conf_files_list.txt` – seznam `.conf` souborů
* `nginx_sites_enabled.txt` – listing sites-enabled/sites-available
* `nginx_effective_config.txt` – `nginx -T` (pozor na tajemství; případně redigovat)
* `nginx_logs_tail_access.txt` – tail relevantních access logů
* `nginx_logs_tail_error.txt` – tail relevantních error logů

### 28.14 app/

* `app_dirs_overview.txt` – přehled typických umístění aplikace: `/srv`, `/opt`, `/var/www`, `~/apps`
* `systemd_units_related.txt` – grep na služby (gunicorn/uvicorn/celery/worker)
* `app_env_files_found.txt` – nalezené `.env`, `config*.yml`, `settings.py` (jen cesty + práva + hash)
* `app_permissions_summary.txt` – vlastník/práva pro app dir + data dir
* `webhook_configs_found.txt` – konfigurace webhooků (jen cesty + metadata)

### 28.15 python/

Cíl: odhalit konflikty mezi více instalacemi, venv, pyenv, poetry, pipx.

* `python_versions.txt` – `which -a python python3 pip pip3` + `python3 --version`
* `python_alternatives.txt` – `update-alternatives --display python3` (pokud existuje)
* `python_sys_path.json` – `python3 -c "import sys, json; print(json.dumps(sys.path, indent=2))"`
* `python_site.json` – `python3 -c "import site, json; print(json.dumps({'sitepackages': site.getsitepackages(), 'usersite': site.getusersitepackages()}, indent=2))"`
* `pip_debug.txt` – `python3 -m pip debug -v` (pozor na index/token; redigovat)
* `pip_config_list.txt` – `pip config list -v` (cesty na configy)

Venv/Poetry/Pipenv/Pyenv discovery:

* `venv_candidates_found.txt` – hledání `pyvenv.cfg` a `bin/python` v `/srv`, `/opt`, `/var/www`, `/home`, `/root`
* `venv_tree_summaries/venv_<name>.txt` – strom venv (bin/, lib/, site-packages) + velikost
* `venv_reports/venv_<name>__report.json` – pro každou venv:

  * `sys.executable`, `sys.prefix`, `sys.base_prefix`, `sys.path`,
  * `pip --version`, `pip list`, `pip freeze`

### 28.16 database/

Bez exportu dat; jen konfigurace, verze, stav, připojení, logy.

PostgreSQL (pokud existuje):

* `postgres_version.txt` – `psql --version`
* `postgres_service_status.txt` – `systemctl status postgresql --no-pager`
* `postgres_conf_locations.txt` – cesty na `postgresql.conf`, `pg_hba.conf` (jen metadata + hash)
* `postgres_listen_ports.txt` – `ss -lntup | grep postgres`
* `postgres_logs_tail.txt` – tail relevantních logů

MySQL/MariaDB (pokud existuje):

* `mysql_version.txt`
* `mysql_service_status.txt`
* `mysql_conf_locations.txt` – my.cnf locations (metadata)
* `mysql_logs_tail.txt`

Redis (pokud existuje):

* `redis_version.txt`
* `redis_service_status.txt`
* `redis_conf_metadata.txt`
* `redis_logs_tail.txt`

### 28.17 certs/

* `cert_locations.txt` – přehled `/etc/letsencrypt`, `/etc/ssl`, custom cert dirs
* `certbot_status.txt` – `certbot certificates` (pokud)
* `cert_expiry_report.txt` – expirace certů (např. `openssl x509 -enddate` pro vybrané)
* `tls_private_keys_presence.txt` – jen detekce přítomnosti klíčů (bez exportu obsahu)

### 28.18 cron_webhooks/

* `crontab_root.txt` – `crontab -l` (root)
* `crontab_users_listing.txt` – které user crontaby existují
* `system_cron_dirs_tree.txt` – `/etc/cron.*` listing
* `webhook_endpoints_inventory.txt` – soupis endpointů (z nginx/app config; bez tajemství)

### 28.19 logs/

* `journal_nginx_last500.txt` – `journalctl -u nginx -n 500 --no-pager`
* `journal_app_last500.txt` – `journalctl -u <app> -n 500 --no-pager` (pokud)
* `journal_db_last500.txt` – pro DB (pokud)
* `syslog_tail.txt` – `/var/log/syslog` tail (Ubuntu) nebo `journalctl` fallback
* `auth_log_tail.txt` – `/var/log/auth.log` tail (opatrně)
* `kernel_log_tail.txt` – `journalctl -k -n 400 --no-pager`

### 28.20 virtualization_containers/

* `docker_info.txt` – `docker info` (pokud)
* `docker_ps.txt` – `docker ps -a` (pokud)
* `docker_compose_files_found.txt` – nalezené `docker-compose*.yml` (cesty + metadata)
* `k8s_hint.txt` – pokud je microk8s/k3s (stav)

### 28.21 Automatizovaný sběr – doporučený běh

1. Na Ubuntu spustit sběr (bash skript), který vytvoří `Diag_YYYYMMDD-HHMMSS/` a naplní podsložky.
2. Volitelně stáhnout celý adresář na Windows přes SCP.

### 28.22 Jednořádkový příkaz z Windows PowerShellu (remote sběr + stažení)

Varianta A (doporučeno): SSH klíč, bez hesla v příkazu

```powershell
$ip="<IP_ADDRESS>"; $user="root"; $script=@'
#!/usr/bin/env bash
set -o pipefail
set +e
timestamp=$(date +%Y%m%d-%H%M%S)
remote_root="/root/Diag_${timestamp}"
mkdir -p "$remote_root"
mkdir -p "$remote_root/system"
(cat /etc/os-release) > "$remote_root/system/os_release.txt" 2>&1
mkdir -p "$remote_root/system"
(uname -a) > "$remote_root/system/uname.txt" 2>&1
mkdir -p "$remote_root/system"
(uptime && who -b) > "$remote_root/system/uptime.txt" 2>&1
mkdir -p "$remote_root/system"
(locale && timedatectl) > "$remote_root/system/locale_timezone.txt" 2>&1
mkdir -p "$remote_root/system"
(hostnamectl && cat /etc/hosts && cat /etc/hostname) > "$remote_root/system/hostname_hosts.txt" 2>&1
mkdir -p "$remote_root/system"
(sysctl -a) > "$remote_root/system/sysctl_all.txt" 2>&1
mkdir -p "$remote_root/system"
(ulimit -a && ls /etc/security/limits.conf /etc/security/limits.d 2>/dev/null) > "$remote_root/system/limits_summary.txt" 2>&1
mkdir -p "$remote_root/system"
(systemctl --failed) > "$remote_root/system/systemd_failed_units.txt" 2>&1
mkdir -p "$remote_root/system"
(systemctl list-units --type=service --state=running) > "$remote_root/system/systemd_running_units.txt" 2>&1
mkdir -p "$remote_root/system"
(journalctl -b -p err..alert --no-pager) > "$remote_root/system/journal_boot_errors.txt" 2>&1
mkdir -p "$remote_root/system"
(env | grep -v -E '(PASS|KEY|SECRET|TOKEN|PASSWORD)') > "$remote_root/system/env_root_sanitized.txt" 2>&1
mkdir -p "$remote_root/hardware"
(lscpu) > "$remote_root/hardware/cpu.txt" 2>&1
mkdir -p "$remote_root/hardware"
(dmidecode -t memory) > "$remote_root/hardware/memory_modules.txt" 2>&1
mkdir -p "$remote_root/hardware"
(free -h) > "$remote_root/hardware/memory_summary.txt" 2>&1
mkdir -p "$remote_root/storage"
(lsblk -o NAME,SIZE,TYPE,MOUNTPOINT,FSTYPE) > "$remote_root/storage/volumes.txt" 2>&1
mkdir -p "$remote_root/storage"
(mount | column -t) > "$remote_root/storage/mount_points.txt" 2>&1
mkdir -p "$remote_root/storage"
(if command -v smartctl >/dev/null 2>&1; then for dev in /dev/sd?; do smartctl -H "$dev"; done; else echo 'smartctl missing'; fi) > "$remote_root/storage/smart_status.txt" 2>&1
mkdir -p "$remote_root/network"
(ip addr) > "$remote_root/network/ipconfig_all.txt" 2>&1
mkdir -p "$remote_root/network"
(ip link) > "$remote_root/network/adapters_details.txt" 2>&1
mkdir -p "$remote_root/network"
(resolvectl status || systemd-resolve --status || cat /etc/resolv.conf) > "$remote_root/network/dns_client_config.txt" 2>&1
mkdir -p "$remote_root/network"
(cat /etc/hosts) > "$remote_root/network/hosts_file.txt" 2>&1
mkdir -p "$remote_root/network"
(ip route) > "$remote_root/network/routes.txt" 2>&1
mkdir -p "$remote_root/network"
(ss -tunlp) > "$remote_root/network/listening_sockets.txt" 2>&1
mkdir -p "$remote_root/firewall"
(ufw status verbose) > "$remote_root/firewall/ufw_status.txt" 2>&1
mkdir -p "$remote_root/firewall"
(iptables -S && iptables -L -n -v) > "$remote_root/firewall/iptables_rules.txt" 2>&1
mkdir -p "$remote_root/firewall"
(nft list ruleset) > "$remote_root/firewall/nft_ruleset.txt" 2>&1
mkdir -p "$remote_root/users_permissions"
(getent passwd && getent group) > "$remote_root/users_permissions/users_groups.txt" 2>&1
mkdir -p "$remote_root/users_permissions"
(cat /etc/sudoers && ls /etc/sudoers.d 2>/dev/null) > "$remote_root/users_permissions/sudoers_listing.txt" 2>&1
mkdir -p "$remote_root/users_permissions"
(umask) > "$remote_root/users_permissions/umask.txt" 2>&1
mkdir -p "$remote_root/processes_services"
(ps -ef) > "$remote_root/processes_services/process_list.txt" 2>&1
mkdir -p "$remote_root/processes_services"
(systemctl list-units --type=service --all) > "$remote_root/processes_services/services_state.txt" 2>&1
mkdir -p "$remote_root/processes_services"
(systemctl list-unit-files --state=enabled) > "$remote_root/processes_services/startup_items.txt" 2>&1
mkdir -p "$remote_root/packages"
(dpkg -l) > "$remote_root/packages/dpkg_list.txt" 2>&1
mkdir -p "$remote_root/packages"
(apt-cache policy nginx openssl python3 certbot postgresql redis) > "$remote_root/packages/apt_policy_key_pkgs.txt" 2>&1
mkdir -p "$remote_root/packages"
(snap list) > "$remote_root/packages/snap_list.txt" 2>&1
mkdir -p "$remote_root/web"
(find /var/www /srv/www -maxdepth 3 -type d -print 2>/dev/null) > "$remote_root/web/web_root_overview.txt" 2>&1
mkdir -p "$remote_root/web"
(du -h --max-depth=2 /var/www /srv/www 2>/dev/null | sort -hr) > "$remote_root/web/static_assets_sizes.csv" 2>&1
mkdir -p "$remote_root/web"
(curl -I http://127.0.0.1 2>&1 || true) > "$remote_root/web/web_server_headers_hint.txt" 2>&1
mkdir -p "$remote_root/nginx"
(nginx -V) > "$remote_root/nginx/nginx_version.txt" 2>&1
mkdir -p "$remote_root/nginx"
(nginx -t) > "$remote_root/nginx/nginx_test.txt" 2>&1
mkdir -p "$remote_root/nginx"
(find /etc/nginx -maxdepth 4 -print 2>/dev/null) > "$remote_root/nginx/nginx_conf_tree.txt" 2>&1
mkdir -p "$remote_root/app"
(find /srv /opt /var/www ~/apps -maxdepth 2 -type d -print 2>/dev/null) > "$remote_root/app/app_dirs_overview.txt" 2>&1
mkdir -p "$remote_root/app"
(systemctl list-unit-files | grep -E 'gunicorn|uvicorn|celery|worker' || true) > "$remote_root/app/systemd_units_related.txt" 2>&1
mkdir -p "$remote_root/app"
(find /srv /opt /var/www ~/apps -type f \( -name '*.env' -o -name 'config*.yml' -o -name 'settings.py' \) -print -exec ls -l {} \; 2>/dev/null) > "$remote_root/app/app_env_files_found.txt" 2>&1
mkdir -p "$remote_root/python"
(which python && which python3) > "$remote_root/python/where_python.txt" 2>&1
mkdir -p "$remote_root/python"
(python3 -m pip --version && pip3 --version) > "$remote_root/python/py_launcher_list.txt" 2>&1
mkdir -p "$remote_root/python"
(python3 -c "import json,sys; print(json.dumps({'python': sys.executable, 'paths': sys.path}))") > "$remote_root/python/interpreters_inventory.csv" 2>&1
mkdir -p "$remote_root/database"
(psql --version) > "$remote_root/database/postgres_version.txt" 2>&1
mkdir -p "$remote_root/database"
(systemctl status postgresql --no-pager) > "$remote_root/database/postgres_service_status.txt" 2>&1
mkdir -p "$remote_root/database"
(find /etc/postgresql -name 'postgresql.conf' -o -name 'pg_hba.conf' -print 2>/dev/null) > "$remote_root/database/postgres_conf_locations.txt" 2>&1
mkdir -p "$remote_root/certs"
(find /etc/letsencrypt /etc/ssl /etc/pki -maxdepth 3 -type f \( -name '*.pem' -o -name '*.crt' \) -print 2>/dev/null) > "$remote_root/certs/cert_locations.txt" 2>&1
mkdir -p "$remote_root/certs"
(certbot certificates || echo 'certbot missing') > "$remote_root/certs/certbot_status.txt" 2>&1
mkdir -p "$remote_root/certs"
(for cert in /etc/letsencrypt/live/*/cert.pem; do echo CERT: $cert; openssl x509 -enddate -noout -in "$cert"; done 2>/dev/null) > "$remote_root/certs/cert_expiry_report.txt" 2>&1
mkdir -p "$remote_root/cron_webhooks"
(crontab -l) > "$remote_root/cron_webhooks/crontab_root.txt" 2>&1
mkdir -p "$remote_root/cron_webhooks"
(ls /var/spool/cron/crontabs 2>/dev/null) > "$remote_root/cron_webhooks/crontab_users_listing.txt" 2>&1
mkdir -p "$remote_root/cron_webhooks"
(ls /etc/cron.* 2>/dev/null) > "$remote_root/cron_webhooks/system_cron_dirs_tree.txt" 2>&1
mkdir -p "$remote_root/logs"
(journalctl -u nginx -n 500 --no-pager) > "$remote_root/logs/journal_nginx_last500.txt" 2>&1
mkdir -p "$remote_root/logs"
(journalctl -n 500 --no-pager) > "$remote_root/logs/journal_app_last500.txt" 2>&1
mkdir -p "$remote_root/logs"
(journalctl -u postgresql -n 500 --no-pager) > "$remote_root/logs/journal_db_last500.txt" 2>&1
mkdir -p "$remote_root/logs"
(tail -n 200 /var/log/syslog) > "$remote_root/logs/syslog_tail.txt" 2>&1
mkdir -p "$remote_root/logs"
(tail -n 200 /var/log/auth.log) > "$remote_root/logs/auth_log_tail.txt" 2>&1
mkdir -p "$remote_root/logs"
(journalctl -k -n 400 --no-pager) > "$remote_root/logs/kernel_log_tail.txt" 2>&1
mkdir -p "$remote_root/virtualization_containers"
(docker info) > "$remote_root/virtualization_containers/docker_info.txt" 2>&1
mkdir -p "$remote_root/virtualization_containers"
(docker ps -a) > "$remote_root/virtualization_containers/docker_ps.txt" 2>&1
mkdir -p "$remote_root/virtualization_containers"
(find /srv /opt /etc -name 'docker-compose*.yml' -print 2>/dev/null) > "$remote_root/virtualization_containers/docker_compose_files_found.txt" 2>&1
echo "REMOTE_ROOT=$remote_root"
'@; $remoteRoot = $script | ssh "$user@$ip" "bash -s" | Select-String -Pattern '^REMOTE_ROOT=' | Select-Object -First 1 | ForEach-Object { $_.Line -replace '^REMOTE_ROOT=' }; scp -r "$user@$ip:$remoteRoot" .
```

Varianta B (heslo v příkazu – méně bezpečné; vyžaduje sshpass)

```powershell
$ip="<IP_ADDRESS>"; $user="root"; $pw="<ROOT_PASSWORD>"; $script=@'
#!/usr/bin/env bash
set -o pipefail
set +e
timestamp=$(date +%Y%m%d-%H%M%S)
remote_root="/root/Diag_${timestamp}"
mkdir -p "$remote_root"
mkdir -p "$remote_root/system"
(cat /etc/os-release) > "$remote_root/system/os_release.txt" 2>&1
mkdir -p "$remote_root/system"
(uname -a) > "$remote_root/system/uname.txt" 2>&1
mkdir -p "$remote_root/system"
(uptime && who -b) > "$remote_root/system/uptime.txt" 2>&1
mkdir -p "$remote_root/system"
(locale && timedatectl) > "$remote_root/system/locale_timezone.txt" 2>&1
mkdir -p "$remote_root/system"
(hostnamectl && cat /etc/hosts && cat /etc/hostname) > "$remote_root/system/hostname_hosts.txt" 2>&1
mkdir -p "$remote_root/system"
(sysctl -a) > "$remote_root/system/sysctl_all.txt" 2>&1
mkdir -p "$remote_root/system"
(ulimit -a && ls /etc/security/limits.conf /etc/security/limits.d 2>/dev/null) > "$remote_root/system/limits_summary.txt" 2>&1
mkdir -p "$remote_root/system"
(systemctl --failed) > "$remote_root/system/systemd_failed_units.txt" 2>&1
mkdir -p "$remote_root/system"
(systemctl list-units --type=service --state=running) > "$remote_root/system/systemd_running_units.txt" 2>&1
mkdir -p "$remote_root/system"
(journalctl -b -p err..alert --no-pager) > "$remote_root/system/journal_boot_errors.txt" 2>&1
mkdir -p "$remote_root/system"
(env | grep -v -E '(PASS|KEY|SECRET|TOKEN|PASSWORD)') > "$remote_root/system/env_root_sanitized.txt" 2>&1
mkdir -p "$remote_root/hardware"
(lscpu) > "$remote_root/hardware/cpu.txt" 2>&1
mkdir -p "$remote_root/hardware"
(dmidecode -t memory) > "$remote_root/hardware/memory_modules.txt" 2>&1
mkdir -p "$remote_root/hardware"
(free -h) > "$remote_root/hardware/memory_summary.txt" 2>&1
mkdir -p "$remote_root/storage"
(lsblk -o NAME,SIZE,TYPE,MOUNTPOINT,FSTYPE) > "$remote_root/storage/volumes.txt" 2>&1
mkdir -p "$remote_root/storage"
(mount | column -t) > "$remote_root/storage/mount_points.txt" 2>&1
mkdir -p "$remote_root/storage"
(if command -v smartctl >/dev/null 2>&1; then for dev in /dev/sd?; do smartctl -H "$dev"; done; else echo 'smartctl missing'; fi) > "$remote_root/storage/smart_status.txt" 2>&1
mkdir -p "$remote_root/network"
(ip addr) > "$remote_root/network/ipconfig_all.txt" 2>&1
mkdir -p "$remote_root/network"
(ip link) > "$remote_root/network/adapters_details.txt" 2>&1
mkdir -p "$remote_root/network"
(resolvectl status || systemd-resolve --status || cat /etc/resolv.conf) > "$remote_root/network/dns_client_config.txt" 2>&1
mkdir -p "$remote_root/network"
(cat /etc/hosts) > "$remote_root/network/hosts_file.txt" 2>&1
mkdir -p "$remote_root/network"
(ip route) > "$remote_root/network/routes.txt" 2>&1
mkdir -p "$remote_root/network"
(ss -tunlp) > "$remote_root/network/listening_sockets.txt" 2>&1
mkdir -p "$remote_root/firewall"
(ufw status verbose) > "$remote_root/firewall/ufw_status.txt" 2>&1
mkdir -p "$remote_root/firewall"
(iptables -S && iptables -L -n -v) > "$remote_root/firewall/iptables_rules.txt" 2>&1
mkdir -p "$remote_root/firewall"
(nft list ruleset) > "$remote_root/firewall/nft_ruleset.txt" 2>&1
mkdir -p "$remote_root/users_permissions"
(getent passwd && getent group) > "$remote_root/users_permissions/users_groups.txt" 2>&1
mkdir -p "$remote_root/users_permissions"
(cat /etc/sudoers && ls /etc/sudoers.d 2>/dev/null) > "$remote_root/users_permissions/sudoers_listing.txt" 2>&1
mkdir -p "$remote_root/users_permissions"
(umask) > "$remote_root/users_permissions/umask.txt" 2>&1
mkdir -p "$remote_root/processes_services"
(ps -ef) > "$remote_root/processes_services/process_list.txt" 2>&1
mkdir -p "$remote_root/processes_services"
(systemctl list-units --type=service --all) > "$remote_root/processes_services/services_state.txt" 2>&1
mkdir -p "$remote_root/processes_services"
(systemctl list-unit-files --state=enabled) > "$remote_root/processes_services/startup_items.txt" 2>&1
mkdir -p "$remote_root/packages"
(dpkg -l) > "$remote_root/packages/dpkg_list.txt" 2>&1
mkdir -p "$remote_root/packages"
(apt-cache policy nginx openssl python3 certbot postgresql redis) > "$remote_root/packages/apt_policy_key_pkgs.txt" 2>&1
mkdir -p "$remote_root/packages"
(snap list) > "$remote_root/packages/snap_list.txt" 2>&1
mkdir -p "$remote_root/web"
(find /var/www /srv/www -maxdepth 3 -type d -print 2>/dev/null) > "$remote_root/web/web_root_overview.txt" 2>&1
mkdir -p "$remote_root/web"
(du -h --max-depth=2 /var/www /srv/www 2>/dev/null | sort -hr) > "$remote_root/web/static_assets_sizes.csv" 2>&1
mkdir -p "$remote_root/web"
(curl -I http://127.0.0.1 2>&1 || true) > "$remote_root/web/web_server_headers_hint.txt" 2>&1
mkdir -p "$remote_root/nginx"
(nginx -V) > "$remote_root/nginx/nginx_version.txt" 2>&1
mkdir -p "$remote_root/nginx"
(nginx -t) > "$remote_root/nginx/nginx_test.txt" 2>&1
mkdir -p "$remote_root/nginx"
(find /etc/nginx -maxdepth 4 -print 2>/dev/null) > "$remote_root/nginx/nginx_conf_tree.txt" 2>&1
mkdir -p "$remote_root/app"
(find /srv /opt /var/www ~/apps -maxdepth 2 -type d -print 2>/dev/null) > "$remote_root/app/app_dirs_overview.txt" 2>&1
mkdir -p "$remote_root/app"
(systemctl list-unit-files | grep -E 'gunicorn|uvicorn|celery|worker' || true) > "$remote_root/app/systemd_units_related.txt" 2>&1
mkdir -p "$remote_root/app"
(find /srv /opt /var/www ~/apps -type f \( -name '*.env' -o -name 'config*.yml' -o -name 'settings.py' \) -print -exec ls -l {} \; 2>/dev/null) > "$remote_root/app/app_env_files_found.txt" 2>&1
mkdir -p "$remote_root/python"
(which python && which python3) > "$remote_root/python/where_python.txt" 2>&1
mkdir -p "$remote_root/python"
(python3 -m pip --version && pip3 --version) > "$remote_root/python/py_launcher_list.txt" 2>&1
mkdir -p "$remote_root/python"
(python3 -c "import json,sys; print(json.dumps({'python': sys.executable, 'paths': sys.path}))") > "$remote_root/python/interpreters_inventory.csv" 2>&1
mkdir -p "$remote_root/database"
(psql --version) > "$remote_root/database/postgres_version.txt" 2>&1
mkdir -p "$remote_root/database"
(systemctl status postgresql --no-pager) > "$remote_root/database/postgres_service_status.txt" 2>&1
mkdir -p "$remote_root/database"
(find /etc/postgresql -name 'postgresql.conf' -o -name 'pg_hba.conf' -print 2>/dev/null) > "$remote_root/database/postgres_conf_locations.txt" 2>&1
mkdir -p "$remote_root/certs"
(find /etc/letsencrypt /etc/ssl /etc/pki -maxdepth 3 -type f \( -name '*.pem' -o -name '*.crt' \) -print 2>/dev/null) > "$remote_root/certs/cert_locations.txt" 2>&1
mkdir -p "$remote_root/certs"
(certbot certificates || echo 'certbot missing') > "$remote_root/certs/certbot_status.txt" 2>&1
mkdir -p "$remote_root/certs"
(for cert in /etc/letsencrypt/live/*/cert.pem; do echo CERT: $cert; openssl x509 -enddate -noout -in "$cert"; done 2>/dev/null) > "$remote_root/certs/cert_expiry_report.txt" 2>&1
mkdir -p "$remote_root/cron_webhooks"
(crontab -l) > "$remote_root/cron_webhooks/crontab_root.txt" 2>&1
mkdir -p "$remote_root/cron_webhooks"
(ls /var/spool/cron/crontabs 2>/dev/null) > "$remote_root/cron_webhooks/crontab_users_listing.txt" 2>&1
mkdir -p "$remote_root/cron_webhooks"
(ls /etc/cron.* 2>/dev/null) > "$remote_root/cron_webhooks/system_cron_dirs_tree.txt" 2>&1
mkdir -p "$remote_root/logs"
(journalctl -u nginx -n 500 --no-pager) > "$remote_root/logs/journal_nginx_last500.txt" 2>&1
mkdir -p "$remote_root/logs"
(journalctl -n 500 --no-pager) > "$remote_root/logs/journal_app_last500.txt" 2>&1
mkdir -p "$remote_root/logs"
(journalctl -u postgresql -n 500 --no-pager) > "$remote_root/logs/journal_db_last500.txt" 2>&1
mkdir -p "$remote_root/logs"
(tail -n 200 /var/log/syslog) > "$remote_root/logs/syslog_tail.txt" 2>&1
mkdir -p "$remote_root/logs"
(tail -n 200 /var/log/auth.log) > "$remote_root/logs/auth_log_tail.txt" 2>&1
mkdir -p "$remote_root/logs"
(journalctl -k -n 400 --no-pager) > "$remote_root/logs/kernel_log_tail.txt" 2>&1
mkdir -p "$remote_root/virtualization_containers"
(docker info) > "$remote_root/virtualization_containers/docker_info.txt" 2>&1
mkdir -p "$remote_root/virtualization_containers"
(docker ps -a) > "$remote_root/virtualization_containers/docker_ps.txt" 2>&1
mkdir -p "$remote_root/virtualization_containers"
(find /srv /opt /etc -name 'docker-compose*.yml' -print 2>/dev/null) > "$remote_root/virtualization_containers/docker_compose_files_found.txt" 2>&1
echo "REMOTE_ROOT=$remote_root"
'@; $remoteRoot = $script | sshpass -p $pw ssh -o StrictHostKeyChecking=no "$user@$ip" "bash -s" | Select-String -Pattern '^REMOTE_ROOT=' | Select-Object -First 1 | ForEach-Object { $_.Line -replace '^REMOTE_ROOT=' }; sshpass -p $pw scp -o StrictHostKeyChecking=no -r "$user@$ip:$remoteRoot" .
```

### 28.23 MANIFEST.md (šablona)

```markdown
# Diagnostic Manifest

Generated at: {{timestamp_local}}
Host: {{hostname}}
User: {{collector_user}}
Run as root: {{is_root}}
OS: {{os_pretty_name}}
Kernel: {{kernel}}
Uptime: {{uptime}}
Output root: {{output_root}}

## Purpose
This folder contains a full Ubuntu web server diagnostic snapshot for troubleshooting issues in nginx/app/database/certs/network.

## Folder Overview
- system/ – OS/kernel/timezone/sysctl/limits/systemd health
- hardware/ – CPU/RAM/load/dmesg hints
- storage/ – mounts/df/inodes/top sizes
- network/ – IP/routes/DNS/ports
- firewall/ – ufw/iptables/nft rules
- users_permissions/ – users/groups/sudoers listing/permissions summary
- processes_services/ – ps/systemctl statuses/open ports correlation
- packages/ – dpkg/apt versions of key packages
- web/ – web roots overview + size triage
- nginx/ – nginx -V/-t/-T, config tree, log tails
- app/ – app dirs, systemd units, env/config discovery (metadata)
- python/ – interpreter inventory, pip config, venv discovery + per-venv reports
- database/ – postgres/mysql/redis status + config metadata + log tails
- certs/ – certbot inventory + expiry report (no private keys)
- cron_webhooks/ – crons + webhook inventory (metadata)
- logs/ – journal/syslog/auth/kernel tails
- virtualization_containers/ – docker inventory + compose metadata

## File Index
| Path | Description |
|------|-------------|
| README_problem.md | repro steps + timeline |
| system/journal_boot_errors.txt | boot errors from journal |
| network/ss_listen.txt | listening sockets |
| nginx/nginx_test.txt | nginx -t output |
| python/venv_candidates_found.txt | detected venv roots |
| python/venv_reports/venv_*__report.json | per-venv sys/pip snapshot |
| certs/cert_expiry_report.txt | certificate expiry |
| logs/journal_nginx_last500.txt | nginx journal tail |
| logs/journal_app_last500.txt | app journal tail |

## Notes
- Secrets must be redacted before sharing.
- Missing subsystems are recorded as NOT PRESENT.

## Integrity
Total files: {{file_count}}
Total size: {{total_size_human}}
Optional checksums: checksums/sha256.txt
```

---

## 29) FULL Windows Diagnostics

Tento dokument definuje konečný návrh FULL diagnostiky pro Windows, zaměřený na:

* úplnou rekonstrukci systému bez ZIP archivace,
* jednoznačné určení výchozích vs. runtime hodnot,
* detailní rozbor více instalací Pythonu a všech venv,
* transparentní analýzu PATH, registry, aliasů a asociací.

Výstupem je adresář se soubory, nikoli archiv.

### 29.1 Struktura kořenového adresáře

```
Diag_YYYYMMDD-HHMMSS/
│
├─ MANIFEST.md
├─ README_problem.md
├─ CHANGELOG_last_actions.txt
│
├─ system/
├─ registry/
├─ filesystem/
├─ hardware/
├─ storage/
├─ drivers/
├─ processes_services/
├─ network/
├─ security/
├─ python/
├─ devtools/
├─ virtualization/
├─ wsl/
└─ logs/
```

### 29.2 MANIFEST.md (automaticky generovaný)

```markdown
# Diagnostic Manifest

Generated at: YYYY-MM-DD HH:MM:SS
Machine: <COMPUTERNAME>
User: <USERNAME>
PowerShell: 5.1 / 7.x
Run as Administrator: YES / NO

## Folder Overview
system/      – OS, build, PATH, environment
registry/    – authoritative Windows configuration
filesystem/  – actual files, Python installs, venvs
python/      – runtime Python diagnostics
network/     – adapters, DNS, proxy, firewall
logs/        – crashes and event logs
...

## Files
| File | Description | Size |
|------|-------------|------|
| system/systeminfo.txt | OS + HW summary | 45 KB |
| registry/env_machine.reg | System PATH and env | 3 KB |
| python/venv_reports/venv_projA.json | Full venv report | 18 KB |

## Notes
- ACCESS DENIED = requires Administrator
- Missing subsystems are explicitly noted
```

### 29.3 system/

* systeminfo.txt – OS, build, hotfixy, HW
* os_build.txt – edice, build, UBR
* windows_updates_hotfixes.txt
* timezone_locale.txt
* uptime_boot.txt
* power_settings.txt
* group_policy_summary.html
* run_context.txt

Environment & PATH:

* env_machine.txt – HKLM environment (rozparsované)
* env_user.txt – HKCU environment
* path_effective_process.txt – PATH skutečně viděný během běhu
* path_diff_analysis.txt – diff: Machine vs User vs Runtime

### 29.4 registry/

Environment / PATH:

* env_machine.reg – HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment
* env_user.reg – HKCU\Environment

Python – instalace & launcher:

* python_core_hklm.reg
* python_core_hklm_wow6432.reg
* python_core_hkcu.reg
* pylauncher_hklm.reg
* pylauncher_hkcu.reg

App Execution:

* app_paths_python_hklm.reg
* app_paths_python_hkcu.reg
* windowsapps_execution_aliases.txt

Asociace:

* file_associations_python.reg

Instalovaný software:

* uninstall_inventory.txt – Python, Conda, VC++ runtimes

### 29.5 filesystem/

Python & interpreters:

* python_executables_found.txt
* python_install_dirs_tree.txt

Virtual environments:

* venv_candidates_found.txt
* venv_tree_summaries/

  * venv_<name>.txt

Projekty:

* project_requirements_found.txt

User data & AppData overview:

* userprofile_tree_depth3.txt – strom `%USERPROFILE%` do hloubky 3 (bez binárního obsahu)
* userprofile_top_sizes.csv – top složky v `%USERPROFILE%` podle velikosti
* appdata_roaming_tree_depth4.txt – strom `%APPDATA%` do hloubky 4
* appdata_local_tree_depth4.txt – strom `%LOCALAPPDATA%` do hloubky 4
* appdata_locallow_tree_depth4.txt – strom `%USERPROFILE%\AppData\LocalLow` do hloubky 4 (pokud existuje)
* appdata_top_sizes.csv – top složky v AppData podle velikosti
* known_folders_locations.txt – skutečné cesty na Documents/Downloads/Desktop atd. (redirects/OneDrive)
* onedrive_status_hint.txt – indikace OneDrive přesměrování (pokud existuje)

### 29.6 python/

Interpreters:

* where_python.txt
* py_launcher_list.txt
* interpreters_inventory.csv

Default runtime:

* python_version_default.txt
* pip_version_default.txt
* pip_list_default.txt
* pip_freeze_default.txt
* pip_debug_default.txt
* pip_config_all.txt

Runtime internals:

* python_sys_path.json
* python_site_packages.txt
* python_platform.json

Virtual env reports:

* python/venv_reports/venv_<name>__report.json

### 29.7 processes_services/

* process_list.txt
* services_state.txt
* startup_items.txt
* scheduled_tasks_summary.txt

### 29.8 network/

* ipconfig_all.txt
* adapters_details.txt
* dns_client_config.txt
* hosts_file.txt
* routes.txt
* netstat_ano.txt
* firewall_profiles.txt
* firewall_rules_export.wfw
* winhttp_proxy.txt
* internet_proxy_user.txt
* wifi_profiles.txt

### 29.9 security/

* defender_status.txt
* applocker_effective.txt
* uac_settings.txt
* certificates_machine_summary.txt

### 29.10 hardware/

* cpu.txt
* memory_modules.txt
* memory_summary.txt
* motherboard_bios.txt
* gpu_adapters.txt
* monitors_displays.txt

### 29.11 storage/

* volumes.txt
* mount_points.txt
* smart_status.txt
* disk_performance_counters.txt

### 29.12 drivers/

* driverquery_verbose.txt
* pnp_devices.txt
* problem_devices.txt
* signed_drivers.txt

### 29.13 devtools/

* git_version.txt
* node_version.txt
* dotnet_info.txt
* vcpp_runtimes.txt

### 29.14 virtualization/

* hyperv_state.txt
* virtual_machine_platform.txt
* windows_features.txt

### 29.15 wsl/

* wsl_status.txt
* wsl_list_verbose.txt
* wsl_versions.txt

### 29.16 logs/

* eventlog_system_last200.txt
* eventlog_application_last200.txt
* eventlog_security_last50.txt
* wer_crash_list.txt
* reliability_monitor_summary.txt
* windows_setup_logs_hint.txt

---

Konec MASTER zadání.
