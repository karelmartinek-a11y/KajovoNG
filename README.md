# KájovoHotel — desktop orchestrátor pro OpenAI Responses

Tento projekt implementuje desktop aplikaci **Kája** pro Windows 10/11 (PySide6), která umí:
- Generovat projekt do OUT (A: GENERATE)
- Upravit existující projekt (B: MODIFY) s volitelným `file_search` (vector store), fallback bez tools
- Spouštět QA dotazy (QA)
- Rychle vygenerovat jediný soubor bez kaskády (QFILE) — jeden request, OUT povinný, volitelný IN/Response ID
- Spustit dávkový režim (C: SEND AS C / Batch API)
- Ukládat logy per-run do `LOG/RUN_.../` + evidenci nákladů do SQLite (receipts)
- Načíst dostupné OpenAI modely a **pro každé** (best-effort) otestovat kompatibility: `previous_response_id`, `temperature`, `tools/file_search` (uloží do cache)

## Rychlý start (Windows)
1) Instalace:
- `scripts\install.bat` (nebo `scripts\install.ps1`)
2) Nastav API klíč:
- v aplikaci tlačítko **API-KEY** (uloží do `OPENAI_API_KEY`; citlivé údaje aplikace ukládá mimo plaintext JSON přes OS keyring / env fallback)
3) Spuštění:
- `scripts\run.bat` (nebo `scripts\run.ps1`)

## Modely a kompatibility (v2)
Aplikace po nastavení API-KEY:
- načte dostupné modely (`list_models`)
- otestuje kompatibility a uloží je do: `cache/model_capabilities.json`

Poznámky k probe:
- „Probe models“ = force-probe všech modelů (bez TTL).
- Auto-probe běží jen pro chybějící/zastaralé záznamy (TTL 7 dní).
- Kompatibilitu `previous_response_id` se aplikace snaží detekovat bezpečně: za „unsupported“ označí jen model,
  u kterého server **explicitně** odmítne parametr (transient chyby 429/5xx ji nesmí shodit na false).
- Pokud máš historicky uloženou špatnou detekci, smaž `cache/model_capabilities.json` a spusť probe znovu.

„Find model“:
- vyfiltruje modely podle požadovaných funkcí (cascade/temperature/file_search).

## Long prompt (Zadání >150 000 znaků)
Pokud je prompt delší než 150 000 znaků:
- aplikace provede ingest krok **A0** po CHUNKY (20 000 znaků)
- následná kaskáda A1/A2/A3 nebo B1/B2/B3 navazuje přes `previous_response_id` z posledního A0 kroku
- tím se zachová kontinuita na straně OpenAI a zároveň se dodrží limit velikosti requestu

## VERSING
Pokud je zapnutý **VERSING**, před první změnou v OUT se vytvoří snapshot složka:
`<OUT_BASENAME><DDMMYYYYHHMM>` (např. `MyProj090120261030`)

## Logování
Každý RUN má složku: `LOG/RUN_DDMMYYYYHHMM_XXXX/`:
- `events.jsonl` (události)
- `run_state.json`
- `requests/*.json`, `responses/*.json`, `manifests/*.json`

## Náklady (Pricing / Receipts)
- Aplikace ukládá token usage z response do `kajovo.sqlite` (tabulka `receipts`).
- Cena se počítá z cache tabulky v `cache/price_table.json` (pokud existuje) nebo z builtin fallback.

## Poznámky
- Pokud není dostupný `file_search` / vector store, MODIFY může běžet bez tools (pouze s přiloženými file_id).
- Program je navržen tak, aby byl robustní: retry + circuit breaker, logování request/response.

## Vector Stores panel
Záložka **VECTOR STORES** umožní:
- Refresh (list)
- Create / Delete vector store
- List files ve vybraném store
- Add file_id do store
- Remove soubor ze store


## Security hardening
- Citlivé údaje (SMTP/SSH hesla) se neukládají do `kajovo_settings.json`; používá se OS keyring (Credential Manager/Keychain/libsecret) nebo env fallback.
- Repair skripty v Diagnostics OUT jsou defaultně OFF a vyžadují explicitní potvrzení, zobrazení obsahu `readmerepair.txt` a SHA256 skriptu.
- SSH diagnostika používá strict host key checking (`RejectPolicy`) a volitelný pin přes `KAJOVO_SSH_HOSTKEY_SHA256`.

## Virtual environment
- Projekt používá jednotně `.venv` (instalace i run skripty). Nepoužívejte paralelní `venv` pro stejný checkout.

## Pricing
- Primární zdroj je oficiální stránka OpenAI pricing (`https://openai.com/api/pricing/`).
- Pokud parsování selže, aplikace explicitně označí data jako **neověřeno/odhad** a použije fallback ceník.

## Rotace secrets (doporučeno ihned)
1. Zneplatni původní OpenAI API klíč a vytvoř nový.
2. Změň SMTP heslo/app-password.
3. Změň SSH hesla/klíče a ověř host key fingerprint.
4. Vyčisti lokální pracovní soubory (`kajovo_settings.json`, `kajovo_state.json`, `*.sqlite`, `LOG/`, `cache/`) mimo git historii.


## Architecture diagrams
- docs/ARCHITECTURE.md
- docs/diagrams/system-context.mmd
- docs/diagrams/main-flow.mmd
- docs/diagrams/key-sequence.mmd
- docs/diagrams/components.mmd

## Testing
- `python -m unittest discover -s tests -v`

## Baseline po Forenzním Reborne Auditu
- 27. února 2026 se provedla konzolidace vzdálených větví do `main`; větve byly zpracovány kronologicky podle committer date a `main` nyní představuje novou forenzní baseline.
