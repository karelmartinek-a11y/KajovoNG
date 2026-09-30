# R18 — doplnění operation namespace (B-01)

`operation-map.txt` je explicitně autorsky přiřazená posloupnost operací k přesným cestám jednotlivých podkapitol §26. `compile_operation_map.py` ji pouze rozvine proti neměnnému zdrojovému inventáři; negeneruje názvy operací z HTTP metody. Výstup `operation-map.expanded.json` obsahuje 503 konkrétních route→operation→writer→UI/chat/self-test vazeb. Tyto operation names doplňují §42. Zbývající původní interní názvy §42 zůstávají platné.

`canonical_writer` je repository home jediného doménového handleru, nikoli nezávislý scheduler či alternativní authority service. Pure authority/provenance kompilátory mají persistence adapter uvnitř původního parent domain commandu; nezískávají nové rooty ani permission model. Dotčené více-root invariants zachovávají původní aggregate ownership a R07 locks.

Q/C/P značí explicitně OWNER_QUERY/OWNER_COMMAND/PUBLIC_PROTOCOL. P neznamená neautentizované volání: login/MFA/enrollment má původní password/continuation guards, API-key exchange platný jediný OWNER key, browser preview platný current preview ticket/identity. Diagnostická UI/chat projekce nesmí obcházet danou autentizaci. Q může zapisovat povinnou access/audit evidence, nikoli business mutation.

Položka D (`POST /operations/:operationKey/invoke`) není další generic business operation ani permission bypass. Je pouze existující transportní dispatcher: operationKey musí označit konkrétní OWNER_COMMAND record, jeho plné input schema a auth/expected-state policy. Server vloží stejný command/logical operation jako typed route. Operation catalog invoke s neznámým, INTERNAL_PROTOCOL či PUBLIC_PROTOCOL klíčem se odmítá. Dispatcher nesmí emitovat druhý business outcome.

Aliases více rout na secret.bind/unbind a další původní operace mají odlišné transportní path parametry, ale jediný canonical command schema po serverovém resolve. Bulk binding je jedna explicitní saga se zmrazeným target setem a původními per-target guards, nikoli smyčka skrytých best-effort zápisů. Same logical request přes alias nebo typed route sdílí idempotency scope.

OWNER query/export rozlišuje query a vytvoření export artifactu: GET je query; secret/config/log export POST je command s audit/evidence a případným owned artifact lifecycle. POST preview/search/compatibility/config-validate jsou Q tam, kde přesná mapa deklaruje bez vedlejší business mutace; do těchto handlerů nelze později přidat verify/probe dispatch bez změny kontraktu.

## Další doplněné interní a provozní názvy

| Operation | Exposure | Writer | Smysl / parent relation |
| --- | --- | --- | --- |
| auth.password.sync | INTERNAL_PROTOCOL | owner-identity | Pouze deployment PASS step; password sync není OWNER UI edit. |
| auth.mfa.reset | OWNER_COMMAND | owner-identity | Current reauthenticated OWNER; zneplatní MFA/session epoch, povinný nový enrollment. Dostupné přes catalog invoke. |
| configuration.policy.recompute | AUTOMATED_MAINTENANCE | configuration | Výpočet z pinned inputs, ne nepovolené OWNER technical tunables. |
| configuration.effective.report | INTERNAL_PROTOCOL | configuration | Effective version ACK pod current service identity pro apply run. |
| deployment.start | OWNER_COMMAND | deployment | Signed release + exact SHA; catalog invoke/CI používají tentýž domain handler. |
| deployment.step.advance | INTERNAL_PROTOCOL | deployment | Current deployment fence, intent/outcome a unique successor. |
| backup.restore | OWNER_COMMAND | backup | Ověřený backup, nová incarnation, recovery barrier; catalog invoke. |
| dns.preflight | OWNER_COMMAND | deployment | Exact configured WAPI target a scope; žádný arbitrary provider command. |
| dns.record.create | INTERNAL_PROTOCOL | deployment | Parent ACME/DNS operation + exact row identity/author comment. |
| dns.record.remove | INTERNAL_PROTOCOL | deployment | Jen parent-owned TXT row ID, nikdy wildcard cleanup. |
| dns.propagation.verify | INTERNAL_PROTOCOL | deployment | Authoritative NS read-back pro exact challenge. |
| tls.renew | AUTOMATED_MAINTENANCE | deployment | Pinned current cert + renewal policy; canonical manual start přes OWNER command tls.renew.request. |
| tls.renew.request | OWNER_COMMAND | deployment | Rezervuje stejný renewal logical operation jako scheduler. |
| tls.certificate.install | INTERNAL_PROTOCOL | deployment | Parent renewal; SAN/key/digest ověřeny před atomic materialization. |
| tls.effective.verify | INTERNAL_PROTOCOL | deployment | Nginx/read-back evidence current renewal, nikoli další mutace. |
| platform.recovery.inventory | INTERNAL_PROTOCOL | recovery | R09 item scope/fence. |
| platform.recovery.readBack | INTERNAL_PROTOCOL | recovery | R09 registrovaný read-only oracle. |
| platform.recovery.cancel | INTERNAL_PROTOCOL | recovery | R09 existing work cancellation. |
| platform.recovery.cleanup | INTERNAL_PROTOCOL | recovery | R09 exact owned-resource compensation. |
| platform.recovery.resolve | OWNER_COMMAND | recovery | Current evidence CAS nad existujícím blockerem, catalog invoke. |
| platform.recovery.advance | INTERNAL_PROTOCOL | recovery | R09 persistovaný checkpoint/classification. |

Canonical handler IDs tabulky jsou `packages/domain/<writer>`. INTERNAl_PROTOCOL entries nejsou veřejně vyvolatelné přes catalog invoke. Automatická údržba má read-only inspection ve stejné domain UI a manuální canonical parent command; nevytváří druhého writera. SelfTest replay a shrink jsou samostatné operace v explicitní mapě; původní run/evidence nemění, vytvářejí nový test run s lineage.

## Co tento podklad ještě neprokazuje

503/503 coverage dokazuje úplnost pojmenování rout a návrhových parity vazeb. Neprokazuje úplné operation records §55.6, executable schemas, lock plans pro každý command, registr všech ne-API akcí ani existence odpovídajícího UI. Tyto podklady se musí zpracovat do skutečné implementační A2 a quality gate. Názvy nejsou náhradou implementace.
