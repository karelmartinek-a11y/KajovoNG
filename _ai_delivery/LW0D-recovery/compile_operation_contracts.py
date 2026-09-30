"""Lokální návrhový kompilátor B-01; síť ani provozní stav nepoužívá.

Inventář není kontrakt. Nezpracovaná operace zůstává blokující a nikdy
nezíská odhadnuté effect/state/schema hodnoty podle názvu nebo HTTP metody.
"""

from collections import defaultdict
from copy import deepcopy
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parent
EXPOSURES = {
    "OWNER_COMMAND", "OWNER_QUERY", "PUBLIC_PROTOCOL", "INTERNAL_PROTOCOL",
    "AUTOMATED_MAINTENANCE", "EVIDENCE_ONLY",
}
EFFECTS = {
    "READ_ONLY", "LOCAL_STATE_IDEMPOTENT", "EXTERNAL_IDEMPOTENT",
    "EXTERNAL_NON_IDEMPOTENT", "DESTRUCTIVE",
}
FIELDS = """operationId operationName operationRevision operationFamily exposureClass
canonicalWriterId aggregateRoot commandSchemaRef responseSchemaRef expectedStates
expectedStateVersionPolicy stateMachineId allowedTransitionIds idempotencyScope
idempotencyKeySource requestDigestProfile concurrencyScope concurrencyKeyDerivation
concurrencyClaimPoint deadlinePolicy sideEffectClass subsystemSideEffectClass retryClass
retryDirectiveMapRef transactionProfileId orderedLockPlanId fencingPolicy checkpointPolicy
possibleEffectTrigger reconciliationOracleId cancellationPolicy terminalOutcomes
auditEventTypes outboxPurposes successorPolicy cleanupPolicy activationRelation
apiOperationIds uiActionIds chatCapabilityIds selfTestCaseIds acceptanceGateIds requirementIds
authoritySourceRefs canonicalDigest""".split()
REF_FIELDS = {
    "canonicalWriterId": "writers", "aggregateRoot": "roots",
    "commandSchemaRef": "schemas", "responseSchemaRef": "schemas",
    "stateMachineId": "stateMachines", "transactionProfileId": "policies",
    "orderedLockPlanId": "policies", "retryDirectiveMapRef": "policies",
    "reconciliationOracleId": "policies",
    **dict.fromkeys("expectedStateVersionPolicy idempotencyScope idempotencyKeySource "
                    "requestDigestProfile concurrencyScope concurrencyKeyDerivation "
                    "concurrencyClaimPoint deadlinePolicy fencingPolicy checkpointPolicy "
                    "possibleEffectTrigger cancellationPolicy successorPolicy cleanupPolicy "
                    "activationRelation".split(), "policies"),
}
ARRAY_REFS = {
    "allowedTransitionIds": "transitions", "auditEventTypes": "events",
    "outboxPurposes": "outbox", "apiOperationIds": "surfaces",
    "uiActionIds": "surfaces", "chatCapabilityIds": "surfaces",
    "selfTestCaseIds": "tests", "acceptanceGateIds": "gates",
    "requirementIds": "requirements", "authoritySourceRefs": "sources",
}
CLASSIFICATIONS = ["TERMINAL_REPLAY", "RESUME", "RECONCILE", "CANCEL", "CLEANUP", "MANUAL_REVIEW"]
RECOVERY_STATES = ["RECONCILING", "BLOCKED", "MANUAL_REVIEW"]
LIFECYCLES = ["DRAFT", "REVIEW", "APPROVED", "ACTIVE", "SUSPENDED", "QUARANTINED", "RETIRED", "DEREGISTERED"]
ACTIVATIONS = ["INACTIVE", "READY", "READY_FOR_ACTIVATION", "ENABLE_REQUESTED", "ACTIVE", "DISABLE_REQUESTED", "DISABLE_UNCONFIRMED", "BLOCKED"]
OPNAME = re.compile(r"[a-zA-Z][a-zA-Z0-9]*(?:\.[a-zA-Z][a-zA-Z0-9]*)+\Z")
PLUGIN_MODULES = (
    "operation_contracts_auth_secrets_config",
    "operation_contracts_agent_mcp",
    "operation_contracts_browser",
    "operation_contracts_generation",
    "operation_contracts_remaining",
)


def canonical(value):
    """JCS podmnožina: pouze bezpečné integer, žádné floats nebo surrogate."""
    def order(node):
        if isinstance(node, dict):
            return {key: order(node[key]) for key in sorted(node, key=lambda s: s.encode("utf-16-be"))}
        if isinstance(node, list):
            return [order(item) for item in node]
        if isinstance(node, float) or (type(node) is int and abs(node) > 9007199254740991):
            raise ValueError("Digest nepřijímá float ani nepřesné integer.")
        return node
    return json.dumps(order(value), ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def sections(text):
    lines = text.splitlines(keepends=True)
    headings = []
    for index, line in enumerate(lines):
        match = re.match(r"^(#{2,6}) (\d+(?:\.\d+)*)(?:\. | )", line)
        if match:
            headings.append((index, len(match[1]), match[2]))
    found = {}
    for i, (start, level, section) in enumerate(headings):
        end = next((row[0] for row in headings[i + 1:] if row[1] <= level), len(lines))
        found[section] = (start + 1, end, "".join(lines[start:end]))
    return found


class Builder:
    def __init__(self, root=ROOT):
        self.root = root
        self.reg = {name: {} for name in set(REF_FIELDS.values()) | set(ARRAY_REFS.values())}
        self.documents = {}
        self.operations = []
        self.parity = {}
        self.semantic = {}
        self.api_aliases = []
        self.plugin_reports = []
        self.original = self.read("source/SSOT.original.md")
        self.section_index = sections(self.original)

    def read(self, path):
        raw = (self.root / path).read_bytes()
        self.documents[path] = hashlib.sha256(raw).hexdigest()
        return raw.decode("utf-8")

    def source(self, section):
        key = "ssot:" + section
        if key not in self.reg["sources"]:
            start, end, body = self.section_index[section]
            self.reg["sources"][key] = {
                "document": "source/SSOT.original.md", "section": section,
                "startLine": start, "endLine": end, "text": body,
                "sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
            }
        return key

    def amendment(self, path, decision):
        text = self.read(path)
        lines = text.splitlines(keepends=True)
        start = next(i for i, line in enumerate(lines) if re.match(r"^## (?:Rozhodnutí )?" + decision + r"(?:\s|:)", line))
        end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
        body = "".join(lines[start:end])
        key = "amendment:" + decision
        self.reg["sources"][key] = {
            "document": path, "section": decision, "startLine": start + 1,
            "endLine": end, "text": body, "sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
        }
        return key

    def put(self, registry, key, value, refs):
        row = {"definition": value, "authoritySourceRefs": list(dict.fromkeys(refs))}
        row["canonicalDigest"] = digest(row)
        if key in self.reg[registry] and self.reg[registry][key] != row:
            raise ValueError(f"Konflikt definice {key}")
        self.reg[registry][key] = row
        return key

    def policy(self, key, value, refs):
        return self.put("policies", "policy:" + key, value, refs)

    def schema(self, key, properties, refs):
        return self.put("schemas", "schema:" + key, {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            **obj(properties),
        }, refs)


def enum(*values):
    return {"type": "string", "enum": list(values)}


def obj(properties):
    return {"type": "object", "properties": deepcopy(properties), "required": list(properties), "additionalProperties": False}


def arr(item, maximum=256, minimum=0):
    return {"type": "array", "items": deepcopy(item), "minItems": minimum, "maxItems": maximum}


def nullable(value):
    return {"anyOf": [deepcopy(value), {"type": "null"}]}


UUID = {"type": "string", "format": "uuid"}
HASH = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
UINT = {"type": "string", "pattern": "^(0|[1-9][0-9]*)$", "maxLength": 19}
TIME = {"type": "string", "format": "date-time"}
TEXT = {"type": "string", "minLength": 1, "maxLength": 4096}
BOOL = {"type": "boolean"}


def inventory(b):
    """Povinné názvy pouze ze tří explicitních autorit; bez heuristik."""
    raw = b.read("operation-map.expanded.json")
    routes = json.loads(raw)
    b.read("operation-map.txt")
    if len(routes) != 503:
        raise ValueError("API mapa nemá přesně 503 položek.")
    needed = defaultdict(lambda: {"origins": [], "apiOperationIds": []})
    for route in routes:
        if route["exposure"] == "CATALOG_DISPATCH":
            if route["operation"] != "operation.catalog.invoke":
                raise ValueError("Neznámý katalogový dispatcher.")
            continue
        row = needed[route["operation"]]
        row["origins"].append(b.source(route["source_section"]))
        row["apiOperationIds"].append(route["api_operation_id"])
    for section, (_, _, text) in b.section_index.items():
        if section.startswith("42."):
            for block in re.findall(r"```text\s*\n(.*?)```", text, re.S):
                for line in block.splitlines():
                    name = line.strip()
                    if OPNAME.fullmatch(name):
                        needed[name]["origins"].append(b.source(section))
    mapping = b.read("OPERATION_MAPPING.md")
    internal = []
    for line in mapping.splitlines():
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) == 4 and OPNAME.fullmatch(cells[0]) and cells[1] in EXPOSURES:
            name, exposure, writer, meaning = cells
            needed[name]["origins"].append("amendment:R18")
            internal.append({"operation": name, "exposure": exposure, "canonicalWriter": "packages/domain/" + writer, "meaning": meaning})
    if len(internal) != 21:
        raise ValueError("R18 již nemá očekávaných 21 explicitních doplnění; nutná revize.")
    actions = []
    group_names = {
        "40.2": ["component"], "40.3": ["agent"], "40.4": ["mcp"], "40.5": ["secret"],
        "40.6": ["browserSession", "browserAutomation", "bridge"], "40.7": ["alert"],
        "40.8": ["ownerSecurity"], "40.9": ["ownerApiKey"], "40.10": ["chatSelfTest"], "40.11": ["generation"],
    }
    for section, groups in group_names.items():
        blocks = re.findall(r"```text\s*\n(.*?)```", b.section_index[section][2], re.S)
        if len(blocks) != len(groups):
            raise ValueError(f"Změněná struktura akcí §{section}.")
        for group, block in zip(groups, blocks, strict=True):
            for action in block.split():
                actions.append({"actionId": f"action:{group}:{action}", "objectType": group,
                                "key": action, "authoritySourceRefs": [b.source(section)],
                                "operationIds": [], "status": "UNRESOLVED_PROJECTION"})
    for row in needed.values():
        row["origins"] = sorted(set(row["origins"]))
    return routes, dict(sorted(needed.items())), internal, actions


# Výhradně explicitní autorské projekce. Neznámý action key se neodvozuje
# z prefixu objektu ani se nezamění za podobně pojmenovaný command.
ACTION_TARGETS = {
    "component": """open=component.read editMetadata=component.metadata.update
viewRevision=component.revision.list validate=component.validate verify=component.verify
activate=component.activate enable=component.enable disable=component.disable
suspend=component.suspend quarantine=component.quarantine restore=component.restore
runE2E=component.e2e.run queryState=component.state.query.request
challengeHeartbeat=component.heartbeat.challenge repair=component.repair recertify=component.recertify
rollback=component.rollback viewSecrets=component.secret.list viewBindings=component.binding.list
viewMonitoring=monitor.state.history.read viewLogs=component.logs.read viewAudit=component.audit.read
deregister=component.deregister""",
    "agent": """open=agent.definition.read editMetadata=agent.definition.update
createRevision=agent.revision.publish viewRevision=agent.revision.read
validateRevision=agent.revision.validate verifyRevision=agent.revision.verify
previewToolBindings=agent.revision.toolBindings.preview viewToolBindings=agent.revision.toolBindings.read
viewHandoffs=agent.revision.handoffs.read viewGuardrails=agent.revision.guardrails.read
viewPromotionGates=agent.revision.promotionGates.read activate=agent.revision.activate
enable=agent.definition.enable disable=agent.definition.disable run=agent.run.start
viewRun=agent.run.status pauseRun=agent.run.pause resumeRun=agent.run.resume cancelRun=agent.run.cancel
approveToolCall=agent.approval.approve rejectToolCall=agent.approval.reject
viewCheckpoints=agent.run.checkpoints.read manageSessions=agent.session.list
compactSession=agent.session.compact manageMemory=agent.memory.read manageTriggers=agent.trigger.list
runEvaluation=agent.eval.start viewEvaluation=agent.eval.read repair=agent.definition.repair""",
    "mcp": """open=mcp.server.read editMetadata=mcp.server.metadata.update
createRevision=mcp.server.revision.publish viewRevision=mcp.server.revision.read
validateRevision=mcp.contract.validate verifyRevision=mcp.server.verify
previewCompatibility=mcp.contract.compatibility activate=mcp.server.activate
serverDiscoverTest=mcp.test.serverDiscover probeProtocolEra=mcp.era.probe
requestMetadataTest=mcp.test.requestMetadata testHttpJsonRpcMap=mcp.test.wireMatrix
viewRawWireEvidence=mcp.request.event.raw createDiscoverySnapshot=mcp.discovery.snapshot
compareDiscoverySnapshots=mcp.discovery.snapshot.diff listTools=mcp.tool.definition.list
callTool=mcp.tools.call viewCall=mcp.call.read cancelCall=mcp.tools.cancel reconcileCall=mcp.tools.reconcile
viewInputExchange=mcp.input.exchange.read respondInputExchange=mcp.input.respond
openSubscription=mcp.subscription.listen viewSubscription=mcp.subscription.read
cancelSubscription=mcp.subscription.cancel viewStateHandle=mcp.stateHandle.read
closeStateHandle=mcp.stateHandle.close viewTask=mcp.task.get updateTask=mcp.task.update
cancelTask=mcp.task.cancel readResource=mcp.resources.read renderPrompt=mcp.prompts.get
manageAliases=mcp.alias.list manageCallers=mcp.tool.callers.read viewUsage=mcp.tool.usage.read""",
    "secret": """open=secret.read reveal=secret.value.read copy=secret.value.read
editMetadata=secret.metadata.update createVersion=secret.version.create
activateVersion=secret.version.activate rotate=secret.rotate generateValue=secret.generatePassword
import=secret.import export=secret.export bind=secret.bind unbind=secret.unbind
bulkBind=secret.binding.bulkApply bulkUnbind=secret.binding.bulkApply
testResolve=secret.testResolve viewUsage=secret.usage.read delete=secret.remove""",
    "browserSession": """open=browser.session.state attach=browser.session.attach
observe=browser.session.observe openPage=browser.page.open activatePage=browser.page.activate
closePage=browser.page.close takeControl=browser.control.acquire releaseControl=browser.control.release
returnToAI=browser.control.returnToAi pickTarget=browser.target.pick revalidateTarget=browser.target.revalidate
sendCredential=browser.credentials.bind verifyAccount=browser.auth.verify saveAccount=browser.account.save
captureState=browser.state.capture inspectStateBundle=browser.state.bundle.read
respondDialog=browser.dialog.respond respondPermission=browser.permission.respond
resolveChallenge=browser.challenge.resolve upload=browser.upload.create
awaitDownload=browser.download.list pause=browser.session.pause resume=browser.session.resume
recover=browser.session.recover reconcileAction=browser.action.reconcile
resolveActionOutcome=browser.action.resolveOutcome close=browser.session.close
viewPreview=browser.preview.latest viewPages=browser.page.list viewDocuments=browser.document.list
viewActions=browser.action.status viewArtifacts=browser.artifact.list viewAuth=browser.auth.attempt.list""",
    "browserAutomation": """open=browser.automation.read edit=browser.automation.update
createRevision=browser.automation.revision.publish startTeaching=browser.teaching.start
stopTeaching=browser.teaching.stop compileTeaching=browser.teaching.compile
preflight=browser.automation.preflight verify=browser.automation.verify activate=browser.automation.activate
rollback=browser.automation.rollback run=browser.automation.run viewRun=browser.automation.run.read
viewSteps=browser.automation.run.step.list viewSession=browser.automation.run.session.read
takeControl=browser.automation.run.control.acquire returnToRuntime=browser.automation.run.control.returnToRuntime
cancel=browser.automation.cancel reconcile=browser.automation.reconcile resolveOutcome=browser.run.manualReview
resolveChallenge=browser.automation.run.challenge.resolve reauthenticate=browser.automation.reauthenticate
manageSchedule=browser.schedule.list enable=browser.automation.enable disable=browser.automation.disable
repair=browser.automation.repair viewArtifacts=browser.automation.run.artifact.list""",
    "bridge": """enroll=browser.bridge.enroll test=browser.bridge.test
viewCapabilities=browser.bridge.read viewConnections=browser.bridge.connection.list
viewProfiles=browser.bridge.profile.list rotateCertificate=browser.bridge.rotateCertificate
revoke=browser.bridge.revoke""",
    "alert": """open=monitor.alert.read acknowledge=monitor.alert.acknowledge
suppress=monitor.alert.suppress close=monitor.alert.close openCorrelation=log.correlation.read
runProbe=monitor.probe.request startRepair=monitor.repair.enqueue viewDeliveries=monitor.alert.delivery.list""",
    "ownerSecurity": """openSecurity=auth.security.read viewFixedIdentity=auth.security.read
startMfaEnrollment=auth.mfa.enroll resetMfa=auth.mfa.reset rotateRecoveryCodes=auth.recoveryCodes.rotate
revokeSession=auth.session.revoke revokeOtherSessions=auth.session.revokeOthers revokeAllSessions=auth.session.revokeAll""",
    "ownerApiKey": """openApiKey=ownerApiKey.read revealApiKey=ownerApiKey.reveal
copyApiKey=ownerApiKey.reveal rotateApiKey=ownerApiKey.rotate viewUsage=ownerApiKey.usage.read
exchangeForSession=ownerApiKey.session.exchange""",
    "chatSelfTest": """ask=chat.ask sendCommand=chat.command.execute
openBrowserSession=chat.browser.session.create attachBrowserSession=chat.browser.session.attach
takeBrowserControl=chat.browser.control.acquire returnBrowserToAI=chat.browser.control.returnToAi
pickBrowserTarget=chat.browser.target.attach saveBrowserAccount=browser.account.save
resolveBrowserChallenge=browser.challenge.resolve startGeneration=generation.job.create
runSelfTest=selfTest.run.start cancelSelfTest=selfTest.run.cancel cleanupSelfTest=selfTest.run.cleanup
openEvidence=selfTest.evidence.read openApiKey=ownerApiKey.read""",
    "generation": """open=generation.job.read createJob=generation.job.create
sendMessage=generation.message.append steer=generation.turn.interrupt
openBrowserSession=generation.browser.session.create attachBrowserSession=generation.browser.session.attach
takeBrowserControl=generation.browser.control.takeover returnBrowserToAI=generation.browser.control.returnToAi
saveBrowserAccount=generation.browser.account.save addSource=generation.source.add
refreshCapabilitySnapshot=generation.capability.resolve viewSpecification=generation.spec.read
runSpecificationPrecheck=generation.spec.precheck approveSpecification=generation.spec.approve
viewPlan=generation.plan.read viewPhase=generation.phase.read viewModelCall=ai.modelCall.read
viewWorkspace=generation.workspace.revision.read viewValidation=generation.validation.list
resolveBlocker=generation.blocker.resolve cancel=generation.job.cancel resume=generation.job.resume
retry=generation.job.retry followUp=generation.job.followUp viewActivationSet=generation.activation.read
openResultObjects=generation.release.list runSelfTest=selfTest.run.start viewLogs=generation.logs.read
viewAudit=generation.audit.read""",
}


def bind_actions(actions, required):
    for row in actions:
        mapping = dict(pair.split("=", 1) for pair in ACTION_TARGETS[row["objectType"]].split())
        target = mapping.get(row["key"])
        if target:
            if target not in required:
                raise ValueError("Neexistující explicitní action target: " + target)
            row["operationIds"] = ["operation:" + target]
            row["status"] = "NAMED_PROJECTION_NOT_COMPLETE_CONTRACT"
            row["targetResolution"] = "Server ověří vztah zobrazeného objektu k exact target ID/revision; UI ID ani action key nejsou authority. Pole payloadu určuje výhradně canonical operation schema."
    # Reserved secret používá OWNER-key operace, nikoli obecnou Secret rotaci.
    for row in actions:
        if row["objectType"] == "secret":
            row["reservedOwnerApiKey"] = {
                "applicable": row["key"] in {"open", "reveal", "copy", "rotate", "viewUsage", "viewLogs", "viewAudit"},
                "operationId": {
                    "open": "operation:ownerApiKey.read", "reveal": "operation:ownerApiKey.reveal",
                    "copy": "operation:ownerApiKey.reveal", "rotate": "operation:ownerApiKey.rotate",
                    "viewUsage": "operation:ownerApiKey.usage.read",
                }.get(row["key"]),
                "authoritySourceRefs": ["ssot:40.5"],
            }
    return actions


def common(b):
    for decision in ("R02", "R03", "R04", "R05"):
        b.amendment("TECHNICKY_DODATEK.md", decision)
    for decision in ("R06", "R07", "R08", "R09", "R10", "R13", "R14"):
        b.amendment("RESENI_KONKRETNI.md", decision)
    text = b.read("OPERATION_MAPPING.md")
    b.reg["sources"]["amendment:R18"] = {
        "document": "OPERATION_MAPPING.md", "section": "R18", "startLine": 1,
        "endLine": len(text.splitlines()), "text": text,
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }
    for section in ("6.3", "6.6", "6.7", "6.8", "10.12", "26.1", "32.6", "40.1", "42", "49.3", "49.4", "49.6", "49.8", "49.9", "49.10", "49.12", "49.22", "49.33", "49.34", "49.36", "51.2", "51.6", "51.9", "51.14", "51.25", "51.26", "51.28", "51.32", "51.38", "55.6", "55.18"):
        b.source(section)
    refs = ["ssot:49.3", "ssot:49.4", "ssot:51.9", "ssot:55.6"]
    b.policy("digest", {"format": "KCML-CANONICAL-JSON/1", "bytes": "contract ID + revision + canonical business arguments + immutable target snapshot; žádný transportní message/worker/fence ID", "serverAuthority": "§6.6 reserved metadata se nevkládají do client payload; server je pinne samostatně."}, ["ssot:6.3", "amendment:R05", "ssot:49.4"])
    b.policy("workerTransaction", {"profile": "WORKER_COMMIT", "isolation": "READ COMMITTED", "lockTimeoutMs": 3000, "statementTimeoutMs": 15000, "idleTransactionTimeoutMs": 15000, "externalIO": False}, ["ssot:51.2"])
    b.policy("queryTransaction", {"profile": "CONSISTENT_READ", "isolation": "REPEATABLE READ READ ONLY", "lockTimeoutMs": 3000, "statementTimeoutMs": 60000, "idleTransactionTimeoutMs": 60000, "audit": "Povinná access evidence vznikne v oddělené krátké transakci s digestem dokončeného snapshotu, před vydáním response; žádný business write v read-only transakci."}, ["ssot:51.2", "ssot:55.18", "amendment:R18"])
    b.policy("fence", {"serverDerived": ["platformIncarnationId", "databaseStartIdentity", "applicationDeploymentEpoch", "recoveryEpoch", "leaseOwner", "leaseFence", "leaseExpiresAt"], "predicate": "exact current identity a fence, leaseExpiresAt > db_now; znovu při každém write a bezprostředně před dispatch", "expired": "žádné vzkříšení heartbeat; takeover zvýší fence", "late": "jen immutable stale evidence; žádný projection/write/successor"}, ["ssot:49.6", "ssot:51.38"])
    b.policy("deadline", {"absolute": "effective deadline pinovaný parent commandem; min(request deadline, parent deadline, lease expiry pro daný attempt)", "clock": "DB clock_timestamp(), ne klientský nebo process clock", "expiredBeforeDispatch": "bez dispatch; persistovaný cancel/timeout se zachováním původního intentu", "expiredAfterPossibleEffect": "reconcile; deadline není důkaz NOT_APPLIED ani povolení release claims", "extension": "retry deadline neposouvá"}, ["ssot:6.8", "ssot:49.6", "ssot:49.10"])
    b.policy("retry", {"beforeEffect": {"40001": "rollback celého closure; stejná operation", "40P01": "rollback + lock-plan defect; test musí selhat", "55P03": "backoff bez write", "23505": "replay pouze po exact key/digest porovnání", "23503": "DO_NOT_RETRY", "23514": "DO_NOT_RETRY", "57014": "rollback; nejprve ověřit possible-effect evidence"}, "afterEffect": "RECONCILE_THEN_RETRY, nikdy replay dispatchu naslepo", "conflictingEvidence": "MANUAL_REVIEW", "bounds": "původní absolute deadline a pinned parent retry budget; žádný nový budget při opakování", "stateConflict": "REFRESH_AND_RETRY_NEW_COMMAND", "protocolConflict": "DO_NOT_RETRY"}, ["ssot:51.32", "ssot:32.6", "ssot:49.36"])
    b.policy("closure", {"predicate": "všechny možné effects mají known canonical outcome; žádný pending child/successor ani nepřevzatý authority resource; cleanup COMPLETE", "commit": "root/child locks + closure evidence + outcome + event + audit + uvolnění claims v jednom commitu", "unknown": "zachovat claims a MANUAL_REVIEW; nikdy SUCCEEDED/CANCELLED", "resourceTransfer": "current nový owner musí atomicky přijmout resources; potvrzený převod nevyžaduje zastavení legitimního produktu"}, ["ssot:49.34", "ssot:51.26", "amendment:R08", "amendment:R10"])
    b.policy("checkpoint", {"before": "immutable parent snapshot a pending intent/outbox atomicky", "after": "canonical outcome + watermarks + next checkpoint + unique successor atomicky", "resume": "validate digest/current lineage a všechny pending effects; žádný replay process memory"}, ["ssot:49.9", "amendment:R08"])
    b.policy("cancel", {"beforeDispatch": "CAS cancellation intent; suppress pouze neclaimed READY/RETRY_WAIT outbox podle R03", "afterDispatch": "zastavit nové effects a reconciliovat existující; zachovat evidence", "terminalReplay": "již terminal outcome se nemění", "completion": "known outcomes + požadovaný cleanup safe point, nikoli pouhý intent"}, ["ssot:49.10", "amendment:R03", "amendment:R08"])
    b.policy("noSuccessor", {"allowed": False, "reason": "Observation/query pouze vrátí durable evidence; nesmí založit business command, aktivaci ani obnovit lease."}, ["ssot:49.22", "ssot:55.18"])
    b.policy("noOwnedResource", {"resourcesCreated": [], "obligation": "zavřít reader/transport; immutable evidence se nemaže; neměnit runtime ownership", "effect": "žádný cleanup business resource"}, ["ssot:49.34", "amendment:R10"])
    b.policy("noClaim", {"claim": "NONE", "reason": "Čtení a monotonic observation nemají business concurrency claim; root/sequence guards nejsou náhradní effect authority."}, ["ssot:49.22", "ssot:51.6"])
    b.policy("ownerKey", {"source": "Idempotency-Key vytvořený OWNER klientem před prvním sendem", "aliases": "typed route a catalog invoke sdílí jeden key i scope; session/klíč/zařízení scope nemění"}, ["ssot:26.1", "ssot:49.4", "amendment:R18"])
    b.policy("exactCAS", {"required": ["expectedStateVersion", "current evidence digest"], "predicate": "row stateVersion = expectedStateVersion AND evidenceDigest = expectedEvidenceDigest", "conflict": "STATE_VERSION_CONFLICT, žádný last-write-wins"}, refs)
    b.policy("parentIdentity", {"relation": "serverem uložený immutable parent command + exact logical operation + target; request nesmí parent změnit", "scope": "operation ID/revision + stable source revision + target + stable child step key; worker, fence ani transport request ID nejsou business key"}, refs)
    for name in ("component", "recovery"):
        b.put("writers", "writer:" + name, {"repositoryHome": "packages/domain/" + name, "singleWriter": True, "implementationStatus": "PLANNED"}, ["amendment:R18", "ssot:55.6"])
    requirements = json.loads(b.read("source/A0.original.json"))
    for row in requirements["explicit_requirements"] + requirements["implicit_requirements"]:
        if row["id"] in {"E-003", "E-032", "E-033", "E-034", "E-038", "E-145", "E-148", "E-170"}:
            b.put("requirements", row["id"], {"namespace": "A0_RECOVERY_TRACEABILITY", "description": row["description"], "not55_5NormativeAtomId": True}, ["ssot:55.6"])


def add_operation(b, name, family, exposure, effect, retry, expected, input_fields, output_fields, policies, transitions, refs, requirements, semantic, parent):
    """Přidá pouze autorsky specifikovaný kontrakt, žádná operation-name heuristika."""
    opid = "operation:" + name
    machine = b.put("stateMachines", "machine:" + name, {
        "stateDomain": semantic.get("stateDomain", "platform_recovery_head.state" if family == "recovery" else "component lifecycle/activation + monotonic observation/control head"),
        "acceptedStates": expected, "otherTransitions": "FORBIDDEN",
        "semanticGuards": semantic,
    }, refs)
    tids = []
    for index, transition in enumerate(transitions, 1):
        tids.append(b.put("transitions", f"transition:{name}:{index}", {"machineId": machine, **transition}, refs))
    audit = b.put("events", "event:" + name + ".recorded", {
        "kind": "AUDIT_EVIDENCE", "operationId": opid,
        "payload": ["logicalOperationId", "requestDigest", "targetIdentityDigest", "beforeDigest", "afterDigest", "outcomeDigest", "correlationId", "dbTimestamp"],
        "noSecondBusinessOutcome": True,
    }, ["ssot:51.25", *refs])
    schema_in = b.schema(name + ".input", input_fields, refs)
    schema_out = b.schema(name + ".output", output_fields, refs)
    selftest = b.put("tests", "test:" + name, {
        "status": "PLANNED_NOT_EXECUTED", "operationId": opid,
        "cases": ["valid exact request", "same-key same-digest replay", "same-key different-digest conflict", "stale identity/fence", "invalid schema/reserved metadata", "kill before/after commit", *semantic["testCases"]],
        "oracle": "přímý DB outcome, audit a counts; samotná response není PASS",
    }, refs)
    gate = b.put("gates", "gate:" + name, {"status": "PLANNED_NOT_PASS", "testId": selftest, "requires": ["BUILD schema/handler tests", "DB crash/concurrency suite", "RUNTIME exact identity evidence"]}, refs)
    ui = b.put("surfaces", "ui:" + name, {"kind": "OWNER_ACTION" if exposure == "OWNER_COMMAND" else "OWNER_DIAGNOSTIC", "operationId": opid, "status": "PLANNED", "publicMutation": exposure == "OWNER_COMMAND"}, ["ssot:55.18", *refs])
    chat = b.put("surfaces", "chat:" + name, {"kind": "COMMAND" if exposure == "OWNER_COMMAND" else "DIAGNOSTIC_QUERY", "operationId": opid, "status": "PLANNED", "noInternalInvoke": exposure != "OWNER_COMMAND"}, ["ssot:55.18", *refs])
    api = []
    matched_routes = [route for route in b.api_aliases if route["operation"] == name]
    for route in matched_routes:
        api.append(b.put("surfaces", "api:" + route["api_operation_id"], {
            "kind": "CANONICAL_TYPED_ROUTE", "method": route["method"], "path": route["path"],
            "operationId": opid, "status": "PLANNED", "schemaRef": schema_in,
            "transportAlias": route, "sameLogicalOutcome": True,
        }, [b.source(route["source_section"]), "amendment:R18"]))
    if exposure == "OWNER_COMMAND" and not matched_routes:
        api.append(b.put("surfaces", "api:catalog:" + name, {"kind": "CANONICAL_CATALOG_PROJECTION", "method": "POST", "path": "/operations/:operationKey/invoke", "operationKey": name, "operationId": opid, "status": "PLANNED", "schemaRef": schema_in, "sameLogicalOutcome": True}, ["ssot:26.1", "amendment:R18"]))
    record = {
        "operationId": opid, "operationName": name, "operationRevision": 1,
        "operationFamily": family, "exposureClass": exposure,
        "canonicalWriterId": "writer:" + family, "aggregateRoot": "root:" + family,
        "commandSchemaRef": schema_in, "responseSchemaRef": schema_out,
        "expectedStates": expected, "stateMachineId": machine, "allowedTransitionIds": tids,
        "sideEffectClass": effect, "subsystemSideEffectClass": effect, "retryClass": retry,
        "terminalOutcomes": ["SUCCEEDED", "FAILED", "CANCELLED"],
        "auditEventTypes": [audit], "outboxPurposes": [],
        "apiOperationIds": api, "uiActionIds": [ui], "chatCapabilityIds": [chat],
        "selfTestCaseIds": [selftest], "acceptanceGateIds": [gate],
        "requirementIds": requirements, "authoritySourceRefs": list(dict.fromkeys(refs)),
        **policies,
    }
    record["canonicalDigest"] = digest(record)
    if set(record) != set(FIELDS):
        raise ValueError(f"Neúplná §55.6 pole: {name}: {set(FIELDS) ^ set(record)}")
    b.operations.append(record)
    b.semantic[opid] = semantic
    b.parity[opid] = {"exposureClass": exposure, "parentRelation": parent, "notApplicable": ([{"field": "apiOperationIds", "reasonCode": "INTERNAL_PROTOCOL_NO_PUBLIC_MUTATION", "authoritySourceRefs": ["ssot:55.18"]}] if not api else []), "status": "DESIGN_ONLY"}


def load_plugins(b):
    """Pouze pět výslovně dohodnutých lokálních modulů; žádné glob/import magic."""
    # Stejná třída/helper API i při spuštění souboru jako __main__.
    sys.modules.setdefault("compile_operation_contracts", sys.modules[__name__])
    for module_name in PLUGIN_MODULES:
        path = b.root / (module_name + ".py")
        if not path.is_file():
            b.plugin_reports.append({"module": module_name, "status": "NOT_PRESENT", "registered": []})
            continue
        b.read(path.name)
        spec = importlib.util.spec_from_file_location(module_name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        before = {row["operationName"]: deepcopy(row) for row in b.operations}
        module.register(b)
        after = {row["operationName"]: row for row in b.operations}
        if any(after.get(name) != row for name, row in before.items()):
            raise ValueError("Plugin změnil cizí operation: " + module_name)
        b.plugin_reports.append({"module": module_name, "status": "LOADED", "registered": sorted(set(after) - set(before))})


def base_policies():
    return {"requestDigestProfile": "policy:digest", "retryDirectiveMapRef": "policy:retry",
            "transactionProfileId": "policy:workerTransaction", "fencingPolicy": "policy:fence",
            "deadlinePolicy": "policy:deadline", "checkpointPolicy": "policy:checkpoint",
            "cancellationPolicy": "policy:cancel", "cleanupPolicy": "policy:closure"}


def recovery_contracts(b):
    refs = ["ssot:49.33", "ssot:49.36", "ssot:51.38", "amendment:R07", "amendment:R08", "amendment:R09", "amendment:R10", "amendment:R18"]
    b.put("roots", "root:recovery", {"table": "platform_recovery_head", "key": {"singleton_key": 1}, "lockClass": "B0", "item": "platform_recovery_item", "targetOwnership": "immutable item owner kind/id; lock all existing target roots in E order; no synthetic recovery E root"}, refs)
    b.policy("recoveryLocks", {"orderedClasses": ["A0", "A", "B0", "B1", "B2", "B3", "C", "D", "E", "F", "G", "H", "I"], "A0": "shared pg_advisory_xact_lock_shared(1000,1); nikdy upgrade", "B0": "current recovery head FOR SHARE; tyto item operace nesmějí měnit head", "E": "pinned exact item owner roots; všechny multi-parent roots, včetně cleanup root 150 pro cleanup", "F": "existing recovery/account/resource claims před children", "H": "ordered current item/target/attempt/checkpoint children; queue900/outbox910 až po nich", "I": "audit_head FOR UPDATE poslední", "unknownTargetSet": "rollback a replan; nikdy dodatečný nižší lock", "externalIO": "po commitu a uvolnění všech locks"}, refs)
    b.policy("recoveryScope", {"tuple": ["operationId", "operationRevision", "platformIncarnationId", "recoveryAttemptId", "recoveryItemId", "classificationRevision", "stableStepKey"], "excluded": ["workerId", "leaseFence", "HTTP request ID"], "ownerResolution": "OWNER_FULL + item + stable client key; žádný session/fingerprint scope"}, refs)
    b.policy("recoveryKey", {"source": "persistent recovery item + classification revision + typed step; vytvořen v téže transakci jako unique work", "reuse": "nový worker/fence používá původní key"}, refs)
    b.policy("recoveryClaim", {"class": "RECOVERY_RESERVED", "point": "WORKER_CLAIM", "key": ["current recovery attempt", "item owner kind/id", "recovery item ID"], "commit": "claim a current fence se potvrdí atomicky před prací; ordinary queue nesmí rezervaci spotřebovat"}, refs)
    b.policy("recoveryAdmission", {"allowedHeadStates": RECOVERY_STATES, "source": "current trusted controller + exact attempt/item/fence; OWNER resolve přes stejný writer", "forbidden": ["new model create", "new browser business input", "new agent/tool business call", "activation switch"], "callerSwitch": False, "readyHeadTransition": "není součást žádné z těchto šesti item operací; pouze separate controller stable second-pass gate"}, refs)
    b.policy("recoverySuccessor", {"uniqueKey": ["recoveryAttemptId", "recoveryItemId", "classificationRevision", "stepKey"], "commit": "classification+checkpoint+successor atomicky", "dispatch": "recovery-only allowlist; business RESUME successor zůstává nedispatchable před READY", "terminal": "žádný successor terminal parentu"}, refs)
    context = obj({"recoveryAttemptId": UUID, "recoveryItemId": UUID, "expectedStateVersion": UINT,
                   "expectedEvidenceDigest": HASH, "classificationRevision": UINT})
    result = {"recoveryItemId": UUID, "stateVersion": UINT, "classification": enum(*CLASSIFICATIONS),
              "evidenceDigest": HASH, "checkpointDigest": HASH, "idempotencyReplay": BOOL}
    specs = [
        ("inventory", "LOCAL_STATE_IDEMPOTENT", "SAFE_RETRY",
         {"item": context, "inventoryWatermark": UINT},
         {**result, "observedObjectDigest": HASH, "mandatoryStartupBlocker": BOOL},
         "Načte skutečný DB/work/runtime/artifact stav jen pro exact item; persistuje immutable snapshot a jednu klasifikaci. Nevytváří business intent ani work dispatch.",
         "Žádný external mutation trigger; linearizační bod je commit item snapshotu a classification CAS.",
         "Current indexed DB rows a evidence digests + exact runtime/artifact read-only inventory; rozpor nebo chybějící decisive evidence => MANUAL_REVIEW.",
         ["crash po klasifikaci nesmí vytvořit druhý successor", "druhý inventář odhalí phantom blocker"]),
        ("readBack", "READ_ONLY", "SAFE_RETRY",
         {"item": context, "sideEffectOperationId": UUID, "oracleContractDigest": HASH},
         {**result, "observedOutcome": enum("CONFIRMED_APPLIED", "CONFIRMED_NOT_APPLIED", "UNKNOWN"), "oracleEvidenceDigest": HASH},
         "Pouze registrovaný exact read-only oracle existujícího effect intentu; žádný replay původního requestu/inputu. Zápis read-back/audit evidence není business mutation.",
         "Business possible-effect trigger není povolen; target read-back se provádí bez DB locků.",
         "Oracle pinned existujícím side_effect_operation reconciliation contractem; jeho digest i target binding musí souhlasit. Chybějící exact oracle nebo protichůdné evidence => UNKNOWN/MANUAL_REVIEW, nikdy odhad podle GET či timeoutu.",
         ["read-back nesmí volat original dispatch", "conflicting oracle => MANUAL_REVIEW"]),
        ("cancel", "EXTERNAL_NON_IDEMPOTENT", "RETRY_AFTER_RECONCILIATION",
         {"item": context, "existingLogicalOperationId": UUID, "reason": TEXT},
         {**result, "cancellationVersion": UINT, "closureComplete": BOOL},
         "Persistuje monotonic cancellation intent existující práce. Provider/runtime cancel pouze exact cancel contractem existujícího handle; nikdy nová business práce. Nepotvrzené cancel ACK není known outcome.",
         "První cancel-adapter byte/system action po D commitu je možné působení; předem durable cancel intent a immutable attempt.",
         "Read-back původní operation a její cancellation postcondition; absence response není cancelled. Bez cancel capability pouze intent+reconcile, bez externího volání.",
         ["cancel proti terminal commitu vrací původní outcome", "timeout cancel drží claims"]),
        ("cleanup", "DESTRUCTIVE", "RETRY_AFTER_RECONCILIATION",
         {"item": context, "cleanupOperationId": UUID, "cleanupResourceId": UUID, "expectedResourceVersion": UINT},
         {**result, "resourcePostconditionDigest": nullable(HASH), "closureComplete": BOOL},
         "Jediný pinned resource patří current ownerovi a declared cleanup contractu. Smí odstranit pouze tento resource; žádný wildcard, nová business mutace ani odstranění current product pointeru.",
         "Exact declared cleanup adapter trigger; samostatné T1/D/E/T2/T3, compensation má vlastní child effect a původní applied history se nemaže.",
         "Existující resource-kind cleanup oracle ověří desired terminal condition a nepřítomnost live references; UNKNOWN blokuje COMPLETE i uvolnění claims.",
         ["owned resource guard odmítne cizí path/handle", "COMPLETE zakázáno s live pointerem", "cleanup kill po effectu => read-back"]),
        ("resolve", "LOCAL_STATE_IDEMPOTENT", "NO_AUTOMATIC_RETRY",
         {"item": context, "sideEffectOperationId": UUID, "decision": enum("CONFIRMED_APPLIED", "CONFIRMED_NOT_APPLIED", "FAILED_FINAL"), "reason": TEXT},
         {**result, "decision": enum("CONFIRMED_APPLIED", "CONFIRMED_NOT_APPLIED", "FAILED_FINAL"), "resolutionAuditDigest": HASH},
         "Current authenticated OWNER CAS výhradně existujícího UNKNOWN/manual-review blockeru; exact item/operation/evidence digest, důvod a jeden audit. Rozhodnutí není oprávnění k okamžitému novému dispatchi.",
         "Pouze atomický local resolution commit, žádný external call.",
         "Current DB side-effect UNKNOWN + immutable evidence digest porovnaný s commandem. Dvě rozdílná rozhodnutí nesmějí commitnout nad stejnou version.",
         ["concurrent conflicting OWNER resolutions", "stale evidence odmítnuto", "OWNER nemůže poslat controller/fence identity"]),
        ("advance", "LOCAL_STATE_IDEMPOTENT", "SAFE_RETRY",
         {"item": context, "classification": enum(*CLASSIFICATIONS), "checkpointDigest": HASH, "successorStepKey": nullable(TEXT)},
         {**result, "successorId": nullable(UUID), "businessDispatchEnabled": {"const": False, "type": "boolean"}},
         "Potvrdí klasifikaci/current checkpoint a nejvýše jeden successor. RESUME připraví work, ale nedispatchuje jej před READY. Neprovádí recovery-head transition ani activation.",
         "Pouze local item/checkpoint/successor commit; business dispatch není povolen.",
         "Unique successor DB relation + exact checkpoint digest + known outcome všech pending effects; nedostatek evidence zůstává MANUAL_REVIEW.",
         ["duplicate advance nesmí queue amplify", "RESUME nepovolí business dispatch před READY"]),
    ]
    for suffix, effect, retry, inputs, outputs, rule, trigger, oracle, tests in specs:
        name = "platform.recovery." + suffix
        local_refs = refs + ["ssot:49.8", "ssot:49.10", "ssot:51.26", "ssot:51.28"]
        semantic = {"rule": rule, "recoveryOnly": True, "newBusinessEffectsAllowed": False, "testCases": tests,
                    "trustedContext": "server-derived current controller/attempt/item/fence; není součást OWNER command body",
                    "payloadVsEnvelope": "input schema je business payload; KCIP envelope podle §6.3/6.6 se validuje samostatně"}
        policies = {**base_policies(), "expectedStateVersionPolicy": "policy:exactCAS", "idempotencyScope": "policy:recoveryScope", "idempotencyKeySource": "policy:ownerKey" if suffix == "resolve" else "policy:recoveryKey", "orderedLockPlanId": "policy:recoveryLocks", "concurrencyScope": "policy:recoveryClaim", "concurrencyKeyDerivation": "policy:recoveryClaim", "concurrencyClaimPoint": "policy:recoveryClaim", "activationRelation": "policy:recoveryAdmission", "successorPolicy": "policy:recoverySuccessor",
                    "possibleEffectTrigger": b.policy(name + ".trigger", {"trigger": trigger}, local_refs),
                    "reconciliationOracleId": b.policy(name + ".oracle", {"readBack": oracle, "noBlindReplay": True}, local_refs)}
        expected = ["BLOCKED", "MANUAL_REVIEW"] if suffix == "resolve" else RECOVERY_STATES
        add_operation(b, name, "recovery", "OWNER_COMMAND" if suffix == "resolve" else "INTERNAL_PROTOCOL", effect, retry, expected, inputs, outputs, policies,
                      [{"from": state, "to": state, "guard": "current head identity/fence + exact item CAS; head beze změny", "write": rule} for state in expected],
                      local_refs, ["E-003", "E-032", "E-145", "E-148", "E-170"], semantic,
                      "current platform_recovery_attempt + item; existující logical operation, nikoli nový business parent")
        if suffix in {"cancel", "cleanup"}:
            outbox = b.put("outbox", "outbox:" + name, {
                "purpose": "SIDE_EFFECT_DISPATCH", "authority": "pouze exact recovery item + původní cancel handle nebo owned cleanup resource",
                "phases": ["T1", "D", "E", "T2", "T3"], "noNewBusinessEffect": True,
                "cancelWithoutAdapter": "žádný external outbox; jen local cancellation intent + evidence",
                "claim": "RECOVERY_RESERVED; jeden authority outbox pro jeden immutable child attempt",
            }, local_refs)
            b.operations[-1]["outboxPurposes"] = [outbox]
            reseal(b.operations[-1])


def reseal(row):
    row["canonicalDigest"] = digest({key: value for key, value in row.items() if key != "canonicalDigest"})


def component_contracts(b):
    refs = ["ssot:49.12", "ssot:49.22", "ssot:51.6", "amendment:R06", "amendment:R07", "ssot:55.18"]
    b.put("roots", "root:component", {"table": "component", "key": "UUID id", "ordinal": 30, "children": ["control/runtime/pointer H30", "domain_command H35", "admission H40", "monitoring H130"]}, refs)
    b.policy("componentLocks", {"orderedClasses": ["A0", "A", "B1", "B2", "C", "D", "E30", "F", "G", "H30", "H35", "H40", "H130", "H140", "H150", "H900", "H910", "I"], "A0": "shared recovery guard; new control dispatch jen READY, observation pouze evidence i při barrier", "roots": "exact component a všechny target runtime/activation parent roots v globálním E pořadí", "claim": "F před každým H; observation nepotřebuje business claim", "audit": "I poslední; žádný external call pod locky"}, refs)
    b.policy("observationCAS", {"predicate": "exact current runtime/release/service/runtime generation/binding-set/activation + incomingSequence > currentSequence", "equalSameDigest": "ACK replay, žádný projection update", "equalDifferentDigest": "PROTOCOL_CONFLICT", "stale": "immutable stale evidence; freshness i lease beze změny"}, [*refs, b.source("44.1"), b.source("44.3"), "amendment:R13"])
    b.policy("observationKey", {"tuple": ["operation ID/revision", "component ID", "release", "runtime generation", "stream/state key", "source sequence"], "duplicate": "same digest ACK replay; different digest conflict", "notBusinessKey": "nonce ani transport message ID nevytvářejí další business operation"}, refs)
    b.policy("componentAdmission", {"identity": "exact active release/runtime/service generation/binding set/activation epoch z trusted gateway", "observations": "stale evidence se zachová, ale nesmí aktivovat/disable/obnovit lease", "control": "current parent business command + recovery READY + existing admission, žádný druhý business outcome"}, refs)
    b.policy("controlClaim", {"scope": "component desired-state logical operation", "key": ["COMPONENT", "component UUID", "control logical operation"], "point": "T1_PREPARE", "release": "jen known command outcome a COMPLETE closure podle R08; ACK ani timeout neuvolňují"}, refs + ["amendment:R08"])
    b.policy("controlSuccessor", {"parent": "component.enable nebo component.disable; jeden původní logical outcome", "successor": "unique current control outbox/reconciliation checkpoint; žádný nový OWNER command", "unknown": "reconcile/manual review, conflicting controls blokovány"}, refs + [b.source("44.4"), b.source("44.5")])
    b.policy("controlCAS", {"required": ["expectedStateVersion", "target revision/release/runtime generation", "bindingSetRevision", "activationEpoch"], "predicate": "exact component stateVersion a všechny pinned lineage fields; parent command již vlastní idempotency", "conflict": "STATE_VERSION_CONFLICT bez write; expectedEvidenceDigest se u control requestu nevyžaduje"}, refs + ["ssot:44.4"])
    b.policy("queryLocks", {"snapshot": "CONSISTENT_READ bez row/advisory write locks", "projectionWrites": False, "auditTransaction": "samostatná WORKER_COMMIT transakce; immutable access evidence a audit_head I poslední", "interleaving": "response pinne dokončený read snapshot, netvrdí současnost s pozdějším audit commitem"}, refs + ["ssot:51.2", "ssot:55.18"])
    identity = {"releaseId": TEXT, "runtimeId": TEXT, "serviceGeneration": TEXT,
                "runtimeGeneration": UINT, "activationEpoch": UINT, "bindingSetRevision": UINT}
    observation = obj({"stateKey": TEXT, "schemaDigest": HASH, "payloadDigest": HASH,
                       "payload": {"type": ["null", "boolean", "number", "string", "array", "object"], "description": "Druhá povinná validace podle schemaDigest exact component revision; generic JSON sám není přijatý typed payload."}, "observedAt": TIME, "emittedAt": TIME,
                       "sourceSequence": UINT, **identity})
    receipt = {"acceptedAt": TIME, "sourceSequence": UINT, "evidenceDigest": HASH,
               "classification": enum("CURRENT", "DUPLICATE", "STALE"), "idempotencyReplay": BOOL}
    heartbeat = {"componentCode": {"type": "string", "pattern": "^KCML[0-9]{4,}$"},
                 "executionId": UUID, **identity, "heartbeatSequence": UINT,
                 "lifecycleMode": enum(*LIFECYCLES), "operationalState": TEXT, "ready": BOOL,
                 "dependencySummary": {"type": "object"}, "queueDepth": UINT, "activeRuns": UINT,
                 "resourceUsage": obj({"cpuMillis": UINT, "memoryMiB": UINT, "openFiles": UINT}),
                 "lastSuccessfulOperationAt": nullable(TIME), "emittedAt": TIME, "nonce": TEXT}
    query = {"stateKeys": arr(TEXT, 256, 1), "consistency": enum("CURRENT_PROJECTION", "LATEST_VALID_OBSERVATION"),
             "targetRevisionDigest": HASH, "bindingDigest": HASH, "activationEpoch": UINT, "deadline": TIME}
    control = {"commandId": UUID, "logicalOperationId": UUID, "desiredState": TEXT,
               "reason": TEXT, "correlationId": UUID, "causationId": UUID, "deadline": TIME,
               "requestDigest": HASH, "idempotencyKey": TEXT, "componentId": UUID,
               "revisionDigest": HASH, **identity, "expectedStateVersion": UINT}
    ack = {"commandId": UUID, "logicalOperationId": UUID, "requestDigest": HASH,
           "layer": enum("ADMISSION", "OUTCOME"), "status": enum("ACCEPTED", "REJECTED", "COMPLETED", "FAILED", "UNKNOWN"),
           "currentState": enum(*ACTIVATIONS), "observedState": enum(*ACTIVATIONS),
           "stateVersion": UINT, **identity, "sourceSequence": UINT, "detail": TEXT,
           "resultDigest": HASH, "correlationId": UUID, "timestamp": TIME}
    specs = [
        ("component.heartbeat", "44.1", "LOCAL_STATE_IDEMPOTENT", heartbeat,
         {**receipt, "nextDueAt": TIME, "activationEpoch": UINT, "bindingSetRevision": UINT, "pendingControlIntentIds": arr(UUID)},
         "Monotonic heartbeat evidence a freshness pouze při exact current lineage. ACK neobnovuje business lease ani nepotvrzuje command outcome.",
         ["duplicate nesmí prodloužit freshness", "stale heartbeat nesmí aktivovat runtime"]),
        ("component.state.query", "44.2", "READ_ONLY", query,
         {"observations": arr(obj({**observation["properties"], "stateVersion": UINT, "staleness": enum("FRESH", "STALE"), "projected": BOOL})), "snapshotDigest": HASH},
         "CURRENT_PROJECTION pouze DB projection; LATEST_VALID_OBSERVATION může vrátit nepromítnutou observation s projected=false. Žádná změna state/freshness; access audit dovoleno.",
         ["query nesmí obnovit freshness", "nepromítnutá observation musí mít projected=false"]),
        ("component.state.report", "44.3", "LOCAL_STATE_IDEMPOTENT", {"observations": arr(observation, 256, 1)},
         {"receipts": arr(obj({**receipt, "stateKey": TEXT, "stateVersion": UINT}), 256, 1)},
         "Každá observation exact typed schema/digest/sequence; current projection mění jen deklarovaný state contract. Duplicate no-op, stale evidence only; conflict/schema mismatch bez projection change.",
         ["sequence conflict zachová projection", "schema digest musí resolve exact revision"]),
        ("component.control.enable", "44.4", "EXTERNAL_IDEMPOTENT", {**control, "desiredState": {"type": "string", "const": "ACTIVE"}},
         {"commandId": UUID, "logicalOperationId": UUID, "status": {"type": "string", "const": "ACCEPTED"}, "terminal": {"type": "boolean", "const": False}, "stateVersion": UINT},
         "Interní child původního component.enable. Commit command/idempotency/outbox; effective ACTIVE až po současném fenced outcome + route/runtime + state + current heartbeat. Žádný druhý business outcome.",
         ["admission ACK není ACTIVE", "enable neprojde bez readiness a exact epoch"]),
        ("component.control.disable", "44.4", "EXTERNAL_IDEMPOTENT", {**control, "desiredState": {"type": "string", "const": "INACTIVE"}},
         {"commandId": UUID, "logicalOperationId": UUID, "status": {"type": "string", "const": "ACCEPTED"}, "terminal": {"type": "boolean", "const": False}, "stateVersion": UINT},
         "Interní child původního component.disable. Admission stop a lifecycle SUSPENDED/QUARANTINED pod R06; disable potvrzen až exact current outcome/route/runtime/state/heartbeat, timeout jen reconciliation.",
         ["disable timeout nesmí vrátit lifecycle ACTIVE", "duplicitní child nerozmnoží dispatch"]),
        ("component.control.ack", "44.5", "LOCAL_STATE_IDEMPOTENT", ack, receipt,
         "ADMISSION povoluje jen ACCEPTED/REJECTED, OUTCOME jen COMPLETED/FAILED/UNKNOWN. ACK je evidence; current effective transition potřebuje kompletní joined evidence. Stale ACK nemění parent a UNKNOWN drží claims.",
         ["wire CANCELLED zakázáno", "ADMISSION+COMPLETED zakázáno", "UNKNOWN neuzavře parent"]),
    ]
    for name, section, effect, inputs, outputs, rule, tests in specs:
        local_refs = [*refs, b.source(section), "ssot:6.6", "ssot:6.7", "ssot:49.4", "amendment:R05"]
        control_dispatch = name in {"component.control.enable", "component.control.disable"}
        query_only = name == "component.state.query"
        semantic = {"rule": rule, "testCases": tests, "recoveryOnly": False,
                    "newBusinessEffectsAllowed": control_dispatch,
                    "payloadVsEnvelope": "Schema popisuje trusted protocol payload. Duplicitní identity/digest fields se musí rovnat gateway envelope; caller jimi nesmí přepsat authority.",
                    "dependentSchema": "state payload a dependencySummary/operationalState se validují druhou fází proti exact active component revision/state schema digestu; samotné základní JSON schema nestačí.",
                    "exactInteger": "epoch/sequence/version/count je canonical decimal uint64 string <=9223372036854775807; JSON Number se na této hranici nepřijímá."}
        trigger = ("První target control invocation po durable D commitu; same logical key target deduplikuje. Timeout vyžaduje exact state/route/runtime read-back." if control_dispatch else "Žádný external business trigger; pouze evidence/current observation commit. Query nemění projection.")
        oracle = ("Exact requested ACTIVE/INACTIVE postcondition vyžaduje shodný fenced outcome, current runtime/route, state projection a heartbeat z téhož epoch. Neúplná/protichůdná sada => UNKNOWN; ACK samotné nestačí." if control_dispatch or name == "component.control.ack" else "Current DB monotonic head + immutable observation digest; duplicate vrací původní receipt, stale projection se nesmí obnovit.")
        policies = {**base_policies(), "expectedStateVersionPolicy": "policy:controlCAS" if control_dispatch else "policy:observationCAS",
                    "idempotencyScope": "policy:parentIdentity" if control_dispatch else "policy:observationKey",
                    "idempotencyKeySource": "policy:parentIdentity" if control_dispatch else "policy:observationKey",
                    "orderedLockPlanId": "policy:componentLocks", "concurrencyScope": "policy:controlClaim" if control_dispatch else "policy:noClaim",
                    "concurrencyKeyDerivation": "policy:controlClaim" if control_dispatch else "policy:noClaim",
                    "concurrencyClaimPoint": "policy:controlClaim" if control_dispatch else "policy:noClaim",
                    "activationRelation": "policy:componentAdmission", "successorPolicy": "policy:controlSuccessor" if control_dispatch else "policy:noSuccessor",
                    "cleanupPolicy": "policy:closure" if control_dispatch else "policy:noOwnedResource",
                    "possibleEffectTrigger": b.policy(name + ".trigger", {"trigger": trigger}, local_refs),
                    "reconciliationOracleId": b.policy(name + ".oracle", {"readBack": oracle, "noBlindReplay": True}, local_refs)}
        if query_only:
            policies.update({"transactionProfileId": "policy:queryTransaction", "orderedLockPlanId": "policy:queryLocks", "expectedStateVersionPolicy": b.policy("querySnapshot", {"CAS": "NONE_READ_ONLY", "snapshot": "exact requested revision/binding/epoch; metadata current as of repeatable-read snapshot", "sequence": "vrací source sequence; caller neposílá observation sequence"}, local_refs),
                             "idempotencyScope": b.policy("queryIdentity", {"businessIdempotency": "NONE_READ_ONLY", "transport": "canonical query arguments + exact snapshot digest; reissue smí vrátit novější snapshot, ne tvrdit immutable business replay"}, local_refs), "idempotencyKeySource": "policy:queryIdentity"})
        if control_dispatch:
            expected = ["READY_FOR_ACTIVATION"] if name.endswith("enable") else ["ACTIVE"]
            transitions = [{"from": expected[0], "to": "ENABLE_REQUESTED" if name.endswith("enable") else "DISABLE_REQUESTED", "guard": "current parent command + expected component version + binding/epoch + R06 composite lifecycle + readiness", "write": "command, child outbox a admission stop atomicky; žádný effect pod locky"}]
        else:
            expected = LIFECYCLES
            transitions = [{"from": state, "to": state, "guard": "current lineage/sequence, jinak stale evidence only", "write": rule} for state in LIFECYCLES]
        add_operation(b, name, "component", "INTERNAL_PROTOCOL", effect,
                      "RETRY_AFTER_RECONCILIATION" if control_dispatch else "SAFE_RETRY", expected,
                      inputs, outputs, policies, transitions, local_refs,
                      ["E-003", "E-032", "E-033", "E-034", "E-038", "E-170"], semantic,
                      "původní component.enable/disable" if control_dispatch or name == "component.control.ack" else "current component runtime/release observation nebo parent state-query request")
        if name == "component.control.ack":
            schema = b.reg["schemas"]["schema:" + name + ".input"]
            schema["definition"]["allOf"] = [{"if": {"properties": {"layer": {"const": "ADMISSION"}}}, "then": {"properties": {"status": enum("ACCEPTED", "REJECTED")}}, "else": {"properties": {"status": enum("COMPLETED", "FAILED", "UNKNOWN")}}}]
            schema["canonicalDigest"] = digest({key: value for key, value in schema.items() if key != "canonicalDigest"})
            b.operations[-1]["successorPolicy"] = "policy:controlSuccessor"
            b.operations[-1]["checkpointPolicy"] = "policy:checkpoint"
            reseal(b.operations[-1])
        if control_dispatch:
            outbox = b.put("outbox", "outbox:" + name, {"purpose": "SIDE_EFFECT_DISPATCH", "authority": "exact child attempt pod původním parent commandem; one authority row per immutable attempt", "dispatch": "T1/D/E/T2/T3; R08 whole-command closure", "noSecondBusinessOutcome": True}, ["ssot:51.28", "amendment:R08", *local_refs])
            b.operations[-1]["outboxPurposes"] = [outbox]
            b.operations[-1]["canonicalDigest"] = digest({key: value for key, value in b.operations[-1].items() if key != "canonicalDigest"})


def validate_bundle(bundle, root=ROOT, require_complete=False):
    errors = []
    reg = bundle["registries"]
    # Inventář se znovu odvodí z autorit, nikoli ze současného coverage reportu.
    evidence = Builder(root)
    common(evidence)
    _, expected_inventory, expected_internal, expected_actions = inventory(evidence)
    if bundle["requiredOperations"] != expected_inventory or bundle["additionalOperationsR18"] != expected_internal:
        errors.append("REQUIRED_INVENTORY_DRIFT")
    if [row["actionId"] for row in bundle["objectActions"]] != [row["actionId"] for row in expected_actions]:
        errors.append("ACTION_INVENTORY_DRIFT")
    for action in bundle["objectActions"]:
        for target in action["operationIds"]:
            if target.removeprefix("operation:") not in expected_inventory:
                errors.append("ACTION_TARGET_MISSING: " + action["actionId"])
    original_routes = json.loads((root / "operation-map.expanded.json").read_text(encoding="utf-8"))
    if bundle["apiAliases"] != original_routes:
        errors.append("API_ALIAS_DRIFT: 503 explicitních mapování se nesmí změnit.")
    aliases = defaultdict(list)
    for route in bundle["apiAliases"]:
        aliases[route["operation"]].append(route)
    if len({(r["method"], r["path"]) for r in bundle["apiAliases"]}) != 503:
        errors.append("API_ALIAS_DUPLICATE")
    for name, rows in aliases.items():
        if len({(r["exposure"], r["canonical_writer"]) for r in rows}) != 1:
            errors.append("ALIAS_CONTRACT_CONFLICT: " + name)
    for path, expected in bundle["documentDigests"].items():
        target = (root / path).resolve()
        if not target.is_relative_to(root.resolve()) or not target.is_file():
            errors.append("SOURCE_PATH_INVALID: " + path)
        elif hashlib.sha256(target.read_bytes()).hexdigest() != expected:
            errors.append("SOURCE_DIGEST_DRIFT: " + path)
    for key, source in reg["sources"].items():
        path = root / source["document"]
        if source["document"] not in bundle["documentDigests"]:
            errors.append("SOURCE_DOCUMENT_UNREGISTERED: " + key)
            continue
        lines = path.read_bytes().decode("utf-8").splitlines(keepends=True)
        actual = "".join(lines[source["startLine"] - 1:source["endLine"]])
        if actual != source["text"] or hashlib.sha256(actual.encode("utf-8")).hexdigest() != source["sha256"]:
            errors.append("SOURCE_FRAGMENT_DRIFT: " + key)
    for registry, entries in reg.items():
        if registry == "sources":
            continue
        for key, row in entries.items():
            if not row.get("definition") or not row.get("authoritySourceRefs"):
                errors.append("EMPTY_DEFINITION: " + key)
            if digest({k: v for k, v in row.items() if k != "canonicalDigest"}) != row["canonicalDigest"]:
                errors.append("DEFINITION_DIGEST: " + key)
            for ref in row.get("authoritySourceRefs", []):
                if ref not in reg["sources"]:
                    errors.append("DANGLING_AUTHORITY: " + key)
    ids = set()
    names = set()
    for row in bundle["operations"]:
        name = row.get("operationName", "?")
        if set(row) != set(FIELDS):
            errors.append("OPERATION_FIELDS: " + name)
            continue
        if row["operationId"] in ids or name in names:
            errors.append("OPERATION_DUPLICATE: " + name)
        ids.add(row["operationId"])
        names.add(name)
        if row["exposureClass"] not in EXPOSURES or row["sideEffectClass"] not in EFFECTS:
            errors.append("INVALID_EXPOSURE_OR_EFFECT: " + name)
        if row["retryClass"] not in {"SAFE_RETRY", "RETRY_AFTER_RECONCILIATION", "NO_AUTOMATIC_RETRY"}:
            errors.append("INVALID_RETRY_CLASS: " + name)
        for field, registry in REF_FIELDS.items():
            if row[field] not in reg[registry]:
                errors.append(f"DANGLING_REFERENCE: {name}.{field}")
        for field, registry in ARRAY_REFS.items():
            for reference in row[field]:
                if reference not in reg[registry]:
                    errors.append(f"DANGLING_REFERENCE: {name}.{field}:{reference}")
        for transition_id in row["allowedTransitionIds"]:
            transition = reg["transitions"].get(transition_id, {}).get("definition", {})
            if transition.get("machineId") != row["stateMachineId"]:
                errors.append("TRANSITION_MACHINE_MISMATCH: " + name)
        for reference in row["apiOperationIds"] + row["uiActionIds"] + row["chatCapabilityIds"]:
            surface = reg["surfaces"].get(reference, {}).get("definition", {})
            if surface.get("operationId") != row["operationId"]:
                errors.append("SURFACE_OPERATION_MISMATCH: " + name)
        for field in ("expectedStates", "allowedTransitionIds", "requirementIds", "authoritySourceRefs", "selfTestCaseIds", "acceptanceGateIds"):
            if not row[field]:
                errors.append(f"EMPTY_CONTRACT: {name}.{field}")
        if digest({key: value for key, value in row.items() if key != "canonicalDigest"}) != row["canonicalDigest"]:
            errors.append("OPERATION_DIGEST: " + name)
        for alias in aliases.get(name, []):
            if alias["exposure"] != row["exposureClass"]:
                errors.append("EXPOSURE_CONFLICT: " + name)
        parity = bundle["exposureParity"].get(row["operationId"])
        if not parity or parity["exposureClass"] != row["exposureClass"]:
            errors.append("EXPOSURE_PARITY_MISSING: " + name)
        if row["exposureClass"] == "INTERNAL_PROTOCOL" and (row["apiOperationIds"] or aliases.get(name)):
            errors.append("INTERNAL_PUBLIC_INVOKE: " + name)
        if row["exposureClass"] == "OWNER_COMMAND" and not all(row[field] for field in ("apiOperationIds", "uiActionIds", "chatCapabilityIds", "auditEventTypes")):
            errors.append("OWNER_PARITY_INCOMPLETE: " + name)
        semantics = bundle["semanticContracts"].get(row["operationId"], {})
        if semantics.get("recoveryOnly") and semantics.get("newBusinessEffectsAllowed") is not False:
            errors.append("RECOVERY_BUSINESS_EFFECT_FORBIDDEN: " + name)
        if row["exposureClass"] == "OWNER_QUERY" and row["sideEffectClass"] != "READ_ONLY":
            errors.append("QUERY_BUSINESS_MUTATION: " + name)
    missing = sorted(set(bundle["requiredOperations"]) - names)
    if missing != bundle["coverage"]["missingOperationContracts"]:
        errors.append("COVERAGE_REPORT_DRIFT")
    if names - set(bundle["requiredOperations"]):
        errors.append("UNAUTHORIZED_OPERATION_NAME")
    if require_complete:
        errors += ["MISSING_OPERATION_CONTRACT: " + name for name in missing]
        errors += ["UNRESOLVED_ACTION: " + row["actionId"] for row in bundle["objectActions"] if not row["operationIds"]]
        errors += ["DESIGN_DEPENDENCY: " + item for item in bundle["designDependencies"]]
    if bundle["batchReady"] or bundle["status"] != "PARTIAL_DESIGN":
        errors.append("FALSE_COMPLETION: tento dílčí registr nesmí povolit Batch.")
    return errors


def compile_bundle(root=ROOT):
    b = Builder(root)
    common(b)
    routes, needed, internal, actions = inventory(b)
    b.api_aliases = routes
    actions = bind_actions(actions, needed)
    recovery_contracts(b)
    component_contracts(b)
    load_plugins(b)
    names = {row["operationName"] for row in b.operations}
    bundle = {"format": "LW0D-OPERATION-CONTRACTS/1", "phase": "DESIGN", "status": "PARTIAL_DESIGN", "batchReady": False,
              "documentDigests": b.documents, "apiAliases": routes, "requiredOperations": needed,
              "additionalOperationsR18": internal, "objectActions": actions, "operations": b.operations,
              "registries": b.reg, "semanticContracts": b.semantic, "exposureParity": b.parity, "plugins": b.plugin_reports,
              "coverage": {"apiRoutes": len(routes), "requiredOperationNames": len(needed), "concreteOperationRecords": len(b.operations), "objectActions": len(actions), "missingOperationContracts": sorted(set(needed) - names)},
              "designDependencies": ["Dokončit ostatní explicitní operation records; inventory není schema.",
                                     "Doplnit explicitní §40 action→operation projections včetně čistě lokálních UI akcí a všech chybějících ne-API operací.",
                                     "Převést A0 traceability IDs na skutečné normative atoms §55.5; E-xxx není vydáváno za KCML-REQ hash.",
                                     "Sjednotit pinned parent deadline/retry budget s provozním policy registrem B-10.",
                                     "Materializovat exact revision-dependent state payload schemas a recovery oracle/cleanup registry pro každý podporovaný resource kind.",
                                     "Ověřit DB lock-parent/child ordinal pro platform_recovery_item; §51.38 ho nečísluje, nesmí být odvozen z názvu tabulky."],
              "laterVerificationObligations": ["BUILD: executable schemas/handlers, kompilace, DB/crash/concurrency a parity testy.", "RUNTIME: exact deployment/identity/effect/cleanup evidence. Nejde o podmínku DESIGN podle R01."]}
    errors = validate_bundle(bundle, root)
    if errors:
        raise ValueError("\n".join(errors))
    return bundle


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Vypíše deterministický návrhový JSON na stdout; nic nezapisuje.")
    parser.add_argument("--check-complete", action="store_true", help="Neúplné B-01 vrátí exit 2.")
    args = parser.parse_args()
    bundle = compile_bundle()
    if args.json:
        print(json.dumps(bundle, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(bundle["coverage"], ensure_ascii=False, indent=2))
    if args.check_complete:
        errors = validate_bundle(bundle, require_complete=True)
        print(f"B-01 není dokončeno: {len(errors)} blokujících položek.", file=sys.stderr)
        return 2 if errors else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
