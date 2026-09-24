# Procesní vazby

Kanonické kontrakty určuje [SSOT](SSOT.md). Tento dokument popisuje přípravu a souborovou výrobu GENERATE/MODIFY; není potvrzením úplného auditu ostatních procesů aplikace.

## Společná příprava GENERATE a MODIFY

```mermaid
flowchart TD
    S[Zmrazení zdrojů a konfigurace] --> C{Existuje checkpoint?}
    C -->|ano| V[Kontrola hashů, režimu a quality policy]
    C -->|ne| R[Requirements A0R nebo B0R]
    V --> R
    R --> P[Plán A1 nebo B1]
    P --> I[Index souborů a rozhraní A2_SPINE nebo B2_SPINE]
    I --> D[DETAIL každého vyráběného textového souboru]
    D --> G[Sestavení IMPLEMENTATION_GRAPH_V3]
    G --> Q{Maximum Quality?}
    Q -->|ano| K[Kontrola a oprava návrhu A2Q nebo B2Q]
    Q -->|ne| T{Zastavit po plánu?}
    K --> T
    T -->|ano| E[plan_ready]
    T -->|ne| W[Výroba podle grafu]
```

Již přijaté položky checkpointu se znovu negenerují. MODIFY navíc předává neměnné originály, inventář a povinnosti zachování chování. DETAIL se obnovuje po jednotlivých souborech. Příprava je LIVE i tehdy, když následná souborová výroba používá BATCH.

## Jeden přípravný požadavek

```mermaid
flowchart TD
    I[Explicitní podklady fáze] --> M[Strict JSON maska a payload]
    M --> J{Existuje totožná zmrazená práce?}
    J -->|ano| O[Původní payload včetně původní masky]
    J -->|ne| N[Nový pracovní požadavek]
    O --> R[Stejné Response ID nebo uložená odpověď]
    N --> R
    R --> P{Stav poskytovatele}
    P -->|queued nebo in_progress| R
    P -->|completed| V[Strict parser a přesná odeslaná maska]
    P -->|chyba nebo neúplný výstup| F[Záznam chyby bez přijetí dat]
    V --> B{ready nebo blocked?}
    B -->|blocked| U[Doplnění zadání uživatelem]
    B -->|ready| S[Sémantické vazby ke vstupům]
    S -->|platné| C[Uložení checkpointu a dokončení kroku]
    S -->|neplatné| A{Povolena automatická oprava a zbývá pokus?}
    V -->|neplatné| A
    A -->|ano| X[Původní vstup, poslední kandidát a konkrétní chyba]
    X --> M
    A -->|ne| F
```

Stejný kandidát se stejnou validační chybou ukončuje opravy jako `NO_PROGRESS`. Neznámý výsledek odeslání není povolením pro nový POST. Obnova přípravy může převzít starou masku jen při shodě ostatního obsahu požadavku; nezaměňuje tím změněné zadání za původní práci.

## Souborová výroba

```mermaid
flowchart TD
    G[Validovaný graf a schválený výběr] --> D[Závislosti a dostupné artefakty]
    D --> R{Je cíl připraven?}
    R -->|ne| B[Čekání na zdroj nebo závislost]
    R -->|ano| K{Druh cíle}
    K -->|netextový| X[Určený resource producer]
    K -->|textový| C[ContextCompiler pro jediný cíl]
    C --> T{Transport}
    T -->|LIVE| L[Samostatný FILE_CONTENT_V1 požadavek]
    T -->|BATCH| Q[JSONL připravené dependency vlny]
    Q --> S[Upload a jediný pracovní submit dávky]
    S --> I[Převzetí výsledků podle custom_id]
    L --> V[Odeslaná maska, obsah a pravidlo allow_empty]
    I --> V
    V --> A[Neměnný staged artefakt a jeho hash]
    X --> D
    A --> D
```

Kontraktní závislosti poskytují popis rozhraní; samy nevyžadují pořadí výroby. Obsahové závislosti vyžadují konkrétní dostupný artefakt se shodným kontraktem a hashem. BATCH řádky mají samostatný explicitní kontext bez `previous_response_id` a bez `background`. Stažení a kontrola integrity nejsou testem funkčnosti produktu.
