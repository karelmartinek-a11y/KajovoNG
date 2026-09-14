# Sestavení a release

Požadavky a provozní kontrakty popisuje [SSOT](../docs/SSOT.md). Python musí být alespoň 3.12; CI i release workflow používají Python 3.13. Zdrojová verze releasu je výhradně `[project].version` v `pyproject.toml`.

## CI versus release

`.github/workflows/ci.yml` ověřuje zdrojový kód pomocí Ruff, pytest a `pip check`. Samo CI nevytváří distribuční release.

`.github/workflows/release.yml` nejprve určí přesný cílový Git SHA a vyžaduje úspěšný běh `ci.yml` právě pro tento SHA, větev `main` a událost `push`. Pokud CI chybí, selže, je zrušené nebo skončí jinak než `success`, release se nevytvoří. Běžící CI se polluje až 60 minut; stav a použitý workflow run jsou viditelné v logu.

Release workflow nepoužívá OpenAI API klíče ani neprovádí placené OpenAI API volání.

## Windows

Lokální sestavení:

```powershell
.\Build\build_windows.ps1 -Python ".venv\Scripts\python.exe"
```

Výstup je `dist/Kajovo/Kajovo.exe` a celý adresář `dist/Kajovo` potřebný pro spuštění. Skript instaluje závislosti, vytváří ikony a spouští PyInstaller; chyba kteréhokoli kroku ukončí sestavení.

Release workflow používá tentýž skript, ověří existenci a nenulovou velikost EXE i onedir struktury, vytvoří ZIP, SHA-256 a JSON manifest a uloží je jako GitHub Actions artifact. Produkční název je například `KajovoNG-v0.1.0-windows-x64.zip`; release candidate navíc obsahuje krátký commit SHA.

## macOS

macOS je podle SSOT podporovaná distribuční platforma. Vyžaduje nástroje `sips` a `iconutil`.

```bash
PYTHON_BIN=python3 APP_NAME=Kajovo ./Build/build_macos.sh
```

Výstup je `dist/Kajovo.app`. Sestavení musí proběhnout na cílovém operačním systému. Release workflow se pokusí vytvořit odpovídající macOS ZIP, checksum a manifest. Selhání macOS buildu je v Actions viditelné, ale nezablokuje platný Windows release.

## Git LFS a prostředky

Zdrojové logo je `resources/Kajovo_new.png`. Ikony v `Build/assets` a `resources/app_icon.png` jsou generované a necommitují se. Fonty jsou verzované prostřednictvím Git LFS. Před lokálním sestavením proveďte:

```text
git lfs pull
git lfs fsck
```

GitHub release checkout používá `lfs: true` a kontrolu LFS opakuje. Distribuce obsahuje lokální diagnostický PowerShell skript, logo, symbol studia a oba fonty. Přenosný wheel lze sestavit příkazem `python -m pip wheel --no-deps --wheel-dir dist .`; není povinným assetem GitHub Release.

## Release candidate

V GitHub Actions otevřete workflow **release**, zvolte **Run workflow** a spusťte jej z větve `main`. Veřejný GitHub Release se při ručním branch běhu nevytváří; po úspěšném CI gate a buildu jsou ZIP, `.sha256` a `.manifest.json` dostupné v Artifacts daného workflow runu po dobu 30 dnů.

Stejný neprodukční build se automaticky spouští při pushi do `main` pouze tehdy, když se mění samotný build/release kontrakt (`release.yml`, build skripty, závislosti, LFS nebo distribuované resources). Ani tento běh nevytváří veřejný Release.

## Produkční release

Produkční release vzniká pouze z tagu ve tvaru `vX.Y.Z`, například `v0.1.0`. Před tagováním musí `[project].version` v `pyproject.toml` obsahovat stejnou verzi bez `v`; workflow verzi samo nemění.

Příklad:

```text
git checkout main
git pull --ff-only
git tag v0.1.0
git push origin v0.1.0
```

Workflow ověří, že tag ukazuje na commit dosažitelný z `main`, verze tagu přesně odpovídá `pyproject.toml` a CI pro stejný SHA je úspěšné. Poté sestaví distribuci a vytvoří GitHub Release `KájovoNG vX.Y.Z`. Release je idempotentní: opakovaný běh bezpečně nahradí assety pouze u release vytvořeného touto pipeline pro stejný tag a SHA. Existující release bez pipeline markeru nebo pro jiné SHA se automaticky nepřepisuje.

## Ověření SHA-256

Windows PowerShell:

```powershell
(Get-FileHash .\KajovoNG-v0.1.0-windows-x64.zip -Algorithm SHA256).Hash.ToLowerInvariant()
Get-Content .\KajovoNG-v0.1.0-windows-x64.zip.sha256
```

macOS/Linux:

```bash
shasum -a 256 -c KajovoNG-v0.1.0-macos-arm64.zip.sha256
# nebo na Linuxu: sha256sum -c <soubor>.sha256
```

Hodnota v `.sha256` musí souhlasit s vypočteným hashem. JSON manifest navíc uvádí název aplikace, projektovou verzi, celý a krátký Git SHA, platformu, verzi Pythonu, UTC čas sestavení a SHA-256 archivu.
