# KájovoNG — přesné zadání změn
## Etapa: GENERATE + MODIFY, LIVE + BATCH, Standard + Maximum Quality

### 1. Cíl změny

Zvýšit kvalitu GENERATE a MODIFY tak, aby výsledkem nebylo pouze formální splnění explicitního uživatelského zadání, ale profesionálně dotažené řešení. Model má uživatelské zadání chápat jako minimální explicitní požadavek a systematicky domýšlet všechny implicitní požadavky, které jsou přirozenou součástí kvalitního produkčního řešení.

Výsledek se nesmí považovat za hotový jen proto, že:
- obsahuje výslovně požadované funkce,
- projde testy,
- odpovídá schématu,
- nebo formálně splní checklist.

Za hotový se považuje až tehdy, když je každá relevantní funkce, vazba, stav a uživatelský tok skutečně rozpracován a implementován do profesionálního, vnitřně konzistentního a použitelného stavu.

---

## 2. Pevné invarianty — na toto se nesahá

1. Neměnit existující mechanismus dělení dlouhých vstupů.
2. Neměnit existující mechanismus dělení / chunkování výstupů.
3. Neměnit stávající limity, prahy ani ověřenou logiku pro dlouhá zadání.
4. Neměnit význam existujícího technického ingestion kroku A0 pro dlouhé zadání.
5. Neměnit způsob práce se vstupními soubory, diagnostikou a file search, pokud to není nezbytné pro nové fáze.
6. GENERATE a MODIFY musí zachovat LIVE i BATCH variantu.
7. BATCH musí fungovat tak, že analytické a přípravné fáze proběhnou LIVE a do OpenAI Batch API se odešle až vlastní generování výsledných souborů.
8. QA, QFILE a KASKÁDA nejsou součástí této etapy a nesmí se jejich chování měnit.
9. KASKÁDY budou řešeny samostatně v další etapě.

---

# 3. Nová cílová pipeline

## GENERATE — Standard / LIVE

A0R_REQUIREMENTS → A1_PLAN → A2_STRUCTURE → A3_FILE

## GENERATE — Maximum Quality / LIVE

A0R_REQUIREMENTS → A1_PLAN → A2_STRUCTURE → A2Q_QUALITY_GATE → A3_FILE

## GENERATE — Standard / BATCH

A0R_REQUIREMENTS LIVE  
→ A1_PLAN LIVE  
→ A2_STRUCTURE LIVE  
→ A3_FILE jako samostatné souborové úlohy v BATCH

## GENERATE — Maximum Quality / BATCH

A0R_REQUIREMENTS LIVE  
→ A1_PLAN LIVE  
→ A2_STRUCTURE LIVE  
→ A2Q_QUALITY_GATE LIVE  
→ A3_FILE jako samostatné souborové úlohy v BATCH

## MODIFY — Standard / LIVE

B0R_REQUIREMENTS → B1_PLAN → B2_STRUCTURE → B3_FILE

## MODIFY — Maximum Quality / LIVE

B0R_REQUIREMENTS → B1_PLAN → B2_STRUCTURE → B2Q_QUALITY_GATE → B3_FILE

## MODIFY — Standard / BATCH

B0R_REQUIREMENTS LIVE  
→ B1_PLAN LIVE  
→ B2_STRUCTURE LIVE  
→ B3_FILE pro jednotlivé skutečně měněné soubory v BATCH

## MODIFY — Maximum Quality / BATCH

B0R_REQUIREMENTS LIVE  
→ B1_PLAN LIVE  
→ B2_STRUCTURE LIVE  
→ B2Q_QUALITY_GATE LIVE  
→ B3_FILE pro jednotlivé skutečně měněné soubory v BATCH

### Důležitá změna MODIFY BATCH

Současný jednorázový souborový požadavek typu C_FILES_ALL nesmí představovat celou logiku MODIFY BATCH. MODIFY BATCH musí mít stejnou profesionální LIVE přípravu jako MODIFY LIVE. Do batche se smí přesunout až vlastní výroba kompletních změněných souborů.

---

# 4. Nová volba v UI — „Maximum Quality“

Do hlavního zadání přidat checkbox:

**Maximum Quality — maximální propracovanost**

Výchozí stav: vypnuto.

Doprovodný text:

> Přidá nezávislou kontrolu návrhu před generováním souborů a použije nejvyšší úroveň reasoning, kterou zvolený model pro daný krok podporuje. Zvyšuje kvalitu, cenu a dobu běhu.

### Standard

Standard nesmí být „levný nebo zjednodušený režim“. Už Standard používá nové CORE_INSTRUCTIONS a nové requirements fáze A0R/B0R.

Standard pouze:
- nepřidává druhý nezávislý quality-gate průchod,
- zachová současnou běžnou politiku reasoning parametrů.

### Maximum Quality

Při zapnutí:

1. Použít nejvyšší podporovanou úroveň reasoning effort pro všechny relevantní fáze, kde ji zvolený model podporuje.
2. Nikdy neposílat nepodporovaný reasoning parametr; řídit se capability/model matrix.
3. Přidat A2Q_QUALITY_GATE nebo B2Q_QUALITY_GATE.
4. Quality gate musí skutečně opravit a doplnit specifikaci, nikoli pouze vrátit komentář nebo audit.
5. Teprve výstup quality gate je kanonickým podkladem pro generování souborů.
6. V BATCH variantě proběhne quality gate LIVE ještě před vytvořením dávky.
7. Stav Maximum Quality uložit do historie běhu, projektu/snapshotu a ReRun konfigurace.

Pro nové fáze není nutné přidávat nové modelové comboboxy:
- A0R používá model A1.
- A2Q používá model A2.
- B0R, B1, B2 a B2Q používají hlavní model MODIFY, pokud se samostatné B-modely již v UI nevolí.
- A3/B3 používají stávající model určený pro generování souborů.

---

# 5. Společné CORE_INSTRUCTIONS

CORE_INSTRUCTIONS se musí připojit ke každému internímu kroku GENERATE a MODIFY. Konkrétní fáze k němu přidává pouze svou specializovanou instrukci.

## Přesný navržený text CORE_INSTRUCTIONS

```text
Jsi součást seniorního product-engineering a software-engineering týmu. Tvým cílem není pouze doslovně odškrtnout explicitní body uživatelského zadání, ale dovést zamýšlený výsledek do profesionálně úplného, skutečně použitelného a vnitřně konzistentního stavu.

Uživatelské zadání považuj za minimální explicitní kontrakt, nikoli za úplný výčet všeho, co musí kvalitní řešení obsahovat. Zachovej všechny výslovné požadavky, omezení a záměr uživatele, ale systematicky doplň implicitní požadavky, návaznosti a chování, které seniorní tým přirozeně očekává od produkčně připraveného řešení.

Každou funkci, ovládací prvek, datový tok, integraci a proces rozpracuj jako úplný životní cyklus, ne jako izolovanou větu nebo povrchní implementaci. Domysli relevantní stavy, přechody, validace, chyby, zotavení, persistenci, konzistenci, bezpečnost, použitelnost, zpětnou vazbu uživateli, okrajové případy a návaznosti na ostatní části systému vždy tam, kde jsou pro danou funkci relevantní.

Nezmenšuj scope jen proto, aby byl výstup kratší nebo snazší. Nevyměňuj skutečnou implementaci za demonstraci. Nevytvářej skeletony, placeholdery, TODO, stuby, falešná data ani produkční cestu založenou na mocku, pokud je uživatel výslovně nepožaduje. Nevytvářej funkci, která pouze vypadá funkčně. Stav, progress, potvrzení úspěchu a UI musí odpovídat skutečně provedené operaci.

Testy jsou důkazem implementace, nikoli její náhradou. Nesmí vzniknout řešení, jehož hlavním cílem je pouze projít testy bez reálně dokončeného chování.

Za HOTOVO považuj řešení až tehdy, když při odborné kontrole působí jako promyšlená a rozpracovaná práce seniorního týmu: explicitní zadání je splněno, implicitní profesionální očekávání jsou pokryta, jednotlivé části jsou dotažené do detailu, vazby mezi nimi jsou konzistentní a nejsou přítomna známá provizoria ani předstíraná funkčnost.

Doplňuj pouze takové implicitní požadavky, které logicky vyplývají ze zamýšleného produktu nebo jsou standardní podmínkou profesionální implementace. Nevymýšlej nesouvisející produktové funkce a neměň záměr uživatele. Při skutečné nejednoznačnosti zvol bezpečný a profesionální výchozí předpoklad a tento předpoklad explicitně zaznamenej.

Dodrž přesně kontrakt a strukturovaný výstup požadovaný aktuální fází. Text kontraktu nebo JSON schématu neopakuj v prose instrukcích, pokud je již technicky vynucen response formatem.
```

---

# 6. A0R_REQUIREMENTS — GENERATE

### Úloha

První odborná interpretace uživatelského zadání. Má převést běžné nebo laické zadání na implementačně úplnou specifikaci bez zmenšení původního záměru.

### Instrukce

```text
Jednej jako principal requirements engineer a product architect.

Z uživatelského zadání vytvoř implementačně použitelnou specifikaci. Neomezuj se na přeformulování vět uživatele. U každého požadovaného výsledku rozviň, co musí být pravda, aby šlo o profesionálně dokončenou funkci v reálném produktu.

Identifikuj explicitní požadavky, implicitní profesionální požadavky, invarianty, předpoklady, uživatelské a systémové toky, stavy a životní cykly, vazby mezi funkcemi, validace, chybové a recovery scénáře, relevantní bezpečnostní a kvalitativní požadavky a měřitelná kritéria dokončení.

Každou významnou schopnost rozpracuj dostatečně hluboko, aby následující architekt a implementátor nemuseli hádat, co znamená „hotovo“.

Nedoplňuj nesouvisející produktový scope. Doplňuj pouze to, co je přirozenou součástí úplného profesionálního řešení zamýšleného uživatelem.

V této fázi ještě nevyráběj zdrojové soubory. Výstup je kanonická requirements specifikace pro A1.
```

### Minimální obsah kontraktu A0R_REQUIREMENTS

- product_intent
- explicit_requirements[]
- implicit_requirements[]
- invariants[]
- assumptions[]
- user_flows[]
- system_flows[]
- states_and_lifecycle[]
- validation_and_error_behaviour[]
- quality_attributes[]
- security_and_privacy[]
- persistence_and_consistency[]
- dependencies_and_integrations[]
- acceptance_criteria[]
- definition_of_done[]

Schéma musí být strict JSON Schema stejně jako ostatní interní kontrakty.

---

# 7. A1_PLAN — GENERATE

### Instrukce

```text
Jednej jako principal software architect.

Vycházej z kanonické A0R_REQUIREMENTS specifikace a navrhni úplný realizační plán systému. Zachovej každý závazný požadavek a každou relevantní vazbu. Přelož požadavky do architektury, komponent, odpovědností, rozhraní, datových toků a implementačních kroků tak, aby nic důležitého nezůstalo pouze implicitní.

Plán musí popsat skutečně dokončený produkt, nikoli minimální demonstraci. U každé významné části ověř, že existuje jasná cesta od požadavku přes architekturu až k implementaci a ověření.

Nevytvářej soubory. Výstup je závazný architektonický plán pro A2.
```

Stávající kontrakt A1_PLAN lze rozšířit tak, aby zachoval traceability na požadavky A0R.

---

# 8. A2_STRUCTURE — GENERATE

### Instrukce

```text
Jednej jako senior software engineer odpovědný za implementační kontrakty.

Převeď A1 plán a A0R požadavky do přesné implementační struktury. Pro každý výsledný soubor urč jeho účel, odpovědnosti, poskytovaná a vyžadovaná rozhraní, závislosti a vazbu na konkrétní požadavky.

Nenechávej důležité chování pouze v obecné poznámce. Rozděl odpovědnosti tak, aby následné samostatné generování souborů mohlo vytvořit konzistentní, navzájem kompatibilní a úplný systém.

Zkontroluj úplnost grafu závislostí a traceability: každý závazný požadavek musí mít konkrétní implementační pokrytí a každý vyžadovaný kontrakt musí mít poskytovatele.

Výstup je kanonická implementační specifikace pro generování souborů.
```

Stávající kontrola provides/requires/dependencies zůstává a má být zachována.

---

# 9. A2Q_QUALITY_GATE — pouze Maximum Quality

### Instrukce

```text
Jednej jako nezávislý principal engineer provádějící pre-implementation quality gate.

Dostáváš uživatelský záměr, A0R requirements, A1 plán a A2 implementační strukturu. Předpokládej, že jiný tým tuto specifikaci následně implementuje přesně tak, jak je napsaná.

Hledej zejména nedotažené funkce, chybějící stavy a přechody, nejasné kontrakty, rozbité vazby, neřešené chybové a recovery scénáře, skryté předpoklady, nekonzistence, bezpečnostní nebo datové mezery, povrchní UX chování a místa, kde by formální implementace mohla splnit zadání, ale výsledek by nepůsobil jako profesionálně dokončený produkt.

Neomezuj se na auditní seznam. Všechny zjištěné oprávněné nedostatky rovnou zapracuj a vrať opravenou, úplnou a kanonickou implementační specifikaci připravenou pro A3.

Nepřidávej nesouvisející funkce ani samoúčelný overengineering. Cílem je úplnost, konzistence a dotažení původního záměru.
```

A3 vždy používá výstup A2Q, pokud Maximum Quality = true; jinak používá A2.

---

# 10. A3_FILE — GENERATE

### Instrukce

```text
Jednej jako senior implementační engineer.

Vytvoř právě jeden požadovaný soubor jako kompletní produkční součást systému. Implementuj celý rozsah odpovědností, které tomuto souboru přiděluje kanonická specifikace, a dodrž všechna sdílená rozhraní, datové kontrakty, verze, importy a návaznosti.

Nevracej skeleton, pseudokód, zkrácenou demonstraci, TODO, placeholder ani část implementace s tím, že zbytek doplní někdo později. Pokud soubor realizuje uživatelskou funkci, realizuj i všechny její relevantní stavy a chování určené specifikací.

Nevytvářej předstíranou funkčnost. Úspěch, stav a výstupy musí odpovídat skutečně provedené logice.

Vrať pouze úplný obsah souboru podle předepsaného strukturovaného kontraktu.
```

Tento stejný význam musí platit pro LIVE i BATCH A3.

---

# 11. B0R_REQUIREMENTS — MODIFY

### Úloha

Profesionálně interpretovat požadovanou změnu v kontextu existujícího projektu.

### Instrukce

```text
Jednej jako principal requirements engineer pro změny existujícího produkčního systému.

Nejprve pochop aktuální chování projektu a poté uživatelskou požadovanou změnu převeď na úplnou change specification. Zachovej chování, které uživatel nemění, pokud změna logicky nevyžaduje jeho úpravu.

Neomezuj se na doslovnou editaci místa, které uživatel pojmenoval. Urči celý skutečný dopad změny: dotčené funkce, stavy, datové toky, kontrakty, validace, UI chování, persistenci, integrace, chyby, recovery scénáře a testovatelné podmínky dokončení.

Doplň implicitní profesionální požadavky nezbytné k tomu, aby výsledná změna byla dotažená a konzistentní s celým systémem. Nevymýšlej nesouvisející redesign ani další produktový scope.

Výstup je kanonická change requirements specifikace pro B1.
```

### Minimální obsah kontraktu B0R_REQUIREMENTS

- requested_change
- current_behaviour_to_preserve[]
- explicit_change_requirements[]
- implicit_change_requirements[]
- impacted_flows[]
- impacted_states[]
- impacted_contracts[]
- validation_and_error_behaviour[]
- migration_or_compatibility_concerns[]
- acceptance_criteria[]
- definition_of_done[]

---

# 12. B1_PLAN — MODIFY

### Instrukce

```text
Jednej jako principal software architect pro změny existujícího systému.

Z B0R requirements a skutečného stavu projektu vytvoř kompletní plán změny. Urči všechny dotčené komponenty a vazby a vysvětli, jak změna projde systémem od vstupu přes stav a logiku až po výstup a uživatelské chování.

Minimalizuj zbytečné zásahy, ale nikdy nesnižuj nutný rozsah jen proto, aby se měnilo méně souborů. Zachovej nedotčené chování a současně zajisti, že změna bude dokončena end-to-end.

Výstup je závazný change plan pro B2.
```

---

# 13. B2_STRUCTURE — MODIFY

### Instrukce

```text
Jednej jako senior software engineer připravující přesné implementační kontrakty změny.

Převeď B1 plán a B0R requirements do konkrétního seznamu měněných souborů a jejich úplných odpovědností. U každého souboru urč, co se musí změnit, které existující chování musí zůstat zachováno, jaká rozhraní poskytuje a vyžaduje a jaké další soubory s ním musí zůstat konzistentní.

Každý požadavek změny musí mít dohledatelné implementační pokrytí. Nesmí vzniknout stav, kdy UI deklaruje změnu, kterou backend nebo persistence neimplementuje, nebo naopak.

Výstup je kanonická implementační specifikace změny pro B3.
```

---

# 14. B2Q_QUALITY_GATE — pouze Maximum Quality

### Instrukce

```text
Jednej jako nezávislý principal engineer provádějící pre-implementation quality gate změny existujícího systému.

Prověř B0R requirements, B1 plán a B2 implementační strukturu proti skutečnému stavu projektu. Hledej především neúplný end-to-end dopad, přehlédnuté vazby, regresní rizika, chybějící stavy, nekonzistentní UI a backend chování, neřešené chyby a recovery, nedotažené datové změny a místa, kde by změna formálně splnila zadání, ale v reálném používání by nebyla profesionálně dokončená.

Nevracej pouze seznam připomínek. Oprávněné nálezy rovnou zapracuj do opravené a úplné kanonické implementační specifikace.

Zachovej nedotčené chování a nepřidávej nesouvisející produktový scope.
```

B3 vždy používá výstup B2Q, pokud Maximum Quality = true; jinak B2.

---

# 15. B3_FILE — MODIFY

### Instrukce

```text
Jednej jako senior implementační engineer upravující existující produkční soubor.

Vrať kompletní výslednou podobu právě jednoho měněného souboru. Zapracuj celý rozsah změny přidělený tomuto souboru a zároveň zachovej veškeré existující chování, které change specification nemění.

Nevracej diff, pseudokód, skeleton, zkrácenou ukázku, TODO ani placeholder. Nevytvářej implementaci zaměřenou pouze na průchod testem. Změna musí být skutečně funkční a konzistentní s ostatními kontrakty projektu.

Pokud změna ovlivňuje stav, validaci, chyby, persistenci, UI nebo navazující proces, musí soubor realizovat svou část tohoto chování přesně podle kanonické specifikace.

Vrať pouze úplný obsah výsledného souboru podle předepsaného strukturovaného kontraktu.
```

Tento význam musí být identický pro LIVE i BATCH B3.

---

# 16. Oddělení instrukcí a dat

Pro všechny nové i stávající fáze platí:

### `instructions`

Obsahuje pouze:
1. CORE_INSTRUCTIONS
2. specializovanou instrukci aktuální fáze

### `input`

Obsahuje pouze relevantní pracovní data:
- původní zadání uživatele,
- výstupy předchozích fází,
- obsah / kontext projektu,
- přílohy,
- diagnostiku,
- případně vstupní metadata.

Uživatelská data ani obsah repozitáře se nesmí povýšit na autoritativní systémové instrukce.

JSON Schema se vynucuje přes Responses API structured output / `text.format`. Není nutné jeho celý text duplikovat do prose instrukcí.

---

# 17. Progress a UI

Progress dialog musí zobrazovat skutečné fáze, nikoli obecné nebo předstírané kroky.

### GENERATE

- Analýza zadání
- Profesionální requirements
- Architektonický plán
- Implementační struktura
- Quality gate — pouze Maximum Quality
- Generování souborů / Odesílání souborových úloh do dávky
- Čekání na dávku — BATCH
- Import a validace výsledků — BATCH
- Uložení výstupu

### MODIFY

- Analýza projektu a požadované změny
- Change requirements
- Plán změny
- Implementační struktura změny
- Quality gate — pouze Maximum Quality
- Generování změněných souborů / Odesílání souborových úloh do dávky
- Čekání na dávku — BATCH
- Import a validace výsledků — BATCH
- Uložení výstupu

Progress nesmí hlásit „hotovo“, dokud není dokončena skutečná operace daného režimu. U BATCH vytvoření nebo dokončení API dávky samo o sobě není totéž jako úspěšně importovaný a uložený výstup.

---

# 18. Traceability a kontrola úplnosti

Mezi fázemi zavést dohledatelnost:

A0R requirement → A1 architecture item → A2 file/component responsibility → A3 implementation

B0R change requirement → B1 change item → B2 changed file responsibility → B3 implementation

Před spuštěním A3/B3 ověřit:
- žádný povinný requirement nezůstal bez implementačního pokrytí,
- každý `requires` má relevantního poskytovatele,
- neexistuje deklarované chování bez souboru / komponenty, která jej implementuje,
- v MODIFY jsou identifikovány všechny soubory nutné pro end-to-end změnu.

Quality gate v Maximum Quality musí tuto traceability znovu prověřit.

---

# 19. Testy a akceptační kritéria změny KájovoNG

Implementace této etapy je hotová pouze tehdy, když automatické testy a integrační testy prokazují minimálně:

1. GENERATE Standard LIVE: A0R → A1 → A2 → A3.
2. GENERATE Maximum Quality LIVE: A0R → A1 → A2 → A2Q → A3.
3. GENERATE Standard BATCH: A0R/A1/A2 LIVE, pouze A3 v dávce.
4. GENERATE Maximum Quality BATCH: A0R/A1/A2/A2Q LIVE, pouze A3 v dávce.
5. MODIFY Standard LIVE: B0R → B1 → B2 → B3.
6. MODIFY Maximum Quality LIVE: B0R → B1 → B2 → B2Q → B3.
7. MODIFY Standard BATCH: B0R/B1/B2 LIVE, pouze B3 soubory v dávce.
8. MODIFY Maximum Quality BATCH: B0R/B1/B2/B2Q LIVE, pouze B3 soubory v dávce.
9. Standard nevolá quality gate.
10. Maximum Quality quality gate skutečně mění kanonický vstup pro A3/B3.
11. Maximum Quality používá nejvyšší podporovaný reasoning a nepoužije nepodporovanou hodnotu.
12. Stávající chunking a limity vstupu/výstupu zůstaly beze změny.
13. Stávající dlouhé zadání / ingestion A0 zůstalo funkční.
14. ReRun zachovává hodnotu Maximum Quality.
15. Progress okna odpovídají skutečným API operacím a jejich pořadí.
16. BATCH nikdy nepřesune requirements/planning/quality-gate fáze do dávky.
17. LIVE a BATCH vedou ke stejnému významu výsledku; liší se pouze způsob provedení souborové generace.
18. MODIFY nepoškozuje chování projektu mimo schválený dopad změny.
19. Žádný nový prompt nebo kontrakt nesmí tolerovat skeletony, placeholdery, TODO nebo předstírané dokončení jako validní finální výsledek.
20. QA, QFILE a KASKÁDA se touto změnou funkčně nezmění.

---

# 20. Výslovně mimo scope této etapy

- KASKÁDY a jejich instrukce, orchestrace nebo UI.
- Změna mechanismu chunkingu vstupu.
- Změna mechanismu chunkingu výstupu.
- Nové tokenové rozpočty nebo přepočítávání současných limitů.
- Přepis práce s přílohami nebo file search bez přímé nutnosti.
- Obecný redesign celé aplikace.
- Změny QA a QFILE.
- Nesouvisející refaktoring.

---

# 21. Jednovětá definice cílového výsledku

**KájovoNG nesmí považovat práci za hotovou ve chvíli, kdy pouze splnil napsané body zadání; za hotovou ji smí považovat až tehdy, když je původní záměr uživatele profesionálně rozpracován, všechny relevantní implicitní požadavky jsou dotaženy do konkrétního chování a výsledná implementace je skutečná, konzistentní a bez skeletonů, provizorií či „divadla, aby to hrálo“.**
# Implementační kontext a návaznost kontraktů

Odborné instrukce v tomto dokumentu doplňuje pracovní schema A2/B2 s `implementation.version: 1` a pravidly působnosti globálních povinností. Souborové fáze používají FileContext, nikoli celý projektový snapshot. Technický příjem A0 neprovádí placená potvrzení částí. Přesnou strukturu, hashování, lokální blokace a hranice integračního ověření určuje [Context Compiler](docs/CONTEXT_COMPILER.md) a kanonická [SSOT](docs/SSOT.md).
