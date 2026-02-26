# Forenzní analýza: připravenost KájovoHotel na Windows + macOS

## Cíl
Vyhodnotit, co je potřeba změnit, aby aplikace fungovala na desktopu Windows i macOS, a zda je reálné udržet **jednu společnou verzi zdrojového kódu** pro build do `.exe` (Windows) i `.app`/binárky (macOS).

## Shrnutí závěru
- **Ano, je to možné.** Jedna verze zdrojového kódu může fungovat pro Windows i macOS.
- Dnes je projekt funkčně orientovaný primárně na Windows (README, skripty, API-key persistence přes `setx`, texty/flow v UI), ale architektura je dostatečně čistá na cross-platform refaktor.
- Kritické blokátory nejsou v core business logice, ale v:
  1) launcher/install skriptech,
  2) OS integračních detailech v UI,
  3) packaging pipeline.

## Forenzní nálezy (důkazy)

### 1) Dokumentace a start workflow jsou explicitně windows-first
- README popisuje „desktop aplikaci pro Windows 10/11“ a quickstart odkazuje na `.bat`/`.ps1` skripty.
- Přímý důsledek: pro macOS chybí oficiální bootstrap/run postup.

### 2) Persist API klíče je windows-specific (`setx`)
- V hlavním okně i v API-key dialogu se trvalé uložení `OPENAI_API_KEY` dělá přes `setx`, fallback je pouze current session.
- Na macOS tak uživatel bez dalšího mechanismu nedostane trvalé uložení klíče stejným UX.

### 3) Otevírání složek používá Windows + Linux větev, ale ne macOS
- Kód používá `os.startfile(...)` pro Windows, jinak `xdg-open`.
- Na macOS je standard `open`, takže aktuální fallback není správný.

### 4) Repair script execution je částečně multiplatformní, ale shell branch je Linuxová
- Kód detekuje `.bat` pro Windows, jinak volá `bash script`.
- To na macOS často fungovat může, ale není to explicitní a není to navázané na `open`/Darwin UX ani na podpis/atributy skriptů (Gatekeeper, executable bit).

### 5) Diagnostické UI je namingem i use-cases posunuté k Windows
- UI obsahuje explicitní checkboxy „Windows IN/OUT“ a volitelně SSH OUT.
- Není to technický blokátor, ale pro macOS je potřeba rozšířit/oddělit platform-specific diagnostiku a popisy.

### 6) Security storage je ve správném směru (OS keyring fallback)
- `secret_store` používá `keyring` (na macOS Keychain), fallback do env.
- To je dobrý základ pro sjednocené cross-platform zacházení se secrets.

### 7) Build/distribuce není zatím definovaná pro macOS
- Repo má jen Windows helper skripty (`install/run .bat/.ps1`), chybí packaging profil pro macOS.
- Bez build profilu (např. PyInstaller/Briefcase) není možné garantovat konzistentní releasy na obou platformách.

## Co vše se musí změnit (praktický plán)

## A) Runtime kompatibilita (kód)
1. **Abstrahovat OS operace do `core/platform.py`**
   - Funkce: `open_path()`, `persist_env_var()`, `run_script_file()`, `platform_name()`.
   - Místo přímého `setx`, `os.startfile`, `xdg-open` volat jednotnou vrstvu.

2. **API key persistence bez `setx` závislosti**
   - Primárně ukládat API key přes `keyring` (už je dependency i pattern v projektu).
   - `OPENAI_API_KEY` držet jen jako runtime mirror při spuštění.
   - Výsledek: stejný UX na Windows i macOS, bez shell hacků.

3. **Správné otevření složky podle platformy**
   - Windows: `os.startfile`
   - macOS: `open <path>`
   - Linux: `xdg-open <path>`

4. **Repair script executor rozlišit podle platformy**
   - Windows: `.bat`/`.cmd` přes `cmd /c`
   - macOS/Linux: `.sh` přes `/bin/bash` nebo `/bin/sh` + kontrola executable bitu
   - Přidat explicitní log message „running on darwin/windows/linux“.

5. **UI texty a diagnostické režimy**
   - „Windows IN/OUT“ přejmenovat na obecné „Local IN/OUT“ + platform-specific tooltip.
   - Volitelně přidat „macOS local diagnostics“ profil.

## B) Build & release
1. **Přidat macOS bootstrap skript**
   - `scripts/install.sh`, `scripts/run.sh`.

2. **Zavést jednotné build profily**
   - Např. PyInstaller:
     - `build/windows.spec`
     - `build/macos.spec`
   - Nebo Briefcase pro `.app` packaging.

3. **CI matrix**
   - GitHub Actions: `windows-latest`, `macos-latest`.
   - Kroky: install → smoke test importů → build artifact.

4. **Code signing / notarizace pro macOS**
   - Pro distribuci mimo lokální dev je potřeba Apple signing/notarization proces.

## C) QA a akceptační kritéria
- Smoke scénáře na obou OS:
  - start app,
  - nastavení API key,
  - refresh modelů,
  - run GENERATE/MODIFY,
  - otevření OUT,
  - zápis LOG a sqlite receipts.
- Negativní testy:
  - chybějící keyring backend,
  - chybějící `open`/`xdg-open`,
  - repair script bez práv.

## Odpověď na otázku „jedna verze pro Windows EXE i macOS build“
Ano — **jedna verze zdrojového kódu je realistická a doporučená**. Prakticky to znamená:
- společný runtime kód,
- malé platform adaptery v několika místech,
- oddělené build targety (Windows vs macOS),
- platform-specific release pipeline (signing/notarization hlavně pro macOS).

Jinými slovy: „jedna codebase, dva buildy“. Bez zásadních změn architektury to jde.
