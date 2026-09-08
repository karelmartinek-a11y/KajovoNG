# KájovoNG

Desktopová aplikace v Pythonu a PySide6 pro práci s OpenAI Responses API, soubory, vlastními kaskádami a dávkami.

Kanonická specifikace systému: [SSOT](docs/SSOT.md).

## Spuštění na Windows

Vyžaduje Python 3.12 nebo novější a Git LFS pro fonty.

```powershell
git lfs pull
.\scripts\install.ps1
.\scripts\run.ps1
```

API klíč nastavte v aplikaci v sekci API-KEY na záložce SETTINGS nebo v proměnné prostředí `OPENAI_API_KEY`. Klíč se nevkládá do repozitáře. Běhy, ruční Probe a získání cen modelem mohou být zpoplatněné.

## Vývoj a ověření

```powershell
.venv\Scripts\python.exe -m pip install -e ".[dev]"
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m ruff check --select F,B,E9 kajovo kajovong utf8nobom tests Build
.venv\Scripts\python.exe -m pip check
```

Testy používají náhrady API a nevyžadují skutečný klíč. Sestavení: [Build](Build/README.md).

Logy obsahují zadání, odpovědi a případně zdrojový kód. Redakce známých tajných polí není šifrování ani úplná anonymizace. Adresář LOG, databázi a výstupy chraňte před nepovolaným přístupem.

Licence projektu: [MIT](LICENSE). Licence závislostí a prostředků: [oznámení třetích stran](THIRD_PARTY_NOTICES.md).
