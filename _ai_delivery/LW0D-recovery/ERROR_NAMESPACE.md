# R19 — uzavření namespace chyb (B-02)

Autoritativní wire error namespace je katalog §32 rozšířený přesně o canonical_additions v `error-symbols.json`. Aliasy v témže souboru jsou povoleny pouze při převodu původní specifikace; výsledný API/KCIP/MCP contract a emitovaný wire error obsahují výhradně cílový canonical code. Žádný další symbol není implicitním error code podle názvu nebo suffixu.

`non_error_symbols` výslovně odděluje state, decision, auth mode, blocker/retry classification a schematický placeholder od error code. Pole code a classification mají samostatné enums. PROTOCOL_INVALID v era decision tedy není alternativní wire chyba MCP_PROTOCOL_INVALID. STABLE_ERROR_CODE v ilustračním schema příkladu se nikdy nesmí emitovat jako doslovná chyba.

`verify_error_symbols.py` prošel celý původní SSOT, nikoli pouze hlášené tři výskyty. Zjistil 33 error-like symbolů mimo §32. Všechny jsou jednotlivě rozlišené v `error-symbols.json`; kontrola ověřuje disjunktní role, existenci canonical targetů a nulový počet nerozlišených kandidátů. Výsledek obsahuje otisk zdroje a čísla všech výskytů. Kontrola nezaměňuje úplnost tohoto namespace rozhodnutí za implementaci klasifikátorů či jejich runtime testy.

Operation/schema compiler musí navíc kontrolovat každé skutečné pole errorCode/stableCode proti uzavřenému namespace, bez závislosti na heuristice názvu. Neznámá hodnota blokuje BUILD. Lokalizovaný message ani HTTP status se nesmějí použít jako náhradní machine code.

B-02 je tím rozhodnut na úrovni specifikace. Testy emitovaných errors, classification a retry behavior zůstávají povinnými testy výsledné aplikace.
