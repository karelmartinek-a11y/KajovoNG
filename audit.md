# Forenzní audit zdrojového kódu

## Stav realizace schváleného plánu

Všech 59 nálezů je v rozsahu schváleného plánu uzavřeno implementací a regresním ověřením. Kompletní regresní sada a statické kontroly prošly; kontrolu úniku přes symbolický odkaz prostředí přeskočilo, protože nepovoluje vytváření symlinků. Uzavření těchto nálezů není tvrzením o bezchybnosti celé aplikace ani o ověření funkčnosti generovaných produktů. Přesné výsledky kontrol jsou uvedeny v předání změny.

Tabulka přiřazuje každému původnímu nálezu účinnou změnu a hlavní místo implementace. Původní audit pod tabulkou zůstává zachován jako popis výchozího obsahu; jeho řádky a SHA-256 nepopisují opravenou verzi.

| Nález | Implementace | Hlavní zdroj |
| --- | --- | --- |
| F-001 | Zmrazený inventář SourcePacku je jediným zdrojem archivace IN. | [kajovo/core/runlog.py](kajovo/core/runlog.py) |
| F-002 | Trvalý zápis a readback hesel; při chybě rollback bez změny settings JSON. | [kajovo/core/config.py](kajovo/core/config.py) |
| F-003 | Milník z filtrovaného prázdného indexu; obnova zachovává vyloučené cesty. | [kajovo/core/project_git.py](kajovo/core/project_git.py) |
| F-004 | Sdílené rezervace všech zapisovaných kořenů včetně verzí a převodníku. | [kajovo/studio/operations.py](kajovo/studio/operations.py) |
| F-005 | Při off jediný pokus, při within_approval nejvýše tři; ruční opakování nezávislé. | [kajovo/core/orchestration/authorization.py](kajovo/core/orchestration/authorization.py) |
| F-006 | Textová content dependency vyžaduje textového poskytovatele. | [kajovo/core/orchestration/resource_delivery.py](kajovo/core/orchestration/resource_delivery.py) |
| F-007 | Přeskočený schválený text dodává kompilátoru přesný obsah i hash. | [kajovo/core/orchestration/resource_delivery.py](kajovo/core/orchestration/resource_delivery.py) |
| F-008 | Uzavřené ruční vazby, UI dodání, LIVE potomek a pokračování původní BATCH bez opakování textu. | [kajovo/core/orchestration/manual_resources.py](kajovo/core/orchestration/manual_resources.py) |
| F-009 | file_search je připojen k plánu i obsahovému požadavku QFILE. | [kajovo/core/runs/qfile.py](kajovo/core/runs/qfile.py) |
| F-010 | Definitivní odmítnutí odstraňuje blokující pending; obnova vychází z atomické SQLite evidence. | [kajovo/core/orchestration/batch_recovery.py](kajovo/core/orchestration/batch_recovery.py) |
| F-011 | Explicitní retry zachová celý výběr včetně závislých vybraných vln. | [kajovo/core/generate_batch.py](kajovo/core/generate_batch.py) |
| F-012 | Aktivní staging i obsahové závislosti respektují nejnovější pokus cíle. | [kajovo/core/generate_batch.py](kajovo/core/generate_batch.py) |
| F-013 | Neodeslané prepared pokusy se uzavírají při chybě i po restartu. | [kajovo/core/generate_batch.py](kajovo/core/generate_batch.py) |
| F-014 | Povolený idempotentní přechod partial → imported. | [kajovo/core/orchestration/batch_manifest.py](kajovo/core/orchestration/batch_manifest.py) |
| F-015 | Stejná kontrola provider ID, input Files ID a endpointu při submitu i importu. | [kajovo/core/batch_submit.py](kajovo/core/batch_submit.py) |
| F-016 | Legacy import vyžaduje HTTP status a vlastní úplnou uzavřenou JSON masku. | [kajovo/core/batch_completion.py](kajovo/core/batch_completion.py) |
| F-017 | Nové kroky deterministické; legacy odmítá neodpovídající typované kontrakty a editor je nezpřístupňuje. | [kajovo/core/cascade_contract.py](kajovo/core/cascade_contract.py) |
| F-018 | Obnova převáží ověřené stagingové bajty a fyzické vazby do potomka. | [kajovo/core/cascade_pipeline.py](kajovo/core/cascade_pipeline.py) |
| F-019 | Významový podpis používá obsah vstupů, nikoli jejich přestěhované cesty. | [kajovo/core/cascade_pipeline.py](kajovo/core/cascade_pipeline.py) |
| F-020 | Opakování zneplatní potomky stagingu, zachová původní ochranné hashe OUT. | [kajovo/core/cascade_pipeline.py](kajovo/core/cascade_pipeline.py) |
| F-021 | Nedoložený POST zůstane neurčitý včetně neplatného úspěšného HTTP těla. | [kajovo/core/openai_transport.py](kajovo/core/openai_transport.py) |
| F-022 | Obnova stažení opětovně použije doloženou primární a binární odpověď před novým placeným voláním. | [kajovo/core/cascade_pipeline.py](kajovo/core/cascade_pipeline.py) |
| F-023 | Výraz previous_response_id_expr je součástí podpisu. | [kajovo/core/cascade_contract.py](kajovo/core/cascade_contract.py) |
| F-024 | Uzavřené typované záznamy, pouze známé verze definic 1/2. | [kajovo/core/cascade_types.py](kajovo/core/cascade_types.py) |
| F-025 | Strukturovaný dialog používá striktní JSON VALUE parser. | [kajovo/studio/cascade_items.py](kajovo/studio/cascade_items.py) |
| F-026 | Změna možností definice synchronizuje výstupní ovladač se zablokovanými signály. | [kajovo/studio/cascades.py](kajovo/studio/cascades.py) |
| F-027 | Obnovení modelů zachovává nedostupnou původní volbu. | [kajovo/studio/model_selection.py](kajovo/studio/model_selection.py) |
| F-028 | Aktuálnost bible a jejích stylových podkladů se ověřuje před závislou výrobou. | [kajovo/core/comic_service.py](kajovo/core/comic_service.py) |
| F-029 | Kontinuita používá scénář konkrétního vybraného storyboardu. | [kajovo/core/comic_service.py](kajovo/core/comic_service.py) |
| F-030 | Definitivně odmítnutá obrazová dávka uzavírá připravené položky jako opakovatelné chyby. | [kajovo/core/comic_service.py](kajovo/core/comic_service.py) |
| F-031 | Obrazová evidence hashuje skutečné tělo; endpoint je samostatný parametr. | [kajovo/core/comic_service.py](kajovo/core/comic_service.py) |
| F-032 | Transakční dvouprůchodové kopírování přemapuje typované vazby a materializaci. | [kajovo/core/comic_clone.py](kajovo/core/comic_clone.py) |
| F-033 | Kopie zachovává kompletní provenienci verzí včetně formátů plátna. | [kajovo/core/comic_clone.py](kajovo/core/comic_clone.py) |
| F-034 | Bezeztrátové deterministické dělení textu před kontinuitou, společný měřič a renderer mimo jádro. | [kajovo/comic_layout.py](kajovo/comic_layout.py) |
| F-035 | Panel a prompt vznikají atomicky; kopírovaný název respektuje délku. | [kajovo/core/comic_store.py](kajovo/core/comic_store.py) |
| F-036 | Bez panelu jsou editory nedostupné a nevzniká falešný dirty stav. | [kajovo/studio/comics.py](kajovo/studio/comics.py) |
| F-037 | Úplná kontrola typů a hodnot fotografické šablony. | [kajovo/core/photo_templates.py](kajovo/core/photo_templates.py) |
| F-038 | Rezervované identity builtin jsou v uživatelském úložišti odmítnuté. | [kajovo/core/photo_templates.py](kajovo/core/photo_templates.py) |
| F-039 | Zrušení fotografie ukládá vrácený vzdálený stav před obnovou seznamu. | [kajovo/studio/photos.py](kajovo/studio/photos.py) |
| F-040 | Odstranění značky kódování pouze jednou, obsahový U+FEFF zůstává. | [utf8nobom/app.py](utf8nobom/app.py) |
| F-041 | preparation_v2 validace aktualizuje správnou přijatou odpověď. | [kajovo/core/run_bundle.py](kajovo/core/run_bundle.py) |
| F-042 | Moderní evidence se čte striktně; poškození je viditelné jako corrupt_state. | [kajovo/core/run_bundle.py](kajovo/core/run_bundle.py) |
| F-043 | RequestRecordV2 rozlišuje přípravu, dispatch a HTTP důkaz; žádné předčasné sent_at. | [kajovo/core/run_bundle.py](kajovo/core/run_bundle.py) |
| F-044 | Sdílená množina výstupních rolí zahrnuje komiksové output. | [kajovo/core/run_bundle.py](kajovo/core/run_bundle.py) |
| F-045 | Připravenost k převzetí je korelována s totožným batch_id. | [kajovo/studio/history_models.py](kajovo/studio/history_models.py) |
| F-046 | Průběh odvozuje dokončení jen z explicitních completed událostí. | [kajovo/core/progress_display.py](kajovo/core/progress_display.py) |
| F-047 | Terminální stavy a jejich prezentace zahrnují čekání na zadání i podklady. | [kajovo/core/progress.py](kajovo/core/progress.py) |
| F-048 | Výlučná horní půlnoc následujícího místního dne. | [kajovo/studio/history.py](kajovo/studio/history.py) |
| F-049 | Chybový detail používá kanonické klíče lidského rendereru. | [kajovo/studio/history_details.py](kajovo/studio/history_details.py) |
| F-050 | Runtime a distribuční metadata čerpají verzi projektu ze stejného zdroje. | [kajovo/__init__.py](kajovo/__init__.py) |
| F-051 | Nesoulad expected/staged vypisuje oba směry chybějících cest. | [kajovo/core/cascade_pipeline.py](kajovo/core/cascade_pipeline.py) |
| F-052 | Zprávy rozlišují validaci kontraktu a zápisu od funkčnosti produktu. | [kajovo/core/runs/file_execution.py](kajovo/core/runs/file_execution.py) |
| F-053 | Asynchronní callbacky vážou store ID a generaci; nezachovávají zaniklou Qt položku. | [kajovo/studio/resources.py](kajovo/studio/resources.py) |
| F-054 | Obrazová kvalita a sazba používají účinnou společnou politiku; mrtvé položky odstraněny. | [kajovo/core/orchestration/policies/images.json](kajovo/core/orchestration/policies/images.json) |
| F-055 | Release kontroluje návratový kód každého nativního příkazu samostatně. | [.github/workflows/release.yml](.github/workflows/release.yml) |
| F-056 | Údržbový helper nemaže LOG a validuje přesný cíl odstranění starého desktopu. | [tools/_r05c_finalize_studio.py](tools/_r05c_finalize_studio.py) |
| F-057 | Obnova V2 čte graph a nenavazuje na legacy response historii. | [kajovo/core/recovery.py](kajovo/core/recovery.py) |
| F-058 | ready_tasks vyřazuje již dokončené úlohy. | [kajovo/core/orchestration/waves.py](kajovo/core/orchestration/waves.py) |
| F-059 | Poslední nedokončený běh se vybírá podle skutečného času bez omezení na 30 názvů. | [kajovo/core/runlog.py](kajovo/core/runlog.py) |

## Původní audit — výsledek a rozsah

Audit identifikoval **59 samostatně popsaných nálezů**: **7 P1**, **39 P2** a **13 P3**. Nejde pouze o formální nedostatky masek: některé přijaté vstupy nejsou předány výrobnímu kroku, obnova může ztratit vazbu na již vyrobené soubory a některé BATCH opravné větve uváznou nebo vyberou jiný rozsah práce.

Předmětem je aktuální pracovní strom `D:\kajovong`, včetně již existujících lokálních změn a nových zdrojových souborů. Výchozí HEAD je `6a277dd444d942288c9c8607310a0a04413971e1`; samotný commit **nereprezentuje celý auditovaný obsah**, protože pracovní strom není čistý. Přesný rozsah a SHA-256 přečtených souborů jsou v příloze.

Přečteno bylo **192 souborů, celkem 56 380 fyzických řádků**, od začátku do konce každého souboru. Z toho 187 souborů původního zdrojového inventáře obsahuje 56 286 řádků a pět doplňkových konfiguračních/vektorových souborů 94 řádků. Počet zahrnuje i prázdné řádky a fyzicky přítomné komentáře; **komentáře a docstringy nebyly použity jako důkaz chování**.

Jediným podkladem závěrů je implementace: vykonávaný kód, JSON schémata, runtime konfigurační data a spouštěcí či sestavovací skripty. Dokumentace včetně SSOT, testy, výsledky testů, předchozí audity a jejich závěry nebyly použity jako zdroj pravdy. Pomocné skripty byly čteny jako kód, nikoli spuštěny; vložené texty starých zpráv a testovací tvrzení nebyly důkazem.

Byly sledovány vazby definice → validace → sestavení kontextu → payload → LIVE/BATCH odeslání → odpověď → parsování → stav → staging → publikace a obnova. Součástí čtení byly také desktopové akce, modelová matice, fotografie, komiksy, správa zdrojů, převodník, Git operace a sestavení.

### Co tento audit nedělá

- Nespouští aplikaci, testy, generované produkty ani placená API volání.
- Nečte provozní databáze, přihlašovací údaje ani obsah běhových archivů.
- Nepoužívá externí dokumentaci API. Neoznačuje tudíž samotné jméno modelu či volbu poskytovatele za neplatné jen podle nedoloženého předpokladu.
- Nepovažuje testovací soubory, dokumentaci, binární obrázky/fonty, `CHANGE.zip`, prostředí `.venv` ani generované `Build/lib`, `Build/Kajovo`, `Build/bdist.*` a `dist` za auditovaný zdrojový korpus.
- Ze zdrojového inventáře byly podle zadání vyloučeny testovací/ověřovací harnessy `scripts/accept_comic.py`, `scripts/live_acceptance.py`, `scripts/verify_request_matrix.py`, `tools/compatibility_smoke.py`, `tools/check_critical_coverage.py` a `coverage-critical.toml`. Řídicí tok workflow, který některý z nich volá, byl posouzen bez používání jejich testovacích tvrzení nebo výsledků.
- Neprovádí opravy. Jediným výstupním zápisem tohoto auditu je tato zpráva.

Jde o statické důkazy z kódu a konkrétní podmínky jejich projevu, nikoli o tvrzení, že uvedené scénáře byly experimentálně spuštěny. Úplné přečtení vymezeného korpusu není matematickým důkazem absence dalších chyb. Zaznamenány jsou všechny nálezy, které po prověření návazností zůstaly podložené; vyvrácená podezření nejsou vydávána za chyby.

## Závažnost

| Úroveň | Význam | Počet |
| --- | --- | ---: |
| P1 | Vysoká: porušení ochrany vstupů, významná ztráta návaznosti výroby/obnovy, nechtěné opakování či nahrazení výsledku. | 7 |
| P2 | Střední: konkrétní funkční, kontraktová nebo evidenční chyba v popsaném scénáři. | 39 |
| P3 | Nízká: kosmetika, nepřesná prezentace, neúčinná konfigurace nebo vada pomocné funkce bez současného produkčního volajícího. | 13 |

P1 neznamená, že se chyba projeví v každém běhu. Podmínky jsou uvedené zvlášť. Nálezy o ručním opakování nerozporují možnost ručního opakování při vypnuté automatické opravě. Nálezy o větvení nepožadují výrobu souborů, které legitimně vynechá rozhodovací větev.

## Přehled nálezů

| ID | Závažnost | Oblast | Nález |
| --- | --- | --- | --- |
| [F-001](#f-001) | P1 | Bezpečnost a vstupy | Archivace běhu znovu přibírá soubory odmítnuté při přípravě vstupů |
| [F-002](#f-002) | P2 | Bezpečnost a vstupy | Uložení nastavení zatají neúspěšné trvalé uložení hesla |
| [F-003](#f-003) | P2 | Bezpečnost a vstupy | Git milník zachovává zakázané soubory převzaté z HEAD |
| [F-004](#f-004) | P2 | Bezpečnost a vstupy | Některé zapisující operace obcházejí společnou rezervaci výstupního adresáře |
| [F-005](#f-005) | P1 | GENERATE, MODIFY a QFILE | LIVE automaticky opakuje neplatné odpovědi i při vypnutých automatických opravách |
| [F-006](#f-006) | P2 | GENERATE, MODIFY a QFILE | Přijatá obsahová závislost textu na binárním výstupu nemá realizovatelný kontext |
| [F-007](#f-007) | P2 | GENERATE, MODIFY a QFILE | Přeskočený soubor uznaný za dostupný není dostupný konzumentovi jeho obsahu |
| [F-008](#f-008) | P2 | GENERATE, MODIFY a QFILE | Větev manual_input nemá navazující cestu převzetí dodaného podkladu |
| [F-009](#f-009) | P2 | GENERATE, MODIFY a QFILE | QFILE připraví vyhledávání ve vector store, ale nepřipojí nástroj k žádnému výrobnímu payloadu |
| [F-010](#f-010) | P1 | BATCH | Definitivní odmítnutí následné dávky zanechá stav, který blokuje opakování i zotavení |
| [F-011](#f-011) | P2 | BATCH | Ruční opakování smíšeného výběru vynechá již úspěšné soubory |
| [F-012](#f-012) | P1 | BATCH | Opětovné převzetí staré dávky může nahradit novější výsledek ruční opravy |
| [F-013](#f-013) | P2 | BATCH | Chyba před vlastním odesláním ruční opravy zanechá osiřelou prepared operaci |
| [F-014](#f-014) | P2 | BATCH | Opakované převzetí po lokální chybě narazí na zakázaný přechod partial → imported |
| [F-015](#f-015) | P2 | BATCH | Následné dávky a převzetí neověřují stejnou vzdálenou identitu jako první odeslání |
| [F-016](#f-016) | P2 | BATCH | Kompatibilní import starých bundle nemá stejně přesnou vstupní masku jako nový import |
| [F-017](#f-017) | P1 | Kaskády a kontext | Nově vytvořený krok může ignorovat typované vstupy zadané v editoru |
| [F-018](#f-018) | P1 | Kaskády a kontext | Obnova do nového běhu ponechává stagingové cesty vztažené ke starému běhu |
| [F-019](#f-019) | P2 | Kaskády a kontext | Obnova z historie sama zneplatní podpis nezměněného předchozího kroku |
| [F-020](#f-020) | P2 | Kaskády a kontext | Restart dřívějšího kroku neinvaliduje jeho staré následné souborové výstupy |
| [F-021](#f-021) | P1 | Kaskády a kontext | Obecná obsluha chyby přepíše submission_unknown na failed |
| [F-022](#f-022) | P2 | Kaskády a kontext | Po chybě stažení hotového dokumentu obnova nevyužije uložený vzdálený výsledek |
| [F-023](#f-023) | P2 | Kaskády a kontext | Podpis kroku nezahrnuje explicitní výraz určující předchozí Responses kontext |
| [F-024](#f-024) | P2 | Kaskády a kontext | Parser kaskády přijímá neznámé verze a tiše zahazuje neznámá pole |
| [F-025](#f-025) | P2 | Kaskády a kontext | Úprava celého JSON kontraktu používá parser, který tiše sloučí duplicitní klíče |
| [F-026](#f-026) | P3 | Kaskády a kontext | Změna výstupního adresáře v JSON definici se ihned přepíše starou hodnotou widgetu |
| [F-027](#f-027) | P2 | Modely a uživatelské nastavení | Obnovení nabídky modelů přepíše existující uživatelskou volbu doporučeným modelem |
| [F-028](#f-028) | P2 | Komiksy | Generování přijme zastaralou bibli, kterou výroba panelu následně odmítne |
| [F-029](#f-029) | P2 | Komiksy | Kontinuita může porovnávat storyboard s jinou verzí scénáře, než ze které vznikl |
| [F-030](#f-030) | P2 | Komiksy | Definitivně odmítnutá dávka zůstane s prepared položkami, které nelze přímo opakovat |
| [F-031](#f-031) | P2 | Komiksy | Fyzický hash LIVE obrazového požadavku zahrnuje obálku, která se neposílá |
| [F-032](#f-032) | P2 | Komiksy | Duplikace projektu ztrácí vazby panelů na zkopírovaný storyboard |
| [F-033](#f-033) | P2 | Komiksy | Historické verze duplikovaného projektu ztrácejí vlastní formát plátna |
| [F-034](#f-034) | P2 | Komiksy | Povolená délka storyboardového textu není slučitelná s automaticky vytvořenou sazbou |
| [F-035](#f-035) | P2 | Komiksy | Duplikace dlouhého názvu panelu zanechá neúplný databázový záznam |
| [F-036](#f-036) | P2 | Komiksy | Úprava editoru bez vybraného panelu může zablokovat přidání prvního panelu i zavření aplikace |
| [F-037](#f-037) | P2 | Fotografie a převod textů | Soubor fotografických šablon kontroluje klíče, ale ne typy jejich hodnot |
| [F-038](#f-038) | P2 | Fotografie a převod textů | Vlastní šablona může použít rezervovanou identitu vestavěné šablony |
| [F-039](#f-039) | P3 | Fotografie a převod textů | Zrušení fotografické dávky nepromítne odpověď služby do lokálního stavu |
| [F-040](#f-040) | P2 | Fotografie a převod textů | Normalizace UTF-8 může odstranit skutečný první znak po BOM |
| [F-041](#f-041) | P2 | Evidence a historie | Validace přípravy V2 se nepromítá do validation_status uložené odpovědi |
| [F-042](#f-042) | P2 | Evidence a historie | Čtení moderních bundle v historii používá tolerantní legacy parsování |
| [F-043](#f-043) | P2 | Evidence a historie | Záznam request.sent vzniká ještě před lokální kontrolou a odesláním |
| [F-044](#f-044) | P2 | Evidence a historie | Filtr běhů s výstupy opomíjí výstupní artefakty komiksu |
| [F-045](#f-045) | P2 | Evidence a historie | Historie odvodí připravenost k převzetí z různých dávek téhož běhu |
| [F-046](#f-046) | P3 | Evidence a historie | Průběh označuje nedoložené kroky za hotové podle jejich pozice v seznamu |
| [F-047](#f-047) | P3 | Evidence a historie | Terminální stavy nejsou jednotně promítnuté do průběhu a historie |
| [F-048](#f-048) | P3 | Evidence a historie | Horní mez kalendářního filtru není konec vybraného místního dne |
| [F-049](#f-049) | P3 | Evidence a historie | Lidské zobrazení chyb dostává názvy polí, které neumí vykreslit |
| [F-050](#f-050) | P3 | Evidence a historie | Aplikace a distribuční metadata uvádějí rozdílnou verzi |
| [F-051](#f-051) | P3 | Evidence a historie | Chybové hlášení nesouladu stagingu může vypsat prázdný seznam vadných cest |
| [F-052](#f-052) | P3 | Evidence a historie | Nejednoznačná zpráva o ověření souboru nerozlišuje kontrakt od funkčnosti |
| [F-053](#f-053) | P2 | Správa vzdálených zdrojů | Asynchronní dokončení operace přiřadí soubory jinému právě vybranému vector store |
| [F-054](#f-054) | P3 | Konfigurační zdroje | Část načítané obrazové politiky není navázaná na skutečné výrobní hodnoty |
| [F-055](#f-055) | P2 | Sestavení a pomocné nástroje | Windows release může překrýt selhání dřívějšího nativního příkazu úspěchem posledního |
| [F-056](#f-056) | P2 | Sestavení a pomocné nástroje | Migrační helper bezpodmínečně maže celý LOG a není bezpečný pro opakované spuštění |
| [F-057](#f-057) | P3 | Latentní vady pomocných funkcí | Pomocná obnova očekává structure i u přijatého snapshotu V2 |
| [F-058](#f-058) | P3 | Latentní vady pomocných funkcí | ready_tasks() vrací již dokončené úlohy a nemusí postoupit k další vlně |
| [F-059](#f-059) | P3 | Latentní vady pomocných funkcí | Vyhledání posledního nedokončeného běhu řadí identifikátory místo skutečného času |

## Důkazy a dopady

### Bezpečnost a vstupy

<a id="f-001"></a>

#### F-001 · P1 · Archivace běhu znovu přibírá soubory odmítnuté při přípravě vstupů

Místa: `kajovo/core/runs/executor.py:147`, `kajovo/core/runs/executor.py:163`, `kajovo/core/orchestration/source_pack.py:341`, `kajovo/core/runlog.py:490`, `kajovo/core/run_bundle.py:895`, `kajovo/core/runs/attachments.py:238`.

Podmínka a zdrojový tok: Příprava SourcePacku filtruje citlivé soubory a vynechává například .git, .venv a LOG. Následující uložení ui_state spouští _archive_run_inputs(), které znovu rekurzivně projde celý IN. Přeskočí již archivované soubory, nikoli soubory odmítnuté politikou. Zbylé soubory kopíruje jako in_project_file bez předání bezpečnostní politiky.

Dopad: Do běhového archivu se mohou dostat neanonymizované .env, klíče, interní Git data nebo celé prostředí. Následné načtení příloh může na těchto dodatečných, neschválených souborech skončit chybou; projekt s korektně vyloučenými soubory tak nemusí jít zpracovat. Kód dokládá lokální kopírování; tento nález netvrdí, že již došlo k odeslání tajemství na síť.

<a id="f-002"></a>

#### F-002 · P2 · Uložení nastavení zatají neúspěšné trvalé uložení hesla

Místa: `kajovo/core/config.py:157`, `kajovo/core/secret_store.py:220`, `kajovo/studio/settings.py:161`.

Podmínka a zdrojový tok: set_secret() při selhání keyringu vrací False a nové heslo uchová pouze v prostředí aktuálního procesu. save_settings() návratovou hodnotu obou volání ignoruje, do JSON zapíše prázdná hesla a nevyvolá chybu. Nastavení následně oznámí úspěšné uložení.

Dopad: Po restartu nemusí být nové SMTP/SSH heslo dostupné, případně se opět načte stará hodnota z úložiště. Zobrazené potvrzení neodpovídá skutečnému výsledku operace.

<a id="f-003"></a>

#### F-003 · P2 · Git milník zachovává zakázané soubory převzaté z HEAD

Místa: `kajovo/core/project_git.py:141`, `kajovo/core/project_git.py:163`, `kajovo/core/project_git.py:175`, `kajovo/core/project_git.py:217`, `kajovo/core/project_git.py:265`.

Podmínka a zdrojový tok: Dočasný index milníku vzniká pomocí read-tree HEAD. Filtrovaný seznam candidates aktualizuje jen povolené cesty. Druhá smyčka u již sledované, ale nepovolené cesty provede continue, místo aby ji odstranila z dočasného indexu.

Dopad: Pokud starší HEAD obsahuje například zakázaný citlivý soubor, zůstane v novém milníku, i když ho seznam zahrnutých cest neuvádí. Obnova celého stromu jej může vrátit na disk. Podmínkou je jeho přítomnost v HEAD; netvrdím, že tento repozitář takový soubor aktuálně obsahuje.

<a id="f-004"></a>

#### F-004 · P2 · Některé zapisující operace obcházejí společnou rezervaci výstupního adresáře

Místa: `kajovo/studio/operations.py:267`, `kajovo/studio/operations.py:353`, `kajovo/studio/versions.py:91`, `kajovo/studio/converter.py:125`, `kajovo/studio/converter.py:47`.

Podmínka a zdrojový tok: Správce operací kontroluje kolize podle předaného output_dir. Operace verzování, které zapisují nebo obnovují projektové soubory, a převodník textů spouštějí práci bez této rezervace, přestože mění uživatelem vybraný strom. Převodník navíc používá vlastní instanci Operations, takže jeho příznak active nechrání běžící operace hlavní aplikace.

Dopad: Generování může mít OUT rezervovaný a současně v něm může zapisovat převodník nebo obnova milníku. To vytváří závod mezi čtením, archivací a publikací; podle pořadí se projeví změněným obsahem nebo selháním kontroly původního hashe. Nejde o tvrzení, že každý souběh nutně přepíše výsledek.

### GENERATE, MODIFY a QFILE

<a id="f-005"></a>

#### F-005 · P1 · LIVE automaticky opakuje neplatné odpovědi i při vypnutých automatických opravách

Místa: `kajovo/core/orchestration/preparation.py:900`, `kajovo/core/runs/file_execution.py:114`, `kajovo/core/runs/file_execution.py:270`, `kajovo/core/runs/file_execution.py:297`, `kajovo/core/runs/config.py:62`, `kajovo/core/orchestration/authorization.py`.

Podmínka a zdrojový tok: Přípravný _request() má pevnou smyčku tří pokusů. Výroba souboru nastavuje max_attempts = 3 a po OutputContractError pokračuje dalším pokusem. Tyto větve neodvozují povolení dalšího pokusu od cfg.auto_repair, přestože konfigurace obsahuje hodnotu off a existuje samostatná rozhodovací logika oprav.

Dopad: Po první chybné odpovědi mohou bez zapnuté automatické opravy následovat další placená LIVE volání. Nález se týká automatického opakování; explicitní ruční akce „Opakovat vybrané soubory“ při off není chyba.

<a id="f-006"></a>

#### F-006 · P2 · Přijatá obsahová závislost textu na binárním výstupu nemá realizovatelný kontext

Místa: `kajovo/core/orchestration/resource_delivery.py:139`, `kajovo/core/runs/generate.py:219`, `kajovo/core/context_compiler.py:330`.

Podmínka a zdrojový tok: Validace resource kontroluje jeho vlastní content_dependencies, ale nezakazuje textovému cíli deklarovat obsahovou závislost na binárním resource. Po výrobě se resource označí za dokončený a uloží do resource stagingu. Kompilátor obsahových závislostí však vyžaduje textový záznam ve verified_artifacts; binární výstup tam není předán.

Dopad: Graf projde přípravou a může se zaplatit výroba obrázku, ale navazující textový krok nedostane požadovaný obsah a skončí chybou kompilace kontextu. Kontrola grafu a schopnosti jeho konzumenta nejsou sladěné.

<a id="f-007"></a>

#### F-007 · P2 · Přeskočený soubor uznaný za dostupný není dostupný konzumentovi jeho obsahu

Místa: `kajovo/core/orchestration/resource_delivery.py:599`, `kajovo/core/context_compiler.py:330`, `kajovo/core/runs/generate.py:38`, `kajovo/core/runs/modify.py:84`.

Podmínka a zdrojový tok: Přeskočený cíl lze zařadit mezi completed na základě schváleného originálu v IN. Nový běh začíná s prázdnou mapou ověřených generovaných artefaktů. ContextCompiler ale použije původní obsah jako náhradu pouze pro action=preserve, nikoli pro přeskočený generate/modify.

Dopad: Plánovač uvolní závislý krok, zatímco kompilátor odmítne jeho obsahovou závislost. Přítomnost schváleného originálu tedy neznamená tutéž věc pro plánovač a pro sestavování payloadu.

<a id="f-008"></a>

#### F-008 · P2 · Větev manual_input nemá navazující cestu převzetí dodaného podkladu

Místa: `kajovo/core/orchestration/resource_delivery.py:670`, `kajovo/core/runs/generate.py:106`, `kajovo/core/runs/modify.py:153`, `kajovo/studio/operations.py:71`.

Podmínka a zdrojový tok: dispatch_resource_target() pro manual_input vždy uloží waiting_manual_resource a vrátí waiting_manual. source_or_task_id pouze zapíše do stavu. Na rozdíl od existing_asset jej nepoužije k načtení a zařazení souboru. V produkčním kódu není návazná obsluha, která by dodaný podklad pro tuto větev převzala a stejný graf posunula dál.

Dopad: Povinný ruční resource vede do čekání bez dokončovacího procesu pro tento kontrakt. Změna zadání na jiného producera by byla nový plán, nikoli dokončení existujícího manual_input kroku. Nález se netýká legitimně přeskočené nepovinné větve.

<a id="f-009"></a>

#### F-009 · P2 · QFILE připraví vyhledávání ve vector store, ale nepřipojí nástroj k žádnému výrobnímu payloadu

Místa: `kajovo/core/runs/attachments.py:151`, `kajovo/core/runs/attachments.py:463`, `kajovo/core/runs/qfile.py:98`, `kajovo/core/runs/qfile.py:191`, `kajovo/core/runs/qfile.py:208`.

Podmínka a zdrojový tok: Společná příprava příloh pro QFILE sestaví konfiguraci file_search a instrukce odkazují na podklady. Payload plánu i payload obsahu souboru však tools neobsahují; evidence příloh u nich navíc výslovně dostává prázdný seznam nástrojů.

Dopad: Pokud je rozhodující podklad dostupný pouze ve vector store, model jej touto cestou nemůže vyhledat. Krok přesto pokračuje jako tvorba souboru podle podkladů. Běžné přímé input_file přílohy nejsou předmětem tohoto nálezu.

### BATCH

<a id="f-010"></a>

#### F-010 · P1 · Definitivní odmítnutí následné dávky zanechá stav, který blokuje opakování i zotavení

Místa: `kajovo/core/generate_batch.py:1105`, `kajovo/core/generate_batch.py:1218`, `kajovo/core/generate_batch.py:1245`, `kajovo/core/generate_batch.py:1834`, `kajovo/core/generate_batch.py:2001`, `kajovo/core/generate_batch.py:2032`, `kajovo/core/batch_completion.py:183`.

Podmínka a zdrojový tok: Před odesláním další wave nebo ruční opravy se uloží pending_batch_submission. Při request_sent=False nebo vybraném definitivním HTTP odmítnutí se operace označí za neodeslanou, manifest failed a submission_unknown=False, ale pending_batch_submission se neodstraní.

Dopad: Další odeslání blokuje přítomná pending položka, zatímco obnova neznámého odeslání vyžaduje submission_unknown=True. Běh tak zůstane zablokovaný i poté, co je jisté, že dávka nebyla přijata.

<a id="f-011"></a>

#### F-011 · P2 · Ruční opakování smíšeného výběru vynechá již úspěšné soubory

Místa: `kajovo/core/generate_batch.py:268`, `kajovo/core/generate_batch.py:1873`, `kajovo/core/generate_batch.py:1936`, `kajovo/core/generate_batch.py:1969`.

Podmínka a zdrojový tok: Uživatel vybere k opakování A, který má platný výsledek, a B, který jej nemá. _repeat_v3_batch() předá oba cíle spolu s verified_artifacts do společného builderu. Ten zvolí requested - verified_targets, pokud tato množina není prázdná. V příkladu tedy vybere pouze B. Ruční větev následně vyprázdní deferred_paths i blocked_requested_paths.

Dopad: A se neodešle ani neodloží k pozdějšímu opakování, ačkoli byl explicitně vybraný. Samostatný výběr pouze úspěšných souborů funguje jinak než jejich kombinace s neúspěšnými.

<a id="f-012"></a>

#### F-012 · P1 · Opětovné převzetí staré dávky může nahradit novější výsledek ruční opravy

Místa: `kajovo/core/generate_batch.py:1444`, `kajovo/studio/batches.py:159`.

Podmínka a zdrojový tok: Import slučuje dosavadní staged_files a nově importované soubory výhradně podle cesty přes merged.update(). Neporovnává pořadí pokusů ani to, zda již stejnou cestu dodala novější ruční oprava. UI umožňuje dokončovací akci i pro dříve převzatou dávku.

Dopad: Po importu starší dávky, úspěšné novější opravě a opětovném importu starší dávky se kandidát pro stejnou cestu vrátí na starší obsah. Stav kandidáta se ukládá ještě před dokončením celého importního toku, takže ani pozdější chyba tuto změnu sama nevrací.

<a id="f-013"></a>

#### F-013 · P2 · Chyba před vlastním odesláním ruční opravy zanechá osiřelou prepared operaci

Místa: `kajovo/core/generate_batch.py:995`, `kajovo/core/generate_batch.py:1083`, `kajovo/core/generate_batch.py:1972`, `kajovo/core/generate_batch.py:1980`, `kajovo/core/orchestration/repository.py:674`.

Podmínka a zdrojový tok: Ruční opakování nejprve v _prepare_v3_followup() zaregistruje provider operace jako prepared. Až potom provádí validate_access() a upload JSONL. Chyby těchto kroků neleží ve větvi, která operaci uzavře jako neodeslanou.

Dopad: Po odstranění příčiny selhání může další pokus narazit na dřívější prepared operaci a být odmítnut jako již probíhající práce. Jde o stav vzniklý před vytvořením vzdálené dávky, nikoli o oprávněnou blokaci neznámého výsledku odeslání.

<a id="f-014"></a>

#### F-014 · P2 · Opakované převzetí po lokální chybě narazí na zakázaný přechod partial → imported

Místa: `kajovo/core/generate_batch.py:1370`, `kajovo/core/generate_batch.py:1461`, `kajovo/core/generate_batch.py:1547`, `kajovo/core/orchestration/batch_manifest.py:148`.

Podmínka a zdrojový tok: První import může po chybě zápisu nebo jiné lokální importní chybě skončit partial. Po odstranění příčiny UI dovoluje stejné výsledky převzít znovu. Import znovu materializuje kandidáty a uloží část stavu, ale na konci žádá přechod na imported. Automat manifestu má partial jako terminální stav bez přechodů.

Dopad: Zotavení z opravitelné lokální chyby selže na vlastním stavovém automatu. Část souborů a run_state již může odrážet druhý import, zatímco manifest zůstává partial.

<a id="f-015"></a>

#### F-015 · P2 · Následné dávky a převzetí neověřují stejnou vzdálenou identitu jako první odeslání

Místa: `kajovo/core/runs/batch_execution.py:214`, `kajovo/core/generate_batch.py:1269`, `kajovo/core/generate_batch.py:2051`, `kajovo/core/generate_batch.py:1678`.

Podmínka a zdrojový tok: První dávka kontroluje vazbu odpovědi na input_file_id a endpoint. Následné wave a ruční opravy po submitu kontrolují hlavně přítomnost id. process_saved_batch() ověří vlastnictví identifikátoru předaného volajícím, ale neporovná jej s batch.id ani neověří vrácené input_file_id a endpoint proti uloženému manifestu.

Dopad: Nesprávně přiřazený nebo nekonzistentní objekt batch projde touto hranicí a jeho output_file_id se použije pro stahování. Další kontroly řádků část nesouladů zachytí, nenahrazují však chybějící ověření identity samotné dávky. Nález je chybějící kontrola konkrétního vstupu, ne tvrzení o běžném chybném chování poskytovatele.

<a id="f-016"></a>

#### F-016 · P2 · Kompatibilní import starých bundle nemá stejně přesnou vstupní masku jako nový import

Místa: `kajovo/core/batch_completion.py:325`, `kajovo/core/batch_completion.py:635`.

Podmínka a zdrojový tok: Dosažitelná legacy větev import_bundle() dosadí chybějícímu response.status_code hodnotu 200. Následné kontroly kontraktu a cest nenahrazují přesnou validaci celé obálky a builtin výstupního tvaru používanou novým zpracováním.

Dopad: Neúplná obálka může být vyhodnocena jako úspěšná odpověď a projít do importu. Striktnost parsování závisí na zvolené importní větvi, nikoli pouze na kontraktu přijímaných dat. Dopad je omezen na podporovaný legacy import.

### Kaskády a kontext

<a id="f-017"></a>

#### F-017 · P1 · Nově vytvořený krok může ignorovat typované vstupy zadané v editoru

Místa: `kajovo/core/cascade_types.py:195`, `kajovo/studio/cascades.py:180`, `kajovo/studio/cascades.py:225`, `kajovo/core/cascade_contract.py:300`, `kajovo/core/cascade_pipeline.py:1038`, `kajovo/core/cascade_pipeline.py:1091`.

Podmínka a zdrojový tok: CascadeStep má výchozí deterministic=False a UI při přidání kroku tento výchozí režim zachová. Současně umožní přidat typované inputs/outputs; jejich přidání režim nezmění. Validace nedeterministický krok přeskočí. Výrobní větev pak čte legacy input_text, input_content_json a files_local_paths, nikoli step.inputs.

Dopad: Uživatel přidá například soubor v záložce vstupů, validace jej neodmítne, ale soubor se nepředá výrobnímu payloadu. Také volba wire formátu zůstane řízena legacy output_type, nikoli očekáváním typovaného editoru. Chyba je nesoulad dostupného editoru a vykonávané větve, nikoli samotná existence legacy režimu.

<a id="f-018"></a>

#### F-018 · P1 · Obnova do nového běhu ponechává stagingové cesty vztažené ke starému běhu

Místa: `kajovo/studio/cascades.py:366`, `kajovo/studio/history_launcher.py:207`, `kajovo/studio/history_launcher.py:341`, `kajovo/core/cascade_pipeline.py:699`, `kajovo/core/cascade_pipeline.py:921`, `kajovo/core/cascade_pipeline.py:764`, `kajovo/core/orchestration/publish.py:225`.

Podmínka a zdrojový tok: Pokračování kaskády vytváří nový run_id. _load_resume_cache() pouze překopíruje záznamy cascade_staged_files, jejichž staged_path vznikla jako relativní cesta v původním run_dir. Fyzické stagingové soubory do nového běhu nepřenese a relativní cesty nepřeváže. Publikace je řeší proti adresáři nového loggeru.

Dopad: Již dokončené soubory předchozích kroků nejsou při publikaci nalezeny v novém běhu. Pokračování od pozdějšího kroku proto nedokáže korektně využít zachované souborové výsledky.

<a id="f-019"></a>

#### F-019 · P2 · Obnova z historie sama zneplatní podpis nezměněného předchozího kroku

Místa: `kajovo/studio/history_launcher.py:317`, `kajovo/core/cascade_contract.py:392`, `kajovo/core/cascade_pipeline.py:907`.

Podmínka a zdrojový tok: Historie přepíše cesty lokálních vstupů na archivované kopie. Podpis kroku zahrnuje tyto cesty. Při startu od pozdějšího kroku se nový podpis porovná s původním podpisem z cache, který obsahoval původní lokální cestu.

Dopad: Identická archivovaná data se vyhodnotí jako změněný předchozí krok a pokračování se odmítne. To je samostatný problém od chybějících stagingových souborů: může zabránit obnově ještě před výrobou.

<a id="f-020"></a>

#### F-020 · P2 · Restart dřívějšího kroku neinvaliduje jeho staré následné souborové výstupy

Místa: `kajovo/core/cascade_pipeline.py:921`, `kajovo/core/cascade_pipeline.py:1422`, `kajovo/core/cascade_pipeline.py:730`.

Podmínka a zdrojový tok: Při startu od vybraného kroku se z cache odstraňují následné hodnoty, kontext a informace o vykonaných krocích. Obdobné odstranění se neprovádí v _cascade_staged_files ani _cascade_expected_hashes. Nové výsledky pouze doplňují nebo nahrazují stejné cesty.

Dopad: Pokud opakované rozhodnutí zvolí jinou větev, soubory staré, nyní neprovedené větve zůstávají mezi kandidáty k publikaci. V současném toku může nejdříve narazit obnova na chybné relativní cesty; to neruší samostatnou chybu invalidace obsahu cache.

<a id="f-021"></a>

#### F-021 · P1 · Obecná obsluha chyby přepíše submission_unknown na failed

Místa: `kajovo/core/cascade_pipeline.py:245`, `kajovo/core/cascade_pipeline.py:1348`, `kajovo/core/cascade_pipeline.py:1667`, `kajovo/core/cascade_pipeline.py:2036`, `kajovo/core/openai_transport.py:375`.

Podmínka a zdrojový tok: Vnitřní obsluha nejednoznačného selhání označí provider operaci a runtime jako submission_unknown a znovu vyvolá původní výjimku. Jestliže nejde o zvlášť zachycený typ neznámého odeslání, vnější obecný except uloží failed do loggeru i runtime. Příkladem je nečekaný tvar úspěšné transportní odpovědi po odeslání požadavku.

Dopad: Trvalá evidence operace a stav kaskády si odporují. Ochrana nového spuštění vychází z runtime pending/unknown, takže nový běh může znovu odeslat práci, jejíž předchozí vzdálený výsledek není vyřešen. Nelze zaručit, že první požadavek nebyl vykonán.

<a id="f-022"></a>

#### F-022 · P2 · Po chybě stažení hotového dokumentu obnova nevyužije uložený vzdálený výsledek

Místa: `kajovo/core/cascade_production.py:24`, `kajovo/core/cascade_production.py:123`, `kajovo/core/cascade_production.py:148`, `kajovo/studio/history_launcher.py:341`, `kajovo/core/cascade_pipeline.py:1552`.

Podmínka a zdrojový tok: Dokumentová výroba uloží odpověď Code Interpreteru před následným GET souboru. Cache odpovědi se ale hledá pouze přes logger aktuálního běhu. Pokud GET selže a uživatel pokračuje novým během, uložená odpověď ani její identita nejsou do této cache přeneseny.

Dopad: Místo opětovného stažení již vytvořeného artefaktu může dojít k opakování výrobního kroku včetně placené výroby dokumentu. Rozpor je mezi lokální možností zotavení uvnitř stejného loggeru a reálnou UI obnovou s novým run_id.

<a id="f-023"></a>

#### F-023 · P2 · Podpis kroku nezahrnuje explicitní výraz určující předchozí Responses kontext

Místa: `kajovo/core/cascade_contract.py:392`, `kajovo/core/cascade_pipeline.py:1186`, `kajovo/core/cascade_pipeline.py:907`.

Podmínka a zdrojový tok: step_signature() před hashováním odstraňuje previous_response_id_expr. Sestavení legacy LIVE payloadu přitom tento výraz skutečně použije k určení previous_response_id.

Dopad: Změna explicitní kontextové návaznosti nezneplatní cache již provedeného kroku. Při pokračování může být použit výsledek vyrobený nad jiným kontextem, než jaký určuje aktuální definice.

<a id="f-024"></a>

#### F-024 · P2 · Parser kaskády přijímá neznámé verze a tiše zahazuje neznámá pole

Místa: `kajovo/core/cascade_types.py:277`, `kajovo/core/cascade_types.py:423`.

Podmínka a zdrojový tok: from_dict() vybírá známé atributy bez kontroly úplné množiny klíčů. Verze definice se kontroluje pouze jako kladné celé číslo; například version=99 je přijatá stejným dekodérem. Neznámé pole v kroku se do objektu nepřenese.

Dopad: Překlep nebo pole novějšího formátu může při načtení a dalším uložení zmizet bez upozornění. Definice je přijatá, přestože runtime neumí doložit význam všech jejích částí. Týká se samotné hranice serializované definice, nikoli JSON výstupu modelu.

<a id="f-025"></a>

#### F-025 · P2 · Úprava celého JSON kontraktu používá parser, který tiše sloučí duplicitní klíče

Místa: `kajovo/studio/resources.py:51`, `kajovo/studio/cascades.py:279`, `kajovo/studio/cascades.py:292`, `kajovo/studio/cascade_items.py:87`.

Podmínka a zdrojový tok: ValueDialog.submit() používá obyčejné json.loads(). Přes tento dialog vede úprava kontraktu i celé definice kaskády. Objekt s dvěma stejnými klíči je ještě před následnou validací převeden na jedinou poslední hodnotu. Dílčí editor naproti tomu používá striktní parser.

Dopad: Nejednoznačná JSON maska či definice se přijme a jedna uživatelská hodnota zmizí. Pozdější validace už duplicitu nemůže zjistit. Různé editory téhož procesu nemají stejnou přesnost parsování.

<a id="f-026"></a>

#### F-026 · P3 · Změna výstupního adresáře v JSON definici se ihned přepíše starou hodnotou widgetu

Místa: `kajovo/studio/cascades.py:290`, `kajovo/studio/cascades.py:303`.

Podmínka a zdrojový tok: definition_options() nahradí definici podle přijatého JSON a aktualizuje název, ale nepřenese default_out_dir do výstupního widgetu. Následující validace zapíše do nové definice hodnotu právě z tohoto nezměněného widgetu.

Dopad: Uživatel přijme platnou změnu default_out_dir, ale aplikace ji tiše vrátí na předchozí adresář. Při spuštění se použije jiný výstupní adresář než v potvrzené JSON úpravě.

### Modely a uživatelské nastavení

<a id="f-027"></a>

#### F-027 · P2 · Obnovení nabídky modelů přepíše existující uživatelskou volbu doporučeným modelem

Místa: `kajovo/studio/cascades.py:123`, `kajovo/studio/cascades.py:213`, `kajovo/studio/workbench.py:145`, `kajovo/studio/workbench.py:233`, `kajovo/studio/photos.py:177`.

Podmínka a zdrojový tok: Obnova seznamu modelů vybírá doporučený model namísto zachování aktuálního, pokud je stále přípustný. Ve workbenchi je obnovování navázané i na přepnutí LIVE/BATCH. V kaskádě následný commit zapíše takto změněnou volbu přímo do vybraného kroku.

Dopad: Pouhé obnovení katalogu nebo přepnutí režimu může změnit použitý model a uloženou definici bez explicitní volby uživatele. Nález se týká případu, kdy původní model zůstává dostupný; oprávněná změna při skutečné nekompatibilitě není problém.

### Komiksy

<a id="f-028"></a>

#### F-028 · P2 · Generování přijme zastaralou bibli, kterou výroba panelu následně odmítne

Místa: `kajovo/core/comic_store.py:305`, `kajovo/core/comic_store.py:361`, `kajovo/core/comic_store.py:404`, `kajovo/core/comic_service.py:600`, `kajovo/core/comic_service.py:672`, `kajovo/core/comic_service.py:1210`.

Podmínka a zdrojový tok: Úprava stylu nebo referencí projektu ponechá bible_id. Start textových kroků a start reference entity ověřují hlavně existenci této identity. Výroba reference tedy může pracovat se starou biblí. compile_panel() naproti tomu porovnává bibli s aktuálním stylem a referencemi a nesoulad odmítne.

Dopad: Po změně stylu lze zaplatit mezikrok založený na neaktuální bibli, ale navazující panel jej v aktuálním projektu nemůže použít. Kontroly společného předpokladu jsou rozdílné na vstupu jednotlivých výrobních kroků.

<a id="f-029"></a>

#### F-029 · P2 · Kontinuita může porovnávat storyboard s jinou verzí scénáře, než ze které vznikl

Místa: `kajovo/core/comic_service.py:654`, `kajovo/core/comic_service.py:665`, `kajovo/core/comic_service.py:895`.

Podmínka a zdrojový tok: start_continuity() nezávisle načte poslední storyboard a poslední script; scénář nehledá podle source_id vybraného storyboardu. Výsledek kontroly je navázán na storyboard. Materializace pak kontroluje tuto storyboard identitu.

Dopad: Vznikne storyboard ze scénáře S1, poté nový scénář S2. Kontinuita může zhodnotit storyboard S1 vůči S2 a tento výsledek použít jako podklad pro materializaci storyboardu S1. Kontrola neověřuje zamýšlený pár producent–konzument.

<a id="f-030"></a>

#### F-030 · P2 · Definitivně odmítnutá dávka zůstane s prepared položkami, které nelze přímo opakovat

Místa: `kajovo/core/comic_service.py:1279`, `kajovo/core/comic_service.py:1320`, `kajovo/core/comic_service.py:1418`, `kajovo/core/comic_service.py:1652`, `kajovo/core/comic_service.py:1671`.

Podmínka a zdrojový tok: Po definitivním odmítnutí se změní stav dávky na rejected, ale její položky zůstávají prepared. retry_failed() vybírá pouze failed položky a nové start_panels() připravené položky blokuje. Chybová větev přitom uživatele směruje k opakování.

Dopad: Přímé opakování odmítnuté práce nenajde žádné způsobilé položky. Dodatečné zrušení operace je může převést na failed a posloužit jako obcházení problému; nejde tedy o nevratnou ztrátu, ale o vadný standardní opravný tok.

<a id="f-031"></a>

#### F-031 · P2 · Fyzický hash LIVE obrazového požadavku zahrnuje obálku, která se neposílá

Místa: `kajovo/core/comic_service.py:281`, `kajovo/core/comic_service.py:1086`, `kajovo/core/comic_service.py:1094`, `kajovo/core/orchestration/repository.py:686`.

Podmínka a zdrojový tok: Příprava image operace dostává objekt obsahující endpoint a body a jeho hash přebírá evidence fyzického požadavku. Následující create_image() ale jako požadavek odesílá pouze body.

Dopad: Údaj physical_request_hash nereprezentuje stejný JSON objekt jako skutečné tělo LIVE požadavku. Ověřování důkazů proti skutečnému tělu by hlásilo rozdíl, přestože se tělo při odeslání nezměnilo.

<a id="f-032"></a>

#### F-032 · P2 · Duplikace projektu ztrácí vazby panelů na zkopírovaný storyboard

Místa: `kajovo/core/comic_service.py:1713`, `kajovo/core/comic_service.py:1756`, `kajovo/core/comic_store.py:499`, `kajovo/studio/comics.py:946`.

Podmínka a zdrojový tok: Duplikace vytvoří mapu nových dokumentů, ale panely zakládá přes obecné store.panel() a save_panel(). Nepřenese storyboard_id a storyboard_position na odpovídající nové dokumenty.

Dopad: Panely zkopírovaného projektu se pro následnou materializaci jeví jako ruční, nikoli jako panely daného storyboardu. Opětovná materializace zkopírovaného storyboardu místo aktualizace odpovídajících panelů vytvoří další sadu.

<a id="f-033"></a>

#### F-033 · P2 · Historické verze duplikovaného projektu ztrácejí vlastní formát plátna

Místa: `kajovo/core/comic_service.py:1770`, `kajovo/core/comic_store.py:645`.

Podmínka a zdrojový tok: Při kopírování panel_versions se provenance nahradí objektem obsahujícím pouze copied_from. Formát a snapshot původní verze se nepřenesou. restore_version() bere formát z provenance, a pokud chybí, použije současný formát panelu.

Dopad: Pokud měl starší obrázek jiný rozměr nebo DPI, obnova této verze v kopii projektu ponechá aktuální metadata plátna. Obnovený obraz a uložený formát tak nemusí odpovídat stejné verzi.

<a id="f-034"></a>

#### F-034 · P2 · Povolená délka storyboardového textu není slučitelná s automaticky vytvořenou sazbou

Místa: `kajovo/core/comic_types.py:68`, `kajovo/core/comic_types.py:78`, `kajovo/core/comic_service.py:988`, `kajovo/studio/comic_editor.py:159`, `kajovo/studio/comic_editor.py:183`, `kajovo/studio/comics.py:814`.

Podmínka a zdrojový tok: Kontrakt připouští dialogy řádově tisíců znaků a caption do 3000 znaků. Materializace jim přidělí pevné relativní rozměry a pevnou velikost písma; není zde odvození layoutu od skutečně přijaté délky. Renderer při překročení prostoru odmítne render_panel().

Dopad: Platný storyboard může projít materializací i placenou výrobou obrázku, ale následný export panelu selže na textovém přetečení. Uživatel musí ručně změnit layout nebo text. Problém je nespojitost kontraktu textu a jeho sazby, nikoli požadavek na nové testování hotového produktu.

<a id="f-035"></a>

#### F-035 · P2 · Duplikace dlouhého názvu panelu zanechá neúplný databázový záznam

Místa: `kajovo/core/comic_store.py:574`, `kajovo/core/comic_store.py:584`, `kajovo/core/comic_store.py:620`, `kajovo/studio/comics.py:651`.

Podmínka a zdrojový tok: Platný název panelu může mít 200 znaků. Duplikace přidá příponu „ – kopie“. panel() nejprve samostatnou transakcí vloží záznam a až potom volá save_panel(), kde delší název neprojde. Vložení se již nevrátí zpět.

Dopad: Zůstane panel bez dokončeného prompt_id. Následné otevření panelu předpokládá existující prompt a může selhat. Chyba validace vstupu se tak mění na trvalou nekonzistenci lokální databáze.

<a id="f-036"></a>

#### F-036 · P2 · Úprava editoru bez vybraného panelu může zablokovat přidání prvního panelu i zavření aplikace

Místa: `kajovo/studio/comics.py:328`, `kajovo/studio/comics.py:382`, `kajovo/studio/comics.py:390`, `kajovo/studio/comics.py:672`, `kajovo/studio/comics.py:703`, `kajovo/studio/comics.py:729`.

Podmínka a zdrojový tok: Po výběru projektu bez panelů jsou záložky a editor dostupné. Změna pole nastaví dirty=True, i když panel_id není nastaven. save_panel() pak vrací False. Přidání panelu, změna projektu a uložení před zavřením vyžadují úspěch právě této funkce.

Dopad: Uživatel se běžnou editací dostane do stavu, kdy nemůže vytvořit první panel ani změny standardně uložit. Chybí odpovídající práce s rozpracovaným novým panelem nebo možnost v tomto stavu změny zahodit; nález nevyžaduje chybnou databázi.

### Fotografie a převod textů

<a id="f-037"></a>

#### F-037 · P2 · Soubor fotografických šablon kontroluje klíče, ale ne typy jejich hodnot

Místa: `kajovo/core/photo_templates.py:123`, `kajovo/core/photo_templates.py:129`, `kajovo/core/photo_templates.py:132`.

Podmínka a zdrojový tok: Po kontrole přesné množiny klíčů se vytvoří dataclass bez runtime kontroly typů. Hned poté se na name a prompt volá strip(). Například JSON s name=123 je syntakticky korektní a má správné klíče, ale zde vyvolá AttributeError místo řízené chyby neplatného kontraktu. Nehashovatelné template_id obdobně narazí na množinu seen.

Dopad: Poškozený či ručně upravený soubor šablon nemá bezpečnou typovou hranici. Místo přesné informace o vadném poli selže načítání seznamu šablon neočekávaným typem výjimky. Kontrola klíčů není plnohodnotnou maskou hodnot.

<a id="f-038"></a>

#### F-038 · P2 · Vlastní šablona může použít rezervovanou identitu vestavěné šablony

Místa: `kajovo/core/photo_templates.py:130`, `kajovo/core/photo_templates.py:138`, `kajovo/core/photo_templates.py:141`, `kajovo/core/photo_templates.py:172`.

Podmínka a zdrojový tok: Loader vlastní šablony kontroluje builtin=False a duplicity pouze uvnitř vlastního seznamu. Neodmítá prefix builtin- ani kolizi s identitou vestavěné šablony. list() řadí vestavěné položky před vlastní a get() vrací první shodu; aktualizace a mazání rezervovaný prefix odmítají.

Dopad: Importovaná vlastní položka může být zastíněna jinou šablonou se stejným ID. Rezervovanou vlastní položku navíc nelze standardními operacemi upravit nebo smazat. Nález se týká načtení souboru, nikoli create(), které generuje nové tpl_ ID.

<a id="f-039"></a>

#### F-039 · P3 · Zrušení fotografické dávky nepromítne odpověď služby do lokálního stavu

Místa: `kajovo/studio/photos.py:407`.

Podmínka a zdrojový tok: cancel_job() zavolá cancel_batch(), nepoužije vrácený stav a následně obnoví seznam z nezměněných lokálních záznamů. Aktualizaci vzdáleného stavu provádí jiná akce refresh_job().

Dopad: Po úspěšném požadavku na zrušení UI nadále ukazuje předchozí stav, dokud uživatel neprovede další obnovu. To může vyvolat dojem, že zrušení nebylo provedeno. Nález netvrdí, že se vzdálená dávka nezruší.

<a id="f-040"></a>

#### F-040 · P2 · Normalizace UTF-8 může odstranit skutečný první znak po BOM

Místa: `utf8nobom/app.py:182`, `utf8nobom/app.py:188`, `utf8nobom/app.py:193`.

Podmínka a zdrojový tok: Dekódování přes utf-8-sig už odstraní jeden úvodní BOM. Následující větev znovu odstraní první U+FEFF z dekódovaného textu. Vstup tvořený UTF-8 BOM a skutečným počátečním znakem U+FEFF tak ztratí i tento obsahový znak.

Dopad: Převod neprovádí pouze odstranění značky kódování, ale v uvedeném okrajovém případě mění obsah dokumentu. Existující záloha umožňuje obnovu; neodstraňuje samotnou chybu transformace.

### Evidence a historie

<a id="f-041"></a>

#### F-041 · P2 · Validace přípravy V2 se nepromítá do validation_status uložené odpovědi

Místa: `kajovo/core/orchestration/preparation.py:955`, `kajovo/core/orchestration/preparation.py:977`, `kajovo/core/run_bundle.py:713`, `kajovo/core/run_bundle.py:771`.

Podmínka a zdrojový tok: Nová příprava zapisuje validační záznamy s target_type=preparation_v2. record_validation() aktualizuje odpovídající response record jen pro target_type=preparation. Výchozí validation_status odpovědi je pending.

Dopad: I po provedené úspěšné nebo neúspěšné validaci může odpověď zůstat označená pending. Ve stejném bundle si pak odporuje evidence odpovědi a samostatný validační záznam.

<a id="f-042"></a>

#### F-042 · P2 · Čtení moderních bundle v historii používá tolerantní legacy parsování

Místa: `kajovo/core/run_bundle.py:1188`, `kajovo/core/run_bundle.py:1231`, `kajovo/core/run_bundle.py:1253`.

Podmínka a zdrojový tok: LegacyRunAdapter načítá state, events a evidence pomocí _read_json_legacy/_read_jsonl_legacy i v případě současného bundle. Tyto čtečky tolerují problematické hodnoty a při nečitelném záznamu vracejí náhradní výsledek nebo řádek vynechají místo předání informace o poškození.

Dopad: Poškozená odpověď nebo událost může z historie zmizet jako chybějící záznam; duplicity JSON klíčů nemají stejný režim jako striktní runtime čtečky. Zobrazená historie proto nerozlišuje neexistující důkaz od poškozeného důkazu. Tento nález se netýká přísnějšího načítání pro runtime obnovu.

<a id="f-043"></a>

#### F-043 · P2 · Záznam request.sent vzniká ještě před lokální kontrolou a odesláním

Místa: `kajovo/core/run_bundle.py:660`, `kajovo/core/runs/file_execution.py:205`, `kajovo/core/runs/file_execution.py:227`.

Podmínka a zdrojový tok: Uložení připraveného requestu přes logger vytváří request.sent a vyplňuje čas odeslání. Ve výrobním toku následuje teprve validace a příprava skutečného volání, která může požadavek lokálně odmítnout.

Dopad: Evidence může tvrdit, že byl požadavek odeslán, ačkoli transport vůbec nebyl zavolán. To je nesprávný forenzní fakt důležitý zejména při rozlišování neodeslané práce a neznámého výsledku odeslání.

<a id="f-044"></a>

#### F-044 · P2 · Filtr běhů s výstupy opomíjí výstupní artefakty komiksu

Místa: `kajovo/core/comic_service.py:1532`, `kajovo/core/comic_service.py:1639`, `kajovo/core/run_bundle.py:1427`, `kajovo/studio/history_models.py:153`, `kajovo/studio/history_models.py:266`.

Podmínka a zdrojový tok: Komiks registruje vytvořené artefakty s role=output. Index historie a výpočet has_output počítají jiné explicitní role, například generated_file a batch_output, ale output v nich chybí. Detail běhu tuto roli na jiném místě rozpoznává.

Dopad: Komiksový běh se skutečně uloženým výstupem může zmizet při filtrování historie na běhy s výstupy. Přehled a detail stejného běhu mají rozdílnou interpretaci artefaktového kontraktu.

<a id="f-045"></a>

#### F-045 · P2 · Historie odvodí připravenost k převzetí z různých dávek téhož běhu

Místa: `kajovo/studio/history_models.py:135`, `kajovo/studio/history_models.py:142`, `kajovo/studio/history_models.py:166`.

Podmínka a zdrojový tok: pending_import se odvozuje z existence libovolné dosud nepřevzaté dávky, zatímco remote_complete z existence libovolné dokončené dávky. Pro ready_to_import se tyto příznaky spojí, aniž by musely náležet stejnému batch_id.

Dopad: Stačí stará dokončená a převzatá dávka A a nová dosud probíhající nepřevzatá dávka B. Historie označí běh jako připravený k převzetí, přestože žádná dosud nepřevzatá dávka hotová není.

<a id="f-046"></a>

#### F-046 · P3 · Průběh označuje nedoložené kroky za hotové podle jejich pozice v seznamu

Místa: `kajovo/core/progress_display.py:138`, `kajovo/core/progress_display.py:155`, `kajovo/progress_ui.py:465`, `kajovo/progress_ui.py:496`, `kajovo/core/runs/diagnostics.py:109`.

Podmínka a zdrojový tok: Nový neznámý stage se přidá na konec přednastaveného seznamu. Všechny položky před aktuálním indexem dostanou done i bez události completed. Diagnostika je konkrétní stage mimo základní výrobní seznam a může proběhnout před výrobou. Aktuální položka navíc zůstává current i po vlastní completed události, dokud nepřijde další krok.

Dopad: Uživatel může během diagnostiky vidět výrobní a ukládací kroky jako dokončené, ačkoli ještě nezačaly. Zobrazení není pouze nepřesný časový odhad; tvrdí nedoložené dokončení konkrétních kroků.

<a id="f-047"></a>

#### F-047 · P3 · Terminální stavy nejsou jednotně promítnuté do průběhu a historie

Místa: `kajovo/core/progress.py`, `kajovo/progress_ui.py:145`, `kajovo/progress_ui.py:590`, `kajovo/studio/history_state.py:40`.

Podmínka a zdrojový tok: Jádro ukončuje práci i ve stavech qfile_plan_ready, needs_clarification a waiting_manual_resource. Lokální množina TERMINAL_STATES v progress_ui je neobsahuje. Překlady a mapování historie zároveň nepokrývají všechny tyto stavy; označení files_complete_unverified v historii neodpovídá jednoznačně tomu, zda již byly soubory převzaty.

Dopad: Po ukončení workeru může zůstat neurčitý běžící indikátor, zobrazit se interní anglický stav nebo „Neznámý výsledek“. Uživatel nemá jednotnou informaci, zda aplikace pracuje, čeká na odpověď, nebo již předala soubory.

<a id="f-048"></a>

#### F-048 · P3 · Horní mez kalendářního filtru není konec vybraného místního dne

Místa: `kajovo/studio/history.py:223`.

Podmínka a zdrojový tok: Horní časová mez se počítá jako timestamp místní půlnoci + 86399 sekund. Den při změně letního času nemusí mít 86400 sekund; timestampy běhů navíc mohou obsahovat zlomky sekund.

Dopad: Na podzim filtr vynechá konec vybraného dne, na jaře může zahrnout část dalšího dne. V běžný den vynechá záznamy po 23:59:59.000 před následující půlnocí.

<a id="f-049"></a>

#### F-049 · P3 · Lidské zobrazení chyb dostává názvy polí, které neumí vykreslit

Místa: `kajovo/studio/history_details.py:363`, `kajovo/studio/evidence.py:104`.

Podmínka a zdrojový tok: Detail předává EvidenceView objekt s klíči poslední_chyba, validace, dávky a zotavení. Netechnické renderování vybírá výhradně klíče z vlastní anglické mapy NAMES, kde tyto klíče nejsou.

Dopad: Záložka chyb může místo existujícího obsahu ukazovat obecnou informaci, že záznam nemá lidské shrnutí. Skutečné podklady se zobrazí až po přepnutí na technický JSON. Nejde o ztrátu uložených dat, ale o vadné mapování prezentace.

<a id="f-050"></a>

#### F-050 · P3 · Aplikace a distribuční metadata uvádějí rozdílnou verzi

Místa: `kajovo/__init__.py:2`, `pyproject.toml:7`, `kajovo/core/runlog.py:273`, `.github/workflows/release.yml:56`.

Podmínka a zdrojový tok: Runtime __version__ má hodnotu 1.0.0, zatímco project.version v pyproject.toml je 0.1.0. Logování přebírá první hodnotu a release metadata druhou.

Dopad: Artefakt distribuce a běhové důkazy téže zdrojové sestavy uvádějí odlišnou verzi aplikace. Podle samotného údaje o verzi nelze spolehlivě párovat instalaci a zaznamenaný běh.

<a id="f-051"></a>

#### F-051 · P3 · Chybové hlášení nesouladu stagingu může vypsat prázdný seznam vadných cest

Místa: `kajovo/core/cascade_pipeline.py:766`.

Podmínka a zdrojový tok: Podmínka porovnává rovnost cest expected a staged, ale zpráva vypočítá pouze staged - expected. Při opačném nesouladu, kdy očekávaná cesta ve stagingu chybí, je tato množina prázdná.

Dopad: Publikace správně skončí chybou, ale zpráva neuvede žádnou cestu a popisuje neúplnost očekávání, i když chybí stagingový soubor. To ztěžuje určení skutečně vadné strany vazby.

<a id="f-052"></a>

#### F-052 · P3 · Nejednoznačná zpráva o ověření souboru nerozlišuje kontrakt od funkčnosti

Místa: `kajovo/core/runs/file_execution.py:373`, `kajovo/core/runs/generate.py:261`.

Podmínka a zdrojový tok: Zpracování jednotlivého souboru oznamuje ověření celého souboru po kontrole výstupního kontraktu, obsahu a integrity. Závěrečný stav současně rozlišuje files_complete_unverified; funkčnost vytvořeného produktu v tomto kroku doložená není.

Dopad: Průběžná zpráva může být chápána jako širší potvrzení, než které kód skutečně provedl. Jde o nesoulad textu UI a rozsahu kontroly, nikoli o návrh přidávat spuštění či testování vygenerovaného produktu.

### Správa vzdálených zdrojů

<a id="f-053"></a>

#### F-053 · P2 · Asynchronní dokončení operace přiřadí soubory jinému právě vybranému vector store

Místa: `kajovo/studio/resources.py:124`, `kajovo/studio/resources.py:133`, `kajovo/studio/resources.py:215`, `kajovo/studio/resources.py:241`, `kajovo/studio/resources.py:252`, `kajovo/studio/resources.py:264`.

Podmínka a zdrojový tok: Během operace se vypnou tlačítka, nikoli výběrové seznamy. Samostatná obnova souborů kontroluje identitu vybraného store, ale callbacky přidání a odstranění souborů naplní seznam bez této kontroly. Uživatel mezitím může zvolit jiný store. Úprava atributů navíc drží odkaz na položku, kterou změna výběru může odstranit.

Dopad: Soubory původního store se mohou zobrazit pod nově vybraným store a následná akce kombinuje aktuální store ID se souborovými ID jiného store. Callback s již odstraněnou Qt položkou může skončit RuntimeError. Jde o konkrétní závod mezi identitou zahájené operace a aktuálním výběrem.

### Konfigurační zdroje

<a id="f-054"></a>

#### F-054 · P3 · Část načítané obrazové politiky není navázaná na skutečné výrobní hodnoty

Místa: `kajovo/core/orchestration/policies/images.json:11`, `kajovo/core/orchestration/policies/images.json:34`, `kajovo/core/orchestration/policies/images.json:44`, `kajovo/core/orchestration/image_slots.py:19`, `kajovo/core/comic_service.py:1155`, `kajovo/core/orchestration/resource_delivery.py:402`, `kajovo/core/cascade_production.py:45`.

Podmínka a zdrojový tok: Runtime načítá images.json jako politiku, ale quality=high v tomto zdroji se nepoužije v uvedených výrobních větvích, které přímo nastavují max. Hodnoty story.max_scenes/max_panels a comic_overlay min/max_font_points nemají v produkční implementaci odpovídající konzumenty; sazba používá jinou, relativní reprezentaci.

Dopad: Úprava těchto položek konfigurace nemění uváděné chování a pro jednu oblast existuje více nespojených zdrojů hodnot. Nález neříká, že konkrétní hodnota high či max je u vzdáleného API neplatná; bez externí dokumentace se platnost endpointu tímto auditem neposuzuje.

### Sestavení a pomocné nástroje

<a id="f-055"></a>

#### F-055 · P2 · Windows release může překrýt selhání dřívějšího nativního příkazu úspěchem posledního

Místa: `.github/workflows/release.yml:211`.

Podmínka a zdrojový tok: Jeden PowerShell run blok volá postupně compatibility_smoke.py, pip check a pip_audit bez kontroly návratového kódu mezi příkazy. Nativní neúspěch předchozího Python procesu sám o sobě není PowerShell výjimkou; poslední nativní příkaz může přepsat LASTEXITCODE na nulu.

Dopad: Když první nebo druhý příkaz selže a poslední uspěje, krok může skončit úspěšně a pokračovat k zabalení releasu. Nález vychází z řídicího toku workflow, nikoli z výsledků těchto kontrol; samotné kontroly nebyly spuštěny ani použity jako důkaz.

<a id="f-056"></a>

#### F-056 · P2 · Migrační helper bezpodmínečně maže celý LOG a není bezpečný pro opakované spuštění

Místa: `tools/_r05c_finalize_studio.py:575`, `.github/workflows/r05c_finalize_studio.yml`.

Podmínka a zdrojový tok: cleanup_repo() volá shutil.rmtree() nejprve na kajovo/desktop a potom na kořenový LOG. Neověřuje, zda jde o prázdná či ukázková data, a nedělá zálohu. Pokud první adresář již neexistuje, skončí FileNotFoundError ještě před druhým voláním.

Dopad: Při přítomném starém desktopovém adresáři může spuštění helperu odstranit veškerá lokální běhová data v LOG. V již migrovaném stromu naopak tuto část nelze bezpečně opakovat. Jde o explicitně spouštěný údržbový nástroj, nikoli automatickou činnost běžného startu aplikace.

### Latentní vady pomocných funkcí

<a id="f-057"></a>

#### F-057 · P3 · Pomocná obnova očekává structure i u přijatého snapshotu V2

Místa: `kajovo/core/recovery.py:70`, `kajovo/core/recovery.py:75`, `kajovo/core/delivery_preparation.py:94`.

Podmínka a zdrojový tok: recover_run() přijme přes validate_preparation_snapshot() také verzi 2 s graph. Bez rozlišení verze potom přistoupí ke snapshot["structure"], případně k dalším polím starého formátu.

Dopad: Volání s platným novým snapshotem skončí KeyError místo sestavení obnovy. V aktuálním produkčním kódu nebyl nalezen volající této pomocné funkce, proto ji neuvádím jako prokázané selhání současného UI pokračování.

<a id="f-058"></a>

#### F-058 · P3 · ready_tasks() vrací již dokončené úlohy a nemusí postoupit k další vlně

Místa: `kajovo/core/orchestration/waves.py:77`.

Podmínka a zdrojový tok: Funkce sestaví available z evidence a projde vlny od začátku. Vybere každou úlohu se splněnými závislostmi, ale nevyřadí úlohy, které samy již v available jsou. Vrací první neprázdnou vlnu.

Dopad: Při opakovaném použití pro posouvání grafu vrací stále první dokončené úlohy namísto následných. V aktuální aplikaci nebyl nalezen volající; současný BATCH používá vlastní výběr a tento nález nelze automaticky vztáhnout na něj.

<a id="f-059"></a>

#### F-059 · P3 · Vyhledání posledního nedokončeného běhu řadí identifikátory místo skutečného času

Místa: `kajovo/core/runlog.py:834`, `kajovo/core/utils.py:15`.

Podmínka a zdrojový tok: find_last_incomplete_run() řadí jména RUN_ sestupně lexikograficky a zkoumá nejvýše prvních 30. Identifikátor však obsahuje datum s dnem před měsícem a rokem.

Dopad: Například konec předchozího měsíce se může řadit před začátek aktuálního. Funkce pak vrátí starší běh nebo novější kvůli limitu vůbec nenajde. V aktuálním produkčním kódu nebyl nalezen její volající; jde o latentní vadu pomocného API.

## Průřezový závěr

Přesná JSON maska na jedné hranici sama o sobě nezajišťuje správný proces. V nalezených případech se odlišuje to, co editor nebo předchozí krok přijme, od toho, co další krok skutečně spotřebuje. Nejvýraznější příklady jsou typované vstupy kaskády, obsahové závislosti resources, párování komiksového scénáře a storyboardu a rozdílné importní hranice BATCH.

LIVE a BATCH mají odlišné vedlejší efekty a odlišný způsob obnovy. Vady se soustřeďují zejména na klasifikaci výsledku odeslání, vazbu vzdálené identity na místní manifest a zachování již hotových výsledků při opakování. Společná příčina několika evidenčních a UI nálezů je samostatné udržování stejných stavů, rolí nebo hodnot na více místech bez úplné návaznosti.

Z této zprávy neplyne zavedení dodatečných produktových testů, generovacích pokusů, budgetových mechanismů ani nových schvalovacích mezikroků. Audit pouze popisuje nalezené vady aktuální implementace. Opravy nebyly součástí této práce.

## Příloha: úplný přečtený korpus

Každý níže uvedený soubor byl přečten celý. SHA-256 identifikuje jeho obsah, nikoli pouze verzi v Gitu. Porovnání kontrolních hashů před zápisem zprávy nezjistilo změnu auditovaných zdrojů. Čísla řádků v nálezech se vztahují k tomuto stavu.

| Soubor | Fyzické řádky | SHA-256 |
| --- | ---: | --- |
| `.editorconfig` | 16 | `f1998568ae43bea112851234b5ffd98817b776bcc5aa4657298f282745cc70c8` |
| `.gitattributes` | 5 | `a4b3d2ca3d79cdc5f0dadb628c97d6a4f65f7a43366b19f88346d2462c99fd2c` |
| `.github/workflows/ci.yml` | 168 | `14ad69408e141e06bbb2360ed86b80c0ec458a3be278af5803c103806692999c` |
| `.github/workflows/contract-links.yml` | 76 | `f2a60854f50ae68856ea00db2d1166cc3bdff0dec20850fca68087276f690e58` |
| `.github/workflows/finalize_nine_hardening.yml` | 138 | `c4c6b3b28e3fd77f57555f5855a744204c14bdecdbb1a57c4590836dd0b306e6` |
| `.github/workflows/r05c_finalize_studio.yml` | 92 | `677578d9774a3767775e8a249ea3a8770fcc3f2803acacd1d3e204f7705d5e06` |
| `.github/workflows/release.yml` | 505 | `37d41ca06ee6ad579638403d504fdbf3106357376b85d86c201151b046c241e5` |
| `.gitignore` | 41 | `a0b4c944fe3d5121ad8da0beaaa22c775ecd94518b495e489f3aa4450e89e5ab` |
| `.pre-commit-config.yaml` | 33 | `4679a511806d47f7ff81213b361b3bc2a5c3d3871aa22507bb67841b5689f9b6` |
| `Build/assets/.gitignore` | 3 | `aae815b9313ef60fb99d51bec324f3de1cea5256d6bbf58a660578b3e2d5815c` |
| `Build/build_macos.sh` | 52 | `4d84d7a03f7361cbf796f0fbd713f626fe38079b8a82a57671e0f2f56a1b2274` |
| `Build/build_windows.ps1` | 44 | `d32ad50a73d8546838e7556d4baea6068a1b7f4345b0a969896741991774e925` |
| `Build/generate_icons.py` | 52 | `c0c848241ce7fa4c5009d59e4457b674e6a55f72d3137eda501259a2dc53f789` |
| `kajovo_settings.example.json` | 61 | `c63a765ced55477ad94fc39554129bc48929898a9522d35ba0e716bcdf6bf386` |
| `kajovo/__init__.py` | 2 | `d94b3867fcaa0c3f653aae1d1f014534ba2ba3a44c497fb4a28fa0d6d3ad3c2a` |
| `kajovo/app/__init__.py` | 0 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| `kajovo/app/__main__.py` | 4 | `4ac953d9d43e4ef3a1f1a054d0ceb385a87076d105a99db4166d79426a737ca4` |
| `kajovo/app/main.py` | 93 | `d8e40f6c47c156730db7291cebcf2b35b013e25bfc6deaf9033ad45f5d1b9644` |
| `kajovo/core/__init__.py` | 0 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| `kajovo/core/batch_completion.py` | 733 | `fe0aeea0cb2c69101905279f4dd5926869e23ea8b37cae27361f7a574665ab23` |
| `kajovo/core/batch_result.py` | 17 | `96a36099070a7f16e4d738739a482f245a791130b7ad59a728d595445fa70d9a` |
| `kajovo/core/batch_submit.py` | 86 | `a2ac7f1d77ad53a9d2c9730ebf299e04d1f8fbaa5b17d716a91558abc042dd01` |
| `kajovo/core/cascade_contract.py` | 526 | `3c5f942925b9835c608ce7e9173add6dcc4e92112afb2cb9f3696e3afa6eaa00` |
| `kajovo/core/cascade_log.py` | 14 | `4d608302f57416229fa071b5bd25887de1e9f4f26b2a554697e61ce8a30d4a78` |
| `kajovo/core/cascade_pipeline.py` | 2065 | `87e81eb2817db30f1463c318fa181b0ba6c9ec28878c889c8cc88ea8cd1b9beb` |
| `kajovo/core/cascade_production.py` | 178 | `9ea6337710dde8eb8831115cce874187e78b8910d6d04fe56ffe52626dad1e40` |
| `kajovo/core/cascade_types.py` | 435 | `58f180dcf92e9e8dcaa6f959cf8c3ea238d3f5cda452d8ce5b1f4ca67062ef5f` |
| `kajovo/core/comic_service.py` | 1773 | `c62f6d7c622379940e40703774ef5f190e56ac39128652cd67db54e0436e3bb3` |
| `kajovo/core/comic_store.py` | 680 | `5dbd21d0586560befac9856229e3b1bfde5e5dc6b472acff0055fae0f0889cca` |
| `kajovo/core/comic_types.py` | 369 | `0215e60696175cf8a58596f3bd5b279a9a82fbe86de7e01d28abdc544282263d` |
| `kajovo/core/compat.py` | 41 | `9d25dd50f1a540bb097644f0343394e3ce205c3c84fc6f7d9d8d3842eecb5224` |
| `kajovo/core/config.py` | 165 | `7ff4087017f35a9707b415a69b916886b76f362dbb28da281a8c245c0ca6061b` |
| `kajovo/core/context_compiler.py` | 531 | `c6277837195f51e8e28e7cc248a11b6427fd1b6a6c263b8c7094eb8831015b39` |
| `kajovo/core/context_limits.py` | 272 | `460329cb765779aed39079c5ed3fde03721ea16f3c90958ef0e71418566c9c6c` |
| `kajovo/core/contracts.py` | 197 | `dfacd0cd64ec52d0e1380cae7b6a6b364ceba8fc7261f68e7f3e627a479968b8` |
| `kajovo/core/delivery_preparation.py` | 182 | `670ffc8feadf1bcc0ed7ae21f4ce6a5b341afa305be31ec08a2ab09ad215e2ef` |
| `kajovo/core/diagnostics/__init__.py` | 1 | `359eee834a628e867f9dbf7d34196d211475b093658f7395206e9f37c9f9bd93` |
| `kajovo/core/diagnostics/ssh.py` | 116 | `d041c1d1ea839ac48adac2cd9574f0b676935cbb16be989b4df84826a6b7af3e` |
| `kajovo/core/diagnostics/windows_collect.ps1` | 13 | `33e19c3fe5de45f3001f78f9cbde515ede1848e93cf67d6bd413d2ed61a90605` |
| `kajovo/core/diagnostics/windows.py` | 97 | `3e3bb443183438ee648f5511097ba2a2b34d0a82276cc2d464493ddc1854f04a` |
| `kajovo/core/filescan.py` | 155 | `a135f3fe8d0e7b1cad5328d596579ff8893e9659c9e9aa62710ca995fc01720f` |
| `kajovo/core/generate_batch.py` | 2111 | `25123b3c3113201a3ae5f3b7e00c646ae49653f2ce87cd97cdad6134604bbe71` |
| `kajovo/core/image_runtime.py` | 149 | `f50382b6859422eb0ac9fcbb42b1f3fa684b5db303774bef018ada90a1f04682` |
| `kajovo/core/model_capabilities.py` | 84 | `03206b00f5b12adf120a66d8eb4ef8b406cfa9ff05cf592df30bc0420806da28` |
| `kajovo/core/model_catalog.py` | 101 | `194ca10bc3e6bcbdefc3b9eec92b0ac00969484a26b322dd2199dcacb086376e` |
| `kajovo/core/model_registry.py` | 293 | `71a920d9defb7d638473b627a8a0e2653a233d3406905474c66b9dcd558bf6b7` |
| `kajovo/core/notifications.py` | 52 | `9cfa4023f02ca483a14f9e036efc65afd657e33e06349eae6a21503d631f4bb5` |
| `kajovo/core/openai_client.py` | 571 | `ec292451758126893c0ce8fe98082ae42db99944d7ce9143f41f4d857052cb53` |
| `kajovo/core/openai_model_matrix.json` | 9318 | `9b7e6e4a9354b1443717974f88b63b20682a3c962574f385a91fb44c3d9aedc4` |
| `kajovo/core/openai_transport.py` | 406 | `400e6e8fe2ef5653b83288720701d1ae6d7fdd99c11ab2141aa2edf4fb0d1017` |
| `kajovo/core/orchestration/__init__.py` | 1 | `d5d7c778724efffd6aa05ba415bfab76cdc407cbb492853a0d79b5d0da5f5f4a` |
| `kajovo/core/orchestration/authorization.py` | 162 | `4c9c4607447376223f9a455a87b30948a226f6d5f47d58ba94fb928b53808d95` |
| `kajovo/core/orchestration/batch_manifest.py` | 185 | `a01eeaf7bf74b7bde4c5d0df87e3d34453fabf67e6a40d00ab702f3a6f5febd7` |
| `kajovo/core/orchestration/contracts.py` | 76 | `0d828c253359e26238e26cf5ed91b6ebc74ded7d2d95614bdf92325aaa3434a6` |
| `kajovo/core/orchestration/errors.py` | 10 | `f3c818de1257a2b0d5db3511b8cb3e6cbd885c9bacad90229b493d5e9449961a` |
| `kajovo/core/orchestration/executor.py` | 86 | `8539802b0835b44875c99a09516e715f6641532b9c7a24ea6c7d9941b12c026f` |
| `kajovo/core/orchestration/image_slots.py` | 127 | `9bae1630fdc2ef7771942832083abce7aa6605544997777f552dcc98842877a0` |
| `kajovo/core/orchestration/policies/images.json` | 51 | `cf666915ecc7b609877965be3847ffd84347e50b571ee8c1f88af72a4105c96f` |
| `kajovo/core/orchestration/preparation.py` | 1612 | `e7678fa12b2be18a1de6f0a07877b09ea0b84f348aa9f4a9c656698fd0fc3339` |
| `kajovo/core/orchestration/projection.py` | 149 | `557c34d1a6d050466709280349c516722d7790f0e66d4badaf3e00971a24bd4a` |
| `kajovo/core/orchestration/provider_operations.py` | 227 | `49069236fcc9ff93c6374995148468f1dddd2b9611db5ef3ea554d51f9fd278d` |
| `kajovo/core/orchestration/publish.py` | 584 | `cc045ff6b3066e1fd00e3dae13f3b585d40efcc16115b056847107ab243da92a` |
| `kajovo/core/orchestration/repository.py` | 1043 | `f41b047089c719e9cc1bb6a75f38e41ea360e081f002d68d65376fcdeda521c6` |
| `kajovo/core/orchestration/request_binding.py` | 36 | `b8a2905ece5567143b44d541cb88f03c8f9e73360dd5c0021d89f31dd8eb98c5` |
| `kajovo/core/orchestration/resource_delivery.py` | 903 | `99b2266c76585a97d362feb02c91e89754e0763995d53a979dc38aca1b157913` |
| `kajovo/core/orchestration/run_config.py` | 138 | `43cab0f1e5f53ee6f9b39ea0f4040b93d5eda9b4201264198487fbbc7339878b` |
| `kajovo/core/orchestration/source_pack.py` | 557 | `bff490004e9038b6034bd20eb7f3f844d68da30ea234b64765f702589bac6de7` |
| `kajovo/core/orchestration/verification.py` | 714 | `ae12ddd8aab5db5f21f8cb5f241506d7346d26267d2a0739f0e649080f3ff774` |
| `kajovo/core/orchestration/waves.py` | 99 | `ba7a836c09fd91281a156f43fb3d4c6ab1154da1363a894a586be854eabf7278` |
| `kajovo/core/orchestration/work_order.py` | 330 | `b9d5d1944ffcc6b7cf32aa4e50ff0a6de837b6917d2449d83d99bdcc52c10ea7` |
| `kajovo/core/photo_batch.py` | 1196 | `65f8ea353f4ec7156634a89121cef9c50b231aeb2831a3b9624e89bbf7254326` |
| `kajovo/core/photo_prompt.py` | 272 | `235ae0ab750ffc4f38802599afe975d54dd8aa1d580ee17b2951c612067d8ec6` |
| `kajovo/core/photo_templates.py` | 217 | `41a9215b4a5d083bf0fd7e1d5e21d07d78bd3372f14c71b3c8e834c360489ff2` |
| `kajovo/core/pipeline.py` | 6 | `f33aebf82925ba77c312fd3f923c9bf0e51dade3704bc0862c27e756a264f468` |
| `kajovo/core/progress_display.py` | 168 | `1234c1d30e7cf2d2859304a70d38ee983552641622da9a7ca69c3e1fe72c91b5` |
| `kajovo/core/progress.py` | 112 | `98370004b3e66813b6536c9fa490541ff710cdad18f0b2762067043801b0e16e` |
| `kajovo/core/project_git.py` | 336 | `e6dbc4cbad3f1ebc59cf551c0269d6ed42d72bf824a05c0402fd283e740f7d30` |
| `kajovo/core/recoverable_artifacts.py` | 100 | `f341305884c435ad5dba937527e77f321dd9072ef45ad8e1dd11d0fa230ed31a` |
| `kajovo/core/recovery.py` | 164 | `ab47e741f844e70a1ef964a3864436897db82ccae6b413db418fb9a4b3d9d778` |
| `kajovo/core/repair_execution.py` | 119 | `86ad267d28baf653cf4c282ba5bdc1fdbc5aaf54c155c281e0b81bedefce8484` |
| `kajovo/core/request_rules.py` | 250 | `3ccb76c931d2aecf80b6b37dbe3a5586b52004ac795c2ea0d50132dc3adea93d` |
| `kajovo/core/requirements.py` | 477 | `0c3177f916d3facda269f2302715d065ca34f9a72cb3b4860ede54bd9330d7c7` |
| `kajovo/core/resources.py` | 12 | `6f0f235866b7b0ec245637ca05abfd5ba032393112d366e9b969eb6b9d948907` |
| `kajovo/core/response_journal.py` | 263 | `3890f9cc6cedba4ffbf0f54f5238360f8bff441db106a73255ca398f3e8949b6` |
| `kajovo/core/response_policy.py` | 67 | `817b0aa4bbaa3eef334f7fe73c3ce2780414209e791547f6929bbc815a2983a1` |
| `kajovo/core/retry.py` | 67 | `1bebad932ba20e38310a9ade4570972765cd796346c5f93306dfc749e5adc284` |
| `kajovo/core/run_bundle.py` | 1491 | `8ba34bea1039a11001f71975730227ccf93f3f3dd084e1ecf7ecb321f0c0f028` |
| `kajovo/core/runlog.py` | 854 | `529ece00a16886d1ecfc2c7853be2a425b81b7eab488b189001258a5b388a3ed` |
| `kajovo/core/runs/__init__.py` | 37 | `40afce9c7a0df4d41f0c928e8216255863a5de5965554aa1fca31864c1996892` |
| `kajovo/core/runs/attachments.py` | 592 | `3bac2d303ca5bc4a42afffecd840fdf608a3e5c6a1f135ef5e52540a20dadd7c` |
| `kajovo/core/runs/batch_execution.py` | 268 | `97aba69777d90f3a04f0a268b9c01443bbcbac40484155cbfc670cd6418d4f32` |
| `kajovo/core/runs/cancellation.py` | 24 | `c061fb6ef5494c91653e5077371099f6d2e7ac1cc21c06a4792997a9f0fb490a` |
| `kajovo/core/runs/config.py` | 81 | `4fb7b48adae99437d1985b4378e14933b891f21fd2c61c8c5134d17ecd4dbf5a` |
| `kajovo/core/runs/context.py` | 229 | `5f49fa15400fe38eeffbee3aaedc39ac70dfad330f3e9bd62985cd56872bea4c` |
| `kajovo/core/runs/contracts.py` | 187 | `e2e6a5e9b1caa541c83272ab3f4d70ec1fe7705222309c7a6c231ed1da27d5dc` |
| `kajovo/core/runs/delivery_execution.py` | 148 | `16401aff8d0d5dc3c07ac539b383767fe60187f2291a867a6051fd79a3a2d231` |
| `kajovo/core/runs/delivery.py` | 295 | `3043f695d514efb2ee8162ee1d7f28d32064426d6e850a8c7f6a8e6673e0ad71` |
| `kajovo/core/runs/diagnostics.py` | 211 | `5e36679886b61217454119fb640a8e9d03aa73c962092fdd747d49b82c17fbf2` |
| `kajovo/core/runs/executor.py` | 311 | `aaf169dcff6086f2207eef8e79a088c50bed7e60310fdd6d48a6ced39f0a8861` |
| `kajovo/core/runs/file_execution.py` | 418 | `a38ac36b108799310310a3f8a3005983120fe7af237abe14c6c770b2eebd2ca5` |
| `kajovo/core/runs/generate.py` | 497 | `a75da4ace3ecc7ba5bd65aa8e4e52e3ebf94a562d0668c05f998bb0bce55b9bf` |
| `kajovo/core/runs/locking.py` | 70 | `71fa631b6c8eb344735093ed52eeaeea966cde335657a203a40bdc729d45660e` |
| `kajovo/core/runs/modify.py` | 392 | `e1b6f2b9ab5794fdf3cae8c669cb3711888f925526288f43163334d0203925af` |
| `kajovo/core/runs/observability.py` | 47 | `b0cc4c7c04854c1188128122c47f2500e7c39c8314c77ac10a514b9bd9dff18b` |
| `kajovo/core/runs/polling.py` | 139 | `4888191bf9c9e7e4768bdf7188ceb083c8ccb72c0575c4567029a8153236ca3c` |
| `kajovo/core/runs/ports.py` | 28 | `89bc26a3558aa1465d33c1b552038b291c8184a10bd8427c7b12f558e26218ca` |
| `kajovo/core/runs/qa.py` | 171 | `8674a84f2854bfbf69cd5295e90bac1cc2828033dfe58a349a9397e42c354a5f` |
| `kajovo/core/runs/qfile.py` | 345 | `7590efc82613d332798c439bfe3376f395940bc711963b4f62e61f58745f3615` |
| `kajovo/core/runs/recovery.py` | 176 | `3fb898f9e8a06ef871fb61500ac11946a42841b54c985591b0ea54c8d4d26c5e` |
| `kajovo/core/runs/response_execution.py` | 304 | `a9c2e8782dddf866ced3234892a658cbc83867cce8e00c0ca99b2cbcb884a6c4` |
| `kajovo/core/safe_config.py` | 135 | `21b5b45ab33205cd1988b00b4a86a8c5f59797436eb68c8ce5fca4f8211e7eb8` |
| `kajovo/core/secret_store.py` | 256 | `1635858cbada14d53bf7fce2ba1ccd7ec685e4efdd6f598d9e4062f5affc61c1` |
| `kajovo/core/structured_output.py` | 369 | `951ee05fccbe0e00843d8ac35950dd50fbd2b3eaa74e4023f729162d3a4aecc2` |
| `kajovo/core/user_errors.py` | 169 | `114bce411cce26b9dc1d55120156d322fe88db22cbf120d6a1087350fe9b7c5c` |
| `kajovo/core/utils.py` | 92 | `7a74d977df71239e8cb2009a33004139dde0a588df8d7ed7bb2e3fcd23d101fa` |
| `kajovo/progress_ui.py` | 665 | `6b888a1f1020a22e26c3a1e0100029d9c7eb82d937e0eab470d44a98a4568457` |
| `kajovo/studio/__init__.py` | 1 | `10d8c7e3c3ef9d7d0342afccbf097786db941c375af57f09a654895a605129bc` |
| `kajovo/studio/application.py` | 357 | `b4e409aa58463090ca22f87b2cbc67d28262b0c4f57528995f2b4b8864313fe5` |
| `kajovo/studio/batches.py` | 222 | `33beca8d80217bbef74eb5702de73e25e164002b3d2d495b335c8d1109b5e7ae` |
| `kajovo/studio/cascade_items.py` | 109 | `622c771250a6d99dafde2e52a2b9dbc98a7515df25f13abb769c490f68662fe6` |
| `kajovo/studio/cascades.py` | 373 | `b9e41c64afb77cb3ac5e56e88354f2637dd0ca0713c000fef0b6486719573058` |
| `kajovo/studio/comic_editor.py` | 343 | `e250628f99c1e02a835b9246c2cc647b01e57969d8bc2f4bd88065b671d43779` |
| `kajovo/studio/comics.py` | 976 | `e5d8b0b18a652b9defdd8069f4fa7f2aca61290f6106fcac11c7c6f196a1745a` |
| `kajovo/studio/components.py` | 351 | `30960cb9305458771f8f4a6a3e43a9058dc8476e18709cc4ff2e71dbd58bbd42` |
| `kajovo/studio/context.py` | 113 | `a60cd1a402f9171b2ea7f969d54060041ce75ee684808cfd1e37c887a621d233` |
| `kajovo/studio/converter.py` | 144 | `5ecf3b38f726381cfc2d52c92e199c937abf7210799337c91c51131b5c704370` |
| `kajovo/studio/evidence.py` | 118 | `07e4cf00a9ae3056ab4fd483c80807377f5df69b97387cb512038850c0330753` |
| `kajovo/studio/history_artifacts.py` | 514 | `5fe8a334f5c0af01dc36cfd90e6a2992a9e862b502616e2853eeb64d730b820c` |
| `kajovo/studio/history_cascade.py` | 103 | `9a6afa01338e3270a16656a65c6d95784ab1a1f2a08a9fa60d607795ebef1408` |
| `kajovo/studio/history_composer.py` | 148 | `2575dbfd873e7ae5b9c2ef5078aee364b78656bfecc531e77662ccfbfb6e81cb` |
| `kajovo/studio/history_data.py` | 113 | `13c4bd8c1c0095b9c808ceec353ff22679fd233b813373bda0177cb9185cdb48` |
| `kajovo/studio/history_details.py` | 534 | `fdea11e0fab9d0a6332b42031c4495d8ab34e7cb8b10ec709d4f32fff0414edd` |
| `kajovo/studio/history_launcher.py` | 350 | `87a1b93eb9436848aea62de33aeef364dccbc3a4eb42c060cd1b550511cea784` |
| `kajovo/studio/history_models.py` | 296 | `a8c47bb38a0c2cde18e9a053a34765b92dce1d8950fdb3eca0b23bb5ca83e5e4` |
| `kajovo/studio/history_overview.py` | 124 | `47b3edb19379b8481042569e11917f181bc028874b87eded126b63ac2436f98c` |
| `kajovo/studio/history_policy.py` | 148 | `b7e978e20a53d2b73c91450c618ead2083f5a53d2f8d737d70553be373e78893` |
| `kajovo/studio/history_state.py` | 70 | `0b769229f1cf25fc6f26109d8295aaf1c3743824829b2d00086b210901a23720` |
| `kajovo/studio/history_timeline.py` | 284 | `82eea99b7ed257098fbabf9f997e1de380310cfce96b7bff69d1e5c8d027c8ae` |
| `kajovo/studio/history.py` | 607 | `b87e1ec5a3503d2dd48745a1233322876d0a634a89856e3314f0cd6eddf0523e` |
| `kajovo/studio/operations.py` | 521 | `a8207b922d572b440185be32ee7a917e3334c8ba9915ddd2ced18b9571d8de47` |
| `kajovo/studio/photos.py` | 459 | `9bb04994791814ff5666a30757d2c7ecadceb6cc0a5531b5bdf7454b47a2f93e` |
| `kajovo/studio/resources.py` | 272 | `0e32a072d47702e18b17e17a0d14a4387cdb1084613cb13a08a303cecbe434dd` |
| `kajovo/studio/settings.py` | 197 | `69ed27a064a9acc411eaca5e22ade7c636ccf8d261352a9a03b9b3e9bf75d2e0` |
| `kajovo/studio/ui_audit.py` | 485 | `82a18bab8266b184f1470a56f7cfd8b8b95c58dc25a49d18d858379275854a28` |
| `kajovo/studio/versions.py` | 182 | `9bbdac2efc742102afd45acccb2eccae77086416d7d86e95c54e75bd9bbf56e4` |
| `kajovo/studio/workbench.py` | 495 | `becdd22af1eecbfad5a4a0a990f5b600a0872ffbf1b2da9578f9b460502c12d8` |
| `kajovo/studio/workers/__init__.py` | 5 | `b1f5573829cd73abb2240b39384538ec4617bb2f751574621f671ac59e7e640e` |
| `kajovo/studio/workers/cascade_worker.py` | 60 | `3ea4dae2c7e9560cdcb8c3020096bbe21a8e6312207f09cac8b41a47fdc858a4` |
| `kajovo/studio/workers/run_worker.py` | 73 | `13be9a8ca93762b0e0112a412f52a8df1f963cc55ae98e7f9d43d66daffdaefa` |
| `kajovong/__init__.py` | 4 | `1c3aee02d865f149623b0aedc3b4a17a1b6e45d9e023195e1e222b07c62c7bd4` |
| `kajovong/__main__.py` | 9 | `bf3cfbe46e81f32afe6e82f05aa77257214a54c31a39237a2c28fe3b1a0d0db6` |
| `pyproject.toml` | 71 | `b184eedfb8a40ad909000fd2ddfc9f7d6fc66ab9cfb12bb97aafbfde07bd924d` |
| `requirements-dev.txt` | 1 | `9d6d2c2e18451cd8eb86a739cbcb1664e4342ef4ae478266a3c333dd4f73674a` |
| `requirements.txt` | 1 | `0cac0e472eba3359aa79e9674ff11a5fd0484b9c465fc1cb774c30938edcc5d7` |
| `requirements/constraints.txt` | 86 | `cf9ee2fda19c36ec41ec8802ff9eca34834ee08e1da65e4077f0de438f45d8a4` |
| `resources/orchestration/contracts/local/BATCH_MANIFEST_V4.schema.json` | 148 | `08a6c9173270e4b4bae4cd2044ebf86a4c8b98750b58021d0f652a85ab1a0bec` |
| `resources/orchestration/contracts/local/RUN_CONFIG_V2.schema.json` | 28 | `b5e5c992d273956c2e5e49df4ec4cfc671d86dd6aa7945935ae3263178fc15e4` |
| `resources/orchestration/contracts/local/VERIFICATION_REPORT_V3.schema.json` | 192 | `9b67f41cc4f76e01e971d3d72276e96f3bb33a64819ad9ba20d6afd230500bb6` |
| `resources/orchestration/contracts/local/WORK_ORDER_V2.schema.json` | 123 | `ffb735667173df57186929874217d0110798c369bf08668a767e70d7a00f5fe1` |
| `resources/orchestration/contracts/local/WORK_ORDER_V3.schema.json` | 130 | `456af3c08f75fb1f7713e3f430136511e0d59f35a46250525a5acb47643ddfd0` |
| `resources/orchestration/contracts/wire/FILE_CONTENT_V1.schema.json` | 8 | `f539c75af081036ba4ba54fb044b1ae714596ca8348e1879e8b96ef98bb64edc` |
| `scripts/analyze_id92_context.py` | 164 | `4598fb13f898cf7c2a5893c4aa4f16e6f3f332af63c48e14c7c0cad9d01760ac` |
| `scripts/audit_studio.py` | 57 | `83a244cfeedf8b5269daac4ec69a13d1f1979664b7477d4bd5a076cd6b34bda3` |
| `scripts/audit_ui.py` | 42 | `8053c276c72c5cdb53810517cb55347d37015c26e5625bb0c0a9f7181f4bd719` |
| `scripts/benchmark_history.py` | 54 | `f4ea9b9a790c8102f4fd1fdf096f988437e6879ee8209293b54b7d71c07097f5` |
| `scripts/bootstrap_windows.ps1` | 60 | `12fb2a2902a847d885322f2c2dd3c0c6be1a8066e67a22f5e5f9eb45e8441711` |
| `scripts/build_ui_gallery.py` | 47 | `2dc975c64d7231c6f59b0473d13cf2bd81b505a4947e960769f3e6e044943dc9` |
| `scripts/export_model_matrix.py` | 68 | `2fdc8f1086f4dd0218ccf3edfc91a0bd7ca2429984f3f88454b28bf42a31def4` |
| `scripts/export_ui_validation.py` | 42 | `8a6fd3a5d34718469e61b919f0e8ef68d13c6eadc404970d3aa649f179b12514` |
| `scripts/install.bat` | 4 | `520cce72d552af9cb50b0ba40bf7401d41c561c43b48c1780d4934e78b6b5013` |
| `scripts/install.ps1` | 2 | `3d15292360ff68362d899dad71d4df1c33298dbb41e8f0029c94f9bd37847044` |
| `scripts/measure_request_context.py` | 33 | `fd42cce33d414a95222a5aef8932417d24aa0f07f86e493862dac5078ec8112d` |
| `scripts/render_studio.py` | 490 | `b7192e6bf08c735276cfef833703d13e2eb656276994ff0d34c0e25821c61cd7` |
| `scripts/render_ui.py` | 16 | `f8eab889129bedf14f41acd44ce92341ba93ad5351768c871fc477faec9ceac5` |
| `scripts/run.bat` | 10 | `e333526e2b35c45f7c995e5cc125a1b55442fed9187b3b4ea9ca3e727a809655` |
| `scripts/run.ps1` | 5 | `5da73cf21ef81702f670e7976b57f34eff4cbb6ffc871da77dc6600625423b9d` |
| `scripts/start_app.py` | 71 | `1a79f204b7cf0d2fe3d520d1521ef2f701de6ab44748d55154c0312a0b4050f4` |
| `scripts/start.ps1` | 51 | `d14baa824c219e17c68ae1e17883e65bcefd8fdbc1c921adde7b8badd0dd64e3` |
| `signace/signace.svg` | 29 | `f13d8a83501d80079b3a0aa800927c79fc797a9c5c62316efb86858226ad6bb7` |
| `start.bat` | 12 | `5c30abb0e147b171687d18ee3631d1ad4ada7556111e414833b93139caf892f6` |
| `tools/_finalize_lfs_checkout.py` | 55 | `50ed1c2f155a33c6448f1799d61838476e767178fe0ce70a51aa9ee1bc540110` |
| `tools/_finalize_nine_hardening.py` | 326 | `cf3a14a69982727ac28fe6661b1c2db49b504a9e76a770255314fe9e6065a9fa` |
| `tools/_finalize_nine_postfix.py` | 33 | `822da1f3ef6732dcb23ee589efe92f8994705bc5742c067d6bb016330c0386d7` |
| `tools/_fix_cascade_type_shadow.py` | 32 | `f36d17f2da6d5e31d13d80cd68f8cf9294853bb0e2069be252634b8a077201df` |
| `tools/_r05c_finalize_studio.py` | 637 | `7c52d20be3fa681d13122b889bf3e1e6d5e3f872eace83309b556099f1c5498d` |
| `tools/_r05c_postfix.py` | 52 | `b3b45e576fcd15831a1e75de2294a66a6551eb99ab1de259d91dc85dd1ccd849` |
| `tools/verify_contract_links.py` | 303 | `a1e8ef690a28096f5dd0ae9dfe1764098c99ccbfd0589e97e25b9e007ed53a56` |
| `tools/verify_dependency_contract.py` | 92 | `14b7e07a7a9a5971d412f50b8eec9cdd6bf8a96a28b45b651f0e2a5a2b9d2c5e` |
| `tools/write_build_metadata.py` | 63 | `e3da52c60d83a827b8015b37bf2231e69c9c527921d451e9db2beefbf4f00ea4` |
| `utf8nobom/__init__.py` | 5 | `ba81e66f4e75c06cd7c53022498e8bd0d63734b23ec25daa614ba3231a498159` |
| `utf8nobom/app.py` | 537 | `5f83aa627bcdac0fb4bcd535c5fd888deee25c30c7943615b823099886dfe838` |
| `utf8nobom/py.py` | 7 | `99c50dec10f71336df0800b215bc3662775dee840e5cdc4a421b1cb992789fe2` |

Celkem: **192 souborů / 56 380 řádků**. Výsledky testů nejsou součástí této zprávy a žádný produkční soubor nebyl tímto auditem opraven.
