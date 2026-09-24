# Forenzní audit zdrojového kódu

## Výsledek a identita auditovaného stavu

Nový nezávislý statický audit nalezl **43 konkrétních odchylek**: **12 P1, 24 P2 a 7 P3**. Nálezy jsou níže uvedeny všechny, nejen nejzávažnější. Nebyla provedena žádná oprava implementace.

Auditovaným objektem je pracovní strom `D:\kajovong`, nikoli čistý commit. Referenční HEAD: `6a277dd444d942288c9c8607310a0a04413971e1`. Strom obsahoval již při zahájení rozsáhlé uživatelské změny i nové zdrojové soubory. Rozhodující identitou auditu jsou proto SHA-256 jednotlivých skutečně čtených souborů v příloze, ne samotný HEAD. Závěrečné porovnání všech otisků s inventářem při zahájení nezjistilo změnu žádného z těchto souborů.

Přečteno bylo **204 textových zdrojových a konfiguračních souborů, celkem 58 529 fyzických řádků**, každý v úplném rozsahu. Číslo zahrnuje také prázdné řádky, komentáře, vložené řetězce a 9 318 řádků modelové matice; není to počet příkazů ani tvrzení o 58 529 řádcích spustitelného kódu. Prázdné moduly jsou v inventáři uvedeny s nulou řádků.

## Metoda, důkaz a omezení

Jediným věcným podkladem závěrů byl současný zdrojový kód a strojově používané konfigurace/masky. Komentáře, dokumentační tvrzení, SSOT, testy, výsledky testů, starší audity ani zprávy nebyly použity jako důkaz správnosti nebo chybnosti. V pomocných migračních skriptech se vyskytují řetězce generující testy/dokumentaci; jejich tvrzení se nepovažovala za důkaz. Auditní pomocné programy byly čteny jako programy, nikoli spuštěny jako zdroj autoritativních výsledků.

Průchod zahrnoval vstupní body, core, orchestrace, desktopové Studio, převodník, build/spouštěče, workflow, modelovou matici, JSON masky a pomocné skripty. Po souvislém čtení následovalo dohledání volajících a kontrola vazeb producent–konzument, identity požadavku, LIVE/BATCH, obnovy, persistence a zobrazení. Za důkaz slouží konkrétní sled příkazů a dosažitelná podmínka, ne výsledek regexového hledání samotný.

Vyloučeny byly testové soubory, dokumentace a staré zprávy, provozní data, databáze, přihlašovací údaje, závislosti a virtuální prostředí, binární média a generované distribuční stromy `Build/lib`, `Build/Kajovo`, `Build/bdist.*`, `dist`. Binární ZIPy ani starý `audit.md` nebyly zdrojem závěrů.

Aplikace, testy, lint, migrační nástroje ani sestavení nebyly spuštěny. Nebylo voláno OpenAI API ani jiná placená služba. Nebyla externě ověřována aktuální dostupnost modelů nebo skutečná odpověď provideru. U hraničních JSON nálezů se proto uvádí, jaký chybný vstup kód přijme, nikoli že jej provider nutně vrací. Statický audit není důkazem bezchybnosti, reprodukcí všech závodů ani garancí funkčnosti generovaných produktů.

P1 označuje závažné riziko ztráty/přepsání dat, chybné identity či placeného opakování nebo blokaci významné cesty. P2 označuje funkční či kontraktní chybu v konkrétní situaci. P3 označuje kosmetickou, evidenční nebo latentní pomocnou chybu s omezeným dopadem. Závažnost vždy platí za podmínek popsaných u nálezu, ne pro každé spuštění.

## Souhrnný registr

| ID | Priorita | Nález |
|---|---|---|
| A2-001 | P1 | Distribuce neobsahuje schéma potřebné pro ruční podklady |
| A2-002 | P2 | DETAIL připravuje vazby na povinnosti bez předání jejich definic |
| A2-003 | P2 | Prázdné zdrojové soubory mizí z inventáře MODIFY |
| A2-004 | P2 | Obrazový resource payload přepisuje rozměry a průhlednost zadání pevnými hodnotami |
| A2-005 | P3 | Validátor připouští vynucený nástroj, který není připojen |
| A2-006 | P1 | Další BATCH vlna přijímá změněné archivované originály jako nový základ |
| A2-007 | P1 | Starý BATCH manifest může znovu uznat překonanou závislost |
| A2-008 | P2 | Nevalidovaná usage metadata mohou zablokovat import celé dávky |
| A2-009 | P1 | Monitor BATCH zapisuje celý stav bez zámku běhu |
| A2-010 | P1 | Publikace nemá společný zámek se změnami téhož běhu |
| A2-011 | P1 | Rollback nového souboru může přes symlink smazat jiný soubor uvnitř OUT |
| A2-012 | P1 | Identita runtime kaskády je pouze její sanitizovaný název |
| A2-013 | P2 | Legacy lokální vstup závislý na předchozím kroku selže ještě před jeho provedením |
| A2-014 | P2 | Přemístění stagingu ruší identitu opakovaně použitelné legacy odpovědi |
| A2-015 | P1 | Tvrdý pád kaskády mezi primární odpovědí a dokončením kroku ztrácí její cache pro běžné pokračování |
| A2-016 | P2 | Chybný runtime JSON kaskády uniká standardnímu zpracování chyby |
| A2-017 | P2 | Správce operací ignoruje samostatně emitovaný finished_err |
| A2-018 | P1 | COMIC nastavuje neurčitý submit před dokončením lokální přípravy |
| A2-019 | P2 | PHOTO/COMIC přijímají neúplnou identitu vzdálené dávky |
| A2-020 | P2 | Bible komiksu zůstává platná po změně svého obsahového vstupu |
| A2-021 | P2 | Automatická sazba storyboardu ignoruje uložený druh bublin |
| A2-022 | P2 | Neúspěšné duplikování panelu zanechá nově vytvořený prázdný panel |
| A2-023 | P1 | Chybějící WorkOrder soubor tiše vypne centrální vazby moderního PHOTO jobu |
| A2-024 | P2 | PHOTO neváže skutečné rozměry na explicitní size požadavku |
| A2-025 | P1 | Dva PHOTO importy mohou přepsat shodně pojmenovaný výstup |
| A2-026 | P2 | Pořadová čísla událostí nejsou jednotná mezi instancemi RunBundle |
| A2-027 | P2 | Transportní GET vymaže model a reasoning původního kroku |
| A2-028 | P2 | Klon běhu používá redigovanou UI projekci místo původního zadání |
| A2-029 | P1 | Pokračování historie může vytvořit nový request vedle známé rozpracované LIVE odpovědi |
| A2-030 | P2 | Asynchronní výsledek přepisuje mezitím změněnou konfiguraci workbench |
| A2-031 | P2 | Odložený profesionální návrh PHOTO nemá cestu k použití |
| A2-032 | P2 | Uložené nastavení dry_run_modify nemá účinek |
| A2-033 | P2 | Částečné smazání vzdálených prostředků zanechá neplatné přílohy v kontextu |
| A2-034 | P3 | Historie QA nevykresluje odpověď z aktuálního JSON kontraktu |
| A2-035 | P3 | Dokončený průběh nadále ukazuje aktivní a čekající mikrokroky |
| A2-036 | P3 | Přesun komiksové bubliny mění její bounds bez oznámení scéně |
| A2-037 | P2 | Inventura vazeb schémat chybně označuje skutečně používanou masku jako nenavázanou |
| A2-038 | P2 | Git filtr nepozná databáze s příponou sqlite3 |
| A2-039 | P3 | Kontrola Git indexu může číst index jiného repozitáře |
| A2-040 | P2 | Přepsání ZIPu převodníkem ztrácí oprávnění vnějšího souboru |
| A2-041 | P2 | Export artefaktu může při chybě zničit dřívější cílový soubor |
| A2-042 | P3 | Metoda invalidated neumí porovnat V3 graf s obsahovou závislostí |
| A2-043 | P3 | Index historie může přehlédnout změnu obsahu existující evidence |

## Důkazní popisy nálezů

### A2-001 · P1 · Distribuce neobsahuje schéma potřebné pro ruční podklady

Zdroj: [pyproject.toml:45–51](pyproject.toml#L45); [Build/build_windows.ps1:22–35](Build/build_windows.ps1#L22); [Build/build_macos.sh:31–44](Build/build_macos.sh#L31); [kajovo/core/resources.py:6–12](kajovo/core/resources.py#L6); [kajovo/core/orchestration/manual_resources.py:14–16](kajovo/core/orchestration/manual_resources.py#L14); [kajovo/core/orchestration/resource_delivery.py:736](kajovo/core/orchestration/resource_delivery.py#L736).

Setuptools balí pouze vyjmenované prostředky; PyInstaller přidává obrázky a fonty, nikoli resources/orchestration. Modul manual_resources přitom při importu bez fallbacku čte MANUAL_RESOURCE_BINDINGS_V1.schema.json z této složky. V čisté instalaci/distribuci bez zdrojového stromu selže import FileNotFoundError. Tím jsou blokovány ruční podklady a pokračování, které tento modul potřebuje. Nejde o doložený pád každého startu aplikace.

### A2-002 · P2 · DETAIL připravuje vazby na povinnosti bez předání jejich definic

Zdroj: [kajovo/core/orchestration/preparation.py:1499–1544](kajovo/core/orchestration/preparation.py#L1499); [kajovo/core/orchestration/preparation.py:656–663](kajovo/core/orchestration/preparation.py#L656); [kajovo/core/orchestration/preparation.py:872–884](kajovo/core/orchestration/preparation.py#L872); [kajovo/core/context_compiler.py:273–284](kajovo/core/context_compiler.py#L273).

A2_DETAIL/B2_DETAIL dostávají target, vybrané požadavky, acceptance a interfaces, nikoli mapu obligation_owners a kanonické definice invariants/flows/lifecycles. Současně se po jejich facets.obligation_ids požadují přesná ID těchto povinností. Požadavek nemá previous_response_id, takže chybějící struktury nedoplní historie. Prázdné facets/required_facets projdou touto kontrolou. A3 sice definice později znovu dostává, ale detailový kontrakt mezikroku vzniká bez podkladů pro přesné přiřazení; nelze tvrdit, že povinnosti zmizí ze všech dalších kroků.

### A2-003 · P2 · Prázdné zdrojové soubory mizí z inventáře MODIFY

Zdroj: [kajovo/core/filescan.py:99–101](kajovo/core/filescan.py#L99); [kajovo/core/orchestration/source_pack.py:354–363](kajovo/core/orchestration/source_pack.py#L354); [kajovo/core/orchestration/preparation.py:688–718](kajovo/core/orchestration/preparation.py#L688); [kajovo/core/orchestration/preparation.py:1527–1532](kajovo/core/orchestration/preparation.py#L1527).

Každý nulabajtový soubor je neuploadable a SOURCE_PACK jej vyřadí před archivací. Inventář a original_map následně vznikají pouze z archivovaných in_project_file. Existující prázdný __init__.py, marker či konfigurace tak nejsou součástí skutečného projektového inventáře. Navazující rozlišení add/modify/preserve pracuje s neúplnou existencí souborů; obsahově prázdný soubor přitom může mít funkční význam.

### A2-004 · P2 · Obrazový resource payload přepisuje rozměry a průhlednost zadání pevnými hodnotami

Zdroj: [kajovo/core/orchestration/resource_delivery.py:286–318](kajovo/core/orchestration/resource_delivery.py#L286); [kajovo/core/orchestration/resource_delivery.py:395–414](kajovo/core/orchestration/resource_delivery.py#L395).

Požadavky se promítnou pouze do textu promptu. Každý image_workflow však odešle size=1024x1024 a background=opaque bez převodu explicitních obrazových parametrů z výrobního zadání. Například požadavek na transparentní PNG nebo obdélníkový asset je v rozporu s fyzickým payloadem. Jde o chybu sestavení požadavku před odesláním, nikoli návrh dodatečného testování obrazu.

### A2-005 · P3 · Validátor připouští vynucený nástroj, který není připojen

Zdroj: [kajovo/core/request_rules.py:65–89](kajovo/core/request_rules.py#L65).

Kontrola tool_choice ověřuje povolenou hodnotu a neprázdnost tools, ne přítomnost konkrétního vynuceného typu. Kombinace tools=[file_search] a tool_choice={type:code_interpreter} proto tuto vazební kontrolu projde. Jde o neúplný kontrakt veřejného validátoru; při průchodu nebyl nalezen běžný interní sestavovač, který tuto konkrétní chybnou kombinaci sám vytváří.

### A2-006 · P1 · Další BATCH vlna přijímá změněné archivované originály jako nový základ

Zdroj: [kajovo/core/generate_batch.py:1163–1203](kajovo/core/generate_batch.py#L1163); [kajovo/core/generate_batch.py:210–221](kajovo/core/generate_batch.py#L210); [kajovo/core/generate_batch.py:2032–2049](kajovo/core/generate_batch.py#L2032).

Automatické pokračování čte in_project_file z bundle bez porovnání s artifact.sha256 nebo s original_hashes původního manifestu. Build_manifest z těchto bajtů vytvoří nové hashe. Změní-li se archiv mezi vlnami, navazující MODIFY request používá jiný originál, aniž se změna zachytí na této hranici. Ruční retry naopak původní hashe kontroluje; LIVE/ruční a automatická BATCH cesta nejsou v tomto bodě ekvivalentní.

### A2-007 · P1 · Starý BATCH manifest může znovu uznat překonanou závislost

Zdroj: [kajovo/core/generate_batch.py:892–940](kajovo/core/generate_batch.py#L892); [kajovo/core/generate_batch.py:1519–1530](kajovo/core/generate_batch.py#L1519); [kajovo/core/generate_batch.py:1669–1706](kajovo/core/generate_batch.py#L1669); [kajovo/core/generate_batch.py:1727–1760](kajovo/core/generate_batch.py#L1727).

_v3_verified_artifacts začne kopií verified_dependency_artifacts uložených v manifestu. Nejnovější číslo pokusu ověřuje pouze u aktuálních staged_files. Jestliže novější neúspěšný pokus odstranil starý staging producenta, producent už není v tomto seznamu a jeho stará manifestová kopie se neodstraní. Při importu starší konzumentské vlny tak může znovu vstoupit do ready-dependencies i do výpočtu chybějících výstupů. Manifestová kopie navíc nenese attempt_no pro vlastní kontrolu čerstvosti.

### A2-008 · P2 · Nevalidovaná usage metadata mohou zablokovat import celé dávky

Zdroj: [kajovo/core/generate_batch.py:1808–1841](kajovo/core/generate_batch.py#L1808).

Před řádkovým vyhodnocením se nad přijatými řádky volá .get a sčítají input_tokens/output_tokens. Například usage={input_tokens:null} způsobí TypeError a usage jako seznam AttributeError. Tato smyčka není izolována po položkách. Jeden vadný doprovodný údaj proto zabrání předání ostatních výsledků do _process_saved_batch_v3 a nevytvoří běžný výsledek chyby dané položky.

### A2-009 · P1 · Monitor BATCH zapisuje celý stav bez zámku běhu

Zdroj: [kajovo/core/batch_completion.py:113–135](kajovo/core/batch_completion.py#L113); [kajovo/core/batch_completion.py:183–247](kajovo/core/batch_completion.py#L183); [kajovo/studio/batches.py:99–112](kajovo/studio/batches.py#L99); [kajovo/core/generate_batch.py:1776–1784](kajovo/core/generate_batch.py#L1776).

remember_remote_batch_state a recovery provádějí read–modify–write run_state.json mimo ExecutionLock. Obnovení stránky dávek tuto cestu skutečně volá, zatímco import používá locked_run_operation. Monitor může načíst starý stav, import uloží nové staging/pokusy a monitor jej následně přepíše svou starou kopií doplněnou pouze o batch_records. Atomický rename chrání celistvost JSON, nikoli ztrátu souběžné aktualizace.

### A2-010 · P1 · Publikace nemá společný zámek se změnami téhož běhu

Zdroj: [kajovo/core/orchestration/publish.py:544–583](kajovo/core/orchestration/publish.py#L544); [kajovo/core/orchestration/publish.py:506–519](kajovo/core/orchestration/publish.py#L506); [kajovo/studio/history.py:435–460](kajovo/studio/history.py#L435); [kajovo/core/generate_batch.py:1776–1784](kajovo/core/generate_batch.py#L1776).

publish_staged_run načte staging a stav mimo execution.lock a po publikaci uloží celou dříve načtenou kopii. Zámek cílového OUT chrání commit souborů, nikoli stav a nové pokusy téhož běhu. Ve druhém procesu proto může souběžný import/retry změnit aktuální staging nebo stav, zatímco publikace použije starou sadu a následně přepíše novější evidenci. UI rezervace platí pouze uvnitř procesu.

### A2-011 · P1 · Rollback nového souboru může přes symlink smazat jiný soubor uvnitř OUT

Zdroj: [kajovo/core/orchestration/publish.py:306–333](kajovo/core/orchestration/publish.py#L306); [kajovo/core/orchestration/publish.py:361–400](kajovo/core/orchestration/publish.py#L361); [kajovo/core/orchestration/publish.py:102–131](kajovo/core/orchestration/publish.py#L102).

Při obnově smíšené nedokončené publikace se cesty nejprve resolveují. V rollback větvi bez backup_ref se pak volá unlink nad vyřešenou cestou bez _assert_no_link_boundary; tato kontrola je pouze ve větvi se zálohou. Pokud se původně nový cíl mezitím nahradí odkazem na jiný soubor uvnitř OUT se stejným new_hash, hashová kontrola projde a odstraní se cíl odkazu. Podmínkou je nedokončená smíšená publikace a taková změna odkazu; nejde o únik mimo OUT.

### A2-012 · P1 · Identita runtime kaskády je pouze její sanitizovaný název

Zdroj: [kajovo/core/cascade_pipeline.py:445–471](kajovo/core/cascade_pipeline.py#L445); [kajovo/core/cascade_pipeline.py:918–928](kajovo/core/cascade_pipeline.py#L918); [kajovo/core/cascade_pipeline.py:1403–1409](kajovo/core/cascade_pipeline.py#L1403); [kajovo/studio/cascades.py:379–386](kajovo/studio/cascades.py#L379).

Runtime/cache se sdílí podle názvu v jedné složce cascades/.runtime bez projektu, cílového OUT nebo stabilní identity definice. Stejný název v jiném projektu, případně různé názvy se stejným sanitizovaným výsledkem, sdílí stav i blokaci submission_unknown. UI dovoluje nezávislé běhy s různými OUT. Při pokračování lze načíst cache a zdrojový run jiného projektu; souběžné zápisy také nemají vlastní zámek této sdílené identity.

### A2-013 · P2 · Legacy lokální vstup závislý na předchozím kroku selže ještě před jeho provedením

Zdroj: [kajovo/core/cascade_contract.py:301–315](kajovo/core/cascade_contract.py#L301); [kajovo/core/cascade_pipeline.py:387–396](kajovo/core/cascade_pipeline.py#L387); [kajovo/core/cascade_pipeline.py:473–499](kajovo/core/cascade_pipeline.py#L473); [kajovo/core/cascade_pipeline.py:1200–1201](kajovo/core/cascade_pipeline.py#L1200).

Legacy files_local_paths mohou obsahovat podporovaný placeholder například {{step.1.out_file_path:result.txt}}. Archivace všech vstupů však před spuštěním kroků vyhodnocuje tyto hodnoty s prázdným context. Resolver proto vyvolá chybějící odkaz; pozdější upload navíc očekává již zmrazený vstup. Typované source=output tímto nálezem dotčeny nejsou.

### A2-014 · P2 · Přemístění stagingu ruší identitu opakovaně použitelné legacy odpovědi

Zdroj: [kajovo/core/cascade_pipeline.py:964–984](kajovo/core/cascade_pipeline.py#L964); [kajovo/core/cascade_pipeline.py:1642–1655](kajovo/core/cascade_pipeline.py#L1642).

Obnova kaskády správně přesune archivované soubory do nového bundle a přepíše absolutní cesty v legacy_context. primary_key ale zahrnuje celý tento kontext včetně fyzických cest; na rozdíl od typovaných file hodnot jej nenormalizuje obsahovým hashem. Při pokračování legacy kroku po selhání navazující delivery proto dříve uložená primární odpověď nemusí být nalezena a provede se nový placený request, přestože obsah zdrojů zůstal stejný.

### A2-015 · P1 · Tvrdý pád kaskády mezi primární odpovědí a dokončením kroku ztrácí její cache pro běžné pokračování

Zdroj: [kajovo/core/cascade_pipeline.py:918–926](kajovo/core/cascade_pipeline.py#L918); [kajovo/core/cascade_pipeline.py:1574–1587](kajovo/core/cascade_pipeline.py#L1574); [kajovo/core/cascade_pipeline.py:1848–1855](kajovo/core/cascade_pipeline.py#L1848); [kajovo/core/cascade_pipeline.py:1943–1950](kajovo/core/cascade_pipeline.py#L1943); [kajovo/studio/cascades.py:379](kajovo/studio/cascades.py#L379).

Primární odpověď se po přijetí zapíše do run_state, ale sdílená runtime cache se aktualizuje až po dokončení delivery kroku. Tvrdé ukončení procesu mezi těmito body neprojde except cestou. Nové spuštění od vybraného kroku bez explicitního resume_snapshot čte starou sdílenou cache, nikoli novější cascade_runtime v původním run_state. Nový run pak odpověď znovu generuje. Nález se týká této UI cesty pokračování, ne každé obnovy přes explicitní checkpoint.

### A2-016 · P2 · Chybný runtime JSON kaskády uniká standardnímu zpracování chyby

Zdroj: [kajovo/core/cascade_pipeline.py:454–461](kajovo/core/cascade_pipeline.py#L454); [kajovo/core/cascade_pipeline.py:1419–1433](kajovo/core/cascade_pipeline.py#L1419); [kajovo/studio/workers/cascade_worker.py:59–60](kajovo/studio/workers/cascade_worker.py#L59).

_read_runtime_state běží před try v execute a wrapper QThread nemá vlastní zachycení. Poškozený či nekanonický JSON proto ukončí worker bez failure_detail, finished_err a standardní terminální události. V kombinaci s výchozím completed ve správci operací může UI ukázat úspěch místo původní příčiny.

### A2-017 · P2 · Správce operací ignoruje samostatně emitovaný finished_err

Zdroj: [kajovo/studio/operations.py:412–426](kajovo/studio/operations.py#L412); [kajovo/studio/operations.py:445–466](kajovo/studio/operations.py#L445); [kajovo/core/runs/executor.py:68–72](kajovo/core/runs/executor.py#L68).

U workeru s failure_detail se připojí pouze tento signál, nikoli finished_err. RunExecutor při neúspěšném získání execution.lock emituje pouze finished_err a vrátí se. Správce nemá error ani výsledek a nastaví completed. Další callback může teprve vyvolat sekundární chybu, ale skutečný důvod 'běh používá jiná instance' se nepřenese.

### A2-018 · P1 · COMIC nastavuje neurčitý submit před dokončením lokální přípravy

Zdroj: [kajovo/core/comic_service.py:1062–1096](kajovo/core/comic_service.py#L1062); [kajovo/core/comic_service.py:1372–1414](kajovo/core/comic_service.py#L1372); [kajovo/core/comic_service.py:1325–1340](kajovo/core/comic_service.py#L1325).

image_submitting=True a stav batch=submitting se trvale uloží před _prepare_image_effect/bind_physical_request/mark_submission_started. Výjimka v této lokální části nastane před try obalujícím POST. Při dalším spuštění je však operace považována za neurčitě odeslanou a hledá se vzdálený efekt, který nikdy nevznikl. Stejná chyba pořadí postihuje LIVE referenci i obrazový BATCH; bezpečné pokračování je zablokované bez skutečného odeslání.

### A2-019 · P2 · PHOTO/COMIC přijímají neúplnou identitu vzdálené dávky

Zdroj: [kajovo/core/comic_service.py:1502–1512](kajovo/core/comic_service.py#L1502); [kajovo/core/photo_batch.py:854–873](kajovo/core/photo_batch.py#L854); [kajovo/core/batch_submit.py:11](kajovo/core/batch_submit.py#L11).

Kontroly input_file_id a endpoint proběhnou jen tehdy, pokud je provider vrátí neprázdné. PHOTO navíc toleruje chybějící id i status doplněním z lokálního jobu. Terminální záznam bez těchto vazeb tak může aktualizovat stav a nabídnout výsledkové soubory bez úplného potvrzení identity. Responses BATCH používá odlišný přísnější validate_batch_identity. Nález popisuje přijatelný chybný tvar, nikoli tvrzení, že jej současný provider běžně vrací.

### A2-020 · P2 · Bible komiksu zůstává platná po změně svého obsahového vstupu

Zdroj: [kajovo/core/comic_service.py:563–566](kajovo/core/comic_service.py#L563); [kajovo/core/comic_service.py:602–625](kajovo/core/comic_service.py#L602); [kajovo/core/comic_store.py:305–313](kajovo/core/comic_store.py#L305).

Bible se generuje i z description. Při následném použití se však porovnávají jen style a asset reference, zatímco update_project mění description a ponechává bible_id. Po změně popisu vznikne story request s novým popisem a současně starou závaznou bibli odvozenou ze starého popisu. Chybí rozlišení, zda obsahová změna invaliduje bibli; současný freshness kontrakt tuto změnu vůbec nezohledňuje.

### A2-021 · P2 · Automatická sazba storyboardu ignoruje uložený druh bublin

Zdroj: [kajovo/studio/comics.py:187](kajovo/studio/comics.py#L187); [kajovo/studio/comics.py:418](kajovo/studio/comics.py#L418); [kajovo/core/comic_service.py:839–842](kajovo/core/comic_service.py#L839); [kajovo/comic_layout.py:46–59](kajovo/comic_layout.py#L46).

Projekt dovoluje dialogovou, myšlenkovou a narativní podobu bublin. Ruční editor tuto volbu mapuje na dialog/thought/caption, automatická sazba však nedostane styl a všem dialogue bezpodmínečně nastaví kind=dialog. Uložená explicitní volba je proto dodržena jen u ručního přidávání, nikoli u automaticky vzniklých overlayů.

### A2-022 · P2 · Neúspěšné duplikování panelu zanechá nově vytvořený prázdný panel

Zdroj: [kajovo/core/comic_store.py:590–595](kajovo/core/comic_store.py#L590); [kajovo/core/comic_store.py:623–629](kajovo/core/comic_store.py#L623).

panel_action nejprve samostatně vytvoří a commitne nový panel, až potom volá save_panel s obsahem originálu. Selhání validace nebo zápisu druhého kroku první transakci nevrátí. Konkrétní cesta: existující panel se SFX, změna stylu na zákaz SFX, duplikování bez editace originálu. Validace overlayů selže, ale prázdná kopie už zůstane v knihovně.

### A2-023 · P1 · Chybějící WorkOrder soubor tiše vypne centrální vazby moderního PHOTO jobu

Zdroj: [kajovo/core/photo_batch.py:647–658](kajovo/core/photo_batch.py#L647); [kajovo/core/photo_batch.py:917–946](kajovo/core/photo_batch.py#L917).

Ověření provider-operation se řídí existencí work_order_v2.json. Pokud soubor u moderního jobu zmizí nebo nebyl dokončen zápis, _verify_photo_operation_binding vrátí bez kontroly a refresh podmíněné bloky úplně přeskočí. Nejde o explicitně označenou migraci legacy jobu: tentýž fallback se použije i pro schema_version >=2. Chybějící centrální kontrakt tedy není blokující závada, ale deaktivuje jeho vlastní kontrolu.

### A2-024 · P2 · PHOTO neváže skutečné rozměry na explicitní size požadavku

Zdroj: [kajovo/core/photo_batch.py:137–148](kajovo/core/photo_batch.py#L137); [kajovo/core/photo_batch.py:1107–1146](kajovo/core/photo_batch.py#L1107).

Job má konkrétní size, ale inspect_photo_bytes dostane jen formát a model. Rozměry dekódovaného obrazu se pouze zapíší do output_width/output_height a výsledek dostane technical_validation=passed. Například validní PNG jiné podporované velikosti proto projde bez porovnání s požadovaným rozměrem. Je to mezera existující vazby request→result, ne požadavek zavádět nový testovací proces.

### A2-025 · P1 · Dva PHOTO importy mohou přepsat shodně pojmenovaný výstup

Zdroj: [kajovo/core/photo_batch.py:951–959](kajovo/core/photo_batch.py#L951); [kajovo/core/photo_batch.py:1010–1034](kajovo/core/photo_batch.py#L1010); [kajovo/core/photo_batch.py:1131–1137](kajovo/core/photo_batch.py#L1131); [kajovo/studio/operations.py:401–402](kajovo/studio/operations.py#L401).

Volný název se vybere pomocí exists a následný zápis provede os.replace. Bez sdíleného procesového zámku mohou dva importy ze dvou instancí vybrat stejný <stem>_edited.png, oba projít posledním exists a druhý přepsat první. UI rezervace OUT je pouze paměťová. Oba joby pak mohou evidovat tutéž cestu s různými hashi.

### A2-026 · P2 · Pořadová čísla událostí nejsou jednotná mezi instancemi RunBundle

Zdroj: [kajovo/core/run_bundle.py:515–525](kajovo/core/run_bundle.py#L515); [kajovo/core/orchestration/publish.py:522–541](kajovo/core/orchestration/publish.py#L522); [kajovo/core/runs/executor.py:80–90](kajovo/core/runs/executor.py#L80); [kajovo/core/runlog.py:532–550](kajovo/core/runlog.py#L532).

Čítač sequence se načte jednou a dál žije v konkrétní instanci. Publikace vytváří další RunBundle nad stejným adresářem; při obnově se pak používá i původní logger. Nová instance zapíše sequence N+1 a původní instance s cache N následně použije N+1 znovu. Není nutný souběh vláken: vznikají duplicitní pořadová čísla ve forenzním event streamu i při sekvenčním střídání vlastníků.

### A2-027 · P2 · Transportní GET vymaže model a reasoning původního kroku

Zdroj: [kajovo/core/openai_client.py:86–93](kajovo/core/openai_client.py#L86); [kajovo/core/run_bundle.py:624–628](kajovo/core/run_bundle.py#L624); [kajovo/core/run_bundle.py:659–662](kajovo/core/run_bundle.py#L659); [kajovo/core/run_bundle.py:594–604](kajovo/core/run_bundle.py#L594); [kajovo/core/orchestration/provider_operations.py:83](kajovo/core/orchestration/provider_operations.py#L83).

Observer zapisuje každý transportní request do aktuálního evidence_step_id. Polling GET nemá json_body ani model; record_request přesto aktualizuje model=None a reasoning_effort=None na témže kroku. update_step hodnoty skutečně přepíše. Evidence kroku po pollingu tedy může ztratit původní model/reasoning, přestože jednotlivý pracovní request je stále obsahuje.

### A2-028 · P2 · Klon běhu používá redigovanou UI projekci místo původního zadání

Zdroj: [kajovo/studio/history_data.py:55–56](kajovo/studio/history_data.py#L55); [kajovo/studio/history.py:414–430](kajovo/studio/history.py#L414); [kajovo/core/safe_config.py:28–29](kajovo/core/safe_config.py#L28); [kajovo/core/safe_config.py:44–66](kajovo/core/safe_config.py#L44).

HistoryData rediguje celý state včetně ui_state.prompt. Clone následně vezme tento cacheovaný _state a předá jej do workbench jako nový funkční vstup. I nevinný příklad token=abc v zadání se tak změní na token=[REDACTED]. Redakce vhodná pro zobrazení se stala transformací obsahu dalšího požadavku; kanonický uložený prompt přitom existuje.

### A2-029 · P1 · Pokračování historie může vytvořit nový request vedle známé rozpracované LIVE odpovědi

Zdroj: [kajovo/studio/history_policy.py:62–85](kajovo/studio/history_policy.py#L62); [kajovo/studio/history_launcher.py:70–77](kajovo/studio/history_launcher.py#L70); [kajovo/studio/history_launcher.py:223–315](kajovo/studio/history_launcher.py#L223).

Stav response_pending patří mezi stavy pro Continue. Launcher blokuje neurčité odeslání bez ID a nedokončený BATCH, ale ne známé pending Response ID. Vytvoří nový RunLogger/RunWorker a nepřenese journal rozpracované Response; přenáší konfiguraci a checkpoint přípravy. Po timeoutu LIVE tak Continue může místo převzetí již běžící odpovědi znovu zaplatit tutéž nedokončenou fázi. Nález se týká Continue, nikoli vědomého Rerun.

### A2-030 · P2 · Asynchronní výsledek přepisuje mezitím změněnou konfiguraci workbench

Zdroj: [kajovo/studio/workbench.py:351–382](kajovo/studio/workbench.py#L351).

Callback uzavírá původní cfg, ale bez kontroly identity nynějšího formuláře mění response_id, qa_continue_conversation a QFILE plán/cestu/formát. Formulář zůstává použitelný pro další práci. Pokud uživatel během čekání změní zadání nebo připraví jiný projekt, starý výsledek přepíše jeho nové volby a může při další akci připojit cizí plán či Response ID.

### A2-031 · P2 · Odložený profesionální návrh PHOTO nemá cestu k použití

Zdroj: [kajovo/studio/photos.py:301–313](kajovo/studio/photos.py#L301); [kajovo/studio/photos.py:327–332](kajovo/studio/photos.py#L327).

Při změně zadání během professionalize se placený výsledek uloží do pending_professional a UI oznámí připravený návrh. V celém souboru se však tato hodnota pouze nastavuje nebo maže; není zobrazovací ani aplikační cesta. Výsledek se sice uchová v paměti objektu, uživatel jej ale z tohoto stavu neumí otevřít ani použít a další úpravy jej zahodí.

### A2-032 · P2 · Uložené nastavení dry_run_modify nemá účinek

Zdroj: [kajovo/core/config.py:103](kajovo/core/config.py#L103); [kajovo/studio/settings.py:24](kajovo/studio/settings.py#L24); [kajovo/studio/workbench.py:30–49](kajovo/studio/workbench.py#L30).

Nastavení vystavuje volbu 'Připravovat úpravy bez zápisu', ale default_state nastavuje dry_run vždy na False. Ve zdrojích není spotřebitel dry_run_modify, který by tuto hodnotu přenesl do konfigurace MODIFY. Uživatel tak uloží bezpečnostně významnou preferenci, kterou nový běh nepoužije. Samostatný přepínač dry_run v běhu fungovat může; nález míří na nefunkční globální volbu.

### A2-033 · P2 · Částečné smazání vzdálených prostředků zanechá neplatné přílohy v kontextu

Zdroj: [kajovo/studio/resources.py:208–218](kajovo/studio/resources.py#L208).

Mazání probíhá po jednotlivých ID, ale odstranění připojených ID z context proběhne až v úspěšném receive po dokončení všech DELETE i následného list. Selže-li druhý DELETE nebo list, již úspěšně smazaný první soubor zůstane připojený a zobrazený jako dostupný. Další request může převzít neexistující file_id/vector_store_id. Chybí aktualizace podle skutečně dokončených jednotlivých efektů.

### A2-034 · P3 · Historie QA nevykresluje odpověď z aktuálního JSON kontraktu

Zdroj: [kajovo/studio/history_overview.py:50–67](kajovo/studio/history_overview.py#L50); [kajovo/core/runs/qa.py:88–101](kajovo/core/runs/qa.py#L88).

human_answer hledá pouze text nebo JSON.text. QA_ANSWER_V2 ukládá odpověď do result.data.answer. Přehled tak místo samotné odpovědi zobrazí serializovaný kontraktní JSON, případně bez vhodného output_text nic. Jde o nesoulad parseru zobrazovací vrstvy s aktuální maskou, nikoli chybu samotné QA odpovědi.

### A2-035 · P3 · Dokončený průběh nadále ukazuje aktivní a čekající mikrokroky

Zdroj: [kajovo/progress_ui.py:478–501](kajovo/progress_ui.py#L478); [kajovo/progress_ui.py:506–530](kajovo/progress_ui.py#L506); [kajovo/studio/operations.py:199–214](kajovo/studio/operations.py#L199).

finish pošle RUN/completed, ale _micro_steps ani pro tento terminální stav nevrací dokončený tok; u local označí Lokální zpracování jako current a Validace jako pending. _process_steps také nezakončí aktuální fázi podle úspěšného RUN, pokud samostatná fáze neměla completed. Souhrn říká hotovo, vnitřní graf přitom tvrdí, že práce ještě běží.

### A2-036 · P3 · Přesun komiksové bubliny mění její bounds bez oznámení scéně

Zdroj: [kajovo/studio/comic_editor.py:184–209](kajovo/studio/comic_editor.py#L184).

boundingRect zahrnuje ocas relativně k layer.x/y. mouseReleaseEvent tyto hodnoty změní bez prepareGeometryChange a bez explicitní aktualizace vykreslení. Tím se po přetažení může změnit lokální ohraničující obdélník mimo oznámený životní cyklus QGraphicsItem; hrozí chybný hit-test nebo zbytky/ořez ocasu v editoru. Exportní render je jiná cesta a tímto nálezem se neprohlašuje za vadný.

### A2-037 · P2 · Inventura vazeb schémat chybně označuje skutečně používanou masku jako nenavázanou

Zdroj: [tools/verify_contract_links.py:167–181](tools/verify_contract_links.py#L167); [kajovo/core/orchestration/manual_resources.py:14–29](kajovo/core/orchestration/manual_resources.py#L14).

physical_contracts neobsahuje MANUAL_RESOURCE_BINDINGS_V1.schema.json. Rozdíl actual_contracts−expected_contracts jej proto bezpodmínečně přidá do errors s tvrzením, že nemá runtime vazbu. Zdroj manual_resources tuto vazbu přímo obsahuje a masku používá ve validátoru. Jde o chybu samotného pomocného programu, odvozenou z jeho kódu; nástroj nebyl spuštěn a jeho staré výsledky nebyly použity.

### A2-038 · P2 · Git filtr nepozná databáze s příponou sqlite3

Zdroj: [kajovo/core/project_git.py:22–30](kajovo/core/project_git.py#L22); [kajovo/core/project_git.py:100–105](kajovo/core/project_git.py#L100); [kajovo/core/orchestration/repository.py:1067](kajovo/core/orchestration/repository.py#L1067).

allowed_file zakazuje .db a .sqlite, ale nikoli .sqlite3. Aplikace sama používá orchestration.sqlite3. Takový soubor mimo vyloučený adresář projde nabídkou, milníkem i kontrolou tracked souborů před push. Nález se netýká automatického odeslání každé databáze: předpokladem je databáze v povolené části zvoleného repozitáře a její zařazení do Git.

### A2-039 · P3 · Kontrola Git indexu může číst index jiného repozitáře

Zdroj: [kajovo/core/project_git.py:46–48](kajovo/core/project_git.py#L46); [kajovo/core/project_git.py:133–139](kajovo/core/project_git.py#L133); [kajovo/core/project_git.py:195–202](kajovo/core/project_git.py#L195).

git rev-parse --git-path index může vrátit relativní .git/index vůči ProjectGit.root. Kód ji převádí pouze na Path, ale čte ji vůči pracovnímu adresáři aplikace, nikoli root zvoleného projektu. Při práci s jiným projektem tak before/after kontroluje jiný index nebo None. Izolovaný GIT_INDEX_FILE pro samotný snapshot tím není popřen, vadná je následná kontrola jeho deklarované neporušenosti.

### A2-040 · P2 · Přepsání ZIPu převodníkem ztrácí oprávnění vnějšího souboru

Zdroj: [utf8nobom/app.py:392–409](utf8nobom/app.py#L392); [utf8nobom/app.py:438–445](utf8nobom/app.py#L438).

Změněný ZIP se nahradí souborem vytvořeným mkstemp bez přenosu režimu původního ZIPu. Na POSIX se například původně skupinově čitelný archiv změní na soukromý soubor s režimem dočasného souboru. Běžná textová větev naopak před replace volá copymode. Zachování ZipInfo uvnitř archivu neřeší oprávnění samotného .zip.

### A2-041 · P2 · Export artefaktu může při chybě zničit dřívější cílový soubor

Zdroj: [kajovo/studio/history_artifacts.py:182–189](kajovo/studio/history_artifacts.py#L182).

Po ověření zdrojového hashe se export provede přímo shutil.copyfile do zvolené cesty. Pokud cíl existuje, otevření jej zkrátí ještě před dokončením kopie. Při nedostatku místa nebo přerušení zůstane částečný soubor a původní obsah je ztracen. Uživatel potvrzuje nahrazení výsledkem, nikoli ztrátu původního cíle při neúspěšné operaci; chybí atomický publikační krok tohoto exportu.

### A2-042 · P3 · Metoda invalidated neumí porovnat V3 graf s obsahovou závislostí

Zdroj: [kajovo/core/context_compiler.py:520–524](kajovo/core/context_compiler.py#L520); [kajovo/core/context_compiler.py:329–342](kajovo/core/context_compiler.py#L329).

invalidated volá compile bez verified_artifacts a ani je nepřijímá jako parametr. Pro existující soubor s content_dependency na generovaný výstup compile vyvolá chybějící provider artefakt místo výsledku invalidace, i když se porovnávají stejné grafy. V auditovaném produkčním stromu nebyl nalezen volající invalidated; jde o konkrétní latentní chybu veřejné pomocné metody, ne doložený pád běžného generate.

### A2-043 · P3 · Index historie může přehlédnout změnu obsahu existující evidence

Zdroj: [kajovo/core/run_bundle.py:1388–1402](kajovo/core/run_bundle.py#L1388); [kajovo/core/run_bundle.py:1404–1413](kajovo/core/run_bundle.py#L1404); [kajovo/core/run_bundle.py:1499–1500](kajovo/core/run_bundle.py#L1499).

Klíčem obnovy indexu je maximum mtime několika hlavních souborů a adresářů, nikoli obsahu response/checkpoint/artifact souborů. Přepsání existujícího response JSON bez změny hlavních souborů nemění mtime jeho adresáře, takže běžný refresh vrátí starý souhrn. Stejně může maximum překrýt změnu jiné položky se starším časem. Detailní HistoryData má vlastní jemnější podpis, proto se přehled a otevřený detail mohou rozejít.

## Závěrečná kontrola závěrů

Nálezy popisují současný kód, nikoli převzaté seznamy z dřívějších auditů. Podobné projevy byly spojeny jen tam, kde sdílejí příčinu; například předčasný submit latch COMIC je jeden nález pro LIVE i BATCH. Naopak procesový zámek publikace a nebezpečná rollback větev jsou dvě nezávislé vady.

Rozlišení důležitá pro interpretaci:

- Chybějící distribuční schéma prokazuje selhání při načtení daného modulu, nikoli automaticky pád každého startu.
- Absence strukturovaných povinností v DETAIL neznamená jejich absenci i ve všech A3 kontextech; A3 je znovu doplňuje.
- Nálezy veřejného tool validátoru a metody `invalidated` výslovně rozlišují latentní chybu od doložené běžné produkční cesty.
- Rozdíl Responses BATCH a image BATCH nebyl sám o sobě označen za chybu modelové matice. Jde o různé endpointové schopnosti.
- Selhání trvalého ukládání hesel nebylo automaticky označeno za tiché úspěšné uložení: `save_settings` kontroluje návratovou hodnotu a obnovuje předchozí hodnoty. Tento kandidát nebyl započten.
- Nebyly započteny domněnky o správnosti textu generovaného modelem, stylistické preference autora auditu ani absence rozsáhlých dodatečných produktových testů.

Zpráva nezavádí nové guardy, testovací brány, schvalovací mezikroky, cenový modul ani změnu rozsahu procesů. Zachycuje vady a hranice důkazu. Jejich případné odstranění je samostatná implementační úloha.

## Příloha: úplný inventář přečtených souborů

U každého neprázdného souboru byl přečten rozsah **1 až uvedený počet řádků včetně**. SHA-256 se vztahuje k původním bajtům souboru, nikoli normalizovanému textu. Všechny otisky byly na závěr znovu porovnány; shoda 204/204. Samotná tato nově vytvořená zpráva není součástí auditovaného inventáře.

| Soubor | Řádky | SHA-256 |
|---|---:|---|
| [.editorconfig](.editorconfig) | 16 | `f1998568ae43bea112851234b5ffd98817b776bcc5aa4657298f282745cc70c8` |
| [.gitattributes](.gitattributes) | 5 | `a4b3d2ca3d79cdc5f0dadb628c97d6a4f65f7a43366b19f88346d2462c99fd2c` |
| [.github/workflows/ci.yml](.github/workflows/ci.yml) | 168 | `14ad69408e141e06bbb2360ed86b80c0ec458a3be278af5803c103806692999c` |
| [.github/workflows/contract-links.yml](.github/workflows/contract-links.yml) | 76 | `f2a60854f50ae68856ea00db2d1166cc3bdff0dec20850fca68087276f690e58` |
| [.github/workflows/finalize_nine_hardening.yml](.github/workflows/finalize_nine_hardening.yml) | 138 | `c4c6b3b28e3fd77f57555f5855a744204c14bdecdbb1a57c4590836dd0b306e6` |
| [.github/workflows/r05c_finalize_studio.yml](.github/workflows/r05c_finalize_studio.yml) | 92 | `677578d9774a3767775e8a249ea3a8770fcc3f2803acacd1d3e204f7705d5e06` |
| [.github/workflows/release.yml](.github/workflows/release.yml) | 508 | `b20fdd0380260bc141eabe9a70413a2d0330bc152213e141d56d4ee73b467967` |
| [.gitignore](.gitignore) | 41 | `a0b4c944fe3d5121ad8da0beaaa22c775ecd94518b495e489f3aa4450e89e5ab` |
| [.pre-commit-config.yaml](.pre-commit-config.yaml) | 33 | `4679a511806d47f7ff81213b361b3bc2a5c3d3871aa22507bb67841b5689f9b6` |
| [Build/assets/.gitignore](Build/assets/.gitignore) | 3 | `aae815b9313ef60fb99d51bec324f3de1cea5256d6bbf58a660578b3e2d5815c` |
| [Build/build_macos.sh](Build/build_macos.sh) | 53 | `49d6a3c3ee4c6b97c3f18745deb51611647707733e08b207b3d1ff09390ff01b` |
| [Build/build_windows.ps1](Build/build_windows.ps1) | 45 | `7278c702b6e8a30296fefa487298e4fe218d854aa0ab1c62d990e1ddf4154a09` |
| [Build/generate_icons.py](Build/generate_icons.py) | 52 | `c0c848241ce7fa4c5009d59e4457b674e6a55f72d3137eda501259a2dc53f789` |
| [coverage-critical.toml](coverage-critical.toml) | 83 | `719861f59964f2ec400010a88e367d1b8c2915eae91f5422307bc2bb73c0b200` |
| [kajovo/__init__.py](kajovo/__init__.py) | 19 | `a64605bf2170a09de6e4f9917ac26fe444609421074cd7e07e8057ebb2d90efd` |
| [kajovo/app/__init__.py](kajovo/app/__init__.py) | 0 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| [kajovo/app/__main__.py](kajovo/app/__main__.py) | 4 | `4ac953d9d43e4ef3a1f1a054d0ceb385a87076d105a99db4166d79426a737ca4` |
| [kajovo/app/main.py](kajovo/app/main.py) | 93 | `d8e40f6c47c156730db7291cebcf2b35b013e25bfc6deaf9033ad45f5d1b9644` |
| [kajovo/comic_layout.py](kajovo/comic_layout.py) | 122 | `eb0767dc4e93e004d3c18ae4181e99b35434f2f60995db4b63bda633cb0aa15e` |
| [kajovo/core/__init__.py](kajovo/core/__init__.py) | 0 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| [kajovo/core/batch_completion.py](kajovo/core/batch_completion.py) | 740 | `c868445da90401dd033788cc038aa42519310d037e78ee08e9db52405da39ffd` |
| [kajovo/core/batch_result.py](kajovo/core/batch_result.py) | 17 | `96a36099070a7f16e4d738739a482f245a791130b7ad59a728d595445fa70d9a` |
| [kajovo/core/batch_submit.py](kajovo/core/batch_submit.py) | 109 | `39b64020558eddeb6cdc7255627933ef7dbd8d99edcdaae27c82fce2d6367c9c` |
| [kajovo/core/cascade_contract.py](kajovo/core/cascade_contract.py) | 538 | `b98f3a14e27c7a541a41385b3e119b0837bee198e391dc56c378156c8c3fde46` |
| [kajovo/core/cascade_log.py](kajovo/core/cascade_log.py) | 14 | `4d608302f57416229fa071b5bd25887de1e9f4f26b2a554697e61ce8a30d4a78` |
| [kajovo/core/cascade_pipeline.py](kajovo/core/cascade_pipeline.py) | 2186 | `a887dad6041d9712b44bb66704f59f4089c4a7f7cf509f0658ea43d2b2678431` |
| [kajovo/core/cascade_production.py](kajovo/core/cascade_production.py) | 186 | `a3df03e254a45080872e2efc8808a069576d8c96518384341ef3cabfb1c6ac5a` |
| [kajovo/core/cascade_types.py](kajovo/core/cascade_types.py) | 448 | `caac328282c4e1e31d804f91ac58e986c866a513ee6c77880e8e2b0019b2910d` |
| [kajovo/core/comic_clone.py](kajovo/core/comic_clone.py) | 107 | `e3ef16c074f62b71b92c2f35c8179ae471c8545310f9b09b5c879c8b3667fa38` |
| [kajovo/core/comic_service.py](kajovo/core/comic_service.py) | 1682 | `540e425b7e7d4e72b342a1f05ea4e3ff6b41ff58a26d6d92495dd2f172bc9e93` |
| [kajovo/core/comic_store.py](kajovo/core/comic_store.py) | 687 | `6d8fa05806b9fab24f9aec233964a4bd8495aa32b783ba80f9d2fa010d86c0ff` |
| [kajovo/core/comic_types.py](kajovo/core/comic_types.py) | 370 | `ac90353dd3d628adbe9a28c934681ec0ba45ad406ff30001a537a50496104bbc` |
| [kajovo/core/compat.py](kajovo/core/compat.py) | 41 | `9d25dd50f1a540bb097644f0343394e3ce205c3c84fc6f7d9d8d3842eecb5224` |
| [kajovo/core/config.py](kajovo/core/config.py) | 175 | `816edb768a59fec407bea3bf16960fe85ce31286008dc056784790c847cfc1d9` |
| [kajovo/core/context_compiler.py](kajovo/core/context_compiler.py) | 531 | `c6277837195f51e8e28e7cc248a11b6427fd1b6a6c263b8c7094eb8831015b39` |
| [kajovo/core/context_limits.py](kajovo/core/context_limits.py) | 272 | `460329cb765779aed39079c5ed3fde03721ea16f3c90958ef0e71418566c9c6c` |
| [kajovo/core/contracts.py](kajovo/core/contracts.py) | 209 | `134e051ab9ff644c37f25ce9e16ecddf32f7eafb6d0f3a0cbdf0ca120d0306d8` |
| [kajovo/core/delivery_preparation.py](kajovo/core/delivery_preparation.py) | 182 | `670ffc8feadf1bcc0ed7ae21f4ce6a5b341afa305be31ec08a2ab09ad215e2ef` |
| [kajovo/core/diagnostics/__init__.py](kajovo/core/diagnostics/__init__.py) | 1 | `359eee834a628e867f9dbf7d34196d211475b093658f7395206e9f37c9f9bd93` |
| [kajovo/core/diagnostics/ssh.py](kajovo/core/diagnostics/ssh.py) | 116 | `d041c1d1ea839ac48adac2cd9574f0b676935cbb16be989b4df84826a6b7af3e` |
| [kajovo/core/diagnostics/windows.py](kajovo/core/diagnostics/windows.py) | 97 | `3e3bb443183438ee648f5511097ba2a2b34d0a82276cc2d464493ddc1854f04a` |
| [kajovo/core/diagnostics/windows_collect.ps1](kajovo/core/diagnostics/windows_collect.ps1) | 13 | `33e19c3fe5de45f3001f78f9cbde515ede1848e93cf67d6bd413d2ed61a90605` |
| [kajovo/core/filescan.py](kajovo/core/filescan.py) | 155 | `a135f3fe8d0e7b1cad5328d596579ff8893e9659c9e9aa62710ca995fc01720f` |
| [kajovo/core/generate_batch.py](kajovo/core/generate_batch.py) | 2249 | `73ac8748f5e0172df4193216b63a4b7ae4a6fd594b3ae2fef4800b17793f51dc` |
| [kajovo/core/image_runtime.py](kajovo/core/image_runtime.py) | 149 | `f50382b6859422eb0ac9fcbb42b1f3fa684b5db303774bef018ada90a1f04682` |
| [kajovo/core/model_capabilities.py](kajovo/core/model_capabilities.py) | 84 | `03206b00f5b12adf120a66d8eb4ef8b406cfa9ff05cf592df30bc0420806da28` |
| [kajovo/core/model_catalog.py](kajovo/core/model_catalog.py) | 101 | `194ca10bc3e6bcbdefc3b9eec92b0ac00969484a26b322dd2199dcacb086376e` |
| [kajovo/core/model_registry.py](kajovo/core/model_registry.py) | 293 | `71a920d9defb7d638473b627a8a0e2653a233d3406905474c66b9dcd558bf6b7` |
| [kajovo/core/notifications.py](kajovo/core/notifications.py) | 52 | `9cfa4023f02ca483a14f9e036efc65afd657e33e06349eae6a21503d631f4bb5` |
| [kajovo/core/openai_client.py](kajovo/core/openai_client.py) | 585 | `8b11b7fedfc602b0ec35e5b5589ddb986925aca75926994e2265789d992b76f8` |
| [kajovo/core/openai_model_matrix.json](kajovo/core/openai_model_matrix.json) | 9318 | `9b7e6e4a9354b1443717974f88b63b20682a3c962574f385a91fb44c3d9aedc4` |
| [kajovo/core/openai_transport.py](kajovo/core/openai_transport.py) | 417 | `f21fba9a0be674eaa4a9d0df651e51fc4694dfc3c8713bd77a48fdcaebe0229c` |
| [kajovo/core/orchestration/__init__.py](kajovo/core/orchestration/__init__.py) | 1 | `d5d7c778724efffd6aa05ba415bfab76cdc407cbb492853a0d79b5d0da5f5f4a` |
| [kajovo/core/orchestration/authorization.py](kajovo/core/orchestration/authorization.py) | 168 | `9f527866532c25d35dd80d2811ae3870123ddff1ebb7b0d9a0e28f0c3b64d9ae` |
| [kajovo/core/orchestration/batch_manifest.py](kajovo/core/orchestration/batch_manifest.py) | 185 | `6398051f2f64c76e9bd9dfd9262e6563c4596b6b59d61a19d7cb6104c9555af8` |
| [kajovo/core/orchestration/batch_recovery.py](kajovo/core/orchestration/batch_recovery.py) | 31 | `071014aa6b7975c884e2dc50b7f62a374dcb55935e3dce7888f238c0f9dfd008` |
| [kajovo/core/orchestration/contracts.py](kajovo/core/orchestration/contracts.py) | 76 | `0d828c253359e26238e26cf5ed91b6ebc74ded7d2d95614bdf92325aaa3434a6` |
| [kajovo/core/orchestration/errors.py](kajovo/core/orchestration/errors.py) | 10 | `f3c818de1257a2b0d5db3511b8cb3e6cbd885c9bacad90229b493d5e9449961a` |
| [kajovo/core/orchestration/executor.py](kajovo/core/orchestration/executor.py) | 86 | `8539802b0835b44875c99a09516e715f6641532b9c7a24ea6c7d9941b12c026f` |
| [kajovo/core/orchestration/image_slots.py](kajovo/core/orchestration/image_slots.py) | 127 | `9bae1630fdc2ef7771942832083abce7aa6605544997777f552dcc98842877a0` |
| [kajovo/core/orchestration/manual_resources.py](kajovo/core/orchestration/manual_resources.py) | 136 | `7cd340334f74313feae1d23960b6129aa2ca3c765119ba9b0aa28056ff5f3e49` |
| [kajovo/core/orchestration/policies/images.json](kajovo/core/orchestration/policies/images.json) | 41 | `a0fd2c7143262e44c63edc3fff98a0b1bc97d369ee8258a974e8d72e61fdc3b2` |
| [kajovo/core/orchestration/preparation.py](kajovo/core/orchestration/preparation.py) | 1617 | `315ccc378f2fafcc66e57451107c4eba1e837569acbb3985519f8bbdeb5e0ef4` |
| [kajovo/core/orchestration/projection.py](kajovo/core/orchestration/projection.py) | 149 | `557c34d1a6d050466709280349c516722d7790f0e66d4badaf3e00971a24bd4a` |
| [kajovo/core/orchestration/provider_operations.py](kajovo/core/orchestration/provider_operations.py) | 229 | `f24ae050d18e2068e566906b70919c1f9abde5d62ff569c06f44317ad767780e` |
| [kajovo/core/orchestration/publish.py](kajovo/core/orchestration/publish.py) | 584 | `cc045ff6b3066e1fd00e3dae13f3b585d40efcc16115b056847107ab243da92a` |
| [kajovo/core/orchestration/repository.py](kajovo/core/orchestration/repository.py) | 1068 | `92cab136dbcd1ce5b3fcdf1388d099ca21ee0ca6b0fba1e5e35b849ab3f7d0af` |
| [kajovo/core/orchestration/request_binding.py](kajovo/core/orchestration/request_binding.py) | 36 | `b8a2905ece5567143b44d541cb88f03c8f9e73360dd5c0021d89f31dd8eb98c5` |
| [kajovo/core/orchestration/resource_delivery.py](kajovo/core/orchestration/resource_delivery.py) | 973 | `fa0b0d0e95fbbb4da6d33c27ee2e023718dce5eebe0ff1f28bcdd36fb426e6fc` |
| [kajovo/core/orchestration/run_config.py](kajovo/core/orchestration/run_config.py) | 138 | `43cab0f1e5f53ee6f9b39ea0f4040b93d5eda9b4201264198487fbbc7339878b` |
| [kajovo/core/orchestration/source_pack.py](kajovo/core/orchestration/source_pack.py) | 557 | `bff490004e9038b6034bd20eb7f3f844d68da30ea234b64765f702589bac6de7` |
| [kajovo/core/orchestration/verification.py](kajovo/core/orchestration/verification.py) | 714 | `ae12ddd8aab5db5f21f8cb5f241506d7346d26267d2a0739f0e649080f3ff774` |
| [kajovo/core/orchestration/waves.py](kajovo/core/orchestration/waves.py) | 99 | `0286043acbef26d2188546e280f317d99be44d9525d28ae86fc3a4f198cd90b8` |
| [kajovo/core/orchestration/work_order.py](kajovo/core/orchestration/work_order.py) | 330 | `b9d5d1944ffcc6b7cf32aa4e50ff0a6de837b6917d2449d83d99bdcc52c10ea7` |
| [kajovo/core/photo_batch.py](kajovo/core/photo_batch.py) | 1196 | `65f8ea353f4ec7156634a89121cef9c50b231aeb2831a3b9624e89bbf7254326` |
| [kajovo/core/photo_prompt.py](kajovo/core/photo_prompt.py) | 272 | `235ae0ab750ffc4f38802599afe975d54dd8aa1d580ee17b2951c612067d8ec6` |
| [kajovo/core/photo_templates.py](kajovo/core/photo_templates.py) | 232 | `fc78c66bbe50236e04fccc6d03e4c58b190d429343a9953eab81b69e57ad5c95` |
| [kajovo/core/pipeline.py](kajovo/core/pipeline.py) | 6 | `f33aebf82925ba77c312fd3f923c9bf0e51dade3704bc0862c27e756a264f468` |
| [kajovo/core/progress.py](kajovo/core/progress.py) | 113 | `3888e49ab2269d8f2d40d76bd10d436fdc43f47f85d89f407406a228dbb4d133` |
| [kajovo/core/progress_display.py](kajovo/core/progress_display.py) | 170 | `8acd856a69f01291328e8b30d73bf4c3d8ba6fa47a47a01b65572b57fddd6eff` |
| [kajovo/core/project_git.py](kajovo/core/project_git.py) | 350 | `923f5baec81f35f2444a77ae323b6d05f99bd72fa98b7e19aa1196793ebf1e32` |
| [kajovo/core/recoverable_artifacts.py](kajovo/core/recoverable_artifacts.py) | 100 | `f341305884c435ad5dba937527e77f321dd9072ef45ad8e1dd11d0fa230ed31a` |
| [kajovo/core/recovery.py](kajovo/core/recovery.py) | 168 | `a6ce2f577fba47af33754e0d4e49a89adf63dc9a7bbed0c613c2392208a4795d` |
| [kajovo/core/repair_execution.py](kajovo/core/repair_execution.py) | 119 | `86ad267d28baf653cf4c282ba5bdc1fdbc5aaf54c155c281e0b81bedefce8484` |
| [kajovo/core/request_rules.py](kajovo/core/request_rules.py) | 250 | `3ccb76c931d2aecf80b6b37dbe3a5586b52004ac795c2ea0d50132dc3adea93d` |
| [kajovo/core/requirements.py](kajovo/core/requirements.py) | 477 | `0c3177f916d3facda269f2302715d065ca34f9a72cb3b4860ede54bd9330d7c7` |
| [kajovo/core/resources.py](kajovo/core/resources.py) | 12 | `6f0f235866b7b0ec245637ca05abfd5ba032393112d366e9b969eb6b9d948907` |
| [kajovo/core/response_journal.py](kajovo/core/response_journal.py) | 263 | `3890f9cc6cedba4ffbf0f54f5238360f8bff441db106a73255ca398f3e8949b6` |
| [kajovo/core/response_policy.py](kajovo/core/response_policy.py) | 67 | `817b0aa4bbaa3eef334f7fe73c3ce2780414209e791547f6929bbc815a2983a1` |
| [kajovo/core/retry.py](kajovo/core/retry.py) | 67 | `1bebad932ba20e38310a9ade4570972765cd796346c5f93306dfc749e5adc284` |
| [kajovo/core/run_bundle.py](kajovo/core/run_bundle.py) | 1533 | `a00f123558d3c43ad697bb386612979e6c7aed987308a3734fd55b5d31dc9d53` |
| [kajovo/core/runlog.py](kajovo/core/runlog.py) | 839 | `0459da8e2ebe7ef540d030380421fe183f491c9c68587d611c13798ac6fb3d25` |
| [kajovo/core/runs/__init__.py](kajovo/core/runs/__init__.py) | 37 | `40afce9c7a0df4d41f0c928e8216255863a5de5965554aa1fca31864c1996892` |
| [kajovo/core/runs/attachments.py](kajovo/core/runs/attachments.py) | 592 | `3bac2d303ca5bc4a42afffecd840fdf608a3e5c6a1f135ef5e52540a20dadd7c` |
| [kajovo/core/runs/batch_execution.py](kajovo/core/runs/batch_execution.py) | 265 | `2f6618aab762d9cc65fa9d79281ea81256bfaf9689a6fa837eae12857b9c6061` |
| [kajovo/core/runs/cancellation.py](kajovo/core/runs/cancellation.py) | 24 | `c061fb6ef5494c91653e5077371099f6d2e7ac1cc21c06a4792997a9f0fb490a` |
| [kajovo/core/runs/config.py](kajovo/core/runs/config.py) | 81 | `4fb7b48adae99437d1985b4378e14933b891f21fd2c61c8c5134d17ecd4dbf5a` |
| [kajovo/core/runs/context.py](kajovo/core/runs/context.py) | 229 | `5f49fa15400fe38eeffbee3aaedc39ac70dfad330f3e9bd62985cd56872bea4c` |
| [kajovo/core/runs/contracts.py](kajovo/core/runs/contracts.py) | 187 | `e2e6a5e9b1caa541c83272ab3f4d70ec1fe7705222309c7a6c231ed1da27d5dc` |
| [kajovo/core/runs/delivery.py](kajovo/core/runs/delivery.py) | 295 | `3043f695d514efb2ee8162ee1d7f28d32064426d6e850a8c7f6a8e6673e0ad71` |
| [kajovo/core/runs/delivery_execution.py](kajovo/core/runs/delivery_execution.py) | 151 | `f74a4902fa816a92c8aab65963abd51100b29d26f95277ddaef957ec2f5a3aab` |
| [kajovo/core/runs/diagnostics.py](kajovo/core/runs/diagnostics.py) | 211 | `5e36679886b61217454119fb640a8e9d03aa73c962092fdd747d49b82c17fbf2` |
| [kajovo/core/runs/executor.py](kajovo/core/runs/executor.py) | 312 | `89880c46d4c6289156a38c1822c00adb135aca4533212ea0e2ef4bde7f7d9c0e` |
| [kajovo/core/runs/file_execution.py](kajovo/core/runs/file_execution.py) | 420 | `ef46d1309cc72038804f9aff5da2aebe7243f6376f1ac1096ab3ab2774a153c7` |
| [kajovo/core/runs/generate.py](kajovo/core/runs/generate.py) | 497 | `075638df06b9c00aa95bcc742bd525a76510e8ae78a9749c384e6130323ca50e` |
| [kajovo/core/runs/locking.py](kajovo/core/runs/locking.py) | 80 | `d0a8d4925521c2f446888667f0a799b7d7447c7dd8b266f3aa089c2890638a9c` |
| [kajovo/core/runs/modify.py](kajovo/core/runs/modify.py) | 392 | `0186a217f7341167ff4af67daa7eaea73768125b1cff8db08aaa86b92a19bae4` |
| [kajovo/core/runs/observability.py](kajovo/core/runs/observability.py) | 47 | `b0cc4c7c04854c1188128122c47f2500e7c39c8314c77ac10a514b9bd9dff18b` |
| [kajovo/core/runs/polling.py](kajovo/core/runs/polling.py) | 139 | `4888191bf9c9e7e4768bdf7188ceb083c8ccb72c0575c4567029a8153236ca3c` |
| [kajovo/core/runs/ports.py](kajovo/core/runs/ports.py) | 28 | `89bc26a3558aa1465d33c1b552038b291c8184a10bd8427c7b12f558e26218ca` |
| [kajovo/core/runs/qa.py](kajovo/core/runs/qa.py) | 171 | `8674a84f2854bfbf69cd5295e90bac1cc2828033dfe58a349a9397e42c354a5f` |
| [kajovo/core/runs/qfile.py](kajovo/core/runs/qfile.py) | 353 | `5f244b610737fff83f11a0469e1bdfc5d5f23e1424f76d0ed97ca391641c3bcf` |
| [kajovo/core/runs/recovery.py](kajovo/core/runs/recovery.py) | 176 | `3fb898f9e8a06ef871fb61500ac11946a42841b54c985591b0ea54c8d4d26c5e` |
| [kajovo/core/runs/response_execution.py](kajovo/core/runs/response_execution.py) | 304 | `a9c2e8782dddf866ced3234892a658cbc83867cce8e00c0ca99b2cbcb884a6c4` |
| [kajovo/core/safe_config.py](kajovo/core/safe_config.py) | 135 | `21b5b45ab33205cd1988b00b4a86a8c5f59797436eb68c8ce5fca4f8211e7eb8` |
| [kajovo/core/secret_store.py](kajovo/core/secret_store.py) | 258 | `6a86411b8c39f4aaec8fadb7a06a20b2b025176c927396f7fcd362aa910b1032` |
| [kajovo/core/structured_output.py](kajovo/core/structured_output.py) | 369 | `951ee05fccbe0e00843d8ac35950dd50fbd2b3eaa74e4023f729162d3a4aecc2` |
| [kajovo/core/user_errors.py](kajovo/core/user_errors.py) | 169 | `114bce411cce26b9dc1d55120156d322fe88db22cbf120d6a1087350fe9b7c5c` |
| [kajovo/core/utils.py](kajovo/core/utils.py) | 92 | `7a74d977df71239e8cb2009a33004139dde0a588df8d7ed7bb2e3fcd23d101fa` |
| [kajovo/progress_ui.py](kajovo/progress_ui.py) | 667 | `f43e2c48a5d20a22ffeb0301e75b5268a3f776d83e23eb3c3a5ebe565a298b0f` |
| [kajovo/studio/__init__.py](kajovo/studio/__init__.py) | 1 | `10d8c7e3c3ef9d7d0342afccbf097786db941c375af57f09a654895a605129bc` |
| [kajovo/studio/application.py](kajovo/studio/application.py) | 357 | `b4e409aa58463090ca22f87b2cbc67d28262b0c4f57528995f2b4b8864313fe5` |
| [kajovo/studio/batches.py](kajovo/studio/batches.py) | 222 | `33beca8d80217bbef74eb5702de73e25e164002b3d2d495b335c8d1109b5e7ae` |
| [kajovo/studio/cascade_items.py](kajovo/studio/cascade_items.py) | 109 | `622c771250a6d99dafde2e52a2b9dbc98a7515df25f13abb769c490f68662fe6` |
| [kajovo/studio/cascades.py](kajovo/studio/cascades.py) | 386 | `1e458686e5d499bed1cad65434edea5b8d458308932025fbdc8de9c0a07f98be` |
| [kajovo/studio/comic_editor.py](kajovo/studio/comic_editor.py) | 328 | `13a9f1955abf8f564b240367838b8542e0d8a9b6a7c14629dfae0f4180ce3ee7` |
| [kajovo/studio/comics.py](kajovo/studio/comics.py) | 983 | `aac6278b930f656e53e1c9a5ba641b6fde63093dd9b6363030c05054710579db` |
| [kajovo/studio/components.py](kajovo/studio/components.py) | 351 | `30960cb9305458771f8f4a6a3e43a9058dc8476e18709cc4ff2e71dbd58bbd42` |
| [kajovo/studio/context.py](kajovo/studio/context.py) | 113 | `a60cd1a402f9171b2ea7f969d54060041ce75ee684808cfd1e37c887a621d233` |
| [kajovo/studio/converter.py](kajovo/studio/converter.py) | 147 | `42b6d55acce31e18572f820f0f3424d1357b1ce7f4fbe075cdc91603bf3bb0b1` |
| [kajovo/studio/evidence.py](kajovo/studio/evidence.py) | 120 | `f61750c4139e91a1d29a0292aad593f439dd47dbd963d4102f6ee08fe00a2304` |
| [kajovo/studio/history.py](kajovo/studio/history.py) | 637 | `549189aaba6d69697c95daaacb8d422b41953846531bed3e974d459001679429` |
| [kajovo/studio/history_artifacts.py](kajovo/studio/history_artifacts.py) | 514 | `5fe8a334f5c0af01dc36cfd90e6a2992a9e862b502616e2853eeb64d730b820c` |
| [kajovo/studio/history_cascade.py](kajovo/studio/history_cascade.py) | 103 | `9a6afa01338e3270a16656a65c6d95784ab1a1f2a08a9fa60d607795ebef1408` |
| [kajovo/studio/history_composer.py](kajovo/studio/history_composer.py) | 148 | `2575dbfd873e7ae5b9c2ef5078aee364b78656bfecc531e77662ccfbfb6e81cb` |
| [kajovo/studio/history_data.py](kajovo/studio/history_data.py) | 113 | `13c4bd8c1c0095b9c808ceec353ff22679fd233b813373bda0177cb9185cdb48` |
| [kajovo/studio/history_details.py](kajovo/studio/history_details.py) | 535 | `ea386ce04174b05e7981b8272ccf35500bcf9bd5f78c419240641022a62f218f` |
| [kajovo/studio/history_launcher.py](kajovo/studio/history_launcher.py) | 337 | `d427f8081531cbcd78f18d2fc55332bba495be37beaa898b925c17ef413fb642` |
| [kajovo/studio/history_models.py](kajovo/studio/history_models.py) | 299 | `cdd0e659acbc98c4da9438cd3deb6cdbd412e705232981559e7975ec2834df3c` |
| [kajovo/studio/history_overview.py](kajovo/studio/history_overview.py) | 124 | `47b3edb19379b8481042569e11917f181bc028874b87eded126b63ac2436f98c` |
| [kajovo/studio/history_policy.py](kajovo/studio/history_policy.py) | 155 | `d0acfb0b2e711de00c8b14bd5f8dd375c1d05f29da0b267a40372189d0128845` |
| [kajovo/studio/history_state.py](kajovo/studio/history_state.py) | 72 | `be0221df45a67ee9c018020cc4bf77665f0b2617768df54a63270cc77434f499` |
| [kajovo/studio/history_timeline.py](kajovo/studio/history_timeline.py) | 284 | `82eea99b7ed257098fbabf9f997e1de380310cfce96b7bff69d1e5c8d027c8ae` |
| [kajovo/studio/model_selection.py](kajovo/studio/model_selection.py) | 20 | `ccb81c683ed0c00ad81e82ea48f99f6274c47648facb0be18b2e7ed513387b07` |
| [kajovo/studio/operations.py](kajovo/studio/operations.py) | 534 | `3559926fc727c38e1e784b16603a55c266edee608b12b6a353f3caa991b8a1b4` |
| [kajovo/studio/photos.py](kajovo/studio/photos.py) | 462 | `8e8ec2b0552e19341e5044a722cd4373948c7eb18d81d93eae3c484a0e65f7f8` |
| [kajovo/studio/resources.py](kajovo/studio/resources.py) | 292 | `0eeff5bce5b625cb36aaa89e761bd179fc40056d881adcf5189b8f01249b882f` |
| [kajovo/studio/settings.py](kajovo/studio/settings.py) | 197 | `69ed27a064a9acc411eaca5e22ade7c636ccf8d261352a9a03b9b3e9bf75d2e0` |
| [kajovo/studio/ui_audit.py](kajovo/studio/ui_audit.py) | 485 | `82a18bab8266b184f1470a56f7cfd8b8b95c58dc25a49d18d858379275854a28` |
| [kajovo/studio/versions.py](kajovo/studio/versions.py) | 187 | `914999b048e7adb503a94ba12e2d0c87dcf6f7b963324757205bc37a43a7c263` |
| [kajovo/studio/workbench.py](kajovo/studio/workbench.py) | 492 | `72db382668f8f8232863ac491f5595dc16ac1f94bbf49ad3db13b5a323c68f1c` |
| [kajovo/studio/workers/__init__.py](kajovo/studio/workers/__init__.py) | 5 | `b1f5573829cd73abb2240b39384538ec4617bb2f751574621f671ac59e7e640e` |
| [kajovo/studio/workers/cascade_worker.py](kajovo/studio/workers/cascade_worker.py) | 60 | `3ea4dae2c7e9560cdcb8c3020096bbe21a8e6312207f09cac8b41a47fdc858a4` |
| [kajovo/studio/workers/run_worker.py](kajovo/studio/workers/run_worker.py) | 73 | `13be9a8ca93762b0e0112a412f52a8df1f963cc55ae98e7f9d43d66daffdaefa` |
| [kajovo_settings.example.json](kajovo_settings.example.json) | 61 | `c63a765ced55477ad94fc39554129bc48929898a9522d35ba0e716bcdf6bf386` |
| [kajovong/__init__.py](kajovong/__init__.py) | 4 | `1c3aee02d865f149623b0aedc3b4a17a1b6e45d9e023195e1e222b07c62c7bd4` |
| [kajovong/__main__.py](kajovong/__main__.py) | 9 | `bf3cfbe46e81f32afe6e82f05aa77257214a54c31a39237a2c28fe3b1a0d0db6` |
| [pyproject.toml](pyproject.toml) | 71 | `b184eedfb8a40ad909000fd2ddfc9f7d6fc66ab9cfb12bb97aafbfde07bd924d` |
| [requirements.txt](requirements.txt) | 1 | `0cac0e472eba3359aa79e9674ff11a5fd0484b9c465fc1cb774c30938edcc5d7` |
| [requirements/constraints.txt](requirements/constraints.txt) | 86 | `cf9ee2fda19c36ec41ec8802ff9eca34834ee08e1da65e4077f0de438f45d8a4` |
| [requirements-dev.txt](requirements-dev.txt) | 1 | `9d6d2c2e18451cd8eb86a739cbcb1664e4342ef4ae478266a3c333dd4f73674a` |
| [resources/orchestration/contracts/local/BATCH_MANIFEST_V4.schema.json](resources/orchestration/contracts/local/BATCH_MANIFEST_V4.schema.json) | 148 | `08a6c9173270e4b4bae4cd2044ebf86a4c8b98750b58021d0f652a85ab1a0bec` |
| [resources/orchestration/contracts/local/MANUAL_RESOURCE_BINDINGS_V1.schema.json](resources/orchestration/contracts/local/MANUAL_RESOURCE_BINDINGS_V1.schema.json) | 24 | `f76484077566ee6cd217f2a2d82a200b81e8a7367b4f32158ffa5aa96e33b8fc` |
| [resources/orchestration/contracts/local/RUN_CONFIG_V2.schema.json](resources/orchestration/contracts/local/RUN_CONFIG_V2.schema.json) | 28 | `b5e5c992d273956c2e5e49df4ec4cfc671d86dd6aa7945935ae3263178fc15e4` |
| [resources/orchestration/contracts/local/VERIFICATION_REPORT_V3.schema.json](resources/orchestration/contracts/local/VERIFICATION_REPORT_V3.schema.json) | 192 | `9b67f41cc4f76e01e971d3d72276e96f3bb33a64819ad9ba20d6afd230500bb6` |
| [resources/orchestration/contracts/local/WORK_ORDER_V2.schema.json](resources/orchestration/contracts/local/WORK_ORDER_V2.schema.json) | 123 | `ffb735667173df57186929874217d0110798c369bf08668a767e70d7a00f5fe1` |
| [resources/orchestration/contracts/local/WORK_ORDER_V3.schema.json](resources/orchestration/contracts/local/WORK_ORDER_V3.schema.json) | 130 | `456af3c08f75fb1f7713e3f430136511e0d59f35a46250525a5acb47643ddfd0` |
| [resources/orchestration/contracts/wire/FILE_CONTENT_V1.schema.json](resources/orchestration/contracts/wire/FILE_CONTENT_V1.schema.json) | 8 | `f539c75af081036ba4ba54fb044b1ae714596ca8348e1879e8b96ef98bb64edc` |
| [scripts/accept_comic.py](scripts/accept_comic.py) | 102 | `92fdb9ef2946f4d2f12117ecf20bce15aaba356c23ea1b15517b50d35cfd170b` |
| [scripts/analyze_id92_context.py](scripts/analyze_id92_context.py) | 164 | `4598fb13f898cf7c2a5893c4aa4f16e6f3f332af63c48e14c7c0cad9d01760ac` |
| [scripts/audit_studio.py](scripts/audit_studio.py) | 57 | `83a244cfeedf8b5269daac4ec69a13d1f1979664b7477d4bd5a076cd6b34bda3` |
| [scripts/audit_ui.py](scripts/audit_ui.py) | 42 | `8053c276c72c5cdb53810517cb55347d37015c26e5625bb0c0a9f7181f4bd719` |
| [scripts/benchmark_history.py](scripts/benchmark_history.py) | 54 | `f4ea9b9a790c8102f4fd1fdf096f988437e6879ee8209293b54b7d71c07097f5` |
| [scripts/bootstrap_windows.ps1](scripts/bootstrap_windows.ps1) | 60 | `12fb2a2902a847d885322f2c2dd3c0c6be1a8066e67a22f5e5f9eb45e8441711` |
| [scripts/build_ui_gallery.py](scripts/build_ui_gallery.py) | 47 | `2dc975c64d7231c6f59b0473d13cf2bd81b505a4947e960769f3e6e044943dc9` |
| [scripts/export_model_matrix.py](scripts/export_model_matrix.py) | 68 | `2fdc8f1086f4dd0218ccf3edfc91a0bd7ca2429984f3f88454b28bf42a31def4` |
| [scripts/export_ui_validation.py](scripts/export_ui_validation.py) | 42 | `8a6fd3a5d34718469e61b919f0e8ef68d13c6eadc404970d3aa649f179b12514` |
| [scripts/install.bat](scripts/install.bat) | 4 | `520cce72d552af9cb50b0ba40bf7401d41c561c43b48c1780d4934e78b6b5013` |
| [scripts/install.ps1](scripts/install.ps1) | 2 | `3d15292360ff68362d899dad71d4df1c33298dbb41e8f0029c94f9bd37847044` |
| [scripts/live_acceptance.py](scripts/live_acceptance.py) | 770 | `29b5c4807bef5c572beda996aa14a60e610c825cd9924363a0471cd02c217ef4` |
| [scripts/measure_request_context.py](scripts/measure_request_context.py) | 33 | `fd42cce33d414a95222a5aef8932417d24aa0f07f86e493862dac5078ec8112d` |
| [scripts/render_studio.py](scripts/render_studio.py) | 490 | `b7192e6bf08c735276cfef833703d13e2eb656276994ff0d34c0e25821c61cd7` |
| [scripts/render_ui.py](scripts/render_ui.py) | 16 | `f8eab889129bedf14f41acd44ce92341ba93ad5351768c871fc477faec9ceac5` |
| [scripts/run.bat](scripts/run.bat) | 10 | `e333526e2b35c45f7c995e5cc125a1b55442fed9187b3b4ea9ca3e727a809655` |
| [scripts/run.ps1](scripts/run.ps1) | 5 | `5da73cf21ef81702f670e7976b57f34eff4cbb6ffc871da77dc6600625423b9d` |
| [scripts/start.ps1](scripts/start.ps1) | 51 | `d14baa824c219e17c68ae1e17883e65bcefd8fdbc1c921adde7b8badd0dd64e3` |
| [scripts/start_app.py](scripts/start_app.py) | 71 | `1a79f204b7cf0d2fe3d520d1521ef2f701de6ab44748d55154c0312a0b4050f4` |
| [scripts/verify_request_matrix.py](scripts/verify_request_matrix.py) | 59 | `147b441437b5c8e027873c8b40e82f5288dd79ab82258ed3b22a94e04e194934` |
| [signace/signace.svg](signace/signace.svg) | 29 | `f13d8a83501d80079b3a0aa800927c79fc797a9c5c62316efb86858226ad6bb7` |
| [start.bat](start.bat) | 12 | `5c30abb0e147b171687d18ee3631d1ad4ada7556111e414833b93139caf892f6` |
| [tools/_finalize_lfs_checkout.py](tools/_finalize_lfs_checkout.py) | 55 | `50ed1c2f155a33c6448f1799d61838476e767178fe0ce70a51aa9ee1bc540110` |
| [tools/_finalize_nine_hardening.py](tools/_finalize_nine_hardening.py) | 326 | `cf3a14a69982727ac28fe6661b1c2db49b504a9e76a770255314fe9e6065a9fa` |
| [tools/_finalize_nine_postfix.py](tools/_finalize_nine_postfix.py) | 33 | `822da1f3ef6732dcb23ee589efe92f8994705bc5742c067d6bb016330c0386d7` |
| [tools/_fix_cascade_type_shadow.py](tools/_fix_cascade_type_shadow.py) | 32 | `f36d17f2da6d5e31d13d80cd68f8cf9294853bb0e2069be252634b8a077201df` |
| [tools/_r05c_finalize_studio.py](tools/_r05c_finalize_studio.py) | 642 | `fc56c9c6eecb884e1280bc240dfeab1cac843b83883edf4ed8f1f67f2871f144` |
| [tools/_r05c_postfix.py](tools/_r05c_postfix.py) | 52 | `b3b45e576fcd15831a1e75de2294a66a6551eb99ab1de259d91dc85dd1ccd849` |
| [tools/compatibility_smoke.py](tools/compatibility_smoke.py) | 29 | `3140aebaf64b272e7fc40813cee18e8dadaf42486a4887a9cf1640d95d6b9ac8` |
| [tools/check_critical_coverage.py](tools/check_critical_coverage.py) | 89 | `07a778cd09b333e2331a41bfb4977a34900cde4b533629928598717dfe7337e2` |
| [tools/verify_contract_links.py](tools/verify_contract_links.py) | 303 | `a1e8ef690a28096f5dd0ae9dfe1764098c99ccbfd0589e97e25b9e007ed53a56` |
| [tools/verify_dependency_contract.py](tools/verify_dependency_contract.py) | 92 | `14b7e07a7a9a5971d412f50b8eec9cdd6bf8a96a28b45b651f0e2a5a2b9d2c5e` |
| [tools/write_build_metadata.py](tools/write_build_metadata.py) | 63 | `e3da52c60d83a827b8015b37bf2231e69c9c527921d451e9db2beefbf4f00ea4` |
| [utf8nobom/__init__.py](utf8nobom/__init__.py) | 5 | `ba81e66f4e75c06cd7c53022498e8bd0d63734b23ec25daa614ba3231a498159` |
| [utf8nobom/app.py](utf8nobom/app.py) | 537 | `3705b4993087ae0d0ab965a979f44f561b04ed02ec680bd3c39a4ecb1375ddbf` |
| [utf8nobom/py.py](utf8nobom/py.py) | 7 | `99c50dec10f71336df0800b215bc3662775dee840e5cdc4a421b1cb992789fe2` |

