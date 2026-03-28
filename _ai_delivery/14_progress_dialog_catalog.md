# 14 Progress dialog catalog

## TaskProgressDialog

Použití:
- import dávky,
- zpracování čekajících pokusů,
- další background úlohy připojené přes `_run_background_task`.

## Co dialog ukazuje

- název operace,
- krok pipeline,
- lidsky čitelný detail kroku,
- procenta,
- ETA.

## Zdroj pravdy pro procenta a ETA

- procenta jdou primárně z progress payloadu,
- fallback je `current / total`,
- ETA je heuristika z reálně naměřeného času od startu úlohy.

## Chování při zrušení

Dialog přejde do režimu bezpečného zastavení a explicitně sdělí, že čeká na doběh aktuálního kroku.
