# R16 — jediný field-level allowlist (B-09)

Tato oprava má přednost před větami o OWNER-editable technických hodnotách v §8.5, §19.3, §21.3 a UI kapitolách. Nemění dostupné business funkce. OWNER může spustit canonical rotate/restart/probe/repair action; nesmí tím změnit jejich technickou policy. UI/API/chat/import používají tentýž schema allowlist, nikoli pouze skrytí UI polí.

| Field / uzavřená skupina | Mode | Writer a pravidlo |
| --- | --- | --- |
| ai.globalModel, ai.chatModel, ai.generationModel, agentRevision.model | OWNER_INPUT | Configuration/agent revision writer; model ID musí existovat v current capability snapshotu. |
| secretRecord.label, description, type, value, externalAccountId, externalTenantId | OWNER_INPUT | Secret writer; value pouze immutable version. Žádný field pro OS/DB identity nebo permission. |
| secretVersion.externalExpiresAt | OWNER_INPUT | Přesný údaj externího vydavatele; není policy pro automatickou rotaci. |
| externalTarget.url, externalAccountId, externalTenantId, credentialBinding | OWNER_INPUT | External-target writer a původní target schema, URL a binding validation. |
| componentRevision.businessConfig | OWNER_INPUT | Výhradně pole konkrétního immutable component business schema, additionalProperties=false. Technické runtime/policy keys jsou zakázané i vnořeně. |
| generationJob.sources, content, target, businessRules, attachments | OWNER_INPUT | Generation writer; soubory jsou data/provenance, nikoli nová instruction authority. |
| browserOperation.url, goal, accountBinding, tenant, saveAccount, localTarget, localProfile | OWNER_INPUT | Browser writer; exact identity/scope dle původních kontraktů. |
| browserOperation.executionMode, compatibilityEngine | OWNER_INPUT | Volba podporovaného režimu či engine při explicitním business compatibility důvodu; žádné executable path/args. |
| browserSchedule.businessRule, timezone, schedule, eventTrigger, overlapPreference | OWNER_INPUT | Automation revision writer; technický catch-up/concurrency enforcement zůstává SYSTEM_MANAGED. |
| browserChallenge.response, confirmation, saveCredentialState | OWNER_INPUT | Jednorázový current challenge/confirmation command, ne trvalá libovolná konfigurace. |
| ui.layout, ui.language, ui.displayTimezone, ui.presentationMode, ui.savedQueries, ui.approvalPresentation | OWNER_PREFERENCE | Presentation writer; nemění ukládaná data, authority ani approval requirements. |
| alertChannel.destination, credentialBinding | OWNER_INPUT | Integrační business destination/credential; výběr primárního/záložního kanálu a retry spravuje systém. |
| owner.password | DEPLOYMENT_INPUT | Pouze PASS synchronizace canonical auth writerem, mimo config import/UI. |

Všechny ostatní položky §20.4 a §41 jsou SYSTEM_MANAGED, ownerEditable=false. Explicitně: retention, retry/rate limits, monitoring intervals/SLO/thresholds, session/trusted-device duration, MFA requirement, login throttling, recovery-code count, technical Secret rotation cadence, worker/concurrency/lease/deadline/budget, paths/ports/network/TLS/DNS a runtime identity/security profiles.

`PUT /monitoring/profiles/:componentId` přijímá pouze změnu povoleného business alert destination/runbook bindingu, pokud je v immutable schema této komponenty, nebo požadavek na přepočet profilu z jeho current vstupů. Body s technickým intervalem/SLO/threshold/retention je validation error bez částečného zápisu. Pro samotné vyžádání recompute je canonical operation `monitor.profile.recompute`; interní effective-value update není veřejnou editací.

Config reset znamená návrat OWNER preference/input na deklarovanou default/inherited hodnotu, nikoli reset secrets nebo technické safety policy. Import atomicky odmítne celý soubor při neznámém, nepovoleném či SYSTEM_MANAGED desired fieldu; smí obsahovat read-only effective snapshot jen v oddělené export evidence části, která se neaplikuje. Všechny skutečné business-schema leaf paths se při BUILD rozvinou do registru; unknown path je zakázaný, prefix sám nestačí k povolení.

Povinné testy: UI/API/chat/import shodně odmítají změnu lease/retention/MFA; model preference projde capability validací; secret external expiry se nezamění za rotation cadence; businessConfig nepřijme vnořený technický bypass; preference nemění audit content.

## R17 — pravidla policy derivace (B-10, zatím neúplný katalog)

Veškeré effective values vznikají deterministicky z immutable policy revision a persistovaných inputs. Baseline níže nemá skryté runtime autotuning ani fallback na process env. Bounds jsou inkluzivní; default se používá pouze při výslovně nepřítomném volitelném vstupu, nikdy při nevalidním vstupu. Chybějící povinný vstup blokuje affected operation. Recompute vytváří novou effective version; rozpracovaný run zůstává na pinned verzi.

| Stable key | Algoritmus baseline v1 | Default/min/max | Jednotka a trigger |
| --- | --- | --- | --- |
| worker.heartbeatMs | constant(5000) | 5000/5000/5000 | ms; service start |
| worker.leaseMs | 6 × worker.heartbeatMs | 30000/30000/30000 | ms; claim, nikoli resurrection po expiry |
| worker.minDispatchWindowMs | 2 × worker.heartbeatMs | 10000/10000/10000 | ms; fresh pre-dispatch |
| queue.pollMs | constant(1000) | 1000/1000/1000 | ms; worker start |
| queue.normalCapacity | constant(1024) | 1024/1024/1024 | durable reservations na queue kind |
| queue.recoveryCapacity | constant(64) | 64/64/64 | oddělené reservations; normal admission je nespotřebuje |
| worker.claimBatch | constant(16) | 16/16/16 | items na bounded claim cycle |
| worker.concurrency | max(1,min(4,floor(availableCpuCores/2))) | bez default/1/4 | slots; capability inventory, pin při startu |
| provider.readOnlyRetryAttempts | constant(5) | 5/5/5 | celkové attempts včetně prvního |
| provider.createSdkRetries | constant(0) | 0/0/0 | retries; create-class request descriptor |
| retry.availableDelayMs | min(30000,1000×2^min(attempt-1,5)) + uint16be(SHA256(operationId + ':' + attempt)[0:2]) mod 251 | bez default/1000/30250 | ms; pouze retry-safe attempt; bez process random |
| auth.sessionDurationMs | constant(43200000) | 43200000/43200000/43200000 | ms; session create |
| auth.idleTimeoutMs | constant(1800000) | 1800000/1800000/1800000 | ms; accepted authenticated activity |
| auth.trustedDeviceDurationMs | constant(2592000000) | 2592000000/2592000000/2592000000 | ms; evidence only, nikdy MFA bypass |
| auth.mfaRequired | constant(true) | true/true/true | boolean; interaktivní login |
| auth.recoveryCodeCount | constant(10) | 10/10/10 | codes; enrollment/rotation |
| auth.loginFailureWindowMs | constant(900000) | 900000/900000/900000 | ms; login failure |
| auth.loginFailureThreshold | constant(5) | 5/5/5 | failed attempts za window |
| auth.loginLockMs | constant(900000) | 900000/900000/900000 | ms; threshold crossing |
| monitor.schedulerMs | constant(1000) | 1000/1000/1000 | ms; tick, stable occurrence dedupe |
| monitor.probeMs | constant(30000) | 30000/30000/30000 | ms; component readiness |
| monitor.staleMs | 3 × monitor.probeMs | 90000/90000/90000 | ms; evidence evaluation |
| monitor.repairCooldownMs | constant(300000) | 300000/300000/300000 | ms; alert episode + policy revision |
| monitor.recertificationMs | constant(86400000) | 86400000/86400000/86400000 | ms; stable schedule |
| monitor.alertDedupeMs | constant(300000) | 300000/300000/300000 | ms; within same episode/condition digest |
| log.debugRetentionMs | constant(604800000) | 604800000/604800000/604800000 | ms; pouze data nechráněná pending/closure/replay reference |
| audit.retention | constant('UNTIL_VERIFIED_ARCHIVE_AND_NO_LIVE_REFERENCES') | jediná enum hodnota | žádné time-only smazání evidence |
| release.retention | retain current + frozen rollback + all referenced releases | žádný časový fallback | recompute při pointer/reference změně |
| deployment.healthySamples | constant(3) | 3/3/3 | consecutive exact release/epoch samples |
| deployment.healthySampleMs | constant(5000) | 5000/5000/5000 | ms; verify phase |
| deployment.startupDeadlineMs | constant(300000) | 300000/300000/300000 | ms; konec waitu vede do recovery, ne known-not-applied |
| tls.renewBeforeExpiryMs | constant(2592000000) | 2592000000/2592000000/2592000000 | ms; cert inventory |

Při rozporu s konkrétním explicitním operation contractem z původního SSOT je policy compilation BLOCKED, ne automatické min/max sloučení. Safety hodnoty se nesmějí měnit za běhu podle latence. Výše je vyplněný základ; browser/runtime/model/generation/backup a zbývající network/resource policy stále vyžadují úplné jednotlivé záznamy. B-10 proto zatím není uzavřen.
