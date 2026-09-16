# Architecture hardening 2026-09-16

Tento dokument eviduje uzavřený refaktor vzniklý z forenzního auditu repozitáře. Nemění produktové kontrakty v `docs/SSOT.md`; popisuje pouze technické hranice, které nyní vynucuje zdrojový kód a CI.

## Runtime hranice

- Produkční desktopové UI je výhradně `kajovo.studio` a vstupní bod `kajovo.app.main` používá `kajovo.studio.application.create_window`.
- `kajovo.desktop` zůstává pouze zdrojový regresní referenční materiál. Není součástí instalovaného ani distribuovaného balíku a produkční `app`/`studio` jej nesmí importovat.
- Historický `scripts/render_ui.py` je kompatibilní vstup, který deleguje na produkční `scripts/render_studio.py`.
- `kajovo.core` nesmí záviset na žádném UI balíku.

## Run vrstva

`kajovo.core.pipeline.RunWorker` zůstává kompatibilním Qt adaptérem, ale oddělené odpovědnosti jsou přesunuty do `kajovo.core.runs`:

- `config.py` — serializovatelná konfigurace běhu bez Qt,
- `delivery.py` — bezpečný zápis OUT s path validací, konfliktními hash guardy, atomickým zápisem a per-file evidencí,
- `polling.py` — vector-store polling s monotónním timeoutem, rozlišením provider chyb a omezením po sobě jdoucích selhání,
- `observability.py` — best-effort evidence/state/signal porty bez tichého spolknutí chyby a bez logování citlivého payloadu.

Nová run vrstva je záměrně Qt-free a má samostatnou Python 3.12 quality lane s Ruff a mypy.

## Secrets

OpenAI API klíč se persistuje přes OS credential storage (`keyring`). Starý Windows `HKCU\\Environment\\OPENAI_API_KEY` je pouze migrační zdroj. Migrace používá write → readback → delete; při neúspěchu se legacy hodnota nemaže. Explicitní smazání je reprezentováno interním sentinel stavem, aby se nemohl obnovit stale klíč ze zděděného prostředí.

## CI a lokální guardy

- Plná regrese běží na Windows / Python 3.13.
- Architektonická lane běží na Python 3.12 a kontroluje `core.runs` pomocí širšího Ruff profilu, mypy a cílených testů.
- Plný testovací běh měří coverage a provádí `pip check`; `pip-audit` zatím slouží jako měřený baseline a není blokující gate.
- Pre-commit navíc kontroluje merge konflikty, privátní klíče, nadměrně velké nové soubory a stejné architektonické kontrakty nové run vrstvy.

## Záměrně neprovedené administrativní kroky

Branch protection pro `main` je nastavení repozitáře, nikoli změna zdrojového kódu. Po merge má být na GitHubu administrativně zapnuto alespoň: zákaz force-push, required `ci` a merge pouze po úspěšném CI.

Release provenance attestation nebyla do workflow přidána, protože dostupný GitHub App token nemá oprávnění `workflows` pro bezpečný zápis změny release workflow. Stávající release pipeline proto zůstává beze změny; nadále používá exact-SHA CI gate, manifesty a SHA-256 kontroly.
