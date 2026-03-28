# 24 Release notes

## Co se změnilo oproti původnímu ZIPu

- OpenAI klient má audit metadata a explicitní boundary verifikaci.
- DocumentLoader vrací provenance skutečného souboru a OCR confidence.
- Progress dialog ukazuje krok, detail, procenta a ETA.
- Logování má bohatší JSON schema.
- Přibyly důkazní testy pro OpenAI, OCR, logging, progress, encoding a DB persistence.
- README a `docs/` lépe popisují skutečný pipeline.
- Ve finální verifikaci byla navíc opravena životnost SQLite spojení v repository vrstvě.

## Co je důležité pro provoz

- OCR testy a OCR pipeline vyžadují dostupný Tesseract.
- UI testy běží v `QT_QPA_PLATFORM=offscreen`.
- OpenAI logy nezapisují tajné klíče, ale zapisují bezpečná auditní metadata.

## Migrační poznámky

- Neproběhla změna DB schématu vyžadující ruční migraci.
- Oprava connection lifecycle je transparentní vůči datům i API repozitáře.
