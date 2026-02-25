# FORENSIC AUDIT REPORT

## Executive summary
Audit proběhl v režimu: inventory → read-only audit → cílené opravy jádra (OpenAI wrapper, logování, contracts, receipts DB) → testy.

Hlavní závěr: aplikace je funkční, ale před auditem měla slabší odolnost v HTTP retry/timeout vrstvě, neatomické zápisy run-state/log JSON, slabší DB indexaci receipts a nedostatečně striktní validaci JSON kontraktu.

## Mapování modulů a datových toků (UI → pipeline → OpenAI → LOG/DB)
- Entry point desktop aplikace: `kajovo/app/main.py` (QApplication + MainWindow). (`kajovo/app/main.py:19-36`)
- Orchestrace běhu je v `RunWorker` (`kajovo/core/pipeline.py`).
- OpenAI API wrapper je `OpenAIClient` (`kajovo/core/openai_client.py`).
- Logování run artefaktů: `RunLogger` (`kajovo/core/runlog.py`).
- Evidence pricing/receipts: `ReceiptDB` (`kajovo/core/receipt.py`).

## Detailní nálezy AUD-001…

### AUD-001 [API][P0] OpenAI wrapper bez robustního retry/backoff pro 429/5xx
- Důkaz (před opravou): jediný request bez řízeného retry, bez `Retry-After` respektu.
- Oprava: přidán retry rozhodovač, exponenciální backoff, `Retry-After`, klasifikace síťových timeout/connection chyb a sanitace chybových výpisů. (`kajovo/core/openai_client.py:29-82`)
- Status: **Fixed**.

### AUD-002 [LOG][DATA-LOSS][P1] Neatomické zápisy `run_state.json` a artefaktů
- Důkaz (před opravou): přímé zápisy přes `open(..., "w")` mohly při pádu zanechat partial JSON.
- Oprava: atomický zápis přes temp file + `os.replace`, `fsync`, sjednoceno pro state i `save_json`. (`kajovo/core/runlog.py:66-75`, `kajovo/core/runlog.py:97-129`)
- Status: **Fixed**.

### AUD-003 [SECURITY][LOG][P1] Chybějící redakce citlivých hodnot v run logu
- Důkaz: event/state ukládaly payload bez redakce.
- Oprava: zaveden `_REDACT_KEYS` + rekurzivní redakce dict/list/string payloadů před zápisem. (`kajovo/core/runlog.py:13-22`, `kajovo/core/runlog.py:82-95`, `kajovo/core/runlog.py:111-114`)
- Status: **Fixed**.

### AUD-004 [DB][P1] Receipts DB bez indexů na deduplikační/lookup pole
- Důkaz: chyběly indexy `run_id`, `response_id`, `batch_id`.
- Oprava: doplněny indexy a WAL režim + timeout pro robustnější souběh zapisovačů. (`kajovo/core/receipt.py:30-33`, `kajovo/core/receipt.py:60-65`)
- Status: **Fixed**.

### AUD-005 [API][DX][P1] `parse_json_strict` akceptovalo i non-object JSON / nejednoznačný strict contract
- Důkaz: strict kontrakt vracel i jiné JSON typy.
- Oprava: explicitní vynucení JSON object + fallback extrakce embedded objektu. (`kajovo/core/contracts.py:33-53`)
- Status: **Fixed**.

### AUD-006 [UI-FREEZE][RACE][P1] Násilné `worker.terminate()` v UI shutdown cestě
- Důkaz: může přerušit worker v nekonzistentním stavu.
- Evidence: (`kajovo/ui/mainwindow.py:1554-1562`)
- Návrh: preferovat cooperative stop + bounded wait, terminate jen jako poslední fallback s explicitním rollbackem/state markerem.
- Status: **Remaining**.

### AUD-007 [DX][P2] Velké množství `except Exception: pass` v UI cestách
- Důkaz: chyby mohou být umlčené bez telemetry.
- Evidence: (`kajovo/ui/mainwindow.py:1560-1591`, `kajovo/ui/mainwindow.py:1622-1641`)
- Návrh: logovat minimálně debug event + typ chyby.
- Status: **Remaining**.

### AUD-008 [SECURITY][P1] SSH diagnostika je správně strict-host-key, ale pin je volitelný
- Důkaz: `RejectPolicy` + `load_system_host_keys()` + optional pin env.
- Evidence: (`kajovo/core/diagnostics/ssh.py:27-54`)
- Návrh: v enterprise profilu vyžadovat pin mandatory.
- Status: **Remaining (policy hardening)**.

### AUD-009 [SECURITY][P1] Repair script execution má explicitní potvrzení + SHA256 preview (pozitivní nález)
- Důkaz: dvojité potvrzení, zobrazení README, SHA256, shell=False.
- Evidence: (`kajovo/ui/mainwindow.py:2311-2371`)
- Status: **Verified / Good practice**.

## Sekce po doménách

### UI
- Worker orchestrace přes `QThread` je implementována, ale ve stop path je místy agresivní terminate fallback. (`kajovo/ui/mainwindow.py:1554-1562`)

### Pipeline
- `split_text` pro chunking je jednoduchý a deterministický. (`kajovo/core/pipeline.py:24-35`)
- Rezervy: více explicitních error markerů místo silent-pass v některých blocích.

### OpenAI
- Wrapper nyní pokrývá timeouty, retry/backoff a sanitaci chyb. (`kajovo/core/openai_client.py:29-82`)

### Vector Stores
- CRUD endpointy jsou přítomné v wrapperu. (`kajovo/core/openai_client.py:150-186`)

### Pricing fetch
- Pricing tabulka má cache + fallback režim. (`kajovo/core/pricing.py:57-116`, `kajovo/core/pricing.py:140-153`)

### DB receipts
- Schéma a indexy jsou explicitní, WAL zapnuto. (`kajovo/core/receipt.py:7-33`, `kajovo/core/receipt.py:60-65`)

### Logging
- Run logger nyní zapisuje atomicky a rediguje citlivé klíče. (`kajovo/core/runlog.py:66-75`, `kajovo/core/runlog.py:82-99`)

### Security
- Secrets v settings se neukládají plaintextem (password fields nulovány při save). (`kajovo/core/config.py:136-144`)
- SSH používá RejectPolicy + host-key pin volitelně. (`kajovo/core/diagnostics/ssh.py:27-54`)

### Diagnostics (SSH/repair)
- Repair skript je chráněn potvrzením a hash disclosure. (`kajovo/ui/mainwindow.py:2342-2354`)

### Build/Run scripts
- `scripts/install.*` a `scripts/run.*` jsou konzistentní s `.venv` workflow. (`scripts/install.ps1:1-14`, `scripts/run.ps1:1-5`, `scripts/install.bat:1-21`, `scripts/run.bat:1-10`)

### Tests
- Přidány deterministické unit testy pro požadované oblasti bez volání reálného OpenAI. (`tests/test_core_audit_regressions.py:16-103`)

## ZÁVĚR: PULS
- **P (Připravenost): 82 %** – klíčové runtime slabiny zpevněny, testy zelené.
- **U (Úplnost): 78 %** – hlavní P0/P1 body pokryty, zbývá UI error telemetry a lifecycle hardening workeru.
- **L (Limitace):** bez `OPENAI_API_KEY` nešlo end-to-end ověřit reálné Responses/Files/Batches; v tomto prostředí také nelze spustit GUI kvůli chybějícím `libGL.so.1`.
- **S (Stabilita): 84 %** – retry/backoff, atomické logování, WAL receipts; zbývá lifecycle refinements v UI stop flow.
