# Úplná evidence generování ID92

Archiv `ID92_complete.tar.xz` obsahuje původní automatický běh a navazující ruční obnovu v přesném znění místních souborů. Uložení provozních záznamů do Gitu je pro tento konkrétní běh výslovně vyžádané uživatelem. Ostatní provozní adresáře zůstávají ignorované.

## Obsah archivu

- `LOG/RUN_130920260322_ID92/`: původní events, run_state, požadavky, odpovědi a manifesty od příjmu zadání přes A0R, A1 a pokusy A2 až po vyčerpání kreditu.
- `LOG/manual_ID92/`: obnovené původní odpovědi a vstupy, dokončení A2/A2Q, lokální korekce a validace, pomocné skripty, kompletní požadavky A3, počítání tokenů, rozpočty, metadata obou Batch a všechny stažené výsledky a chyby.
- `OUT/SSOT_CURRENT.md`: přesná kopie jediného souboru, který byl v době archivace zapsán do výstupního adresáře `D:/KajovoCMLNG`.

Vygenerovaný obsah A3 je uvnitř odpovědí v `LOG/manual_ID92/gpt-5.6-luna_output_file_id.jsonl`; nebyl automaticky importován do OUT. Zahrnuty jsou dokončené i neúplné odpovědi. U mini nevznikl žádný vygenerovaný soubor; zahrnut je celý chybový soubor s 228 odmítnutými požadavky.

Vynechané jsou pouze odvozené Python cache `__pycache__` a `*.pyc`. Obsah původních záznamů nebyl při balení přepisován. Původní automatické logy mohou již obsahovat redakci provedenou aplikací; archiv tuto redakci nevrací zpět. Ruční část obsahuje také získané původní API odpovědi a rekonstruované zadání.

## Orientace ve výsledcích

- Mini: `batch_6aa6a726026c81908fc7695aac52fa9e`, 228 chyb `context_length_exceeded`.
- Luna: `batch_6aa6a774d5b081908799dc89832c8075`, 38 dokončených Responses a 33 neúplných kvůli `max_output_tokens`.
- `handoff.json` zachycuje starší průběžný stav při předání po odeslání; konečný výsledek dokládají submission metadata a stažené řádky odpovědí.
- Formálně dokončená odpověď neznamená ověřenou funkčnost souboru.
- Podrobný rozbor a návrh řešení je v kořenovém `spec.txt`. Kořenový `aa.txt` je čitelný export jednoho požadavku včetně přílohy.

## Integrita a rozbalení

Vedle archivu je `manifest.json` s názvem, délkou a SHA-256 každého ze zahrnutých souborů i SHA-256 samotného archivu. Po zabalení byl každý soubor přečten z archivu a porovnán s otiskem originálu.

Rozbalujte do nového prázdného adresáře, aby nedošlo k přepsání živých logů. Například z kořene repozitáře:

```powershell
New-Item -ItemType Directory -Path ID92_review
tar -xJf LOG/ID92_complete/ID92_complete.tar.xz -C ID92_review
```

Archivace pouze konzervuje dostupnou evidenci; neposílá požadavky do API a nepotvrzuje úplnost či správnost vygenerovaného projektu.
