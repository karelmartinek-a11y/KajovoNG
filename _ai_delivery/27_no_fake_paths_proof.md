# 27 No fake paths proof

## Prověřená podezřelá místa

### 1. OpenAI integrace

Podezření:
- může jen předstírat AI extrakci bez skutečného HTTP requestu.

Důkaz proti:
- `OpenAIClient` skládá skutečný request na oficiální `v1/responses` endpoint,
- test `tests/test_openai_client_truth.py` kontroluje URL, hlavičky i model,
- audit metadata vznikají z requestu, ne z fake timeru.

### 2. Čtení souborů

Podezření:
- loader může pracovat s předpřipraveným textem místo souboru.

Důkaz proti:
- `DocumentLoader.load()` vrací `source_path`, `source_sha256`, `source_bytes`,
- test zapisuje reálný soubor a ověřuje jeho fingerprint i načtený obsah.

### 3. OCR

Podezření:
- OCR může vracet fake text bez práce se souborem.

Důkaz proti:
- Tesseract se spouští nad cestou k reálnému souboru,
- verifikační OCR běh vrátil text z nově vygenerovaného obrázku,
- confidence i provenance odpovídají skutečné OCR větvi.

### 4. DB persistence

Podezření:
- aplikace může jen měnit stav v working DB a business výstup předstírat.

Důkaz proti:
- smoke test končí dokumentem ve `final_documents` v production DB,
- working a production DB jsou dva fyzicky odlišné soubory,
- `separation_report()` stále hlídá, že nejde o jeden soubor s filtrem.

### 5. Progress UI

Podezření:
- procenta a ETA mohou být jen kosmetická animace.

Důkaz proti:
- dialog pracuje s progress payloadem,
- ETA se počítá z elapsed času a počtu skutečně zpracovaných kroků,
- není použit fake timer ani pevně naprogramovaná délka čekání.

## Závěr

V produkčním toku nebyla nalezena cesta, která by vracela falešný „hotový“ výsledek bez skutečného čtení souboru, OCR, DB zápisu nebo oficiálního OpenAI boundary requestu.
