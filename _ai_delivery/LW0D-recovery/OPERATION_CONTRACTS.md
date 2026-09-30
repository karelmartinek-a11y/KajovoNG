# B-01 — konkrétní operation contracts a kompilátor

Pracovní návrhový registr, nikoli dokončený Contract Pack nebo povolení Batch.
Každý záznam má všechna pole §55.6, oddělená vstupní/výstupní JSON Schema
2020-12, explicitní sémantiku, state/transaction/lock/retry/reconciliation/closure
policy a zdrojové fragmenty s přesným textem a SHA-256. Inventář všech 503 API
aliasů zůstává byte-logicky shodný s `operation-map.expanded.json`.

`status=PARTIAL_DESIGN` a `batchReady=false` se nezaměňují za failure původního
projektu ani za runtime verdict. Neexistence budoucí implementace není DESIGN
blocker (R01). Chybějící schema, exact read-back evaluator, action mapping nebo
lock-plan metadata však DESIGN blokují. Lokální compiler testy neprokazují
správnost budoucích produkčních handlerů.

## Rozdělení práce a plugin API

Kompilátor spravuje B-01 agent společně se skupinami `component` protocol,
`platform.recovery`, `runtime` a `audit`. Další pomocníci smějí podle zadání
hlavního agenta přidávat tyto oddělené moduly se vstupem `register(builder)`:

- `operation_contracts_auth_secrets_config.py`: auth, ownerApiKey, secret,
  configuration, deployment/backup/DNS/TLS; doplnění rozsahu lze dohodnout.
- `operation_contracts_agent_mcp.py`: agent, MCP, model evidence.
- `operation_contracts_browser.py`: browser a automation.
- `operation_contracts_generation.py`: generation a centrální chat.
- `operation_contracts_remaining.py`: po dohodě zbývající skupiny a OWNER
  component lifecycle (nikoli šest interních component protocol záznamů).

Moduly nic nespouštějí při importu. `register` nemá síťové ani souborové zápisy,
nevytváří celý bundle a nepřepisuje cizí registry/operace. Jméno musí být z
`builder.api_aliases` nebo explicitního §42/R18 inventáře. Nový §40 namespace
je potřeba nejprve dohodnout, nikoli potichu odvodit z názvu tlačítka.
Kompilátor při běhu načte pouze výše uvedené existující soubory; chybějící modul
není sám o sobě blocker, chybějící operation contract ano.

Importovat lze z `compile_operation_contracts`:

```python
Builder, add_operation, base_policies, reseal
obj, arr, enum, nullable
UUID, HASH, UINT, TIME, TEXT, BOOL
```

`Builder` nabízí:

```python
b.root                          # soukromý recovery adresář
b.api_aliases                   # původních 503 explicitních map
b.reg[registry_name][stable_id]  # definice; cizí definice nepřepisovat
b.operations                    # již registrované §55.6 records
b.semantic[operation_id]        # konkrétní sémantické guardy
b.parity[operation_id]          # návrhové exposure metadata
b.source("49.14")               # exact fragment původního SSOT, návrat "ssot:49.14"
b.amendment("RESENI_KONKRETNI.md", "R09")  # exact rozhodnutí
b.put(registry, id, definition, refs)     # id; odmítá rozdílnou redefinici
b.policy(key, definition, refs)          # vrací "policy:" + key
b.schema(key, property_dict, refs)       # vrací "schema:" + key
```

Registry jsou `writers`, `roots`, `schemas`, `stateMachines`, `transitions`,
`policies`, `events`, `outbox`, `surfaces`, `tests`, `gates`, `requirements`,
`sources`. `put` automaticky doplní source refs a canonical digest. `source`
nevytváří textový placeholder: fragment obsahuje původní bytes, řádky a hash.

`add_operation` má přesný podpis:

```python
add_operation(
    b, name, family, exposure, effect, retry, expected,
    input_fields, output_fields, policies, transitions,
    refs, requirements, semantic, parent,
)
```

Parametry:

- `name`: explicitní canonical operation name, nikoli HTTP route.
- `family`: lokální identifikátor writera/rootu; před voláním musí existovat
  `writer:<family>` a `root:<family>`. Repository home writera musí odpovídat
  původní API mapě. Různé aggregate roots téhož modulu mohou mít různé family.
- `exposure`: přesný enum §55.18. INTERNAL nikdy nemá public invoke endpoint.
- `effect`: přesný průřezový enum §10.12, ne odhad podle slovesa.
- `retry`: `SAFE_RETRY`, `RETRY_AFTER_RECONCILIATION`, `NO_AUTOMATIC_RETRY`.
- `expected`: konkrétní stavy cílového automatu, ne `ANY` nebo prázdný seznam.
- `input_fields`, `output_fields`: mapy field → JSON Schema. Wrapper je objekt,
  všechna uvedená pole jsou required a additionalProperties=false; nullable
  pole musí být explicitní. Jde o payload pod samostatně závazným KCIP/API
  envelope; pokud jsou potřeba oneOf/conditionals, doplnit přes registrované
  schema a `reseal(schema_record)`.
- `transitions`: neprázdný seznam `{from, to, guard, write}`. Query používá
  explicitní read-only self-edge; nesmí omylem získat mutation policy.
- `refs`: existující source IDs z `b.source`/`b.amendment`.
- `requirements`: existující A0 traceability IDs v `b.reg['requirements']`.
  Zatím jsou společně registrovány E-003/032/033/034/038/145/148/170. Další
  záznamy přidat z přesného `source/A0.original.json`, nikoli vymýšlet KCML-REQ
  digest. Převod na normativní atomy §55.5 je oddělený DESIGN úkol.
- `semantic`: musí mít `stateDomain`, konkrétní `rule`, `testCases` (list),
  `recoveryOnly` (bool), `newBusinessEffectsAllowed` (bool), popis reserved
  identity a případných exact revision-dependent schema guards.
- `parent`: konkrétní vztah k parent business operation pro interní protokol;
  pro OWNER root explicitně kořenová OWNER logical operation.

`policies` musí mít přesně těchto 21 polí, každé hodnotou existující policy ID:

```text
expectedStateVersionPolicy idempotencyScope idempotencyKeySource
requestDigestProfile concurrencyScope concurrencyKeyDerivation concurrencyClaimPoint
deadlinePolicy retryDirectiveMapRef transactionProfileId orderedLockPlanId
fencingPolicy checkpointPolicy possibleEffectTrigger reconciliationOracleId
cancellationPolicy successorPolicy cleanupPolicy activationRelation
```

Poznámka: výčet obsahuje 19 polí; autoritou je skutečný kontrolovaný seznam
`REF_FIELDS` po odečtení writer/root/schema/state-machine IDs. Nesmí se doplnit
prázdnými hodnotami jen kvůli tvaru.

`base_policies()` vrací společný digest, retry, WORKER_COMMIT transaction,
fence, deadline, checkpoint, cancellation a closure. Není to kompletní
kontrakt: zvláště pro query je nutné nahradit transaction, lock, CAS,
idempotency, checkpoint/cleanup a successor policy konkrétní read-only verzí.

`add_operation` připojí skutečné typed API aliases podle explicitní mapy
(nikoli odvozené z metody), případně OWNER catalog projection, návrhové
UI/chat/test/gate IDs, audit record a celý §55.6 tvar. Pokud operation emituje
outbox, plugin musí registrovat konkrétní `outbox` definition, připojit její ID
do `record['outboxPurposes']` a zavolat `reseal(record)`.

## Vzor struktury plugin funkce

Vzor používá vlastní konkrétní data modulu, nikoli dodatečné implicitní defaulty:

```python
from compile_operation_contracts import add_operation


def register(b):
    # Funkce modulu vracejí plně autorsky zpracované definice, nikoli heuristiky.
    refs = [b.source("49.14"), b.source("51.23"), b.source("26.6")]
    b.put("writers", "writer:agent-run", {
        "repositoryHome": "packages/domain/agent",
        "singleWriter": True,
        "implementationStatus": "PLANNED",
    }, refs)
    b.put("roots", "root:agent-run", {
        "table": "agent_run", "ordinal": 100, "key": "UUID id",
    }, refs)
    for spec in concrete_agent_run_contracts(b, refs):
        add_operation(b, **spec)
```

`concrete_agent_run_contracts` je zde pouze označení autorských dat pomocníka,
není to existující fallback implementace a nesmí vracet obecné kopie pro
všechna jména. Skutečné použitelné vzory jsou funkce `component_contracts` a
`recovery_contracts` v kompilátoru. Pro query zvláště `component.state.query`;
pro internal effect child `component.control.disable`; pro guarded OWNER
resolution `platform.recovery.resolve`.

## Lokální kontrola a předání

```powershell
$env:PYTHONUTF8='1'
$env:PYTHONDONTWRITEBYTECODE='1'
.venv/Scripts/python.exe _ai_delivery/LW0D-recovery/compile_operation_contracts.py
.venv/Scripts/python.exe _ai_delivery/LW0D-recovery/compile_operation_contracts.py --check-complete
```

První příkaz kontroluje konzistenci dosud zpracované části. Druhý musí vracet
exit 2, dokud zůstává missing operation/action nebo skutečná DESIGN mezera.
`--json` vypíše deterministický JSON bez souborového zápisu. Vygenerovaný
`operation-contracts.json` je odvozený artefakt; nesmí se ručně přepisovat ani
vydávat za úplný pouze proto, že projde JSON parserem.
