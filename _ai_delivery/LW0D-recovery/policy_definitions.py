"""Explicitní návrhové hodnoty R20; nejsou odhadem aktuálního serveru."""

from __future__ import annotations


MIB = 1024 * 1024
GIB = 1024 * MIB
DAY = 86400000
DEFINITIONS: list[dict] = []


def fixed(topic: str, key: str, value, unit: str = "record", refs: tuple[str, ...] = ()):
    DEFINITIONS.append({"topic": topic, "key": key, "algorithm": "CONSTANT_V1",
                        "value": value, "unit": unit, "refs": list(refs)})


def derived(topic: str, key: str, algorithm: str, inputs: dict, unit: str,
            minimum: int, maximum: int, refs: tuple[str, ...] = ()):
    DEFINITIONS.append({"topic": topic, "key": key, "algorithm": algorithm,
                        "inputs": inputs, "unit": unit, "minimum": minimum,
                        "maximum": maximum, "refs": list(refs)})


fixed("41.1:1", "network.publicDomain", "kaja.hcasc.cz", "hostname")
fixed("41.1:2", "network.apiBasePath", "/api/v1", "path")
fixed("41.1:3", "network.componentZone", "kaja.hcasc.cz", "hostname")
fixed("41.1:4", "network.publicIpv4", "89.221.222.92", "ipv4")
fixed("41.1:5", "network.trustedProxies", {"cidrs": ["127.0.0.1/32", "::1/128"],
      "requireDirectNginxPeer": True, "stripUntrustedForwarded": True})
fixed("41.1:6", "network.body", {"jsonBytes": MIB, "unaryHardBytes": 16*MIB,
      "headersBytes": 32768, "maxHeaders": 100, "depth": 64, "sseEventBytes": 65536,
      "restDefaultPage": 50, "restMaxPage": 200, "mcpUsesOwnPagination": True}, refs=("31.2", "50.19"))
fixed("41.1:7", "network.upload", {"streamBytes": 256*MIB, "chunkBytes": 65536,
      "inFlightPerOwner": 2, "verifyDigestBeforePublish": True, "unverifiedQuarantine": True})
fixed("41.1:8", "network.sseHeartbeatMs", 15000, "ms")
fixed("41.1:9", "network.http", {"headersMs": 10000, "connectMs": 10000,
      "idleMs": 30000, "unaryDeadlineMs": 120000, "streamDeadlineMs": 3600000,
      "deadlineOnlyShortens": True, "transportTimeoutNeverProvesNoEffect": True})
fixed("41.1:10", "network.dns", {"provider": "WEDOS_WAPI", "zone": "kaja.hcasc.cz",
      "credentialSource": "SECRET_MANAGER", "authoritativeReadBack": True,
      "pollMs": 5000, "propagationDeadlineMs": 600000, "cleanup": "EXACT_OWNED_TXT_ROW_ID",
      "metadataSource": "PINNED_DEPLOYMENT_PROVIDER_DESCRIPTOR", "requireDescriptorDigest": True}, refs=("28.6",))
fixed("41.2:1", "auth.username", "KRMAR78", "string")
fixed("41.2:2", "auth.cardinality", {"ownerIdentity": 1, "ownerApiCredential": 1})
fixed("41.2:3", "auth.permissionModel", {"roles": False, "groups": False, "multiUser": False})
fixed("41.2:4", "auth.passwordSource", "GITHUB_ACTIONS_PASS", "enum")
fixed("41.2:5", "auth.passwordSync", {"everyDeploy": True, "writer": "owner-identity",
      "rotateVerifierAtomically": True, "neverLogPlaintextInDeployment": True})
fixed("41.2:6", "auth.apiCredential", {"randomBytes": 32, "singleton": True,
      "atomicCiphertextVerifierEpoch": True, "idempotentOutcomeReplay": True,
      "genericSecretRotationAllowed": False}, refs=("49.22.1",))
fixed("41.2:7", "auth.sessionDurationMs", 43200000, "ms")
fixed("41.2:8", "auth.idleTimeoutMs", 1800000, "ms")
fixed("41.2:9", "auth.trustedDevice", {"durationMs": 30*DAY, "mfaBypass": False})
fixed("41.2:10", "auth.mfaRequired", True, "boolean")
fixed("41.2:11", "auth.loginThrottle", {"windowMs": 900000, "failures": 5, "lockMs": 900000})
fixed("41.2:12", "auth.recoveryCodeCount", 10, "count")
fixed("41.2:13", "auth.sessionListRetention", {"closedMs": 90*DAY, "retainLive": True,
      "retainReferenced": True, "eraseCredentialMaterialOnRevocation": True})

fixed("41.3:6", "ai.provider", "OPENAI", "enum")
fixed("41.3:7", "ai.sdk", {"packages": ["openai", "@openai/agents"],
      "versions": "EXACT_PNPM_LOCK_AND_RELEASE_MANIFEST", "directImportHome": "packages/openai-runtime"})
fixed("41.3:8", "ai.execution", {"direct": "RESPONSES", "graph": "AGENTS_SDK",
      "unknownExecutionMode": "BLOCK", "storeDescriptorBeforeCall": True})
fixed("41.3:9", "ai.background", {"selection": "BACKGROUND_IF_CAPABILITY_AND_DURABLE_RETRIEVE_AVAILABLE",
      "otherwise": "FOREGROUND", "neverFallbackAfterDispatch": True, "pollMs": 2000,
      "retrieveDeadlineMs": 3600000})
fixed("41.3:10", "ai.structuredOutput", {"strict": True, "additionalProperties": False,
      "schemaRequired": True, "truncation": "disabled", "refusalIsSuccess": False})
fixed("41.3:11", "ai.reasoning", {"select": "HIGHEST_SUPPORTED_IN_PINNED_CAPABILITY",
      "orderedEfforts": ["none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"],
      "unsupportedParameters": "OMIT", "sampling": "OMIT_FOR_REASONING",
      "outputReserveTokens": 32768, "maxOutputTokens": 65536,
      "capabilitySmallerThanRequiredReserve": "BLOCK"})
fixed("41.3:12", "ai.callLimits", {"modelCalls": 64, "sdkTurns": 64, "toolCalls": 256,
      "handoffDepth": 8, "repairCalls": 8})
fixed("41.3:13", "ai.parallelTools", {"default": 1, "verifiedIndependentReadOnlyMaximum": 4,
      "requireDisjointKeys": True, "mutationsParallel": False, "deterministicOutputOrder": True})
fixed("41.3:14", "ai.deadlines", {"runMs": 3600000, "callMs": 900000,
      "childRule": "MIN_PARENT_OPERATION_PLATFORM", "progressExtendsAbsolute": False})
fixed("41.3:15", "ai.budget", {"inputTokens": 2000000, "outputTokens": 500000,
      "totalTokens": 2500000, "costMicroUsd": 50000000, "currency": "USD",
      "reserveBeforeDispatch": True, "priceSource": "PINNED_PRICING_SNAPSHOT",
      "unknownPrice": "BLOCK", "overrun": "PERSIST_USAGE_NO_SUCCESSOR"})
fixed("41.3:16", "ai.retrieve", {"readOnlyAttempts": 5, "baseDelayMs": 1000,
      "maxDelayMs": 30000, "jitterBuckets": 251, "jitter": "SHA256_OPERATION_ATTEMPT_UINT16BE",
      "knownResponseId": "RETRIEVE_EXISTING", "unknownSubmit": "MANUAL_REVIEW_NO_CREATE"})
fixed("41.3:17", "ai.tracing", {"durableEvents": True, "sequenceDedupe": True,
      "persistBeforePublish": True, "providerTraceIsAuthority": False})
fixed("41.3:18", "ai.nonSuccess", {"refusal": "PERSIST_REFUSAL_NO_SUCCESS",
      "incomplete": "PERSIST_REASON_NO_SUCCESS", "schemaInvalidAfterEffect": "RECONCILE_NO_REEXECUTE"})
fixed("41.3:19", "ai.capabilities", {"maxAgeMs": DAY, "pinExactModel": True,
      "requireEndpointSchemaToolsReasoningLimits": True, "unknown": "BLOCK_SUBMIT"})
fixed("41.3:20", "ai.descriptor", {"immutable": True, "digest": "SHA256_RFC8785",
      "exactIntegers": "DECIMAL_STRINGS", "commitBeforeDispatch": True}, refs=("52.5",))
fixed("41.3:21", "ai.createSdkRetries", 0, "count")
fixed("41.3:22", "ai.providerIdentity", {"responseId": "DURABLE_RETRIEVE_HANDLE",
      "requestId": "DIAGNOSTIC_ONLY_NOT_RETRIEVE_HANDLE"})
fixed("41.3:23", "ai.history", {"authority": "CANONICAL_DB_SESSION_ITEMS",
      "compactionAtContextPermille": 700, "preservePendingCallsAndProvenance": True,
      "replaceOnlyAfterValidatedCommit": True, "automaticTruncation": False})
fixed("41.3:24", "ai.resumeCompatibility", {"requireExactSdkSerializerAdapterGraphDigests": True,
      "retainRuntimeUntilPendingClosed": True, "unknownCompatibility": "BLOCK_NO_NEW_EFFECT"})
fixed("41.3:25", "ai.readiness", {"suite": "52.36", "requireAll66Scenarios": True,
      "liveAcceptanceSeparateFromUnitTests": True, "noKeyCoreReadiness": "PASS_AI_CONFIGURATION_REQUIRED"})

derived("41.4:1", "generation.workerConcurrency", "CPU_HALF_CLAMP_1_4_V1",
        {"availableCpuCores": {"type": "integer", "minimum": 1, "maximum": 4096}}, "slots", 1, 4)
fixed("41.4:2", "generation.queue", {"normalCapacity": 1024, "recoveryCapacity": 64,
      "normalWeight": 4, "recoveryWeight": 1, "agingMs": 30000, "claimBatch": 16,
      "recoveryReservationBorrowable": False})
fixed("41.4:3", "generation.phase", {"deadlineMs": 7200000, "leaseMs": 30000,
      "heartbeatMs": 5000, "minDispatchWindowMs": 10000, "coordinatorsPerJob": 1})
fixed("41.4:4", "generation.modelExecution", {"policyKey": "ai.background", "pinToPhase": True})
fixed("41.4:5", "generation.specialists", {"maxRuns": 32, "maxModelTurnsPerPhase": 64,
      "maxModelCallsPerJob": 512})
fixed("41.4:6", "generation.budget", {"toolCalls": 4096, "inputTokens": 16000000,
      "outputTokens": 4000000, "totalTokens": 20000000, "costMicroUsd": 500000000,
      "currency": "USD", "aggregateChildrenUsage": True, "noAutomaticBudgetIncrease": True})
fixed("41.4:7", "generation.plan", {"maxNodes": 4096, "maxEdges": 16384,
      "cycles": "REJECT", "maxDepth": 256})
fixed("41.4:8", "generation.workspace", {"bytes": 4*GIB, "files": 20000,
      "fileBytes": 16*MIB, "pathUtf8Bytes": 1024, "symlinks": False,
      "caseCollision": "REJECT", "reserveBeforeWrite": True})
fixed("41.4:9", "generation.patch", {"operations": 1000, "contentBytes": 16*MIB,
      "requireBaseDigest": True, "atomic": True, "readBackAfterApply": True})
fixed("41.4:10", "generation.repairIterations", 8, "count")
fixed("41.4:11", "generation.stagnation", {"sameFailureDigestIterations": 2,
      "maxDistinctStrategies": 3, "exhausted": "BLOCK_WITH_EVIDENCE"})
derived("41.4:12", "generation.validationConcurrency", "CPU_HALF_CLAMP_1_4_V1",
        {"availableCpuCores": {"type": "integer", "minimum": 1, "maximum": 4096}}, "slots", 1, 4)
fixed("41.4:13", "generation.capabilitySearch", {"maxResults": 200,
      "cacheMs": 300000, "key": "CATALOG_REVISION_QUERY_DIGEST", "revalidateAtApproval": True})
fixed("41.4:14", "generation.checkpoint", {"maxIdleMs": 5000,
      "afterEveryEffectBeforeNextModel": True, "afterEveryWorkspaceCommit": True})
fixed("41.4:15", "generation.artifactRetention", {"unreferencedClosedMs": 30*DAY,
      "retainPendingReplayCurrentRollbackAuditReferences": True, "verifiedArchiveBeforeDelete": True})
fixed("41.4:16", "generation.candidateCleanup", {"graceMs": DAY, "pollMs": 30000,
      "requireClosureAndZeroReferences": True, "unknown": "MANUAL_REVIEW_KEEP_RESOURCES"})
fixed("41.4:17", "generation.compensation", {"order": "REVERSE_COMMITTED_DAG",
      "onlyDeclaredOwnedResources": True, "checkpointEachStep": True,
      "unknown": "RECONCILE_NO_NEXT_DESTRUCTIVE_STEP"})
fixed("41.4:18", "generation.activation", {"barrierDeadlineMs": 300000,
      "postflightDeadlineMs": 300000, "samples": 3, "sampleMs": 5000,
      "exactPointersAndEpoch": True, "timeoutWithUnknown": "BLOCK"})
fixed("41.4:19", "generation.approvalPresentation", {"default": "EXPANDED_EXACT_DIFF",
      "ownerPreferenceAllowed": True, "preferenceChangesAuthority": False})
fixed("41.4:20", "generation.rollback", {"activationSet": "CURRENT_SET_REVERSE_SWITCH",
      "deployment": "NEW_RUN_IF_PRIOR_ACTIVE_TERMINAL", "retainFrozenPrevious": True,
      "verifyEffectiveEpochBeforeSuccess": True}, refs=("49.17", "49.24"))

fixed("41.5:11", "browser.launch", {"source": "PINNED_BROWSER_RUNTIME_BUILD_MANIFEST",
      "allowUserExecutableOrArgs": False, "publicCdp": False, "launch": "TRUSTED_HOST_ONLY"})
fixed("41.5:12", "browser.compatibility", {"exactDigests": ["node", "playwright", "browser",
      "os", "dependencies", "fonts", "runtime"], "requireTestedManifest": True,
      "hotMigrateLiveContext": False})
derived("41.5:13", "browser.hostSlots", "BROWSER_SLOTS_V1",
        {"availableCpuCores": {"type": "integer", "minimum": 1, "maximum": 4096},
         "allocatableMemoryMiB": {"type": "integer", "minimum": 4096, "maximum": 16777216}}, "slots", 1, 4)
fixed("41.5:13", "browser.hostLifecycle", {"warmHosts": 1, "maxAgeMs": 6*3600000,
      "maxClosedContexts": 100, "upgrade": "BLUE_GREEN_DRAIN",
      "drainDeadlineMs": 300000, "liveContext": "NO_MIGRATION_RECONCILE_BEFORE_CLEANUP"})
fixed("41.5:14", "browser.capacity", {"persistentContexts": False, "contextsPerHost": 4,
      "pagesPerContext": 8, "renderersPerHost": 32, "reserveBeforeCreate": True,
      "full": "QUEUE_OR_TYPED_CAPACITY_NO_UNBOUNDED_SPAWN"})
fixed("41.5:15", "browser.identity", {"pageGeneration": "MONOTONIC_BIGINT",
      "frameAttachmentEpoch": "MONOTONIC_BIGINT", "documentEpoch": "MONOTONIC_BIGINT",
      "navigationSequence": "MONOTONIC_BIGINT", "staleEvent": "EVIDENCE_ONLY_NO_STATE_WRITE"})
fixed("41.5:16", "browser.control", {"writersPerSession": 1, "leaseMs": 30000,
      "heartbeatMs": 5000, "drainMs": 30000, "resetInputBeforeGrant": True,
      "newEpochAndFenceOnTakeover": True, "unknownMutation": "BLOCK_TAKEOVER"})
fixed("41.5:17", "browser.timeouts", {"navigationMs": 60000, "actionMs": 30000,
      "challengeWaitMs": 300000, "idleMs": 1800000, "reconcileAttemptMs": 120000,
      "reconcileAlertAgeMs": 300000, "unknownRetentionMs": None,
      "timeoutIsNotNotApplied": True, "parentDeadlineOnlyShortens": True})
fixed("41.5:18", "browser.preview", {"codec": "JPEG", "fps": 10, "widthMax": 1920,
      "heightMax": 1080, "quality": 80, "patch": "FULL_FRAME_ONLY_V1",
      "keyframeMs": 1000, "bufferedFramesPerViewer": 2, "maxViewers": 4,
      "reconnectSnapshotAckRequired": True, "replayInput": False})
fixed("41.5:19", "browser.viewport", {"defaultWidth": 1280, "defaultHeight": 720,
      "defaultDpr": 1, "defaultZoom": 1, "actualTransform": "FRESH_HOST_OBSERVATION",
      "nestedFrameMatrix": "COMPOSE_EXACT_CURRENT_DOCUMENT_CHAIN",
      "inputMustMatchViewportRevision": True})
fixed("41.5:20", "browser.observation", {"domBytes": 4*MIB, "semanticNodes": 20000,
      "screenshotBytes": 8*MIB, "networkEvents": 1000, "deadlineMs": 10000,
      "overflow": "EXPLICIT_TRUNCATION_EVIDENCE_NO_TARGET_GUESS"})
fixed("41.5:21", "browser.locator", {"compiler": "PINNED_AUTOMATION_REVISION",
      "requireFreshUniqueTarget": True, "actionability": True, "trialBeforeMutation": True,
      "force": "ONLY_EXPLICIT_TYPED_ACTION_CONTRACT", "overlay": "REOBSERVE_NO_BLIND_CLICK"})
fixed("41.5:22", "browser.input", {"strategySource": "EXACT_FIELD_CONTRACT",
      "strategies": ["fill", "type", "composition", "contenteditable", "masked", "locale"],
      "unknown": "BLOCK_BEFORE_INPUT", "mutatingEventIntentBeforeFirstCharacter": True})
fixed("41.5:23", "browser.mutation", {"classifier": "EXACT_ACTION_CONTRACT",
      "unresolved": "BROWSER_MUTATION_TRIGGER_UNRESOLVED", "intentBeforePossibleTrigger": True,
      "appendDispatchPhaseBeforeAndAfter": True, "postconditionIndependent": True})
fixed("41.5:24", "browser.waiters", {"types": ["popup", "navigation", "dialog", "filechooser", "download"],
      "armBeforeInput": True, "scope": "CURRENT_ACTION_PAGE_DOCUMENT",
      "cancelOnActionClosure": True, "missingRequiredWaiter": "BLOCK_DISPATCH"})
fixed("41.5:25", "browser.capture", {"screenshot": "BEFORE_AFTER_ACTION_AND_FAILURE",
      "trace": "BOUNDED_PER_RUN", "domSnapshot": "ON_FAILURE_AND_EXPLICIT_OBSERVE",
      "traceBytes": 256*MIB, "artifactsPerRunBytes": GIB, "retainRequiredEvidence": True})
fixed("41.5:26", "browser.transfer", {"uploadBytes": 256*MIB, "downloadBytes": 256*MIB,
      "concurrentPerSession": 2, "streamChunkBytes": 65536,
      "scan": "QUARANTINE_UNTIL_PINNED_SCANNER_PASS", "requireDigestAndDeclaredType": True,
      "scanUnavailable": "BLOCK_PUBLICATION", "neverExecuteDownload": True})
fixed("41.5:27", "browser.stateBundle", {"serializer": "PINNED_MEMBER_TYPE_VERSION",
      "capture": "EXCLUSIVE_ACCOUNT_CONTEXT_BARRIER_NO_PENDING_MUTATION",
      "restoreOrder": ["context", "cookies", "originStorage", "typedAuthenticatorState", "verifyAuthenticatedCondition"],
      "maxBytes": 64*MIB, "maxUnreferencedAgeMs": 30*DAY,
      "verifyBeforeEveryReuse": True, "unknownMember": "BLOCK_NOT_DROP"})
fixed("41.5:28", "browser.account", {"condition": "EXACT_ACCOUNT_TENANT_CONTRACT",
      "defaultConcurrency": "EXCLUSIVE_MUTATION", "readSharedRequiresProof": True,
      "authEpochChangesInvalidateBindings": True, "unknownEffectHoldsResourceClaim": True})
fixed("41.5:29", "browser.challenges", {"adapters": ["OTP", "PUSH", "WEBAUTHN", "PASSKEY",
      "CLIENT_CERTIFICATE", "CAPTCHA", "NATIVE"], "selection": "TYPED_CURRENT_CHALLENGE",
      "ownerDeviceFallback": True, "bypassProtection": False,
      "lateCompletion": "READ_BACK_BEFORE_RETRY", "oneShotResponseCAS": True})
fixed("41.5:30", "browser.bridge", {"mTlsRequired": True, "certificateDays": 30,
      "renewBeforeDays": 7, "leaseMs": 30000, "heartbeatMs": 5000,
      "profileLeaseMs": 30000, "reconnectGraceMs": 60000,
      "reconnectNewEpoch": True, "requireExactBuildCapabilityDigest": True,
      "replayInput": False, "certificateRotationRevokesPriorAdmissions": True})
fixed("41.5:31", "browser.schedule", {"timezone": "PINNED_IANA_OWNER_BUSINESS_INPUT",
      "dstGap": "SKIP_NONEXISTENT_LOCAL_OCCURRENCE", "dstFold": "EARLIER_OFFSET_ONCE",
      "misfire": "COALESCE_LATEST", "maxCatchUp": 1, "maxQueuedPerSchedule": 32,
      "runDeadlineMs": 3600000, "maxActions": 1000, "overlapDefault": "QUEUE",
      "allowedOverlap": ["SKIP", "QUEUE", "COALESCE", "REJECT"],
      "occurrenceKey": "REVISION_LOCAL_OCCURRENCE_SELECTED_OFFSET", "dedupeBeforeEnqueue": True})
fixed("41.5:32", "browser.recovery", {"normalQueue": 1024, "recoveryQueue": 64,
      "dropOnlyReplaceablePreview": True, "cleanupDeadlineMs": 120000,
      "unknown": "MANUAL_REVIEW_KEEP_CLAIM", "reservedRecoveryCapacity": True})
fixed("41.5:33", "browser.drift", {"consecutiveMismatches": 1, "automaticBlindRepair": False,
      "revalidateBeforeUnattendedActivation": True, "heartbeatMs": 5000,
      "leaseMs": 30000, "healthyCleanupP95Ms": 10000,
      "p95ExcludesPendingReconciliationAndArtifactUpload": True})

for number, key, value in [(1, "schedulerMs", 1000), (2, "probeMs", 30000),
                           (3, "staleMs", 90000), (4, "repairCooldownMs", 300000),
                           (5, "recertificationMs", DAY), (6, "alertDedupeMs", 300000)]:
    fixed(f"41.6:{number}", f"monitor.{key}", value, "ms")
fixed("41.6:7", "monitor.channels", {"primary": "PINNED_PRIMARY_BUSINESS_BINDING",
      "backup": "PINNED_DISTINCT_BACKUP_BUSINESS_BINDING", "backupOn": "KNOWN_PRIMARY_FAILURE",
      "unknownDelivery": "RECONCILE_OR_MARK_POSSIBLE_DUPLICATE_NO_FALSE_SUCCESS"})
fixed("41.6:8", "monitor.delivery", {"attempts": 5, "delaysMs": [1000, 2000, 4000, 8000],
      "onlyRetrySafeOrProvenNotApplied": True, "sameAlertEpisodeAndLogicalOperation": True})
fixed("41.6:9", "monitor.slo", {"readApiP95Ms": 500, "uiMutationAcceptP95Ms": 1000,
      "sseP95Ms": 500, "generationSseP95Ms": 250, "healthP95Ms": 200,
      "readinessP95Ms": 2000, "dashboardFirstRenderP95Ms": 2000,
      "windowMs": 300000, "minimumSamples": 20, "insufficientSamples": "NO_SLO_VERDICT",
      "providerLatencyExcludedAndDisplayedSeparately": True}, refs=("31",))
fixed("41.7:1", "log.level", "info", "enum")
fixed("41.7:2", "log.debugRetention", {"ageMs": 7*DAY, "retainLiveEvidenceReferences": True})
fixed("41.7:3", "audit.retention", "UNTIL_VERIFIED_ARCHIVE_AND_NO_LIVE_REFERENCES", "enum")
fixed("41.7:4", "audit.archive", {"root": "/var/lib/kajovocml-ng/audit",
      "segmentBytes": 64*MIB, "verifyChainBeforePrune": True, "archiveNotBackup": True})
fixed("41.7:5", "log.live", {"streamsPerOwner": 4, "eventBytes": 65536,
      "bufferBytes": 4*MIB, "eventsPerSecond": 200,
      "overflow": "GAP_MARKER_AND_DURABLE_CURSOR_NO_AUDIT_DROP"})
fixed("41.7:6", "log.export", {"bytes": GIB, "chunkBytes": 65536,
      "overflow": "PAGINATED_SUCCESSOR_NOT_SILENT_TRUNCATION", "artifactRetentionMs": 7*DAY})
fixed("41.7:7", "log.render", {"format": "ESCAPED_TEXT_OR_JSON", "executeHtml": False,
      "summaryBytes": 65536, "fullValue": "AUTHENTICATED_LAZY_DETAIL", "canonicalPayloadUnchanged": True})
fixed("41.7:8", "log.presentation", "STRUCTURED_SUMMARY_WITH_RAW_DETAIL", "enum")

fixed("41.8:1", "runtime.queue", {"normalCapacity": 1024, "recoveryCapacity": 64,
      "pollMs": 1000, "claimBatch": 16, "recoveryBorrowable": False})
fixed("41.8:2", "runtime.lease", {"durationMs": 30000, "heartbeatMs": 5000,
      "minDispatchWindowMs": 10000, "expiredResurrection": False})
fixed("41.8:3", "runtime.identity", {"generation": "DB_MONOTONIC_BIGINT_NEVER_RECYCLE",
      "instanceId": "UUID", "restoreNewIncarnation": True})
fixed("41.8:4", "runtime.socket", {"profile": "EXACT_50_7_50_8_SERVICE_MANIFEST",
      "mode": "0660", "parentOwner": "root", "parentCallerWritable": False,
      "Accept": False, "RemoveOnStop": True, "creator": "SYSTEMD_SOCKET_UNIT_ONLY",
      "appBindUnlinkChmodChown": False}, refs=("50.7", "50.8"))
fixed("41.8:5", "runtime.peer", {"required": ["InvocationID", "bootId", "startTicks", "cgroup",
      "uid", "gid", "runtimeGeneration", "deploymentEpoch", "incarnation"],
      "pidAloneSufficient": False, "revalidateEveryAdmission": True})
fixed("41.8:6", "runtime.ipc", {"backlog": 128, "connectionsPerInstance": 8,
      "frameBytes": MIB, "unaryBytes": 16*MIB, "chunkBytes": 65536,
      "inFlightPerConnection": 32, "streamsPerConnection": 16,
      "unacknowledgedBytes": MIB, "streamBytesPerCall": 256*MIB,
      "logBytesPerSecond": MIB,
      "overLimit": "TYPED_BACKPRESSURE_NO_UNBOUNDED_BUFFER"}, refs=("50.19",))
fixed("41.8:7", "runtime.namespace", {"releaseReadOnly": True,
      "private": ["user", "mount", "network", "ipc", "uts", "pid", "cgroup"],
      "writable": ["/tmp", "/run", "/work"], "hostProcSysRunVisible": False,
      "profile": "PINNED_RELEASE_NAMESPACE_DIGEST"}, refs=("50.12", "50.13", "50.14"))
fixed("41.8:8", "runtime.security", {"capabilities": [], "NoNewPrivileges": True,
      "syscallArchitectures": "native", "seccomp": "PINNED_NODE24_ALLOWLIST_DIGEST",
      "unknownSyscall": "BLOCK_COMPATIBILITY", "jitCompatible": True,
      "disableSeccompFallback": False}, refs=("50.15",))
fixed("41.8:9", "runtime.environment", {"values": {"LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
      "TZ": "UTC", "NODE_ENV": "production", "HOME": "/work/home", "TMPDIR": "/tmp",
      "PATH": "/runtime/bin", "UV_USE_IO_URING": "0", "KCML_CONTEXT_FD": "3"},
      "fds": [0, 1, 2, 3], "closeAllOthers": True, "capabilityCloexecBeforeGeneratedImport": True}, refs=("50.16",))
fixed("41.8:10", "runtime.spawn", {"onlyExactReleaseAlias": True, "shell": False,
      "maxChildren": 4, "maxThreadsAndProcesses": 128, "childDeadlineMs": 300000,
      "inheritCapabilityFd": False, "subreaper": True, "killMode": "control-group",
      "requireEmptyCgroupBeforeClosure": True}, refs=("50.17",))
fixed("41.8:11", "runtime.resources", {"cpuWeight": 100, "cpuQuotaPercent": 200,
      "memoryHighBytes": 768*MIB, "memoryMaxBytes": GIB, "memorySwapMaxBytes": 0,
      "tasksMax": 128, "fdMax": 4096, "fileBytes": 256*MIB, "coreBytes": 0,
      "tmpfsBytes": 256*MIB, "tmpfsInodes": 65536, "ioWeight": 100,
      "hostStorageBytes": GIB, "startupMs": 120000, "runtimeMaxMs": DAY,
      "zeroUnlimitedAllowed": False, "zeroMeansDisabledFields": ["memorySwapMaxBytes", "coreBytes"],
      "renewRuntime": "DRAIN_NEW_GENERATION_NOT_EXTEND_OLD_IDENTITY"}, refs=("50.18",))
fixed("41.8:12", "runtime.shutdown", {"drainMs": 30000, "sigtermWaitMs": 10000,
      "sigkillVerifyMs": 5000, "incomplete": "MANUAL_REVIEW_KEEP_CLAIMS"})
fixed("41.8:13", "runtime.stateQuota", {"keys": 100000, "valueBytes": MIB,
      "totalBytes": 256*MIB, "reserveBeforeCas": True, "tombstonesUntilReplaySafe": True})
fixed("41.8:14", "runtime.temporaryIdentity", {"previewTicketMs": 60000,
      "stateHandleMs": 900000, "temporaryBindingMs": 900000,
      "expiryNeverChangesExternalOutcome": True, "exactEpochScope": True})
fixed("41.8:15", "runtime.credential", {"generation": "MONOTONIC_DB_COUNTER",
      "requireRestartInvocationAndFingerprintAck": True, "verificationDeadlineMs": 120000,
      "failure": "BLOCK_NEW_ADMISSION_KEEP_EXISTING_EFFECT_EVIDENCE"})
fixed("41.8:16", "runtime.evidenceRetention", {"unreferencedClosedMs": 90*DAY,
      "retainUntilVerifiedArchiveAndNoLiveReferences": True})
fixed("41.8:17", "runtime.releaseRetention", {"keep": ["current", "frozenRollback", "allReferenced"],
      "unreferencedGraceMs": 7*DAY, "timeOnlyDeletion": False})
fixed("41.9:1", "deployment.releaseRoot", "/opt/kajovocml-ng/releases", "path")
fixed("41.9:2", "deployment.dataRoot", "/var/lib/kajovocml-ng/data", "path")
fixed("41.9:3", "deployment.backup", {"scheduleUtc": "02:00", "dailyKeep": 30,
      "weeklyKeep": 12, "beforeEveryMigration": True, "retainReferencedRollback": True,
      "retainLastVerifiedRestorable": True, "restoreVerificationDays": 7,
      "encryptionRequired": True, "neverDeleteOnlyRestorableBackup": True})
fixed("41.9:4", "deployment.readiness", {"samples": 3, "intervalMs": 5000,
      "exactReleaseEpoch": True, "consecutive": True})
fixed("41.9:5", "deployment.startupDeadlineMs", 300000, "ms")
fixed("41.9:6", "deployment.services", {"source": "EXACT_SECTION_27_1_AND_SIGNED_RELEASE_MANIFEST",
      "unknownService": "BLOCK", "oneRepositoryHomeEach": True})
fixed("41.9:7", "tls.renewBeforeExpiryMs", 30*DAY, "ms")
fixed("41.9:8", "deployment.maintenance", {"automaticStartUtc": "02:00",
      "automaticEndUtc": "04:00", "ownerExplicitStartAllowed": True,
      "safetyGuardsOutsideWindowUnchanged": True})
fixed("41.9:9", "deployment.acceptance", {"everyRelease": True, "scheduledUtc": "03:00",
      "expectedShaRequired": True, "mutatingInfrastructureChecks": "EXPLICIT_PARENT_OPERATION",
      "missingDependency": "BLOCK_NOT_SKIP"})
