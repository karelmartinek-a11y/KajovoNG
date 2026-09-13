# Photo Studio — fotografie v KájovoNG

## Účel

Photo Studio je samostatná pracovní sekce pro profesionální úpravu existujících fotografií. Textový prompt lze volitelně převést přes pracovní Responses API do profesionální anglické instrukce. Samotná úprava fotografie se nikdy neposílá LIVE; probíhá výhradně jako pracovní BATCH na `/v1/images/edits`.

## UI

Sekce `Fotografie` se připojuje k hlavní navigaci po `Zadání`. Obsahuje:

- import jednotlivých PNG/JPEG/WebP fotografií nebo celé složky;
- thumbnail grid s vícenásobným výběrem a náhledem;
- stále viditelný seznam vestavěných a uživatelských promptových šablon;
- editovatelný prompt;
- tlačítko **Vylepšit prompt** s pracovním Responses požadavkem a skutečným progress popupem;
- výběr Image Edit BATCH modelu z účtového katalogu omezený pevnou modelovou maticí;
- kvalitu, velikost, výstupní formát a adresář;
- seznam Photo BATCH jobů, obnovu stavu, zrušení, stažení výsledků a porovnání originálu s výsledkem.

## Vylepšení promptu

`kajovo/core/photo_prompt.py` definuje pevnou instrukci `PROFESSIONALIZE_INSTRUCTIONS`. Uživatelův text je odděleně vložen mezi `USER_PROMPT` značky. Výstup používá strict JSON Schema `PHOTO_PROFESSIONAL_PROMPT` s jediným polem `professional_prompt`.

Je to skutečný pracovní Responses request, nikoli validační/preflight request. Výchozí pořadí preferuje `gpt-5.6-luna`, pokud jej účet i pevná matice dovolují. Reasoning je `low`, pokud jej daný model podporuje. `store=false`.

Editor se mění až po dokončené a validní odpovědi. Chyba, timeout, refusal, incomplete nebo neplatný kontrakt ponechá původní text beze změny. Po úspěchu lze jedním tlačítkem vrátit text před vylepšením.

U jobu se odděleně eviduje `human_prompt`, `professional_prompt`, skutečný `final_prompt`, SHA-256 finálního promptu, prompt model a response ID. Ruční úprava profesionálního promptu proto neztrácí provenance.

## Šablony

`photo_templates.py` obsahuje neměnné vestavěné šablony a verzovaný uživatelský soubor `cache/photo_templates.json`.

Vestavěné šablony lze použít a duplikovat, nelze je přepsat ani smazat. Uživatelské šablony lze vytvářet, editovat, duplikovat a mazat. Zápis je atomický a neplatný JSON se nepřepisuje tichým resetem.

Součástí výchozí sady jsou:

- Hotel / Booking – profesionální;
- Hotelový pokoj;
- Koupelna;
- Exteriér;
- Pouze světlo a barvy;
- Perspektiva a geometrie.

## Image Edit BATCH

Podporované modely se neodvozují pouze z názvu ani pouze z výskytu endpointu. `image_edit_model_ids()` vybírá jen přesné identifikátory z `openai_model_matrix.json`, které současně:

1. nejsou deprecated;
2. mají v pevné matici povolený Batch;
3. mají image-edit schopnost `inpainting`;
4. mají endpoint `v1/images/edits`;
5. při načteném katalogu účtu jsou skutečně dostupné na účtu.

Tato kombinace je záměrná. Obecný Responses model se nesmí stát Image API modelem jen proto, že jeho zdrojová dokumentace zmiňuje stejnou endpointovou rodinu. Naopak nový GPT Image model se do pracovního Photo BATCH nezařadí dříve, než pevná matice doloží i jeho Batch kontrakt. Aktualizace modelové matice je samostatná explicitní změna.

Jeden řádek pracovního JSONL znamená právě jednu vstupní fotografii a jeden výsledek:

```json
{
  "custom_id": "photo-00001-...",
  "method": "POST",
  "url": "/v1/images/edits",
  "body": {
    "model": "gpt-image-2",
    "images": [{"file_id": "file_..."}],
    "prompt": "...",
    "n": 1,
    "size": "auto",
    "quality": "high",
    "output_format": "png",
    "background": "auto"
  }
}
```

Celý skutečný JSONL se před uploadem deterministicky lokálně validuje. Teprve potom se nahraje s `purpose=batch`. `ImageEditBatchAdapter.submit()` provede právě jeden pracovní `POST /batches` s endpointem `/v1/images/edits` a transportním `max_attempts=1`. Nevytváří testovací fotografii, testovací JSONL, testovací upload ani testovací BATCH.

Fotografie se do Files API nahrávají s `purpose=user_data`; JSONL s `purpose=batch`.

## Photo Job

Každý job se ukládá do:

`LOG/PHOTO/photojob_<uuid>/photo_job.json`

Vedle něj jsou uchované skutečné pracovní `batch_input.jsonl`, stažený `batch_output.jsonl` a případný `batch_errors.jsonl`.

Záznam obsahuje:

- přesný promptový snapshot;
- model/quality/size/output_format;
- source path a source SHA-256 každé fotografie;
- upload `file_id`;
- `custom_id`;
- Batch `input_file_id`, `batch_id`, output/error file IDs;
- stav a chybu každé fotografie;
- cestu a SHA-256 staženého výsledku.

Originální fotografie se nikdy nepřepisuje. Výsledek používá `<stem>_edited.<ext>` a při kolizi `_edited_2`, `_edited_3` atd.

## Stažení a validace

Output JSONL se nesmí potichu tolerovat. Neplatný JSON, neznámé nebo duplicitní `custom_id` jsou forenzní chyba. API chyba konkrétního řádku se zapíše k položce a neodstraní platné výsledky ostatních položek.

Pro úspěšnou položku musí být v `response.body.data[0].b64_json` dekódovatelný výsledek. Soubor se zapisuje atomicky. Photo Job je po stažení `downloaded`, `partial` nebo `failed` podle skutečných položek.

Lokální umělý upscale se automaticky neprovádí. Požadovaná velikost se objednává od image modelu; program nevytváří falešný dojem nového detailu resamplingem.

## Oddělení od běžného Responses BATCH

Stávající GENERATE/MODIFY Batch nad `/v1/responses` zůstává beze změny. Photo Studio používá vlastní endpointový adaptér pro `/v1/images/edits`; nesmí měnit význam `submit_verified_batch()` ani `OpenAIClient.create_batch()` pro Responses workflow.

## Testovací kontrakt

Testy jsou pouze lokální/mockované a nesmí posílat placené požadavky. Ověřují zejména:

- neměnnost vestavěných šablon;
- CRUD uživatelských šablon;
- tvar strict Responses požadavku pro vylepšení promptu;
- Image Edit JSONL a přesný endpoint;
- odmítnutí obecných Responses modelů v Image Edit BATCH;
- právě jeden pracovní `/batches` submit;
- mapování přes `custom_id`;
- zachování originálu;
- tiché neignorování poškozeného JSONL.
