# Kruhová mapa pracovních operací

## Implementační plán a odpovědnosti

1. Backend určuje známý plán a vydává události na skutečných hranicích práce. Plán není důkazem dokončení. Dynamické větvení může přidat další krok.
2. `core/progress_model.py` uchovává poslední důkaz každé etapy nezávisle na omezeném seznamu technických zpráv. Interní aliasy téže etapy spojuje, nikoli různé funkční etapy.
3. `studio/progress_view.py` vykresluje body kruhu, počty, aktuální činnost, další známý krok, čas a přehled etap. Neznámý stav služby nenahrazuje tvrzením, že služba pracuje.
4. `studio/progress_dialog.py` řídí viditelnost a ovládací prvky. Skrytí ani Escape práci nezastavují. Žádost o zastavení není událost služby a neobnovuje její čas. Opakované sledování resetuje všechny zobrazované údaje.
5. `studio/operations.py` přebírá výsledek až po ukončení pracovníka. Zachovává doménové koncové stavy i při současném chybovém signálu. Samotný konec vlákna bez výsledku není úspěch.
6. Regrese ověřují sémantiku, pořadí potvrzení, chyby, dlouhé čekání a životnost pracovníka. Snímky vznikají z produkčních Qt widgetů a událostí executorů se síťovými službami nahrazenými testovacími klienty.

## Kontrakt zobrazení

- Kruh počítá potvrzené dokončené etapy. Nejde o procenta zbývajícího času. Počet plánovaných etap se může změnit, pokud backend zjistí další práci.
- Událost `PLAN` obsahuje `planned_steps`; sama nezahajuje ani nedokončuje žádný krok a nemění hodiny. Bez plánu jsou zobrazené jen dosud ohlášené etapy.
- Každá etapa vyžaduje své vlastní `completed`. Následující aktivní událost téže etapy zahajuje její další provedení. `skipped` se nezapočítává jako nově dokončená práce.
- `RUN completed` nepotvrzuje jednotlivé etapy. Neprovedený nebo nedoložený krok po konci není označen jako čekající aktivní práce.
- Vrácené `submission_unknown`, `response_pending`, `cancelled` a `stopped` neztrácejí význam kvůli doprovodnému chybovému signálu.
- Dokončená vzdálená fotografická dávka čeká na místní převzetí. Teprve `downloaded` potvrzuje dokončené stažení; neznámé odeslání bez čísla dávky není úspěch.
- Počet vytvořených cílů nezahrnuje čekání na ručně dodané prostředky. Místní staging, publikace do OUT a ověření funkčnosti jsou odlišné skutečnosti.
- Technické podrobnosti obsahují původní události včetně identifikátorů. Uživatelská plocha používá české významové názvy. Původní implementace dialogů ani jejich kopie nejsou součástí runtime.

## Vizuální kontrola

`python scripts/render_progress.py --output _skill_runs/progress/desktop` vytváří snímky čekání a výsledku GENERATE, MODIFY se zastavením po plánu a BATCH ze skutečných executorů. Chybové a nejisté koncové stavy doplňuje řízenými událostmi, jejichž význam ověřují regresní testy. Všechna provozní data vznikají v dočasném adresáři, síť je zablokovaná.

`--replay _skill_runs/progress/desktop/traces.json --size 480,640` přehraje stejné události do úzkého okna. `--scale 1.5` kontroluje větší měřítko. Přehrané časy označují stáří původních testovacích událostí. Snímky a záznam přehrání nejsou provozní evidence uživatele ani součást distribuovaného programu.

Referenční PDF zahrnuje podrobnější rozklad 182 variant. Současný plán executorů slučuje některé referenční podkroky do jedné ověřované etapy; u obecných obslužných úloh potvrzuje pouze skutečně provedenou funkci a případné vnitřní události. Samotné společné vykreslení nezakládá důkaz samostatného pokrytí všech 1 284 referenčních podkroků. Rozsah doložených hranic uvádí [pokrytí](coverage.md).
