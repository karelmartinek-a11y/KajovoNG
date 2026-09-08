# Blokace dokončení auditu KájovoNG

## Rozsah

Repozitář je určen pro desktopovou automatizaci OpenAI (`kajovo`, `kajovong`).
Kopie `kajovospend` je podle výslovného pokynu vlastníka mimo rozsah a byla
odstraněna spolu se spouštěčem, OCR skripty a vlastní dokumentací. Instalace
je přesměrována na `kajovo.app.main:main`.

## Stav ověření

Úplný řádkový audit všech zdrojů není dokončen. Není vydáno potvrzení bezchybnosti
ani finální SSOT. Žádná úplná iterace auditu dosud nebyla uzavřena; limit 50 iterací
nebyl vyčerpán. Tento blocker zaznamenává překážku předepsaného vstupního úklidu.

Automatická kontrola zápisu dvakrát odmítla odstranění testů. První návrh odstraňoval
celou historickou sadu; byl stažen. Druhý návrh ponechával testy automatizace a
odstraňoval pouze soubory odkazující na `kajovospend`, ale byl rovněž odmítnut.
Všechny původní testy proto zůstávají beze změny. Odstranění následujících souborů
vyžaduje vyřešení tohoto odmítnutí; aplikace, kterou testují, již byla na pokyn
vlastníka odstraněna:

- `tests/test_document_loader_truth.py`
- `tests/test_kdgs_gate.py`
- `tests/test_kdgs_widgets.py`
- `tests/test_logging_schema.py`
- `tests/test_main_window_runtime.py`
- `tests/test_openai_client_truth.py`
- `tests/test_openai_pipeline_source_flow.py`
- `tests/test_progress_dialog.py`
- `tests/test_runtime_workflow_smoke.py`
- `tests/test_smoke.py`
- `tests/test_ui_contract.py`

Uvedené testy importují nebo přímo kontrolují cesty odstraněného `kajovospend`.
Jejich odstranění ruší pouze tuto historickou kontrolu; regresní testy `kajovo`
(`test_core_audit_regressions.py`, `test_security_regressions.py`) zůstávají zachované.
Do vyřešení vazeb není stávající kompletní testovací sada průchozí.

Instalace závislostí do lokálního prostředí byla přerušena zprávou
`network approval was cancelled before a decision was returned`. Nebyla dokončena
ani původní instalace, ani následná instalace již zúženého balíku. Běh PySide6,
integrační testy a plná sada proto nejsou potvrzeny.

## Opravy doložené cíleným ověřením

- `utils.safe_join_under_root`: odmítání absolutních cest, cest mimo kořen,
  úniku přes existující symlink a neplatných názvů Windows.
- `contracts.validate_paths`: kontrola typu položek a kolizí názvů bez ohledu na
  velikost písmen.
- `pipeline.RunWorker._save_out_files`: validace celé sady cest a typů obsahu před
  prvním zápisem; použití chráněného spojení cest; úplné SHA-256 před a po zápisu.
- `filescan`: zahrnutí kořenových adresářů do globů `**/`, rozpoznání UTF-8 textů,
  vyřazení souborových symlinků, kontrola tajemství v celém souboru, úplné SHA-256
  a vyřazení souborů, jejichž obsah nebylo možné přečíst.
- `RunLogger` a `CascadeLogger`: validace identifikátoru běhu a odstranění
  nežádoucího vytváření adresáře pojmenovaného identifikátorem běhu v CWD.

Provedeno: šest testů v `tests/test_filesystem_boundaries.py`, kompilace Python
modulů `kajovo` a `kajovong`, kontrola `git diff --check` a cílené ověření obou
loggerů v dočasném adresáři. Všechny tyto kontroly uspěly.

## Otevřené nálezy pro pokračování

- `core/diagnostics/windows.py` odkazuje na chybějící
  `diagnostics/windows_collect.ps1` a nekontroluje návratový kód procesu.
- `core/diagnostics/ssh.py` porovnává proměnnou pojmenovanou SHA256 s MD5
  fingerprintem a nezavírá spojení ve všech chybových větvích.
- `core/openai_client.py` má výchozí JSON Content-Type v session i při multipart
  uploadu, při opakování uploadu nevrací proud na začátek a při chybě SDK může
  opakovat již odeslanou operaci jinou transportní cestou.
- `core/cascade_pipeline.py` ukládá manifest před kontrolou přítomnosti všech
  očekávaných souborů; validace JSON schématu je pouze částečná.
- `core/pipeline.py::_zip_in_dir` obchází bezpečnostní filtr `scan_tree`.

Tyto nálezy zatím nejsou označeny za opravené. Je nutné dokončit jejich analýzu,
opravy a ověření, zbývající zdrojový kód, úklid neaktuální dokumentace a SSOT,
poté opakovat celý audit podle původního zadání.
