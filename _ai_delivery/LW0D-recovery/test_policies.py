"""Návrhové policy regrese; nepoužívají provozní konfiguraci ani síť."""

from copy import deepcopy
from pathlib import Path

import pytest
from jsonschema import ValidationError

from policy_definitions import DEFINITIONS
from verify_policies import compile_catalog, digest, evaluate, validate_catalog


SOURCE = (Path(__file__).parent / "source/SSOT.original.md").read_text(encoding="utf-8")


def test_complete_deterministic_catalog():
    first = compile_catalog(SOURCE)
    assert first == compile_catalog(SOURCE)
    assert first["sourceTopics"] == first["systemManagedTopics"] + len(first["ownerInputTopics"])
    for record in first["policies"]:
        inputs = {key: spec["minimum"] for key, spec in record["inputSchema"]["properties"].items()}
        assert evaluate(record, inputs) == evaluate(record, inputs)
        with pytest.raises(ValidationError):
            evaluate(record, {**inputs, "undeclaredEnvOverride": True})


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "owner_input", "missing_ref", "unknown_algorithm"])
def test_invalid_catalog_blocks(mutation):
    definitions = deepcopy(DEFINITIONS)
    if mutation == "missing":
        definitions.pop(0)
    elif mutation == "duplicate":
        definitions.append(deepcopy(definitions[0]))
    elif mutation == "owner_input":
        definitions[0]["topic"] = "41.3:1"
    elif mutation == "missing_ref":
        definitions[0]["refs"] = ["999.99"]
    else:
        definitions[0]["algorithm"] = "ASSUME_DEFAULT"
        definitions[0].update(minimum=1, maximum=4)
    with pytest.raises(ValueError):
        compile_catalog(SOURCE, definitions)


@pytest.mark.parametrize("cpu,expected", [(1, 1), (2, 1), (7, 3), (8, 4), (4096, 4)])
def test_cpu_derivation(cpu, expected):
    record = next(r for r in compile_catalog(SOURCE)["policies"] if r["stablePolicyKey"] == "generation.workerConcurrency")
    assert evaluate(record, {"availableCpuCores": cpu}) == expected


@pytest.mark.parametrize("inputs", [{}, {"availableCpuCores": 0}, {"availableCpuCores": True},
                                  {"availableCpuCores": 1.5}, {"availableCpuCores": 5000}])
def test_cpu_invalid_inputs_have_no_default(inputs):
    record = next(r for r in compile_catalog(SOURCE)["policies"] if r["stablePolicyKey"] == "generation.workerConcurrency")
    with pytest.raises(ValidationError):
        evaluate(record, inputs)


def test_source_topic_drift_blocks():
    with pytest.raises(ValueError, match="katalog"):
        compile_catalog(SOURCE.replace("- trusted proxy CIDRs,", "- trusted proxy CIDRs,\n- nová položka,"))


@pytest.mark.parametrize("key,field,value", [("runtime.lease", "heartbeatMs", 30000),
    ("runtime.resources", "memoryHighBytes", 2**40), ("runtime.resources", "cpuQuotaPercent", 0),
    ("runtime.ipc", "inFlightPerConnection", 33),
    ("generation.budget", "totalTokens", 1)])
def test_relation_violation_blocks_even_with_valid_digests(key, field, value):
    definitions = deepcopy(DEFINITIONS)
    item = next(d for d in definitions if d["key"] == key)
    item["value"][field] = value
    with pytest.raises(ValueError):
        compile_catalog(SOURCE, definitions)


def test_corrupted_digest_blocks():
    catalog = compile_catalog(SOURCE)
    catalog["policies"][0]["ownerEditable"] = True
    with pytest.raises(ValueError, match="Digest"):
        validate_catalog(catalog)


def test_owner_editable_blocks_even_after_rehash():
    catalog = compile_catalog(SOURCE)
    record = catalog["policies"][0]
    record["ownerEditable"] = True
    record["canonicalDigest"] = digest({k: v for k, v in record.items() if k != "canonicalDigest"})
    with pytest.raises(ValueError, match="editable"):
        validate_catalog(catalog)
