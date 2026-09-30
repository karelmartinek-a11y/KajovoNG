# R15 — auditní hash a canonical vectors (B-11)

Rozšiřuje R05. `chain_format_version=1` má právě tento preimage:

```text
ASCII("KCML-AUDIT") || 0x00 || uint32be(1) || previous_hash[32]
|| uint64be(chain_sequence) || uint64be(canonical_bytes_length) || canonical_bytes
```

Výsledkem je SHA-256 těchto bytes. Bez oddělovačů navíc, newline, BOM či hex encodingu binárních polí. Sequence je 1..9223372036854775807, délka unsigned počet UTF-8 bytes, nikoli znaků. Jiná verze, nesprávná délka previous hash či sequence mimo rozsah se odmítá. Genesis previous hash má přesně 32 nulových bytes. Nedochází k přetečení nebo převodu bigint přes floating point.

Canonical audit JSON používá R05 a vždy obsahuje tyto keys:

```text
chainSequence previousHash eventId eventType actor accessChannel
executionContextId authorityLineageId authorityLineageDigest ownerIntentId
ownerIntentDigest operationContextId operationContextDigest actionPlanDigest
argumentOriginSummary secretUseContextRefs object operation logicalOperationId
commandId beforeDigest afterDigest stateVersionBefore stateVersionAfter
revisionId bindingSetRevisionId activationEpoch fence platformIncarnationId
deploymentEpoch recoveryEpoch correlationId causationId traceId timestamp
```

Chybějící neaplikovatelné skaláry mají explicitní null, množiny prázdné pole. Žádné keys se tiše nevynechávají. `chainSequence`, state versions, epochs a fence jsou canonical decimal strings podle R05; vnější binary sequence musí odpovídat vnitřnímu chainSequence. previousHash je lowercase 64 hex a odpovídá vnějším 32 bytes. eventId, correlation a object identity jsou exact immutable hodnoty původního commandu. `actor` je OWNER nebo SYSTEM, `object` obsahuje kind/id, `argumentOriginSummary` má verzi 1 a pole records se stabilním pořadím podle argument path. secretUseContextRefs se seřadí podle lowercase UUID bytes a nemají duplicity. Timestamp má přesně šest desetinných míst v UTC `YYYY-MM-DDTHH:mm:ss.ffffffZ` z DB času získaného po locks.

Samotný eventHash, archive status, delivery attempt ani proměnlivá projekce nejsou uvnitř canonical_bytes. Before/after se vážou digestem uložených immutable hodnot, nikoli měnitelným JSONB pořadím. Audit format/schema revision je součást build manifestu. Změna kteréhokoli pravidla vyžaduje novou verzi hash formátu, ne tichý upgrade.

`canonical-vectors.json` obsahuje přesné vstupní/canonical páry pro UTF-16 key ordering, escaping, Unicode bez normalizace, zápornou nulu, exponent a bigint strings, i rejection cases. `audit-vectors.json` obsahuje byte-level funkční vectors, nikoli příklady kompletního produkčního audit eventu. Referenční výpočet je ověřen nezávisle Pythonem a Node.js. To neprokazuje existenci PostgreSQL funkce ani produkčního auditu; DB a native implementace musí stejné vectors spotřebovat při BUILD/RUNTIME testech.
