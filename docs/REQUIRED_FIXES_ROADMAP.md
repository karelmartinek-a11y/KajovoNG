# REQUIRED FIXES ROADMAP

## Priority roadmap (P0/P1/P2)

### P0
1. **OpenAI transport hardening (retry/backoff/timeout/error mapping)**
   - Dopad: odolnost proti 429/5xx a transient síťovým chybám.
   - Odhad: **M**
   - Závislosti: žádné.
   - Acceptance criteria:
     - 429/5xx se retryují s backoff.
     - Timeout/connection chyby mají deterministické mapování na `OpenAIError`.
     - Test pokrývá retry i hard fail.
   - AUD map: **AUD-001**
   - Stav: **Done**

### P1
2. **Atomické a redigované run logování**
   - Dopad: ochrana proti partial JSON + leakage secretů.
   - Odhad: **M**
   - Závislosti: žádné.
   - Acceptance criteria:
     - `run_state.json` a `save_json` používají atomic write.
     - klíče `api_key/password/token` jsou redigovány.
   - AUD map: **AUD-002, AUD-003**
   - Stav: **Done**

3. **Receipts DB hardening (indexy + WAL)**
   - Dopad: rychlejší deduplikace a stabilnější write contention.
   - Odhad: **S**
   - Závislosti: žádné.
   - Acceptance criteria:
     - indexy na `run_id/response_id/batch_id`.
     - WAL + timeout při connect.
   - AUD map: **AUD-004**
   - Stav: **Done**

4. **Strict JSON contract vynucení**
   - Dopad: menší riziko špatného překladu model output do souborových operací.
   - Odhad: **S**
   - Závislosti: žádné.
   - Acceptance criteria:
     - `parse_json_strict` akceptuje pouze objekt.
     - embedded JSON object je podporován.
   - AUD map: **AUD-005**
   - Stav: **Done**

5. **UI worker lifecycle hardening (limit terminate fallback)**
   - Dopad: menší riziko nekonzistence při násilném ukončení threadu.
   - Odhad: **M**
   - Závislosti: audit současného cancellation flow.
   - Acceptance criteria:
     - preferovat cooperative stop.
     - explicitní state marker při force kill.
   - AUD map: **AUD-006**
   - Stav: **Planned**

6. **Zrušit silent `except: pass` v kritických UI trasách**
   - Dopad: lepší diagnostičnost produkčních problémů.
   - Odhad: **M**
   - Závislosti: logging style guide.
   - Acceptance criteria:
     - kritické bloky mají minimálně debug/error event.
   - AUD map: **AUD-007**
   - Stav: **Planned**

### P2
7. **SSH policy režim „pin required“ (volitelné hardening profile)**
   - Dopad: vyšší bezpečnost vzdálené diagnostiky.
   - Odhad: **S**
   - Závislosti: dokumentace rollout.
   - Acceptance criteria:
     - konfigurovatelný režim vyžadující `KAJOVO_SSH_HOSTKEY_SHA256`.
   - AUD map: **AUD-008**
   - Stav: **Planned**

## AUD → commit/file mapping
| AUD | Stav | Soubor(y) |
|---|---|---|
| AUD-001 | Done | `kajovo/core/openai_client.py` |
| AUD-002 | Done | `kajovo/core/runlog.py` |
| AUD-003 | Done | `kajovo/core/runlog.py` |
| AUD-004 | Done | `kajovo/core/receipt.py` |
| AUD-005 | Done | `kajovo/core/contracts.py` |
| AUD-006 | Planned | `kajovo/ui/mainwindow.py` |
| AUD-007 | Planned | `kajovo/ui/mainwindow.py` |
| AUD-008 | Planned | `kajovo/core/diagnostics/ssh.py` |
| AUD-009 | Verified | `kajovo/ui/mainwindow.py` |
