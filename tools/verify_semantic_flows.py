"""Forenzní automat sémantických kontraktů a procesních přechodů.

Kontrola skládá čtyři nezávislé důkazy:
1. fyzické/runtime JSON Schema vazby z verify_contract_links.py,
2. inventář všech explicitních a dynamických provider contract sites,
3. úplný manifest podporovaných procesních rodin,
4. skutečný průchod regresních scénářů přes producenty, validátory,
   orchestration/recovery vrstvy a spotřebitele.

Automat nesmí navazovat placené ani jiné živé provider operace. Testovací
scénáře používají lokální/mocked transporty; živá acceptance není součástí
tohoto důkazu.
"""
from __future__ import annotations

import argparse
import ast
import importlib.metadata
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from semantic_runtime_inventory import RUNTIME_VARIANTS, validate_runtime_inventory


@dataclass(frozen=True)
class ProcessFamily:
    id: str
    description: str
    tests: tuple[str, ...]


PROCESS_FAMILIES = (
    ProcessFamily(
        "contract-boundaries",
        "Strict JSON, fyzické masky, kanonické hashe a blocked/ready obálky.",
        (
            "tests/test_contracts.py",
            "tests/test_change_contract_boundaries.py",
            "tests/test_canonical_contract_links.py",
            "tests/test_clarification_wire_contracts.py",
        ),
    ),
    ProcessFamily(
        "source-and-preparation",
        "Zmrazení SourcePacku, GENERATE/MODIFY requirements, plan, SPINE, DETAIL a quality gate.",
        (
            "tests/test_attachments.py",
            "tests/test_audit2_preparation.py",
            "tests/test_preparation_boundaries.py",
            "tests/test_requirements.py",
            "tests/test_context_compiler.py",
        ),
    ),
    ProcessFamily(
        "generate-modify-live",
        "GENERATE/MODIFY LIVE od přípravy přes FILE_CONTENT_V1 po staging a publish hranici.",
        (
            "tests/test_workflows.py",
            "tests/test_delivery_pipeline.py",
            "tests/test_modify_batch.py",
        ),
    ),
    ProcessFamily(
        "batch",
        "BATCH příprava, dependency waves, submit, import, retry, recovery a publication locks.",
        (
            "tests/test_generate_batch.py",
            "tests/test_generate_batch_waves.py",
            "tests/test_batch_completion.py",
            "tests/test_batch_recovery_boundaries.py",
            "tests/test_batch_publication_locks.py",
        ),
    ),
    ProcessFamily(
        "recovery-and-lifecycle",
        "ResponseJournal, restart, continue, cancellation, submission_unknown a terminální lifecycle.",
        (
            "tests/test_response_journal.py",
            "tests/test_run_lifecycle.py",
            "tests/test_run_cancellation.py",
            "tests/test_desktop_preparation_recovery.py",
            "tests/test_history_pending_live.py",
        ),
    ),
    ProcessFamily(
        "work-order-and-storage",
        "WorkOrder, provider operation identity, SQLite vazby, Run Bundle a filesystem hranice.",
        (
            "tests/test_orchestration_repository.py",
            "tests/test_verification_profiles.py",
            "tests/test_filesystem_boundaries.py",
            "tests/test_run_bundle.py",
            "tests/test_run_contracts.py",
        ),
    ),
    ProcessFamily(
        "qa-and-qfile",
        "QA_ANSWER_V2, QFILE_PLAN_V1, FILE_CONTENT_V1 a request pravidla.",
        (
            "tests/test_clarification_wire_contracts.py",
            "tests/test_qa_evidence_links.py",
            "tests/test_qfile_semantics.py",
            "tests/test_qa_qfile_runtime_semantics.py",
            "tests/test_process_audit_regressions.py",
            "tests/test_request_rules.py",
        ),
    ),
    ProcessFamily(
        "provider-transport",
        "Responses transport, retry klasifikace a zákaz dvojího submitu.",
        (
            "tests/test_openai_transport.py",
            "tests/test_openai_retry_semantics.py",
        ),
    ),
    ProcessFamily(
        "cascade",
        "Kaskáda včetně strict masky, větvení, repair, resume, lineage a binárních výstupů.",
        (
            "tests/test_cascade.py",
            "tests/test_cascade_v2.py",
            "tests/test_cascade_production.py",
            "tests/test_cascade_unknown_submission.py",
            "tests/test_cascade_audit2.py",
            "tests/test_cascade_worker_adapter.py",
        ),
    ),
    ProcessFamily(
        "photo",
        "PHOTO_PLAN_V1, image batch, identity výsledků, recovery a zákaz opakovaného submitu.",
        (
            "tests/test_photo_studio.py",
            "tests/test_batch_recovery_boundaries.py",
        ),
    ),
    ProcessFamily(
        "comic",
        "Komiksové strict kontrakty, persistence, batch, retry, resume a obrazové reference.",
        (
            "tests/test_comic_domain.py",
            "tests/test_comic_recovery.py",
            "tests/test_live_acceptance.py",
        ),
    ),
)


# Explicitní wire/provider kontrakty, které mají v podporovaném runtime pevné jméno.
# Dynamické kontrakty kaskády a komiksu se auditují zvlášť přes AST site inventory.
REQUIRED_NAMED_PROVIDER_CONTRACTS = {
    "A0R_REQUIREMENTS_V2",
    "A1_PLAN_V2",
    "A2_SPINE_V2",
    "A2_FILE_SPEC_V1",
    "A2Q_QUALITY_GATE_V3",
    "B0R_REQUIREMENTS_V2",
    "B1_PLAN_V2",
    "B2_SPINE_V2",
    "B2_FILE_SPEC_V1",
    "B2Q_QUALITY_GATE_V3",
    "FILE_CONTENT_V1",
    "QA_ANSWER_V2",
    "QFILE_PLAN_V1",
    "TEXT_RESPONSE",
    "PHOTO_PLAN_V1",
}

# Všechny legitimní runtime body, kde jméno provider kontraktu vzniká dynamicky.
# Přidání nového dynamického site bez explicitního vlastnictví audit zablokuje.
ALLOWED_DYNAMIC_PROVIDER_SITES = {
    "kajovo/core/contracts.py": "legacy-file-contract-factory",
    "kajovo/core/structured_output.py": "structured-output-factory",
    "kajovo/core/requirements.py": "requirements-schema-factory",
    "kajovo/core/orchestration/preparation.py": "preparation-quality-gate-factory",
    "kajovo/core/cascade_pipeline.py": "cascade",
    "kajovo/core/cascade_production.py": "cascade",
    "kajovo/core/comic_service.py": "comic",
}


def _all_tests() -> list[str]:
    values: list[str] = []
    seen: set[str] = set()
    for family in PROCESS_FAMILIES:
        for item in family.tests:
            if item not in seen:
                values.append(item)
                seen.add(item)
    return values


def _git_sha() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()


def _run(command: list[str], *, env: dict[str, str] | None = None) -> dict:
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    completed = subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=merged_env,
        check=False,
    )
    return {
        "command": command,
        "returncode": completed.returncode,
        "output": completed.stdout,
    }


def _response_format_sites() -> dict:
    explicit: list[dict] = []
    dynamic: list[dict] = []
    errors: list[dict] = []
    for path in sorted((ROOT / "kajovo").rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        except (SyntaxError, UnicodeError) as exc:
            errors.append({"path": rel, "error": f"{type(exc).__name__}: {exc}"})
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = (
                node.func.attr
                if isinstance(node.func, ast.Attribute)
                else node.func.id
                if isinstance(node.func, ast.Name)
                else ""
            )
            if name != "response_format":
                continue
            first = node.args[0] if node.args else None
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                explicit.append(
                    {"path": rel, "line": node.lineno, "contract": first.value}
                )
            else:
                dynamic.append(
                    {
                        "path": rel,
                        "line": node.lineno,
                        "owner": ALLOWED_DYNAMIC_PROVIDER_SITES.get(rel),
                        "expression": ast.unparse(first) if first is not None else "",
                    }
                )
                if rel not in ALLOWED_DYNAMIC_PROVIDER_SITES:
                    errors.append(
                        {
                            "path": rel,
                            "line": node.lineno,
                            "error": "Nový dynamický response_format site nemá explicitního vlastníka.",
                        }
                    )
    return {"explicit": explicit, "dynamic": dynamic, "errors": errors}


def _runtime_named_contracts() -> set[str]:
    from kajovo.core.orchestration import preparation
    from kajovo.core.structured_output import (
        file_content_format,
        qa_answer_format,
        qfile_plan_format,
        text_format,
    )

    names = {
        value["format"]["name"]
        for value in preparation.FORMATS.values()
    }
    for factory in (
        file_content_format,
        qa_answer_format,
        qfile_plan_format,
        text_format,
    ):
        names.add(factory()["format"]["name"])

    # PHOTO_PLAN_V1 vzniká přes response_format uvnitř professionalize_payload;
    # jeho jméno proto získáme z AST inventáře, nikoli modelově závislým voláním.
    names.update(
        row["contract"]
        for row in _response_format_sites()["explicit"]
        if row["contract"] == "PHOTO_PLAN_V1"
    )
    return names


def _validate_manifest() -> list[dict]:
    errors: list[dict] = []
    family_ids = [family.id for family in PROCESS_FAMILIES]
    if len(family_ids) != len(set(family_ids)):
        errors.append({"error": "Process family id není unikátní."})

    for family in PROCESS_FAMILIES:
        if not family.tests:
            errors.append({"family": family.id, "error": "Rodina nemá důkazové testy."})
        for test in family.tests:
            if not (ROOT / test).is_file():
                errors.append(
                    {"family": family.id, "test": test, "error": "Důkazový test neexistuje."}
                )

    actual = _runtime_named_contracts()
    missing = sorted(REQUIRED_NAMED_PROVIDER_CONTRACTS - actual)
    unexpected = sorted(actual - REQUIRED_NAMED_PROVIDER_CONTRACTS)
    if missing:
        errors.append(
            {"scope": "named_provider_contracts", "error": "Chybí runtime kontrakty.", "items": missing}
        )
    if unexpected:
        errors.append(
            {
                "scope": "named_provider_contracts",
                "error": "Přibyl pojmenovaný provider kontrakt bez doplnění forenzního manifestu.",
                "items": unexpected,
            }
        )

    site_inventory = _response_format_sites()
    errors.extend(site_inventory["errors"])
    owners = {row["owner"] for row in site_inventory["dynamic"] if row.get("owner")}
    for required_owner in {"cascade", "comic"}:
        if required_owner not in owners:
            errors.append(
                {
                    "scope": "dynamic_provider_contracts",
                    "error": f"Chybí dynamická kontraktní rodina {required_owner}.",
                }
            )
    return errors


def _contract_link_pass(tempdir: Path) -> dict:
    output = tempdir / "contract-links.json"
    result = _run(
        [
            sys.executable,
            "tools/verify_contract_links.py",
            "--output",
            str(output),
        ]
    )
    payload = None
    if output.is_file():
        payload = json.loads(output.read_text(encoding="utf-8"))
    return {"run": result, "report": payload}


def _pytest_pass(tests: Iterable[str]) -> tuple[dict, dict]:
    values = list(tests)
    common_env = {
        "QT_QPA_PLATFORM": os.environ.get("QT_QPA_PLATFORM", "offscreen"),
        "KAJOVO_LIVE_ACCEPTANCE": "0",
    }
    collect = _run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", *values],
        env=common_env,
    )
    execute = _run(
        [sys.executable, "-m", "pytest", "-q", "--tb=short", *values],
        env=common_env,
    )
    return collect, execute


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--skip-pytest",
        action="store_true",
        help="Pro diagnostiku spusť pouze inventuru a contract-link pass.",
    )
    args = parser.parse_args()

    errors = _validate_manifest()
    runtime_errors, runtime_unverified, runtime_inventory = validate_runtime_inventory()
    errors.extend(runtime_errors)
    sites = _response_format_sites()
    named_contracts = sorted(_runtime_named_contracts())
    tests = _all_tests()

    with tempfile.TemporaryDirectory(prefix="kajovo-semantic-audit-") as raw:
        contract_links = _contract_link_pass(Path(raw))

    if contract_links["run"]["returncode"] != 0:
        errors.append(
            {
                "scope": "contract_links",
                "error": "verify_contract_links.py neprošel.",
                "returncode": contract_links["run"]["returncode"],
            }
        )
    report_from_links = contract_links.get("report")
    if isinstance(report_from_links, dict) and report_from_links.get("errors"):
        errors.append(
            {
                "scope": "contract_links",
                "error": "Contract-link report obsahuje chyby.",
                "items": report_from_links["errors"],
            }
        )

    collect = {"returncode": 0, "output": "pytest skipped"}
    execute = {"returncode": 0, "output": "pytest skipped"}
    runtime_collect = {"returncode": 0, "output": "pytest skipped"}
    runtime_execute = {"returncode": 0, "output": "pytest skipped"}
    if not args.skip_pytest:
        runtime_collect, runtime_execute = _pytest_pass(runtime_inventory["test_nodes"])
        if runtime_collect["returncode"] != 0:
            errors.append(
                {
                    "scope": "runtime_evidence_collection",
                    "error": "Cílené runtime důkazy nelze kompletně vybrat.",
                    "returncode": runtime_collect["returncode"],
                }
            )
        if runtime_execute["returncode"] != 0:
            errors.append(
                {
                    "scope": "runtime_evidence_execution",
                    "error": "Jeden nebo více cílených runtime důkazů selhalo.",
                    "returncode": runtime_execute["returncode"],
                }
            )
        collect, execute = _pytest_pass(tests)
        if collect["returncode"] != 0:
            errors.append(
                {
                    "scope": "pytest_collection",
                    "error": "Sémantické důkazové scénáře nelze kompletně vybrat.",
                    "returncode": collect["returncode"],
                }
            )
        if execute["returncode"] != 0:
            errors.append(
                {
                    "scope": "pytest_execution",
                    "error": "Jeden nebo více sémantických scénářů selhalo.",
                    "returncode": execute["returncode"],
                }
            )

    report = {
        "version": 1,
        "git_sha": _git_sha(),
        "python": sys.version.split()[0],
        "openai_sdk": importlib.metadata.version("openai"),
        "named_provider_contracts": named_contracts,
        "required_named_provider_contracts": sorted(REQUIRED_NAMED_PROVIDER_CONTRACTS),
        "provider_contract_sites": sites,
        "runtime_inventory": {
            "variants": [
                {
                    "id": variant.id,
                    "modes": list(variant.modes),
                    "runtime_paths": list(variant.runtime_paths),
                    "steps": [
                        {
                            "name": step.name,
                            "implementation": step.implementation,
                            "transition": step.transition,
                            "validator": step.validator,
                            "positive_test": step.positive_test,
                            "negative_test": step.negative_test,
                            "unverified_reason": step.unverified_reason,
                        }
                        for step in variant.steps
                    ],
                }
                for variant in RUNTIME_VARIANTS
            ],
            "discovered": runtime_inventory,
            "unverified": runtime_unverified,
        },
        "process_families": [
            {
                "id": family.id,
                "description": family.description,
                "tests": list(family.tests),
            }
            for family in PROCESS_FAMILIES
        ],
        "unique_test_modules": tests,
        "contract_links": {
            "returncode": contract_links["run"]["returncode"],
            "report_errors": (
                report_from_links.get("errors", [])
                if isinstance(report_from_links, dict)
                else [{"error": "Contract-link report nebyl vytvořen."}]
            ),
        },
        "runtime_pytest_collect": {
            "returncode": runtime_collect["returncode"],
            "output": runtime_collect["output"],
        },
        "runtime_pytest_execute": {
            "returncode": runtime_execute["returncode"],
            "output": runtime_execute["output"],
        },
        "pytest_collect": {
            "returncode": collect["returncode"],
            "output": collect["output"],
        },
        "pytest_execute": {
            "returncode": execute["returncode"],
            "output": execute["output"],
        },
        "limitations": [
            "Audit používá lokální/mocked provider scénáře a neprovádí placené živé OpenAI volání.",
            "Provider call-sites a dispatchované režimy se objevují nezávisle z AST; nový runtime bod bez vlastníka audit zablokuje.",
            "Položky runtime_inventory.unverified jsou explicitně neuzavřené varianty a nesmějí být interpretovány jako kompletně ověřené.",
        ],
        "errors": errors,
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    summary = {
        "git_sha": report["git_sha"],
        "process_families": len(PROCESS_FAMILIES),
        "test_modules": len(tests),
        "named_provider_contracts": len(named_contracts),
        "dynamic_provider_sites": len(sites["dynamic"]),
        "runtime_variants": len(RUNTIME_VARIANTS),
        "runtime_provider_sites": len(runtime_inventory["provider_sites"]),
        "runtime_unverified": runtime_unverified,
        "runtime_pytest_collect": runtime_collect["returncode"],
        "runtime_pytest_execute": runtime_execute["returncode"],
        "contract_links": contract_links["run"]["returncode"],
        "pytest_collect": collect["returncode"],
        "pytest_execute": execute["returncode"],
        "errors": errors,
    }
    print("SEMANTIC_FLOWS", json.dumps(summary, ensure_ascii=False))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
