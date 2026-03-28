# 31 Last risks matrix

| Riziko | Stav po hardeningu | Závažnost | Důkaz / poznámka |
|---|---|---:|---|
| Synchronous UI blokování při načítání OpenAI modelů | OPRAVENO | původně Medium | `on_load_openai_models()` běží na pozadí, test existuje |
| Projektový log bez `schema_version` / `event_name` / `project_path` | OPRAVENO | původně Medium | `log_event()` opraven, test existuje |
| Retence doběhnutých `QThread` v `_threads` | OPRAVENO | Low | thread se po doběhu odstraňuje |
| ETA může být nepřesná u heterogenní dávky dokumentů | ZBÝVÁ | Low | heuristika je přiznaná; nejde o fake progress |
| Živý produkční OpenAI call nebyl v této verifikaci spuštěn | ZBÝVÁ | Medium | boundary proof existuje, live network proof ne |
| Část runtime textů bez diakritiky | ZBÝVÁ | Low | stylistická nekonzistence, nikoli encoding chyba |
| Formálně validní, ale obsahově slabý AI výstup | ZBÝVÁ | Medium | je tlumen další validací, ARES a promotion guardy |
| Nejasné mapování OCR -> AI -> DB | Přijatelně pokryto | Low | existují docs, smoke test a log walk-through |
| Zbytky mojibake / invalid UTF-8 | Nenalezeno | Low | audit textových souborů čistý |
| Důvěryhodnost tvrzení o „reálném“ chování systému | Převážně podloženo | Medium | soubory/OCR/DB ano; OpenAI live call stále bez produkčního sandbox důkazu |
