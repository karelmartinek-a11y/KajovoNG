# 12 Performance report

## Provedené zásahy

- UI dlouhé operace zůstávají v background workeru a progress dialog teď ukazuje stav bez blokování hlavního okna.
- Runtime progress nese detail, krok, procenta a ETA, takže uživatel není bez informace během čekání.
- OCR pipeline vrací přesnější provenance a confidence, což zkracuje diagnostiku a slepé retry.
- OpenAI audit metadata ukládají duration a status code, takže lze přesněji identifikovat, zda se zdržení děje v síti, validaci nebo v další části pipeline.

## Quick wins

- transparentní progress reporting místo neinformovaného čekání,
- rychlejší analýza výkonových problémů díky `duration_ms` a fingerprintům,
- méně zbytečného ručního dohledávání přes bohatší logy.

## Co zůstává pro další hlubší iteraci

- jemnější fázové progress události uvnitř jednotlivých kroků zpracování jednoho dokumentu,
- detailnější měření DB query latency po jednotlivých metodách repository,
- sampling a agregace časů podle typu dokumentu a OCR větve.
