# Sestavení

Požadavky a provozní kontrakty popisuje [SSOT](../docs/SSOT.md). Python musí být alespoň 3.12.

## Windows

```powershell
.\Build\build_windows.ps1 -Python ".venv\Scripts\python.exe"
```

Výstup je `dist/Kajovo/Kajovo.exe`. Skript instaluje závislosti, vytváří ikony a spouští PyInstaller; chyba kteréhokoli kroku ukončí sestavení.

## macOS

Vyžaduje nástroje `sips` a `iconutil`.

```bash
PYTHON_BIN=python3 APP_NAME=Kajovo ./Build/build_macos.sh
```

Výstup je `dist/Kajovo.app`. Sestavení musí proběhnout na cílovém operačním systému.

## Prostředky

Zdrojové logo je `resources/Kajovo_new.png`. Ikony v `Build/assets` a `resources/app_icon.png` jsou generované a necommitují se. Fonty jsou verzované prostřednictvím Git LFS. Před sestavením proveďte `git lfs pull`.

Distribuce obsahuje lokální diagnostický PowerShell skript, logo a oba fonty. Přenosný wheel se sestaví příkazem `python -m pip wheel --no-deps --wheel-dir dist .`.
