# Build návod (Windows + macOS)

Tento adresář obsahuje kompletní build workflow pro aplikaci **Kája/Kajovo** tak, aby byl výstup konzistentní na Windows i macOS.

## Co je zajištěno

Build skripty vždy před kompilací:
1. doinstalují build závislosti (`pyinstaller`),
2. vygenerují jednotné ikonky aplikace,
3. nastaví stejnou značku/ikonu pro:
   - spustitelný soubor (`.exe` / `.app`),
   - ikonu okna aplikace (runtime `resources/app_icon.png`),
   - favicon (`Build/assets/favicon.ico`) pro případné web/README použití.

Ikony se generují skriptem `Build/generate_icons.py` do `Build/assets/` a zároveň se aktualizuje runtime soubor `resources/app_icon.png`.

> Poznámka: binární icon soubory se necommitují do gitu; generují se vždy při buildu.

---

## Windows build

### Požadavky
- Windows 10/11
- Python 3.10+
- PowerShell

### Spuštění
```powershell
cd <repo>
./Build/build_windows.ps1
```

Volitelné parametry:
```powershell
./Build/build_windows.ps1 -Python py -AppName Kajovo
```

### Výstup
- `dist/Kajovo/Kajovo.exe`

---

## macOS build

### Požadavky
- macOS
- Python 3.10+
- Xcode Command Line Tools (`iconutil`, `sips`)

### Spuštění
```bash
cd <repo>
./Build/build_macos.sh
```

Volitelné env proměnné:
```bash
PYTHON_BIN=python3.11 APP_NAME=Kajovo ./Build/build_macos.sh
```

### Výstup
- `dist/Kajovo.app`

---

## Poznámky k ikonám a brandingu

- `Build/assets/app_icon.ico` → ikona Windows `.exe`
- `Build/assets/app_icon.icns` → ikona macOS `.app`
- `resources/app_icon.png` → runtime ikona hlavního okna (nastavuje aplikace při startu)
- `Build/assets/favicon.ico` → favicon pro dokumentaci/web integrace

Pokud chceš změnit vizuál loga, uprav kreslení v `Build/generate_icons.py` a rebuildni aplikaci.
