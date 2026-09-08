# KájovoNG — specifikace systému

## Účel a autorita

KájovoNG je desktopový klient OpenAI pro generování a úpravu textových souborů, dotazy, definované kaskády a dávkové požadavky. Tento dokument je jedinou kanonickou technickou specifikací. README a návody sestavení popisují obsluhu; testy ověřují konkrétní vlastnosti implementace. Změna chování vyžaduje odpovídající změnu specifikace a testů.

## Runtime a instalace

Runtime je Python 3.12+, PySide6/Qt a závislosti deklarované v `pyproject.toml`. `requirements.txt` instaluje projekt; `requirements-dev.txt` instaluje jeho extra `dev`. Vstupní body jsou `kajovong`, `python -m kajovong` a `python -m kajovo.app.main`.

Windows instalátor vytváří nebo používá `.venv`. Skripty pracují z kořene repozitáře. Relativní cesty nastavení, databáze, LOG a cache se vztahují k pracovnímu adresáři aplikace. Wheel obsahuje diagnostický skript a prostředky; PyInstaller používá vlastní adresář prostředků. Fonty ve zdrojovém stromu vyžadují Git LFS.

## Odpovědnosti modulů

| Oblast | Moduly | Odpovědnost |
| --- | --- | --- |
| Spuštění | `app/main`, `kajovong/__main__` | QApplication, fonty, ikona, načtení konfigurace |
| Běžné běhy | `core/pipeline` | GENERATE, MODIFY, QA, QFILE, dávkový požadavek |
| Vlastní kaskády | `cascade_types`, `cascade_pipeline`, `cascade_log` | Definice kroků, substituce hodnot, JSON Schema, souborové výstupy |
| Komunikace | `openai_client`, `retry`, `compat`, `model_capabilities` | SDK/REST, stránkování, chyby, retry, ruční ověření schopností modelů |
| Souborové hranice | `contracts`, `filescan`, `utils` | Validace cest a manifestů, filtry IN, hashe, atomické zápisy |
| Evidence | `runlog`, `receipt`, `pricing`, `pricing_fetcher`, `pricing_audit` | JSON/JSONL, SQLite účtenky, odhad nákladů, doplnění evidence z logů |
| Konfigurace | `config`, `secret_store`, `resources` | Datové třídy, JSON, úložiště hesel, prostředky |
| Diagnostika | `diagnostics/windows`, `diagnostics/ssh`, `notifications` | Lokální a SSH sběr, SMTP oznámení |
| Desktop | `ui/mainwindow` a panely/dialogy v `ui` | Ovládání běhů, souborů, vector stores, batch, cen, Git a historie |
| Převodník | `utf8nobom/app` | Záloha, heuristická normalizace textu a obsahu ZIP |

## Režimy a kontrakty

| Režim | Tok | Výsledek |
| --- | --- | --- |
| GENERATE | A1 zadání → A2 seznam souborů → A3 obsah jednotlivých souborů | Soubory pod OUT |
| MODIFY | B1 rozbor → B2 plán změn → B3 obsah změn | Aktualizovaný OUT, případně dry-run |
| QA | Jeden Responses požadavek | Text odpovědi |
| QFILE | Jeden Responses požadavek se souborovým kontraktem | Soubory pod OUT; nelze SEND AS BATCH |
| SEND AS BATCH | JSONL pro `/v1/responses`, upload a vytvoření batch | ID dávky; výsledek se stahuje v panelu BATCH |
| KASKADA | Seřazené kroky `CascadeDefinition` | Odpovědi, JSON a očekávané soubory jednotlivých kroků |

GENERATE a MODIFY používají `previous_response_id`; explicitně nepodporovaný model se odmítne. Zadání nad 150 000 znaků se pro tyto režimy zavádí přes A0 po 20 000 znacích. Režim QA a batch používají části textového vstupu. Běh vyžaduje klíč, model a neprázdné zadání.

Souborové kontrakty vyžadují relativní cesty a textový obsah. A3/B3 podporují číslované části, návaznost indexů a příznak pokračování. Nevalidní JSON nebo kontrakt se nesmí změnit na úspěšně uložený prázdný soubor. Kaskáda nesmí být prázdná. JSON výstup kaskády prochází úplnou lokální validací schématu; síťové odkazy ze schématu nejsou povolené. Požadavek používá serverový režim bez přísného omezení schématu `strict: false`, protože obecné uživatelské schéma nemusí odpovídat podporované přísné podmnožině.

Manifest se před zápisem validuje jako celek. Cesty nesmějí obsahovat traversal, absolutní umístění, Windows zařízení ani konflikt velikosti písmen nebo souboru s jeho podadresářem. Vyhodnocená cesta musí zůstat pod OUT i při přítomnosti odkazů. Jednotlivý textový soubor se ukládá přes dočasný soubor, flush/fsync a atomické nahrazení. Vícesouborová změna není databázovou transakcí; chyba disku mezi zápisy může ponechat část změn.

VERSING pořizuje před změnami snapshot OUT s časovým suffixem `DDMMYYYYHHMMSS`. Existující snapshot se nepřepisuje. ReRun smí přeskočit pouze výstupy doložené manifestem zápisu a existujícím souborem, ne pouze odpověď obsahující navržený text.

## IN, přílohy a vzdálená data

Adresář IN se skenuje podle bezpečnostních allow/deny filtrů. Git, prostředí, runtime logy, cache, symlinky, junctions a snapshoty se nepoužívají jako běžný vstup. Výchozí limit jednotlivého souboru je 10 MiB. Citlivé názvy a rozpoznané vzory tajných údajů jsou blokované; rozpoznávání je heuristika, nikoli záruka nepřítomnosti tajných údajů.

Balíček IN je textový JSONL soubor s položkami `path` a `content`, nikoli archiv ZIP. Obsah se kontroluje proti hashi ze skenu; celkový limit balíčku je 40 MiB. Filtry adresářového vstupu nenahrazují uživatelské rozhodnutí při ručním přikládání souborů. Odeslaná data opouštějí počítač a podléhají pravidlům poskytovatele API.

Files, vector stores a batches podporují stránkování. Multipart upload nepřidává nesprávný globální JSON Content-Type a při opakování obnovuje pozici souboru. Selhání SDK mutace nepřechází do druhého provedení stejné operace přes REST. Přechodné chyby mohou být opakovány; při neurčitém síťovém výsledku nelze obecně garantovat přesně jedno provedení na serveru.

Ruční Probe používá placené odpovědi a dočasné vzdálené prostředky. Čeká na indexaci a uklízí jím vytvořený vector store a soubor; neúspěšný úklid hlásí. Běžně nahrané soubory a úložiště spravuje uživatel v příslušných panelech.

## Stav, bezpečnost a souběh

Nastavení je `kajovo_settings.json`; ukázka je `kajovo_settings.example.json`. Načítání kontroluje typy a číselné rozsahy. Hesla SMTP/SSH se nepersistují do JSON, ale přes OS keyring; při nedostupnosti úložiště existuje dočasný fallback prostředí. OPENAI_API_KEY se používá z prostředí, na Windows jej uživatelská akce může uložit do uživatelské větve registru. Nejde o šifrované úložiště API klíče. Klíč se nepředává programu `setx` jako argument procesu.

Každý běh vlastní neměnný snímek své konfigurace. Překrývající se OUT adresáře souběžných zapisujících běhů jsou odmítnuté. Dokončení a oznámení používají konfiguraci dokončeného běhu. STOP je kooperativní; čeká na návrat probíhající operace. Zavření okna nesmí zničit aktivní QThread ani jej násilně ukončit.

SSH pin je Base64 SHA-256 veřejného host key, s volitelným prefixem `SHA256:`. Ověření je citlivé na velikost písmen. Lokální diagnostika používá distribuovaný PowerShell skript a časový limit. Spuštění navržené opravy vyžaduje samostatné potvrzení; generovaný kód není automaticky důvěryhodný.

Panel Git při prostém obnovení nepřepisuje remote. Uložení je dostupné jen pro úspěšně načtený UTF-8 soubor. Mutace Git vyžadují odpovídající uživatelskou akci; příkazy mají časový limit a nepovolují interaktivní terminálový prompt.

## Logy a náklady

LOG obsahuje samostatné adresáře `RUN_DDMMYYYYHHMM_XXXX`, stav běhu, události JSONL, požadavky, odpovědi a manifesty. Existující adresář jiného běhu se nesmí znovu inicializovat. Známá tajná pole a Bearer hodnoty se redigují. Volný text může stále obsahovat citlivá data; logy nejsou šifrované. Neaktivní ovládací prvek šifrování tuto skutečnost uvádí.

SQLite `receipts` používá WAL a uchovává model, režim, ID odpovědi/dávky, usage a odhad ceny. Běžné běhy evidují každou získanou odpověď včetně mezikroků. Auditor evidence doplňuje účtenky z response logů a nespouští placené generování cen. Ruční cenový dotaz a Probe nejsou účetní fakturací celého účtu.

Ceny tokenů mají jednotku USD za 1 000 tokenů. File search cena za 1 000 volání se násobí počtem volání, nikoli počtem vstupních tokenů. Uložení používá GB-dny pouze při známé hodnotě. Neznámá cena vrací nulový neověřený odhad, ne důkaz nulové útraty. Odhady nenahrazují vyúčtování OpenAI; nezahrnují automaticky všechny modality, slevy cache ani kompletní vzdálené skladování.

Cache ceníku má `schema_version: 2`. Jiná verze se nepoužije. Vestavěný neověřený fallback obsahuje GPT-4o mini (0,00015/0,00060 USD za 1 000 vstupních/výstupních tokenů) a GPT-4o (0,00250/0,01000); batch tokenové sazby jsou poloviční. Aktuální sazby ověřujte v oficiálním ceníku. Nestrukturované HTML se nepovažuje za spolehlivý strojový zdroj sazeb.

## Pomocný převodník UTF-8

`python -m utf8nobom.py` spouští samostatné Tk rozhraní. Před úpravami vytváří kopie a ZIP zálohy mimo vstupní adresáře. Překrývající se vstupy se deduplikují, stejně pojmenované adresáře mají odlišné názvy záloh a existující záloha se nepřepisuje. Git metadata a odkazy se nekonvertují. ZIP s traversal položkou se odmítne beze změny; při přepisu se zachovají komentáře a metadata položek. Heuristickou opravu kódování je nutné ověřit na konkrétních datech, originál zůstává v záloze.

## Ověření a hranice důkazů

Automatické testy jsou v `tests`; CI používá pytest včetně Qt testů, statickou kontrolu F/B/E9 a kontrolu konzistence závislostí. Testy nevyžadují produkční API klíč. Sestavení Windows a macOS jsou samostatné platformní operace.

Čistý běh automatických kontrol znamená nepřítomnost nálezu v jejich rozsahu, nikoli důkaz nepřítomnosti všech chyb. Živé OpenAI, SMTP, vzdálené SSH, oprávnění cílového účtu a platformní distribuci je nutné ověřit v odpovídajícím provozním prostředí.

## Externí kontrakty

- [OpenAI Responses a strukturované výstupy](https://developers.openai.com/api/docs/guides/structured-outputs)
- [GPT-4o mini — parametry a ceny](https://developers.openai.com/api/docs/models/gpt-4o-mini)
- [GPT-4o — parametry a ceny](https://developers.openai.com/api/docs/models/gpt-4o)
- [OpenAI SDK — endpointy včetně aktualizace vector store file metodou POST](https://github.com/openai/openai-python/blob/main/api.md)
