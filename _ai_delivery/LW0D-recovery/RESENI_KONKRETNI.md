# Konkrétní doplnění technického dodatku

Normativní návrh obnovy; doplňuje R01–R05 v TECHNICKY_DODATEK.md. Neprohlašuje neprovedené runtime testy za PASS. Původní zadání zůstává zachováno. Při rozporu má přednost pouze konkrétní níže uvedená oprava.

## R06 — kompozitní lifecycle (B-04; §49.12)

Lifecycle určuje business admission, activation technický stav. Oba se mění v jednom commitu pod component root lockem. Původní invariant ACTIVE ⇒ activation ACTIVE zůstává.

| Událost | Před: lifecycle / activation | Po | Povinný guard |
| --- | --- | --- | --- |
| Disable/suspend s vypnutím | ACTIVE / ACTIVE | SUSPENDED / DISABLE_REQUESTED | Admission stop + intent + outbox atomicky. |
| Quarantine aktivního runtime | ACTIVE nebo SUSPENDED / ACTIVE | QUARANTINED / DISABLE_REQUESTED | Current quarantine evidence; admission stop. |
| Disable potvrzeno | SUSPENDED nebo QUARANTINED / DISABLE_REQUESTED nebo DISABLE_UNCONFIRMED | stejný lifecycle / INACTIVE | Runtime, route a heartbeat potvrzují disabled. |
| Disable nejasné | SUSPENDED nebo QUARANTINED / DISABLE_REQUESTED | stejný lifecycle / DISABLE_UNCONFIRMED | Claims zůstávají, business calls zakázané. |
| Disable prokazatelně neprovedeno | SUSPENDED nebo QUARANTINED / DISABLE_UNCONFIRMED | stejný lifecycle / ACTIVE | Technický runtime aktivní; business admission stále zavřené. |
| Disable UNKNOWN | SUSPENDED nebo QUARANTINED / DISABLE_UNCONFIRMED | stejný lifecycle / BLOCKED | Manual review a blocking claims. |
| Příprava enable | APPROVED, SUSPENDED nebo QUARANTINED / INACTIVE | stejný lifecycle / READY | Žádný unresolved effect; quarantine recertification PASS. |
| Enable | stejný lifecycle / READY | READY_FOR_ACTIVATION → ENABLE_REQUESTED | Původní readiness a barrier guards. Lifecycle beze změny. |
| Enable/postflight potvrzen | APPROVED, SUSPENDED nebo QUARANTINED / ENABLE_REQUESTED | ACTIVE / ACTIVE | Exact pointers, bindings, epoch, effective runtime a route současně. |
| Enable selhal | APPROVED, SUSPENDED nebo QUARANTINED / ENABLE_REQUESTED | stejný lifecycle / BLOCKED | UNKNOWN nepovoluje uvolnit claims. |
| Suspend pouze admission | ACTIVE / ACTIVE | SUSPENDED / ACTIVE | Current command; runtime disable se netvrdí. |
| Obnovení admission | SUSPENDED / ACTIVE | ACTIVE / ACTIVE | Explicitní enable + current readiness/bindings/epoch. |
| Quarantine již vypnutého | SUSPENDED / INACTIVE | QUARANTINED / INACTIVE | Current quarantine evidence. |
| Odstranění quarantine | QUARANTINED / INACTIVE nebo ACTIVE | SUSPENDED se stejnou activation | Opravená příčina; admission stále zavřené. |
| Retirement | ACTIVE / ACTIVE | SUSPENDED / DISABLE_REQUESTED | Saga; RETIRED / INACTIVE až po disable a dependency/route closure. |

Přímá hrana ACTIVE → RETIRED se nahrazuje retirement sagou. DEREGISTERED vzniká jen z RETIRED/INACTIVE se splněnými původními closure guards. DRAFT/REVIEW/APPROVED bez runtime zachovávají původní přípravné hrany. Neuvedené kombinace se nesmějí dovodit z heartbeatů. Test musí ověřit invariant po každém commitu, včetně kill po disable intentu a před ACK.

Rollback activation setu zachovává §49.17: current set přechází `VERIFYING|ACTIVE → ROLLING_BACK → ROLLBACK_VERIFYING → ROLLED_BACK`. Nezakládá kvůli tomu nový activation set. Guard ověřuje current head, candidate snapshot, switch epoch a current fence; reverse switch atomicky přidělí vyšší rollback epoch a obnoví celý zmrazený previous snapshot. Při previous `ABSENT` odstraní aktivní pointers a lifecycle komponenty nesmí zůstat ACTIVE. Historický ACTIVE set, který již není current, rollbackovat nelze. ROLLED_BACK vyžaduje potvrzení effective previous/absent epoch a rollback probes; nejasný effective stav vede do MANUAL_REVIEW a blokuje mutující provoz.

Odlišně se řídí rollback celé aplikace podle §49.24: pozdější rollback již aktivního application release zakládá nový deployment run s vlastním logical operation ID a vyšším application deployment epoch. Terminal ACTIVE deployment run se neotevírá. Regrese musí rozlišit oba typy rollbacku, odmítnout stale activation snapshot a ověřit zneplatnění pozdního candidate ACK po reverse switchi.

## R07 — lock plan (B-06; §51.6, §51.8, §51.24, §51.37–38)

Před class A se přidává A0: transaction-scoped advisory lock PLATFORM_RECOVERY_BARRIER, dvojice int4 `(1000,1)`. Běžné admission a recovery-item transakce používají `pg_advisory_xact_lock_shared(1000,1)`; změna recovery headu exclusive `pg_advisory_xact_lock(1000,1)`. Povoleny jsou odpovídající try varianty s úplným rollbackem při neúspěchu. Session locks a upgrade shared → exclusive jsou zakázané. Jde o explicitní rozšíření povolených funkcí §51.8. [PostgreSQL tyto shared/exclusive transaction varianty poskytuje.](https://www.postgresql.org/docs/current/functions-admin.html#FUNCTIONS-ADVISORY-LOCKS)

Úplné pořadí: A0 → A → B0 recovery head → B1..B4 → C → D → E → F → G → H → I. Read-only kontrola recovery headu je chráněna A0; potřebný row lock při jeho změně patří pouze do B0, nikdy za doménové rooty. Existující ordinals se nemění.

Doplňuje se aggregate root BROWSER_BRIDGE = 77, existující tabulka `browser_local_bridge` z §25.13. Nevytváří se druhý bridge root s jiným názvem. Jeho mutable certificate/connection/session-assignment/profile-lease children mají H80. Multi-parent assignment zamyká E70 session → E75 account → E77 bridge, pak F claims, poté H80.

V §51.24.3 se F claims přesouvají před všechny control/context/page/frame/confirmation children H80. V §51.37 se F claims přesouvají před checkpoint H50 a intent/context/action-plan H65. Kompletní lock set se získá z nezamykajících hints a po root/claim locks se znovu ověří jeho verze/digest. Nový dříve řazený root či claim znamená rollback a nové naplánování; ne pozdní získání nižšího locku. Audit I zůstává poslední. Žádný DB lock se nedrží během externího callu.

Regrese: browser F před H80; authority F před H50/H65; bridge E70/E75/E77 před H80; exclusive A0 versus concurrent admissions; zákaz lock upgrade; restart transakce při změně hintu.

## R08 — effect T3 není terminal celého commandu (B-07; §51.28)

Ruší se bezpodmínečné TERMINAL admission v T3 jednoho effectu. Admission vlastní `domain_command`, nikoli jednotlivý effect/attempt.

| T3 výsledek | Admission | Parent a claims |
| --- | --- | --- |
| UNKNOWN | RECONCILING | Nonterminal/manual review; blocking claims zůstávají. |
| Známý effect, další business krok | ADMITTED nebo CHECKPOINTED dle checkpointu | Stejný logical command + unique successor; command-scoped claims zůstávají. |
| Známý business outcome, pending cleanup | CHECKPOINTED | Výsledek lze uložit, closure nikoli; cleanup claims zůstávají. |
| Všechny effects známé, bez successor a s COMPLETE cleanup | TERMINAL | Jeden canonical outcome a uvolnění claims atomicky. |

RECONCILING → ADMITTED/CHECKPOINTED vyžaduje current evidence; přechod do TERMINAL vyžaduje closure celého commandu. TERMINAL je immutable. Activation drain počítá i CHECKPOINTED a RECONCILING. Více-domain admission terminalizuje atomicky pod všemi domain heads. Samotný úspěch jednoho effectu nesmí uvolnit barrier pro další dosud neprovedený effect.

## R09 — recovery-only execution (B-14; §49.33, §51.38)

Při změně DB identity/incarnation/deployment epoch přejde head z libovolného stavu včetně READY do STARTING pod exclusive A0, s novým immutable attemptem a vyšší recovery epoch. STARTING → RECONCILING provádí jediný current fenced controller.

V RECONCILING/BLOCKED/MANUAL_REVIEW jsou pod shared A0 povoleny pouze konkrétní inventory-item operace:

| Operation | Povolené chování |
| --- | --- |
| platform.recovery.inventory | Čtení skutečného stavu, evidence a klasifikace. |
| platform.recovery.readBack | Registrovaný read-only oracle již existujícího effectu. |
| platform.recovery.cancel | Zastavení dřívějšího dispatchu, ne nový business požadavek. |
| platform.recovery.cleanup | Přesně vlastněné resources a jejich deklarovaná compensation/cleanup. |
| platform.recovery.resolve | OWNER CAS existujícího blockeru nad current evidence digestem. |
| platform.recovery.advance | Persistovaná klasifikace + unique successor rezervace nedispatchable před READY. |

Nejde o novou roli ani token. Trusted serverový context musí odpovídat current controlleru, attemptu, itemu a fence. Caller nemůže přepnout běžný command na recovery parametrem. Nový model create, browser business input, agent/tool business call a activation jsou během barrier zakázané. Cleanup s externím účinkem má vlastní T1/D/E/T2/T3 a allowlisted compensation, není obecným mutation bypassem. Read-back GET není read-only pouze podle HTTP metody: rozhoduje exact oracle contract.

Recovery má rezervovanou kapacitu, current DB-time lease a fresh pre-dispatch check. Takeover odmítá starý fence. UNKNOWN se nesmí obejít kvůli READY. READY vyžaduje původní stabilní druhý inventář bez mandatory startup blockers. Zvolená klasifikace RESUME může připravit další work, nesmí jej vykonat před READY. Bez požadované environment/runtime evidence zůstává BLOCKED, ne mock PASS.

## R10 — closure a předání resources (B-18; §48.38, §49.34)

Authority-bearing resource má current owner `(ownerKind,ownerId,ownerGeneration)` a immutable historii transferů. Převod z job/run na product runtime/release/session zamyká oba rooty a atomicky zapisuje acceptance cílovým current ownerem, pointer a novou authority epoch. Nepotvrzené předání stále blokuje starého ownera. Převod na terminal owner, bez cleanup povinností nebo jen změnou booleanu persistent je zakázaný. Starý worker po předání nemá autoritu.

Closure jobu kontroluje resources s current ownerem jobu a všechny jeho nepotvrzené transfer relations. Převzatý aktivní runtime není pending child dokončeného jobu; má vlastního live ownera, runtime closure kontrakt a zůstává v inventáři aktivního produktu. Immutable active release snapshot není totéž jako živý runtime proces.

Oddělují se businessOutcome, cancellationSafePoint a closureState. FAILED/CANCELLED lze podle §48.38 uložit před cleanup, ale COMPLETE closure až s nulovým blocking inventářem. Úspěšný job/run terminal vyžaduje COMPLETE. ACTIVE komponenta vyžaduje dokončené předání a readiness, nikoli zastavení právě spuštěného runtime. Admission/capacity se uvolňují podle R08. Regrese zahrne legitimní runtime handover, orphan process, transfer na terminal parent a late old-owner write.

## R11 — realizovatelná socket identity (B-15; §50.8)

Porovnání device/inode mezi fstat socket FD a lstat filesystem path se ruší; nahrazují je všechny tyto kontroly:

1. Trusted service start přijme pomocí `sd_listen_fds_with_names(1)` právě jeden očekávaně pojmenovaný FD; ověří current PID, unit/InvocationID a release. Environment ani samotné FD name nejsou trust anchor.
2. `sd_is_socket_unix(fd, SOCK_STREAM, 1, canonicalPath, 0)` nebo ekvivalent ověří AF_UNIX, stream, listening a přesnou filesystem adresu. Abstract sockets jsou zakázané. CLOEXEC a NONBLOCK se nastaví a zpětně ověří před event loopem.
3. Path se ověří pod trusted `/run` dirfd přes openat2 BENEATH/NO_SYMLINKS/NO_MAGICLINKS/NO_XDEV. NO_XDEV se neaplikuje přes legitimní mount `/run` z `/`. Parent directories jsou root-owned, pro service/callers nezapisovatelné; socket uid/gid/mode zůstávají podle §50.7–8.
4. fstatat NOFOLLOW ověří filesystem socket type/uid/gid/mode; jeho device/inode se porovnává se svou předchozí hodnotou před/po ověření, nikdy s fstat listeneru. Canonical path může vytvářet/odstraňovat pouze trusted systemd lifecycle.
5. Readiness self-probe se připojí na canonical path a prokáže přijetí právě tímto activation listenerem pomocí jednorázového náhodného nonce, peer credentials a current generation. Probe neuděluje business authority. Změna path, cizí listener nebo chybné peer evidence blokují readiness.

Root/service manager zůstává TCB. Aplikace nesmí bind/unlink/chmod/chown canonical socket. Při restartu uzavře své accepted connections, nikoli sdílený listener přes shutdown. [systemd dokumentuje předání duplikátu listener FD a jeho zachování při restartu service.](https://github.com/systemd/systemd/blob/main/man/sd_listen_fds.xml)

Povinné Linux testy: správný socket, extra FD, špatná adresa/type/name, symlink/mount substitution, writable parent, podvržený probe, path change a restart s novou InvocationID. Tyto runtime důkazy nejsou lokálním testem textu na Windows.

## R12 — první DNS/TLS bootstrap (B-16; §20.6, §28.5–6, §29.4)

První instalace má BOOTSTRAP_LOCAL a PUBLIC_READY. V BOOTSTRAP_LOCAL běží stejné webové UI a API pouze na loopback serveru. OWNER je otevře přes autentizovaný SSH tunnel z vlastního loopback portu; SSH je infrastrukturní přístup k hostu, ne nová aplikační identita. Ověření host key se neobchází. Public reverse proxy aplikaci bez správného certifikátu nezpřístupní.

UI používá skutečné login/MFA a Secret Manager handlers: KRMAR78, PASS, povinný MFA enrollment před full session. WAPI se vloží do tohoto UI; nezadává se přes CLI, environment či CI secret. HTTP je zde povoleno pouze uvnitř SSH tunelu, s exact loopback Host/Origin allowlistem, CSRF a oddělenou host-only bootstrap cookie. Po přechodu na veřejné HTTPS se bootstrap sessions zruší, další login používá Secure cookie. Neexistuje možnost remote bootstrap bindu.

Po vložení WAPI proběhne původní DNS-01/ACME ledger, SAN/key verification, nginx test/reload a skutečný HTTPS read-back. Až pak vzniká PUBLIC_READY a ukončí se bootstrap listener. BOOTSTRAP_LOCAL není production acceptance PASS. Existující instalace se při běžném deploy nevrací do bootstrap a zachová platný certifikát/předchozí release do ověřeného switche. PASS zůstává jediným externě dodaným aplikačním vstupem; chybějící SSH/host přístup je infrastrukturní blocker, nikoli důvod vypnout ochranu.

## R13 — references a chybové kódy (B-02, B-17)

Odkaz 38.1 se opravuje na existující kapitolu 38. Canonical fyzická entita je `browser_bridge_session_assignment`; `browser_bridge_assignment` se při projekci zdrojové reference přeloží na tento název a není druhou tabulkou ani přípustným runtime aliasem.

| Code | Classification | Retry directive | Přesný význam |
| --- | --- | --- | --- |
| APPROVAL_ALREADY_DECIDED | conflict | DO_NOT_RETRY | Zobrazit immutable rozhodnutí/current argument digest; stejný idempotentní request vrátí původní outcome, jiné rozhodnutí jej nesmí přepsat. |
| PROTOCOL_CONFLICT | protocol | DO_NOT_RETRY | Uchovat obě evidence; zastavit producer/stream. Možný effect se samostatně reconciliuje, chyba není důkaz not-applied. |
| BROWSER_MUTATION_TRIGGER_UNRESOLVED | validation | DO_NOT_RETRY | Před dispatch opravit/verifikovat immutable action contract; neopakovat beze změny. |

`UNRESOLVED_MUTATION_SEMANTICS` je výhradně zdrojový alias posledního kódu. Wire emituje canonical code. Codes nemění reserved HTTP/JSON-RPC numeric ranges. Neznámé symboly se odmítají, ne automaticky registrují.

Další zjištěný alias `OUTCOME_UNKNOWN` v §6.7 se na KCIP wire nahrazuje `KCIP_OUTCOME_UNKNOWN` (classification unknown-side-effect, directive MANUAL_REVIEW). Neznámý effect neznamená terminal cancellation. Naproti tomu PROTOCOL_INVALID v era decision je classification, SOURCE_CONFLICT/CREDENTIAL_REQUIRED jsou blocker classifications, OWNER_DEVICE_REQUIRED/CLIENT_CERTIFICATE_REQUIRED jsou browser auth modes a OWNER_DECISION_REQUIRED je approval policy. Tyto hodnoty nejsou nové error codes a nesmějí se automaticky vložit do code katalogu jen podle zápisu velkými písmeny.

## R14 — supporting lifecycles (B-03, B-05)

Tyto hrany doplňují původní přísnější automaty; vždy mají current parent/row version, DB-time a audit v jednom commitu:

| Entita | Hrany | Guard |
| --- | --- | --- |
| Discussion turn | QUEUED → INTERRUPTED | Job cancel/steer vyhrál CAS před claimem; bez model submit/possible effect. Successor jen pro steer, nikdy job cancel. |
| Phase run | QUEUED → CANCELLED | Current parent cancel, bez claim/dispatch, uzavřené children. |
| Idempotency | WAITING_FOR_INPUT → CANCELLED_FINAL | Cancel, žádný pending/unknown effect, invalidované challenges. |
| Idempotency | RESERVED → FAILED_FINAL | Deterministická finální validation chyba před dispatch, jeden outcome/event. |
| Session/MFA continuation | ISSUED → CONSUMED, EXPIRED nebo REVOKED | Jednorázový CAS, bez reuse tokenu. |
| OWNER session | ACTIVE → EXPIRED nebo REVOKED | Expiry/current revoke. Reauthentication pouze během ACTIVE. |
| Approval | PENDING → APPROVED, REJECTED, EXPIRED nebo CANCELLED | Current parent/argument digest; první CAS vyhrává. |
| Alert episode | OPEN → ACKNOWLEDGED, SUPPRESSED nebo CLOSED; ACKNOWLEDGED → SUPPRESSED nebo CLOSED; SUPPRESSED → OPEN nebo CLOSED | Current condition/version. CLOSED se neotevírá; nová epizoda má nové ID. |
| Credential binding mutable head | ACTIVE → REVOKED nebo SUPERSEDED | Invalidace starého a aktivace nové immutable revision atomicky. |
| Bridge connection, assignment, profile lease | ACTIVE → REVOKED, EXPIRED nebo CLOSED | Current epoch/fence; release není důkaz not-applied. |
| Schedule occurrence | PENDING → ENQUEUED nebo CANCELLED; ENQUEUED → COMPLETE, FAILED nebo CANCELLED | Unique occurrence; terminal pouze při known run outcome a cleanup. |

Immutable revisions/provenance/evidence nedostávají mutable lifecycle. Ostatní supporting entities používají své konkrétní automaty §49, nikoli univerzální fallback. Control transfer všechny projekce přebírají přesně z §49.19 včetně RESETTING_INPUT. DRAINING → GRANTED vyžaduje prázdný potvrzený input state; jinak RESETTING_INPUT nelze přeskočit. UNKNOWN effects zůstávají v reconciliation/manual review podle R08.

## Stav zbývajících podkladů

R06–R14 rozhodují konkrétní konflikty; stále je nutné doplnit úplnou operation mapping přílohu B-01, field/policy přílohu B-09/B-10, audit preimage a golden vectors B-11 a ověřit úplnost supporting entity coverage B-03. Dokud tyto přílohy a nová A2 neprojdou kontrolou, dávka není připravena k odeslání.
