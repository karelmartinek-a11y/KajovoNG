# Komiks

Komiks je dlouhodobá místní knihovna desktopového studia. Normativní autoritou zůstává [SSOT](SSOT.md). Knihovna používá SQLite a souborové artefakty v adresáři nastavení `comic_library_dir` (výchozí `COMICS`). Nejde o cloudový účet ani veřejné obrázkové URL. Zálohujte celý tento adresář společně s odpovídajícími Run Bundles v `LOG`.

## Obsluha

1. V sekci Komiks zvolte Nový komiks a název. Na kartě Styl / Bible doplňte volitelná pravidla a reference. Sestavit bibli uloží nastavení a spustí skutečnou strukturovanou Responses operaci.
2. Na kartách Postavy a Prostředí přidejte název, popis a fotografie. Vytvořit / obnovit referenci sestaví popis identity a živě vygeneruje referenční obraz ve stylu bible. Změna podkladů nebo popisu vyžaduje vytvoření nové reference. Historické obrázky zůstanou zachované.
3. Na kartě Příběh / Storyboard lze vést textovou výrobní osu Story → Script → Storyboard → Continuity. Každý krok ukládá verzovaný strict kontrakt a odkaz na zdrojový dokument; převod storyboardu na panely je povolen až po Continuity PASS pro tutéž verzi storyboardu.
4. Přidejte samostatné panely nebo použijte převod schváleného storyboardu. Volba entity v nabídce vloží nedělitelný token do aktivního zadání. Kopírování uvnitř aplikace zachová strukturu; prostý text nezískává oprávnění odkazovat na entitu. Každý panel má vlastní formát.
5. Vyberte panely a zvolte Vygenerovat vybrané panely. Historie ukazuje skutečné stavy a počty. Po zavření aplikace pokračuje dávka u poskytovatele; po otevření se obnoví místní sledování. Po vypršení místního sledovacího limitu použijte Obnovit / převzít.
6. Text přidejte samostatně v náhledu. Bublinu lze přesouvat, měnit její velikost, hrot, písmo a přesné znění. Text, který se nevejde, blokuje export. SFX povoluje globální volba stylu. Kresba se objednává bez textu; náhled a export používají stejný Qt renderer a font Montserrat s diakritikou.
7. Upravit kresbu vytvoří samostatnou obrazovou dávku s poslední schválenou kresbou jako prvním vstupem. Výsledek je kandidátní verze. Použít vybranou verzi ji schválí; stejné tlačítko vrací starší verzi. Změna textu nebo plátna také vytváří verzi bez nového provider generování. Export ukládá jednotlivý panel, nikoli stránku.

Koš projektu je vratný. Knihovna fyzicky nemaže historické reference ani verze. Duplicitní projekt má nové identifikátory a vlastní kopie používaných souborů; běžící operace nekopíruje. Odstranění panelu čekajícího na výsledek je blokované.

Rozpracované platné zadání a styl se ukládají také při přepnutí projektu nebo zavření hlavního okna. Chyba validace zavření zastaví a vrátí uživatele do Komiksu. Současná změna knihovny a běžící operace nepřenese rozpracovaný request do jiné databáze.

## OpenAI kontrakt

Text bible, descriptorů, Story, Script, Storyboardu a Continuity používá `/v1/responses`, strict JSON Schema a existující ResponseJournal. Textový model se nehardcoduje: služba čte modely dostupné účtu a filtruje je přes centrální capability katalog a kompatibilitu workflow. Každá textová operace má `WORK_ORDER_V2`, explicitní `attempt_id` a nefinanční provider-operation evidenci. Obrazy používají snapshot `gpt-image-2.5-sunburst-2026-09-08`, kvalitu `max`, jeden PNG s neprůhledným pozadím. Reference se posílají skutečnými `images[].file_id` na `/v1/images/edits`; parametr `input_fidelity` se neposílá, protože jej Sunburst snapshot odmítá. Panel bez obrazových podkladů používá `/v1/images/generations`. API klíč zůstává v existujícím OpenAIClient; Qt ani komiksová služba neimplementují vlastní HTTP transport.

Kanonický zdroj obrazových možností je `image_capabilities` v `kajovo/core/openai_model_matrix.json`; UI a autoritativní validátor jej čtou přes `image_runtime`. Ověřenými zdroji jsou [model](https://developers.openai.com/api/docs/models/gpt-image-2.5-sunburst), [Image generation](https://developers.openai.com/api/docs/guides/image-generation), [JSON edit](https://developers.openai.com/api/reference/resources/images/methods/edit), [Batch](https://developers.openai.com/api/docs/guides/batch) a centrální modelová matice `kajovo/core/openai_model_matrix.json`. Dokumentované schopnosti neznamenají oprávnění každého účtu; skutečný textový model musí být současně dostupný účtu a kompatibilní s požadovanými capabilities a workflow.

Reference: nejvýše 16 fyzických obrazů na request včetně stylu a editované kresby, dále omezeno vybraným modelem. Pořadí je základ editace, identity podle ID entity, stylové reference podle ID assetu. Deduplikace porovnává normalizované bajty a otisk transformační politiky; příslušné `image_index` a `slot_id` se doplní až potom. Jediný fyzický obraz může zastupovat více rolí, ale žádná role se nesmí ztratit. Snapshot i prompt obsahují `reference_slots` a upload používá totožné konečné pořadí. PNG, JPEG a WebP musí být statické a menší než 50 MB; aplikace dále omezuje dekódovaný vstup na 64 MP. Kontroluje skutečný obsah, celistvost, velikost a hash. Pracovní reference odstraní EXIF, ICC i textová metadata, zohlední EXIF orientaci právě jednou a zachová průhlednost v RGB/RGBA PNG. Původní bajty zůstávají neměnným soukromým artefaktem; odvozená pracovní kopie ukládá původní ID, otisk a verzi transformační politiky a při další kompilaci se znovu ověří a použije. SVG, animace a poškozené soubory jsou odmítnuty.

Generovací rozměry jsou dělitelné 16, hrana nejvýše 3840, poměr nejvýše 3 : 1 a plocha 655360–8294400 pixelů. Plocha nad 2560 × 1440 je experimentální a vyžaduje volbu uživatele. Aplikace vybírá podporovanou velikost podle cílového poměru a plochy. Finální raster může mít vlastní přesné rozměry (nejvýše 16384 na hranu a 64 MP); Pillow provede centrovaný crop nebo padding s Lanczos převzorkováním bez protažení. Papírové formáty používají 300 DPI, volitelně 150–600. UI rozlišuje nativní generování a výsledné pixely. Větší cílový raster nevytváří nové skutečné obrazové detaily.

Konzistenci podporuje stejná bible, neměnné referenční revize, descriptor identity, stabilní pořadí vstupů a vysoká věrnost editace. Generativní model nezaručuje shodu každého detailu. Sestavení nové bible vyžaduje obnovit reference entit před dalšími panely.

## Dávky, obnova a evidence

Jedna aplikační operace se rozdělí pouze podle endpointu nebo limitu 50000 requestů / 200 MB. Každý panel má unikátní trvalé `custom_id` a nezávislý snapshot. Zápis operace, dávek a položek je jedna SQLite transakce. Vstupní JSONL se validuje před uploadem `purpose=batch`; create nastaví `completion_window=24h` a expiraci výsledků 30 dní. Obrázkové vstupy používají Files `purpose=user_data`.

Interní příprava, upload, submit, stahování a lokální zpracování jsou oddělené od stavů API `validating`, `in_progress`, `finalizing`, `completed`, `failed`, `expired`, `cancelling`, `cancelled`. Neznámé procento se nevyrábí. Provozní limit API závisí na účtu; Batch má samostatné queued-token limity a dokumentovaný limit 2000 vytvoření za hodinu. Aplikace nevydává tuto hodnotu za osobní limit účtu.

OS zámek chrání jednu operaci před souběžným spuštěním a uvolní se po pádu procesu. Před submit se zapíše jeho začátek. Neurčitý Batch submit se dohledává přes přesný vstupní soubor a endpoint; bez jediného důkazu se neopakuje. Neurčité živé obrazové volání se automaticky neopakuje, protože API neposkytuje obdobný dohledávací kontrakt. Uživatel musí vyřešit stav původní operace; aplikace nesmí hádat, že nebyla odeslána.

Výsledné soubory se před parsováním archivují bezeztrátově jako binární gzip. Úspěšná položka uloží původní obraz, finální plátno, transformaci a verzi. JSON evidence odkazuje na binární artefakt místo base64; nedochází k zápisu obrázků do textového auditu. Částečný neúspěch zachová hotové panely. Opakovat chybné panely vytváří novou operaci pouze pro chybné položky podle aktuálně uloženého zadání. Obnovit / převzít dokončí lokální ingest a neobjedná znovu hotové výsledky. Stažený archiv dovoluje pokračovat i po expiraci vzdáleného souboru.

Run Bundle používá režim `COMIC`, ID operace a cestu knihovny. Dávky a Historie mohou otevřít komiksovou evidenci. Textové Responses, živé obrazové operace i panelové Image Batch submitty jsou před odesláním svázány s `WORK_ORDER_V2` a společnou SQLite evidencí provider operací; neurčitý submit zůstává ve stavu `submission_unknown` a nesmí se automaticky zopakovat. Provider `usage` se ukládá jako raw API metadata. Program z ní neodvozuje peněžní hodnotu.

## Datový model a migrace

Nová knihovna se vytváří jako SQLite `user_version=2` a tabulka `migrations` eviduje verze 1 i 2. Existující podporovaná knihovna `user_version=1` se transakčně migruje na verzi 2 přidáním textové výrobní osy; neznámou verzi ani neznámou neprázdnou databázi aplikace nepřepisuje. Nepoužívá dřívější provozní databázi aplikace. Tabulky:

- `projects`, `bibles`, `style_refs`: nastavení, jeho snapshoty a verzovaná bible.
- `entities`, `entity_refs`, `entity_revisions`: společná normalizovaná knihovna postav a prostředí, původní i pracovní podklady.
- `comic_documents`: verzované Story, Script, Storyboard a Continuity dokumenty se zdrojovou lineage a provenance.
- `panels`, `prompts`, `bindings`, `panel_versions`: pořadí, verzované AST, FK vazby entit a nedestruktivní výsledky.
- `operations`, `batches`, `batch_items`: obnovitelné operace, provider IDs, stav, snapshot, chyby a custom IDs.
- `assets`, `uploads`, `events`: neměnné soubory s SHA-256, samostatná cache File IDs a audit.

FK jsou zapnuté na každém spojení. Triggery kontrolují vlastníka aktivní bible, entity, promptu a panelové verze; doména navíc kontroluje příslušnost každého vstupního assetu a entity. Optimistická revize brání přepsání souběžných změn. Binární soubory se zapisují přes dočasný soubor, fsync a atomické přejmenování. Pád mezi uložením souboru a DB může zanechat nepoužitý soubor; automatické mazání historických artefaktů se neprovádí.

## Požadavek → operace → implementace → test

| Kontrakt | Operace a implementace | Konkrétní testy |
|---|---|---|
| CML-C01 trvalý projekt a koš | `ComicStore.project/update_project/trash`, `ComicService.duplicate_project` | `test_project_persistence_trash_and_revision`, `test_duplicate_remaps_entities_and_has_no_jobs` |
| CML-C02 strict bible a explicitní volby | `start_bible/_bible`, `normalize_bible` | `test_bible_real_request_contract_and_saved_revision`, `test_bible_explicit_options_always_win` |
| CML-C03 reference postav/prostředí | `import_references/start_entity/_entity` | `test_entity_generated_from_references`, `test_reference_change_invalidates_canonical_entity` |
| CML-C04 atomické entity | `EntityPromptEdit`, `validate_document`, `compile_panel` | `test_atomic_chip_delete_undo_copy_and_roundtrip`, `test_plain_text_never_becomes_entity`, `test_compiler_entity_combinations`, `test_archived_and_foreign_reference_rejected` |
| CML-C05 rozměry a přesná sazba | `PanelFormat`, `postprocess`, `OverlayEditor`, `render_panel` | `test_formats_preserve_exact_target_without_stretch`, `test_text_overlay_renders_and_rejects_overflow`, `test_canvas_edit_and_restore_keep_original` |
| CML-C06 Batch a částečné chyby | `start_panels/_batch/ingest/retry_failed` | `test_batch_partial_retry_and_resume`, `test_unknown_submit_reconciles_without_second_post`, `test_corrupt_image_does_not_discard_successful_panel`, `test_cancel_before_submission_never_calls_provider` |
| CML-C07 editace a obnova | `compile_panel(edit)/store_panel_result/restore_version` | `test_edit_creates_candidate_and_restore_preserves_history`, `test_storage_failure_resumes_from_downloaded_archive` |
| CML-C08 souborová a transakční bezpečnost | `inspect_image`, `asset_path`, `execution_lock` | `test_corrupt_input_never_uploads`, `test_asset_path_cannot_escape_library`, `test_prompt_foreign_key_and_operation_lock` |
| CML-C09 textová výrobní osa | `start_story/start_script/start_storyboard/start_continuity/materialize_storyboard` | `test_story_script_storyboard_continuity_materializes_once`, `test_storyboard_materialization_requires_continuity_pass` |
| CML-C10 skutečné ovládání | `ComicsPage`, `Operations`, `ComicService` | `test_studio_comic_crud_and_panel_save`, `test_chip_selection_replacement_is_atomic` |

Inventář [UI → handler → doména → výsledek → chyba](ui/comic-actions.csv) uvádí související konkrétní kontraktní test, nikoli tvrzení, že každý z nich automatizuje kliknutí na příslušné tlačítko. Nativní Qt průchod včetně exportu ověřuje `test_desktop_end_to_end_with_isolated_provider`.

Testy jsou v `tests/test_comic_domain.py`, `test_comic_recovery.py` a `test_comic_ui.py`. Kontrola mockem dokládá integrační kontrakt, nikoli dostupnost API nebo vizuální konzistenci skutečného modelu. Živá akceptace je výslovně ruční pracovní scénář `scripts/accept_comic.py --workspace <oddělený adresář> --live`, vyžaduje explicitní oprávnění ke skutečným provider requestům a nikdy se nespouští z testů, CI, startu ani validačního tlačítka. Opakované spuštění obnovuje uložené operace. `backend_acceptance=completed` nenahrazuje vizuální kontrolu ani nativní UI akceptaci.

## Obnova provozu

Při chybě úložiště obnovte volné místo a oprávnění, potom zvolte Obnovit / převzít. Při chybě klíče opravte stávající konfiguraci. HTTP 429 nebo serverová chyba neznamenají, že neznámý submit bezpečně selhal. U neznámého submitu nevytvářejte druhou operaci jako automatickou opravu. Chybějící či změněný interní asset obnovte z úplné zálohy; nenahrazujte jej jiným obrázkem pod stejným názvem. Knihovna je určena pro místní souborový systém s transakčními zárukami SQLite a OS zámky. Pro přesun nejprve ukončete místní operace a zkopírujte celou knihovnu, poté změňte její cestu v Nastavení.
