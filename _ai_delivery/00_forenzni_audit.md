# 00_forenzni_audit

## Exekutivní shrnutí

Audit byl proveden nad ZIP repozitářem `KajovoSpendNG-main (5).zip` jako jediným zdrojem pravdy. Kód obsahuje reálné implementace pro čtení souborů, OCR, OpenAI volání i zápis do working/production SQLite databází; nenašel jsem explicitní stuby, fake handlery ani simulované „hotové“ výsledky vydávané za produkční běh. Reálné důkazní body jsou v `kajovospend/integrations/openai_client.py`, `kajovospend/ocr/document_loader.py`, `kajovospend/processing/service.py` a `kajovospend/persistence/repository.py`.

Současně ale repozitář neobsahuje dostatečný důkazní rámec, aby šlo tvrzení o „reálném“ chování bezpečně doložit provozně. Chybí integrační testy pro OpenAI/OCR/DB tok, chybí forenzní telemetry pro AI/OCR větve, progress UI neukazuje ETA ani detailní význam kroku a runtime logování je rozdělené mezi aplikaci a projektový repo log bez jednotného schématu a bez úplné časové stopy. Výsledkem je, že architektura je reálná, ale auditovatelnost a důkaznost jsou jen částečné.

## Potvrzená fakta

1. **OpenAI integrace používá oficiální endpointy OpenAI.**
   - `MODELS_URL = 'https://api.openai.com/v1/models'` a `RESPONSES_URL = 'https://api.openai.com/v1/responses'` v `kajovospend/integrations/openai_client.py:30-31`.
   - Reálné HTTP volání probíhá přes `requests.get(...)` a `requests.post(...)` v `kajovospend/integrations/openai_client.py:38-42` a `88-93`.

2. **Čtení souborů je reálné a jde z disku.**
   - Textové soubory se čtou přes `source_path.read_text(...)` v `kajovospend/ocr/document_loader.py:101-114`.
   - PDF se validují přes hlavičku `read_bytes()[:5]`, poté se otevírají přes `PdfReader` v `kajovospend/ocr/document_loader.py:116-131`.
   - Obrázky se otevírají přes Pillow `Image.open(...)` v `kajovospend/ocr/document_loader.py:195-203`.
   - Tesseract se volá jako externí proces `subprocess.run([tesseract, ...])` v `kajovospend/ocr/document_loader.py:288-301`.

3. **OCR pipeline používá skutečný Tesseract a PDF rasterizaci.**
   - PDF bez textové vrstvy se rasterizují přes `pypdfium2` a následně OCR přes Tesseract v `kajovospend/ocr/document_loader.py:205-241`.
   - Obrázky jdou do OCR přes `_ocr_image_path()` a `_run_tesseract()` v `kajovospend/ocr/document_loader.py:195-203` a `288-301`.

4. **Výsledky se zapisují do working DB a při promotion do production DB.**
   - Uložené stránky a OCR text se zapisují přes `replace_document_pages()` a `replace_page_text_blocks()` v `kajovospend/processing/service.py:751-784`, implementace v `kajovospend/persistence/repository.py:658-699`.
   - Vybraná pole a dodavatel se zapisují do `processing_documents` a `processing_suppliers` přes `update_processing_document()` a `update_processing_supplier()` v `kajovospend/persistence/repository.py:823-856`.
   - Položky se zapisují přes `replace_processing_items()` v `kajovospend/persistence/repository.py:888-904`.
   - Promotion zapisuje `final_documents`/`final_items` do `prod_db` v `kajovospend/persistence/repository.py:3112-3218`.

5. **Dual-DB separace je reálně hlídaná.**
   - Guard proti stejnému souboru je v `kajovospend/persistence/project_context.py:34-45`.
   - `separation_report()` kontroluje cesty, odlišné factory a únik tabulek mezi DB v `kajovospend/persistence/repository.py:2026-2041`.
   - `ProjectService.validate_project()` tuto kontrolu skutečně spouští v `kajovospend/project/project_service.py:95-100`.

6. **Import a zpracování nejsou v hlavním UI threadu.**
   - `BackgroundWorker` běží v `QThread` v `kajovospend/ui/main_window.py:80-102` a `2273-2292`.
   - `start_import()` a `process_pending_attempts()` se spouští přes background task z UI v `kajovospend/ui/main_window.py:2096-2103`.

7. **Progress dialog existuje, ale je minimální.**
   - `TaskProgressDialog` má jen message label, progress bar a storno v `kajovospend/ui/dialogs/forms.py:771-799`.
   - Přijímá pouze `current`, `total`, `message`; ETA ani procentní text nevytváří.

8. **Aplikace už používá dvě logovací větve.**
   - Globální JSON logging přes `configure_logging()` v `kajovospend/diagnostics/logging_setup.py:10-40`.
   - Projektové logy `runtime.log`, `decisions.jsonl` a DB tabulka `operational_log` v `kajovospend/persistence/repository.py:44-45` a `3222-3233`.

9. **Existující testy jsou převážně contract/gate/UI smoke testy.**
   - Asset/UI contract: `tests/test_kdgs_gate.py`, `tests/test_ui_contract.py`.
   - Qt runtime smoke: `tests/test_kdgs_widgets.py`, `tests/test_main_window_runtime.py`.
   - Settings smoke: `tests/test_smoke.py`.

10. **V repozitáři nebyly grepem nalezeny explicitní fake/mock/stub implementace v produkčním toku.**
   - Hledání `TODO|FIXME|HACK|mock|stub|fake|simulate|placeholder` nenašlo produkční simulace; jediné nálezy byly SQL placeholdery v `kajovospend/persistence/repository.py:517-528`.

## Nepotvrzená rizika / limity auditu

1. **Live OpenAI request nebyl v auditu skutečně proveden.**
   - Nebyl k dispozici auditní API klíč ani řízený test fixture s očekávaným výsledkem.
   - Kódová cesta je potvrzená, provozní běh ne.

2. **OCR kvalita a výtěžnost nebyla ověřena na reálném korpusu.**
   - Nebyl dodán corpus/ZIP vzorků dokladů.
   - V auditu byla ověřena implementace, nikoli přesnost na datech.

3. **Plný Qt runtime test suite nebyl spuštěn kvůli chybějícímu `PySide6` v auditním prostředí.**
   - `pytest -q` skončil na `ModuleNotFoundError: No module named 'PySide6'` při collectu `tests/test_kdgs_widgets.py` a `tests/test_main_window_runtime.py`.

4. **Tesseract nebyl v auditu volán nad reálným souborem.**
   - Kódová větev existuje, ale nebyla exekučně potvrzena na fixture.

## Problémy podle závažnosti

### P0

1. **Chybí důkazní integrační testy pro tvrzení „OpenAI/OCR/DB je reálný zdroj pravdy“.**
   - Není žádný test, který by prověřil cestu `soubor -> OCR/load -> extraction -> DB -> promotion`.
   - Nejsou testy pro `openai_client.py`, `document_loader.py`, `processing/service.py`, promotion ani strukturované logování.
   - Dopad: reálnost systému je z kódu pravděpodobná, ale neprokazatelná testy.

2. **Forenzní telemetry pro OpenAI/OCR tok nejsou dostatečné pro plnou rekonstrukci případu.**
   - Logují se generické eventy pokusů, ale chybí model, endpoint, latence requestu, počet stran poslaných do AI, OCR command metrics, fingerprint vstupu, per-step timing a deterministické outcome codes.
   - Dopad: nelze bezpečně prokázat, co přesně se stalo při jednotlivém vytěžení.

### P1

3. **Progress dialog neplní požadavek na podrobný stav, ETA a procentní význam.**
   - `TaskProgressDialog` nemá ETA, elapsed, procentní text, krok pipeline ani detailní význam stavu (`kajovospend/ui/dialogs/forms.py:771-799`).
   - Controller nese jen `current/total/label/mode` (`kajovospend/application/controller.py:565-580`).
   - Processing service reportuje pouze file-level popisky typu název souboru (`kajovospend/processing/service.py:138`, `169`, `192`, `216`).

4. **Některé síťové operace stále běží synchronně z UI akce.**
   - `on_load_openai_models()` volá `controller.fetch_openai_models()` přímo bez background workeru (`kajovospend/ui/main_window.py:1838-1849`).
   - Dopad: UI může při síťové latenci zamrzat.

5. **OCR konfigurace je statická a není doložena benchmarkem jako optimální.**
   - Tesseract běží fixně s `-l ces+eng` a timeout 60 s (`kajovospend/ocr/document_loader.py:288-301`).
   - Chybí evidence adaptace podle typu vstupu, DPI heuristiky, confidence thresholdy a reálné tuning výstupy napojené do runtime.

6. **App-level logging a project-level logging jsou oddělené a nekorelované.**
   - `configure_logging()` zapisuje do user log dir aplikace (`kajovospend/app/bootstrap.py:26-27`, `kajovospend/app/config.py:15-28`).
   - Projektové události jdou do `logs/runtime.log`, `logs/decisions.jsonl` a `operational_log` (`kajovospend/persistence/repository.py:44-45`, `3222-3233`).
   - Dopad: stopa běhu je fragmentovaná.

### P2

7. **Dokumentace nepokrývá AI extraction flow, OCR flow, logging schema ani progress semantics.**
   - `README.md` a `docs/*` pokrývají hlavně dual-DB a KDGS UI, ne detailní processing pipeline.
   - Dopad: provozní ověřování a onboarding jsou slabé.

8. **Textová vrstva je nekonzistentní: část UI používá diakritiku, část kódu a docs ASCII transliteraci.**
   - Nejde o potvrzené mojibake, ale o nekonzistentní lokalizační/text quality stav.
   - Příklady: `README.md:1-40`, `startspend.bat:7-8`, `kajovospend/integrations/openai_client.py:60-68` versus UI texty s diakritikou v `kajovospend/ui/dialogs/forms.py:771-799`.

9. **Build/reproducibility vrstva je základní.**
   - Jsou přítomné bootstrap/build skripty, ale není lockfile, CI config ani explicitní ověření celého toolchainu.

### P3

10. **Repo obsahuje dlouhé monolitické soubory se zvýšeným review rizikem.**
   - `kajovospend/persistence/repository.py` má přes 3200 řádků; `kajovospend/processing/service.py` je rovněž rozsáhlý.
   - Dopad: zvyšuje se riziko regresí a obtížnost cíleného testování.

## Přesný seznam nalezených bottlenecků

1. **Synchronous model fetch z UI threadu**
   - `kajovospend/ui/main_window.py:1838-1849`
   - Síťové volání bez background workeru.

2. **Velký seriový processing file-by-file bez jemnějšího progress modelu**
   - `kajovospend/processing/service.py:110-170` a `172-217`
   - Progress je jen na úrovni souborů; uvnitř dlouhé operace nehlásí fáze.

3. **Opakovaná těžká serializace kontextu pro OpenAI**
   - `kajovospend/processing/service.py:580-698`
   - Skládá rozsáhlý textový kontext z OCR, field candidates a line items.

4. **Dodatečné renderování PDF do obrázků pro OpenAI**
   - `kajovospend/ocr/document_loader.py:243-270`
   - I po OCR/raster průchodu se stránky znovu renderují do data URL.

5. **Velký počet krátkých SQLite spojení/commitů**
   - `ProjectRepository.work_connection()` a `prod_connection()` otevírají nové spojení pro velké množství malých operací (`kajovospend/persistence/repository.py:55-59`).
   - Pro audit to není nutně bug, ale je to kandidát na profilaci a batching.

6. **Monolitický finalize/promotion tok**
   - `kajovospend/persistence/repository.py:3112-3218`
   - Jeden velký transakční blok s více odpovědnostmi zvyšuje latenci i riziko obtížného troubleshooting.

## Přesný seznam nalezených míst s kódováním, encoding a lokalizačními vadami

### Potvrzené vady typu mojibake / invalid UTF-8

- **Nenalezeno.**
- V textových souborech kontrolovaných během auditu nebyl nalezen invalidní UTF-8 decode failure.
- Testy navíc explicitně hlídají známé mojibake patterny v `tests/test_kdgs_gate.py:67-86` a `tests/test_ui_contract.py:103-115`.

### Potvrzené vady textové konzistence / lokalizace

1. **README a část docs jsou psány česky bez diakritiky, zatímco UI používá diakritiku.**
   - `README.md:1-40`
   - Symptom: nekonzistentní uživatelský a dokumentační jazyk.
   - Pravděpodobná příčina: historické ASCII-only psaní, nikoli poškozené kódování.

2. **Start skript Windows vrací ASCII-only české chyby.**
   - `startspend.bat:7-8`
   - Symptom: textová nekonzistence vůči UI.
   - Pravděpodobná příčina: konzervativní BAT skript bez důrazu na lokalizační sjednocení.

3. **OpenAI prompt a velká část processing větve používá transliterovanou češtinu bez diakritiky.**
   - `kajovospend/integrations/openai_client.py:60-68`
   - `kajovospend/processing/service.py:329-437`, `481-563`
   - Symptom: smíšená textová kvalita napříč produktem a auditními hláškami.
   - Pravděpodobná příčina: ručně psané runtime stringy bez následné jazykové normalizace.

## Rozpory mezi kódem a dokumentací

1. **README a dual-db docs silně popisují separaci DB, ale téměř vůbec nepokrývají AI/OCR/persistenční tok.**
   - Kód tento tok má (`processing/service.py`, `ocr/document_loader.py`, `integrations/openai_client.py`), dokumentace ne.

2. **KDGS dokumenty deklarují „forenzní uvedení do souladu“ pro UI vrstvu, ale neřeší provozní forenzní logging hlavního processing pipeline.**
   - `docs/kdgs_finalni_forenzni_report.md` mluví hlavně o UI/brand/test gate, ne o import/OCR/OpenAI/DB.

## Přesné příkazy a výsledky použité v auditu

```bash
unzip -q '/mnt/data/KajovoSpendNG-main (5).zip' -d /tmp/ksaudit
find /tmp/ksaudit/KajovoSpendNG-main -maxdepth 2 -mindepth 1 | sort
rg -n "TODO|FIXME|HACK|XXX|pass #|NotImplemented|raise NotImplementedError|mock|stub|fake|simulate|placeholder" /tmp/ksaudit/KajovoSpendNG-main
python -m pytest -q
python -m pytest -q tests/test_kdgs_gate.py tests/test_smoke.py
python -m pytest -q tests/test_ui_contract.py
python -m compileall -q kajovospend
python -m pip show PySide6
```

Výsledky:
- `pytest -q` → FAIL při collectu kvůli chybějícímu `PySide6`
- `pytest -q tests/test_kdgs_gate.py tests/test_smoke.py` → PASS (`7 passed`)
- `pytest -q tests/test_ui_contract.py` → PASS (`9 passed`)
- `python -m compileall -q kajovospend` → PASS
- `python -m pip show PySide6` → package not found v auditním prostředí
