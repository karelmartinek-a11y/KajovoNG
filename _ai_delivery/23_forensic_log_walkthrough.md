# 23 Forensic log walkthrough

Níže je zjednodušená rekonstrukce jednoho smoke případu ze `logs/decisions.jsonl`.

## Příklad případu

- vstupní soubor: `doc.txt`
- correlation_id: `90196ad5d0de4f109a78613505c054dc`

## 1. Import souboru

Událost:
- `event_type = import.created`

Význam:
- soubor byl skutečně zařazen do workflow,
- log nese `file_id`, `document_id` a `sha256_prefix`.

## 2. Start extrakčního pokusu

Událost:
- `event_type = attempt.started`

Význam:
- dokument vstoupil do workflow pokusu `automatic`.

## 3. Dokončení pokusu

Událost:
- `event_type = attempt.finished`
- zpráva: `Offline vytazeni nad obsahem dokumentu dalo kompletni vysledek a ARES jej potvrdil.`

Význam:
- offline extrakce i ARES validace proběhly úspěšně,
- pokus nebyl pouze kosmetický, protože následně došlo k finalizaci.

## 4. Finalizace dokumentu

Událost:
- `event_type = document.finalized`
- `payload.reason = Promotion completed.`

Význam:
- dokument byl promovaný do production vrstvy,
- případ je z logu rekonstruovatelný od importu po finální stav.

## Co je z logu vidět okamžitě

- které entity patří ke stejnému případu (`correlation_id`),
- jaký byl výsledek pokusu,
- kdy došlo k finalizaci,
- že nejde o tichý happy-path bez auditní stopy.
