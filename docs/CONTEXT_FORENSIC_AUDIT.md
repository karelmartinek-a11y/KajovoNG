# Forenzní audit kontextu

## Důkazní hranice

Audit ID92 vychází z archivovaných requestů, Responses a provider usage metadat. Historické tokenové hodnoty se používají pouze k technické analýze velikosti kontextu, incomplete odpovědí a amplifikace payloadu. Nejsou převáděny na peněžní hodnotu.

Původní incident doložil 228 odpovědí `context_length_exceeded` u mini a 71 odpovědí Luny, z nichž 38 bylo `completed` a 33 `incomplete/max_output_tokens`. Stav `completed` na provider úrovni není důkazem sestavení, integrace ani splnění zadání.

## Technické nálezy

Původní souborové A3/B3 cesty opakovaly široký snapshot v mnoha requestech. Současný FileContext odděluje auditní snapshot od pracovního kontextu, váže cílový soubor na relevantní requirements, interfaces, dependencies a acceptance a zachovává hashe provenance. LIVE pokračování navazuje pouze uvnitř téhož souboru; Batch v3 posílá samostatný FileContext pro každý řádek.

Technické měření používá `context_limits.py`. Kontroluje `context_window`, samostatný input limit, `max_output_tokens`, podporované parametry a při neurčitelném lokálním vstupu ne-generativní token count vázaný na hash requestu. Neprovádí generativní probe a neořezává povinnosti, aby se request uměle vešel.

Neurčitý submit je samostatná recovery situace. WorkOrder, attempt identity, provider operation identity, remote input file ID a provider ID zabraňují automatickému duplicitnímu submitu. Raw provider `usage` zůstává důkazní telemetrií.

## Normativní ekonomická hranice

KájovoNG neimplementuje cenový engine ani runtime finanční budget. Hospodárnost je řešena návrhem workflow a provozním rozhodnutím uživatele. Runtime validuje pouze technické a kontraktní limity API.

Historické archivy mohou obsahovat starší ekonomická pole nebo názvy souborů; současný runtime je nečte pro rozhodování a při migraci orchestration databáze zachovává pouze nefinanční identitu, stav a raw provider evidence.

## Offline ID92

Reprodukční skript `scripts/analyze_id92_context.py` ověřuje SHA-256 archivu, archivované token-count hodnoty, velikost kandidátních FileContextů a dependency graf. Neobsahuje ceník, peněžní prahy ani cenovou predikci a neposílá API requesty. Výsledky jsou v [ID92_CONTEXT_OPTIMIZATION_REPORT.md](ID92_CONTEXT_OPTIMIZATION_REPORT.md).
