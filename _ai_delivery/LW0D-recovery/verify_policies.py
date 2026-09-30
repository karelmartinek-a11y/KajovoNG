"""Deterministická kompilace policy návrhu; bez API a bez čtení credentials."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re

from jsonschema import Draft202012Validator

from policy_definitions import DEFINITIONS
from verify_recovery import write_once


ROOT = Path(__file__).resolve().parent
OWNERS = {"41.1": "deployment", "41.2": "owner-identity", "41.3": "openai-runtime",
          "41.4": "generation", "41.5": "browser", "41.6": "monitoring",
          "41.7": "observability", "41.8": "runtime", "41.9": "deployment"}
EXPECTED_COUNTS = {"41.1": 10, "41.2": 13, "41.3": 25, "41.4": 20,
                   "41.5": 33, "41.6": 9, "41.7": 8, "41.8": 17, "41.9": 9}
OWNER_INPUTS = {*(f"41.3:{n}" for n in range(1, 6)), *(f"41.5:{n}" for n in range(1, 11))}


def digest(value) -> str:
    # Tento návrhový formát nemá floats ani ne-ASCII klíče; nejde o obecný JCS codec.
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def source_topics(source: str) -> dict[str, str]:
    topics = {}
    section = None
    ordinal = 0
    for line in source.splitlines():
        heading = re.match(r"^### (41\.\d+) ", line)
        if heading:
            section, ordinal = heading[1], 0
        elif line.startswith("## "):
            section = None
        elif section in EXPECTED_COUNTS and line.startswith("- "):
            ordinal += 1
            topics[f"{section}:{ordinal}"] = line[2:]
    counts = {section: sum(t.startswith(section + ":") for t in topics) for section in EXPECTED_COUNTS}
    if counts != EXPECTED_COUNTS:
        raise ValueError("Změnil se zdrojový katalog §41; mapping vyžaduje přezkum.")
    return topics


def evaluate(record: dict, inputs: dict):
    Draft202012Validator(record["inputSchema"]).validate(inputs)
    algorithm = record["derivation"]["algorithm"]
    if algorithm == "CONSTANT_V1":
        value = deepcopy(record["derivation"]["value"])
    elif algorithm == "CPU_HALF_CLAMP_1_4_V1":
        value = max(1, min(4, inputs["availableCpuCores"] // 2))
    elif algorithm == "BROWSER_SLOTS_V1":
        # Polovina persistované allocatable RAM zůstává mimo browser hosty.
        value = min(4, max(1, inputs["availableCpuCores"] // 2), inputs["allocatableMemoryMiB"] // 4096)
    else:
        raise ValueError(f"Neznámý algoritmus {algorithm}")
    Draft202012Validator(record["valueSchema"]).validate(value)
    return value


def compile_catalog(source: str, definitions: list[dict] | None = None) -> dict:
    definitions = DEFINITIONS if definitions is None else definitions
    topics = source_topics(source)
    known_sections = set(re.findall(r"^#{2,4} (\d+(?:\.\d+)*)[ .]", source, re.M))
    records = []
    seen = set()
    covered = set()
    for item in definitions:
        key, topic = item["key"], item["topic"]
        if key in seen:
            raise ValueError(f"Duplicitní policy key {key}")
        if topic not in topics or topic in OWNER_INPUTS:
            raise ValueError(f"Chybný SYSTEM_MANAGED topic {topic}")
        section = topic.split(":")[0]
        refs = [section, "41.10", *item["refs"]]
        if any(ref not in known_sections for ref in refs):
            raise ValueError(f"Neexistující source ref u {key}: {refs}")
        inputs = item.get("inputs", {})
        input_schema = {"type": "object", "properties": inputs, "required": sorted(inputs),
                        "additionalProperties": False}
        constant = item["algorithm"] == "CONSTANT_V1"
        value_schema = ({"const": item["value"]} if constant else
                        {"type": "integer", "minimum": item["minimum"], "maximum": item["maximum"]})
        derivation = {"algorithm": item["algorithm"], "version": 1}
        if constant:
            derivation["value"] = item["value"]
        record = {
            "stablePolicyKey": key, "policyRevision": 1, "classification": "SYSTEM_MANAGED",
            "ownerModule": OWNERS[section], "ownerEditable": False,
            "sourceTopic": topic, "sourceText": topics[topic], "sourceTextDigest": digest(topics[topic]),
            "authoritySourceRefs": refs, "inputSchema": input_schema,
            "authoritativeInputRefs": {name: "persistedHostCapabilitySnapshot." + name for name in inputs},
            "derivation": derivation, "derivationDigest": digest(derivation), "unit": item["unit"],
            "valueSchema": value_schema,
            "default": {"present": constant, "value": item.get("value")},
            "bounds": {"kind": "EXACT_CONSTANT" if constant else "INCLUSIVE_INTEGER",
                       "minimum": item.get("minimum"), "maximum": item.get("maximum")},
            "constraints": ["PINNED_IMMUTABLE_REVISION_AND_INPUT_DIGEST",
                            "NO_SILENT_CLAMP_OF_INVALID_INPUT", "NO_LATENCY_AUTOTUNING",
                            "IN_FLIGHT_RUN_RETAINS_PINNED_EFFECTIVE_VERSION"],
            "updateTrigger": "POLICY_REVISION_OR_PERSISTED_INPUT_DIGEST_CHANGE_BEFORE_NEW_ADMISSION",
            "cadence": "EVENT_DRIVEN_NO_BACKGROUND_VALUE_MUTATION",
            "effectiveStateVersion": "DB_BIGINT_MONOTONIC_CAS",
            "fallback": "NONE_ON_INVALID_OR_MISSING_REQUIRED_INPUT",
            "dependencyFailure": "BLOCK_AFFECTED_OPERATION_KEEP_PRIOR_PINNED_RUNS",
            "auditEvent": "configuration.policy.effective", "requirementIds": ["E-005"],
            "testIds": ["policy:" + key + ":deterministic", "policy:" + key + ":invalid-input"],
            "acceptanceGateIds": ["ARCH_NORMATIVE_AMBIGUITY_CLOSED", "ARCH_CONTRACT_PACK_DERIVABLE"],
        }
        Draft202012Validator.check_schema(input_schema)
        Draft202012Validator.check_schema(value_schema)
        record["canonicalDigest"] = digest(record)
        records.append(record)
        seen.add(key)
        covered.add(topic)
    missing = set(topics) - OWNER_INPUTS - covered
    if missing:
        raise ValueError(f"Policy coverage neúplná: {sorted(missing)}")
    catalog = {"formatVersion": 1, "status": "DESIGN_ONLY_NOT_RUNTIME_PASS",
               "sourceDigest": hashlib.sha256(source.encode("utf-8")).hexdigest(),
               "sourceTopics": len(topics), "systemManagedTopics": len(covered),
               "ownerInputTopics": sorted(OWNER_INPUTS), "policies": sorted(records, key=lambda r: r["stablePolicyKey"])}
    validate_catalog(catalog)
    catalog["catalogDigest"] = digest(catalog)
    return catalog


def validate_catalog(catalog: dict) -> None:
    records = catalog["policies"]
    if len({r["stablePolicyKey"] for r in records}) != len(records):
        raise ValueError("Duplicitní policy keys.")
    for record in records:
        body = {k: v for k, v in record.items() if k != "canonicalDigest"}
        if digest(body) != record["canonicalDigest"]:
            raise ValueError("Nesouhlasí canonicalDigest policy.")
        if digest(record["derivation"]) != record["derivationDigest"]:
            raise ValueError("Nesouhlasí derivationDigest policy.")
        if record["ownerEditable"] or record["classification"] != "SYSTEM_MANAGED":
            raise ValueError("Technická policy nesmí být OWNER editable.")
        if record["derivation"]["algorithm"] not in {"CONSTANT_V1", "CPU_HALF_CLAMP_1_4_V1", "BROWSER_SLOTS_V1"}:
            raise ValueError("Není implementován algoritmus policy.")
    values = {r["stablePolicyKey"]: evaluate(r, {}) for r in records if not r["inputSchema"]["properties"]}
    for key in ["runtime.lease", "browser.control", "browser.bridge", "generation.phase"]:
        value = values[key]
        lease = value.get("durationMs", value.get("leaseMs"))
        if 3 * value["heartbeatMs"] > lease:
            raise ValueError("Heartbeat překračuje třetinu lease.")
        if value.get("minDispatchWindowMs", 0) >= lease:
            raise ValueError("Dispatch window nemá místo uvnitř lease.")
    resource = values["runtime.resources"]
    for name, value in resource.items():
        if name.endswith("Bytes") or name in {"cpuWeight", "cpuQuotaPercent", "tasksMax", "fdMax", "ioWeight"}:
            if value == 0 and name not in {"memorySwapMaxBytes", "coreBytes"}:
                raise ValueError(f"Resource limit {name} nesmí být nulový.")
            if type(value) is not int or value < 0:
                raise ValueError(f"Resource limit {name} musí být konečné celé číslo.")
    if not 0 < resource["memoryHighBytes"] < resource["memoryMaxBytes"]:
        raise ValueError("Neplatná MemoryHigh/MemoryMax relace.")
    if resource["memorySwapMaxBytes"] != 0 or resource["coreBytes"] != 0:
        raise ValueError("Swap a core dump musí být vypnuté.")
    ipc = values["runtime.ipc"]
    if not (0 < ipc["chunkBytes"] <= 65536 <= ipc["frameBytes"] <= 1024*1024
            and ipc["frameBytes"] <= ipc["unaryBytes"] <= 16*1024*1024
            and 0 < ipc["streamBytesPerCall"]):
        raise ValueError("Neplatná IPC velikostní relace.")
    if ipc["inFlightPerConnection"] > 32 or ipc["streamsPerConnection"] > 16:
        raise ValueError("IPC překračuje source hard ceiling.")
    if values["ai.createSdkRetries"] != 0:
        raise ValueError("Create retry nesmí vznikat skrytě.")
    for key in ["runtime.queue", "generation.queue"]:
        value = values[key]
        if min(value["normalCapacity"], value["recoveryCapacity"]) <= 0:
            raise ValueError("Chybí vyhrazená recovery kapacita.")
    for key in ["ai.budget", "generation.budget"]:
        value = values[key]
        if value["inputTokens"] + value["outputTokens"] != value["totalTokens"]:
            raise ValueError("Nesouhlasí token budget součet.")
    if "catalogDigest" in catalog and digest({k: v for k, v in catalog.items() if k != "catalogDigest"}) != catalog["catalogDigest"]:
        raise ValueError("Nesouhlasí catalogDigest.")


def main():
    source = (ROOT / "source/SSOT.original.md").read_text(encoding="utf-8")
    catalog = compile_catalog(source)
    # Nová revize nesmí tiše přepsat předchozí odvozený artefakt.
    write_once(ROOT / "policy-catalog.v2.json", catalog)
    print(f"Policy návrh: {len(catalog['policies'])} records; "
          f"{catalog['systemManagedTopics']} SYSTEM_MANAGED topics + {len(OWNER_INPUTS)} OWNER topics.")


if __name__ == "__main__":
    main()
