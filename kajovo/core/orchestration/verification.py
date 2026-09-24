"""Evidence-based verification with safe parsers and an optional container sandbox."""
from __future__ import annotations

import ast
import copy
import hashlib
import os
import platform
import shutil
import subprocess
import tomllib
import xml.etree.ElementTree as ET

import jsonschema
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ..utils import safe_join_under_root, sha256_file
from .contracts import canonical_sha256, parse_json_strict, parse_json_value_strict
from .errors import OrchestrationError


_VERIFICATION_CHECK_V3_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "criterion_id": {"type": "string"},
        "kind": {
            "type": "string",
            "enum": ["deterministic", "model_review", "human"],
        },
        "status": {
            "type": "string",
            "enum": ["passed", "failed", "skipped", "unsupported", "uncertain"],
        },
        "evidence_hashes": {
            "type": "array",
            "items": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        },
        "path": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "detail": {"type": "string"},
    },
    "required": [
        "id",
        "criterion_id",
        "kind",
        "status",
        "evidence_hashes",
        "path",
        "detail",
    ],
    "additionalProperties": False,
}

_RUNNER_V3_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "image_digest": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "platform": {"type": "string"},
        "started_at": {"type": "string", "format": "date-time"},
        "finished_at": {"type": "string", "format": "date-time"},
    },
    "required": ["image_digest", "platform", "started_at", "finished_at"],
    "additionalProperties": False,
}

TECHNICAL_VERIFICATION_REPORT_V3_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "version": {"type": "integer", "enum": [3]},
        "target_id": {"type": "string"},
        "target_hash": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        "profile_id": {"type": "string"},
        "profile_hash": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        "result": {
            "type": "string",
            "enum": ["passed", "failed", "unsupported", "needs_human"],
        },
        "format_result": {
            "type": "string",
            "enum": ["passed", "partial", "failed"],
        },
        "functional_result": {
            "type": "string",
            "enum": ["not_run", "not_applicable", "unsupported", "passed", "failed"],
        },
        "human_result": {
            "type": "string",
            "enum": ["unverified", "accepted", "rejected"],
        },
        "checks": {
            "type": "array",
            "minItems": 1,
            "items": _VERIFICATION_CHECK_V3_SCHEMA,
        },
        "runner": _RUNNER_V3_SCHEMA,
    },
    "required": [
        "version",
        "target_id",
        "target_hash",
        "profile_id",
        "profile_hash",
        "result",
        "format_result",
        "functional_result",
        "human_result",
        "checks",
        "runner",
    ],
    "additionalProperties": False,
}

VERIFICATION_REPORT_V3_SCHEMA: dict[str, Any] = copy.deepcopy(
    TECHNICAL_VERIFICATION_REPORT_V3_SCHEMA
)
VERIFICATION_REPORT_V3_SCHEMA["properties"].update(
    {
        "candidate_mode": {"type": "string", "enum": ["GENERATE", "MODIFY", "QFILE"]},
        "candidate_root": {"type": "string"},
        "candidate_scope": {
            "type": "string",
            "enum": ["approved_source_pack_plus_staged", "staged_outputs"],
        },
    }
)
VERIFICATION_REPORT_V3_SCHEMA["required"].extend(
    ["candidate_mode", "candidate_root", "candidate_scope"]
)


def _validate_verification_report(
    report: dict[str, Any],
    schema: dict[str, Any],
) -> None:
    try:
        jsonschema.Draft202012Validator(
            schema, format_checker=jsonschema.FormatChecker()
        ).validate(report)
    except jsonschema.ValidationError as exc:
        raise OrchestrationError(
            "VERIFY_REPORT_CONTRACT",
            f"Verification report porušuje strict V3 kontrakt: {exc.message}",
        ) from exc


@dataclass(frozen=True)
class VerificationPlan:
    target_id: str
    target_hash: str
    profile_id: str
    profile_hash: str
    commands: tuple[tuple[str, ...], ...]
    required_checks: tuple[str, ...]
    staging_root: str

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "commands": [list(row) for row in self.commands],
            "required_checks": list(self.required_checks),
        }


def _tree_hash(root: str | Path) -> str:
    root_path = Path(root)
    rows: list[dict[str, Any]] = []
    for path in sorted(item for item in root_path.rglob("*") if item.is_file()):
        rows.append({
            "path": path.relative_to(root_path).as_posix(),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "bytes": path.stat().st_size,
        })
    return canonical_sha256(rows)


def plan_checks(
    target: str | Path,
    profile: dict[str, Any],
    *,
    target_id: str = "staged-output",
) -> VerificationPlan:
    root = Path(target).resolve()
    if not root.is_dir():
        raise OrchestrationError("VERIFY_TARGET_MISSING", str(root))
    profile_id = str(profile.get("id") or "")
    commands = profile.get("commands")
    required = profile.get("required_checks")
    if not profile_id or not isinstance(commands, list) or not isinstance(required, list):
        raise OrchestrationError("VERIFY_PROFILE_INVALID", "Neplatný verification profile.")
    normalized: list[tuple[str, ...]] = []
    for command in commands:
        if (
            not isinstance(command, list)
            or not command
            or any(not isinstance(part, str) or not part for part in command)
        ):
            raise OrchestrationError("VERIFY_ARGV_INVALID", profile_id)
        normalized.append(tuple(command))
    return VerificationPlan(
        target_id=target_id,
        target_hash=_tree_hash(root),
        profile_id=profile_id,
        profile_hash=canonical_sha256(profile),
        commands=tuple(normalized),
        required_checks=tuple(str(item) for item in required),
        staging_root=str(root),
    )


class ContainerSandbox:
    """Runs only application-selected argv inside a locked-down local container image."""

    def __init__(self, engine: str, image: str, image_digest: str):
        self.engine = engine
        self.image = image
        self.image_digest = image_digest

    @classmethod
    def discover(cls, image: str) -> "ContainerSandbox | None":
        engine = shutil.which("docker") or shutil.which("podman")
        if not engine:
            return None
        try:
            probe = subprocess.run(
                [engine, "image", "inspect", "--format", "{{.Id}}", image],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
                env={"PATH": os.environ.get("PATH", "")},
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        digest = probe.stdout.strip()
        if probe.returncode != 0 or not digest:
            return None
        return cls(engine, image, digest)

    def run(self, argv: list[str], *, cwd: str, network: bool = False) -> dict[str, Any]:
        if network:
            raise OrchestrationError("VERIFY_NETWORK_FORBIDDEN", "Verifier síť nepovoluje.")
        root = str(Path(cwd).resolve())
        cmd = [
            self.engine, "run", "--rm",
            "--network", "none",
            "--read-only",
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--pids-limit", "128",
            "--memory", "512m",
            "--cpus", "1",
            "--user", "65534:65534",
            "--mount", f"type=bind,src={root},dst=/work,readonly",
            "--workdir", "/work",
            self.image,
            *argv,
        ]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
                env={"PATH": os.environ.get("PATH", "")},
            )
            return {
                "exit_code": result.returncode,
                "stdout": result.stdout[-20000:],
                "stderr": result.stderr[-20000:],
            }
        except subprocess.TimeoutExpired as exc:
            return {
                "exit_code": 124,
                "stdout": (exc.stdout or "")[-20000:] if isinstance(exc.stdout, str) else "",
                "stderr": "verification timeout",
            }


def _report(
    plan: VerificationPlan,
    result: str,
    checks: list[dict[str, Any]],
    *,
    image_digest: str | None,
    started_at: str,
    finished_at: str,
    format_result: str,
    functional_result: str,
    human_result: str = "unverified",
) -> dict[str, Any]:
    normalized_checks = [
        {**check, "path": check.get("path")}
        for check in checks
    ]
    report = {
        "version": 3,
        "target_id": plan.target_id,
        "target_hash": plan.target_hash,
        "profile_id": plan.profile_id,
        "profile_hash": plan.profile_hash,
        "result": result,
        "format_result": format_result,
        "functional_result": functional_result,
        "human_result": human_result,
        "checks": normalized_checks,
        "runner": {
            "image_digest": image_digest,
            "platform": platform.system().lower(),
            "started_at": started_at,
            "finished_at": finished_at,
        },
    }
    _validate_verification_report(report, TECHNICAL_VERIFICATION_REPORT_V3_SCHEMA)
    return report


def _static_format_checks(root: Path) -> tuple[list[dict[str, Any]], str]:
    checks: list[dict[str, Any]] = []
    failed = False
    unsupported = False
    for index, path in enumerate(
        sorted(item for item in root.rglob("*") if item.is_file()), 1
    ):
        raw = path.read_bytes()
        rel = path.relative_to(root).as_posix()
        suffix = path.suffix.lower()
        status = "passed"
        detail = "Formát byl deterministicky ověřen."
        try:
            if suffix == ".py":
                ast.parse(raw.decode("utf-8", errors="strict"), filename=rel)
            elif suffix == ".json":
                parse_json_value_strict(raw.decode("utf-8", errors="strict"))
            elif suffix == ".toml":
                tomllib.loads(raw.decode("utf-8", errors="strict"))
            elif suffix in {".xml", ".svg"}:
                ET.fromstring(raw)
            elif suffix in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
                from PIL import Image
                from io import BytesIO
                with Image.open(BytesIO(raw)) as image:
                    image.verify()
            elif suffix in {
                ".txt", ".md", ".csv", ".html", ".css", ".js", ".mjs", ".cjs",
                ".ts", ".tsx", ".go", ".c", ".h", ".cpp", ".hpp", ".java",
                ".yaml", ".yml", ".ini", ".cfg", ".rst",
            }:
                raw.decode("utf-8", errors="strict")
                if suffix in {".js", ".mjs", ".cjs", ".ts", ".tsx", ".go", ".c", ".cpp", ".java"}:
                    status = "unsupported"
                    detail = "UTF-8 je validní, ale syntax vyžaduje stackový verifier."
                    unsupported = True
            else:
                status = "unsupported"
                detail = "Typ souboru nemá bezpečný vestavěný parser."
                unsupported = True
        except Exception as exc:
            status = "failed"
            failed = True
            detail = f"Parser odmítl {rel}: {type(exc).__name__}: {exc}"
        checks.append({
            "id": f"format-{index:04d}",
            "criterion_id": "format_parser",
            "kind": "deterministic",
            "status": status,
            "evidence_hashes": [hashlib.sha256(raw).hexdigest()],
            "path": rel,
            "detail": detail,
        })
    if not checks:
        return [{
            "id": "format-empty",
            "criterion_id": "artifact_presence",
            "kind": "deterministic",
            "status": "failed",
            "evidence_hashes": [],
            "detail": "Staging neobsahuje žádný artefakt.",
        }], "failed"
    if failed:
        return checks, "failed"
    return checks, "partial" if unsupported else "passed"


def _stack_profile(root: Path) -> tuple[dict[str, Any], str | None]:
    paths = [item.relative_to(root).as_posix() for item in root.rglob("*") if item.is_file()]
    suffixes = {Path(path).suffix.lower() for path in paths}
    if ".py" in suffixes:
        script = (
            "import ast,pathlib,sys;"
            "bad=[];"
            "[(ast.parse(p.read_text(encoding='utf-8'),filename=str(p))) "
            "for p in pathlib.Path('.').rglob('*.py')];"
            "print('python syntax ok')"
        )
        return {
            "id": "python-container-v1",
            "commands": [["python", "-c", script]],
            "required_checks": ["python_project_syntax"],
        }, os.environ.get("KAJOVO_VERIFIER_PYTHON_IMAGE", "python:3.12-slim")
    if suffixes & {".js", ".mjs", ".cjs"} and not suffixes & {".ts", ".tsx"}:
        commands = [["node", "--check", path] for path in paths if Path(path).suffix.lower() in {".js", ".mjs", ".cjs"}]
        return {
            "id": "node-container-v1",
            "commands": commands,
            "required_checks": ["javascript_syntax"] * len(commands),
        }, os.environ.get("KAJOVO_VERIFIER_NODE_IMAGE", "node:22-alpine")
    if suffixes & {".ts", ".tsx"}:
        return {
            "id": "typescript-needs-toolchain-v1",
            "commands": [],
            "required_checks": ["typescript_build"],
        }, None
    return {
        "id": "format-only-v1",
        "commands": [],
        "required_checks": ["format_parser"],
    }, None


def _static_format_checks(root: Path) -> tuple[list[dict[str, Any]], str]:
    checks: list[dict[str, Any]] = []
    failed = False
    unsupported = False
    for index, path in enumerate(
        sorted(item for item in root.rglob("*") if item.is_file()), 1
    ):
        raw = path.read_bytes()
        rel = path.relative_to(root).as_posix()
        suffix = path.suffix.lower()
        status = "passed"
        detail = "Formát byl deterministicky ověřen."
        try:
            if suffix == ".py":
                ast.parse(raw.decode("utf-8", errors="strict"), filename=rel)
            elif suffix == ".json":
                parse_json_value_strict(raw.decode("utf-8", errors="strict"))
            elif suffix == ".toml":
                tomllib.loads(raw.decode("utf-8", errors="strict"))
            elif suffix in {".xml", ".svg"}:
                ET.fromstring(raw)
            elif suffix in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
                from PIL import Image
                from io import BytesIO
                with Image.open(BytesIO(raw)) as image:
                    image.verify()
            elif suffix in {
                ".txt", ".md", ".csv", ".html", ".css", ".js", ".mjs", ".cjs",
                ".ts", ".tsx", ".go", ".c", ".h", ".cpp", ".hpp", ".java",
                ".yaml", ".yml", ".ini", ".cfg", ".rst",
            }:
                raw.decode("utf-8", errors="strict")
                if suffix in {".js", ".mjs", ".cjs", ".ts", ".tsx", ".go", ".c", ".cpp", ".java"}:
                    status = "unsupported"
                    detail = "UTF-8 je validní, ale syntax vyžaduje stackový verifier."
                    unsupported = True
            else:
                status = "unsupported"
                detail = "Typ souboru nemá bezpečný vestavěný parser."
                unsupported = True
        except Exception as exc:
            status = "failed"
            failed = True
            detail = f"Parser odmítl {rel}: {type(exc).__name__}: {exc}"
        checks.append({
            "id": f"format-{index:04d}",
            "criterion_id": "format_parser",
            "kind": "deterministic",
            "status": status,
            "evidence_hashes": [hashlib.sha256(raw).hexdigest()],
            "path": rel,
            "detail": detail,
        })
    if not checks:
        return [{
            "id": "format-empty",
            "criterion_id": "artifact_presence",
            "kind": "deterministic",
            "status": "failed",
            "evidence_hashes": [],
            "detail": "Staging neobsahuje žádný artefakt.",
        }], "failed"
    if failed:
        return checks, "failed"
    return checks, "partial" if unsupported else "passed"


def _stack_profile(root: Path) -> tuple[dict[str, Any], str | None]:
    paths = [item.relative_to(root).as_posix() for item in root.rglob("*") if item.is_file()]
    suffixes = {Path(path).suffix.lower() for path in paths}
    if ".py" in suffixes:
        script = (
            "import ast,pathlib,sys;"
            "bad=[];"
            "[(ast.parse(p.read_text(encoding='utf-8'),filename=str(p))) "
            "for p in pathlib.Path('.').rglob('*.py')];"
            "print('python syntax ok')"
        )
        return {
            "id": "python-container-v1",
            "commands": [["python", "-c", script]],
            "required_checks": ["python_project_syntax"],
        }, os.environ.get("KAJOVO_VERIFIER_PYTHON_IMAGE", "python:3.12-slim")
    if suffixes & {".js", ".mjs", ".cjs"} and not suffixes & {".ts", ".tsx"}:
        commands = [["node", "--check", path] for path in paths if Path(path).suffix.lower() in {".js", ".mjs", ".cjs"}]
        return {
            "id": "node-container-v1",
            "commands": commands,
            "required_checks": ["javascript_syntax"] * len(commands),
        }, os.environ.get("KAJOVO_VERIFIER_NODE_IMAGE", "node:22-alpine")
    if suffixes & {".ts", ".tsx"}:
        return {
            "id": "typescript-needs-toolchain-v1",
            "commands": [],
            "required_checks": ["typescript_build"],
        }, None
    return {
        "id": "format-only-v1",
        "commands": [],
        "required_checks": ["format_parser"],
    }, None


def run_checks(plan: VerificationPlan, sandbox=None) -> dict[str, Any]:
    from datetime import datetime, timezone

    started = datetime.now(timezone.utc).isoformat()
    checks, format_result = _static_format_checks(Path(plan.staging_root))
    if format_result == "failed":
        finished = datetime.now(timezone.utc).isoformat()
        return _report(
            plan, "failed", checks, image_digest=None,
            started_at=started, finished_at=finished,
            format_result="failed", functional_result="not_run",
        )

    functional_result = "not_applicable"
    image_digest = None
    if plan.commands:
        if (
            sandbox is None
            or not isinstance(getattr(sandbox, "image_digest", None), str)
            or not getattr(sandbox, "image_digest", "")
            or not callable(getattr(sandbox, "run", None))
        ):
            functional_result = "unsupported"
            checks.append({
                "id": "sandbox",
                "criterion_id": "sandbox_available",
                "kind": "deterministic",
                "status": "unsupported",
                "evidence_hashes": [],
                "detail": (
                    "VERIFIER_UNAVAILABLE: izolovaný runner s immutable image "
                    "digestem není dostupný; host fallback je zakázán."
                ),
            })
        else:
            image_digest = sandbox.image_digest
            functional_result = "passed"
            for index, argv in enumerate(plan.commands, 1):
                result = sandbox.run(list(argv), cwd=plan.staging_root, network=False)
                if not isinstance(result, dict):
                    raise OrchestrationError(
                        "VERIFY_RUNNER_INVALID", "Sandbox vrátil neplatný výsledek."
                    )
                exit_code = result.get("exit_code")
                stdout = str(result.get("stdout") or "")
                stderr = str(result.get("stderr") or "")
                evidence = canonical_sha256({
                    "argv": list(argv),
                    "exit_code": exit_code,
                    "stdout": stdout,
                    "stderr": stderr,
                    "image_digest": image_digest,
                })
                status = "passed" if exit_code == 0 else "failed"
                if status == "failed":
                    functional_result = "failed"
                checks.append({
                    "id": f"command-{index:04d}",
                    "criterion_id": (
                        plan.required_checks[index - 1]
                        if index - 1 < len(plan.required_checks)
                        else "command"
                    ),
                    "kind": "deterministic",
                    "status": status,
                    "evidence_hashes": [evidence],
                    "detail": (
                        f"argv={list(argv)!r}; exit_code={exit_code}; "
                        f"stdout_sha256={hashlib.sha256(stdout.encode()).hexdigest()}; "
                        f"stderr_sha256={hashlib.sha256(stderr.encode()).hexdigest()}"
                    ),
                })

    if functional_result == "failed":
        result = "failed"
    else:
        # Human/content acceptance is deliberately distinct and still unverified.
        result = "needs_human"
        checks.append({
            "id": "human-acceptance",
            "criterion_id": "declared_assertions",
            "kind": "human",
            "status": "uncertain",
            "evidence_hashes": [],
            "detail": "Technické ověření samo nepotvrzuje úplnost ani významovou správnost.",
        })
    finished = datetime.now(timezone.utc).isoformat()
    return _report(
        plan,
        result,
        checks,
        image_digest=image_digest,
        started_at=started,
        finished_at=finished,
        format_result=format_result,
        functional_result=functional_result,
    )


def build_verification_candidate(
    run_dir: str | Path,
    staged: list[dict[str, Any]],
    *,
    mode: str,
) -> Path:
    """Materialize the exact candidate tree without reopening the user project."""
    run_root = Path(run_dir).resolve()
    candidate = run_root / "staging" / "verification_candidate" / "generated"
    controlled_root = run_root / "staging" / "verification_candidate"
    if controlled_root.exists():
        shutil.rmtree(controlled_root)
    candidate.mkdir(parents=True, exist_ok=True)

    seen: set[str] = set()

    def copy_checked(relative: str, source: Path, expected_hash: str) -> None:
        key = os.path.normcase(relative.replace("\\", "/"))
        if key in seen:
            raise OrchestrationError(
                "VERIFY_PATH_COLLISION",
                f"Kolize kandidátní cesty: {relative}",
            )
        seen.add(key)
        try:
            source = source.resolve(strict=True)
            source.relative_to(run_root)
        except (OSError, ValueError) as exc:
            raise OrchestrationError(
                "VERIFY_SOURCE_ESCAPE", relative
            ) from exc
        if not source.is_file() or sha256_file(str(source)) != expected_hash:
            raise OrchestrationError(
                "VERIFY_SOURCE_HASH", relative
            )
        destination = Path(
            safe_join_under_root(str(candidate), relative)
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        if sha256_file(str(destination)) != expected_hash:
            raise OrchestrationError(
                "VERIFY_COPY_HASH", relative
            )

    if mode == "MODIFY":
        from ..run_bundle import RunBundle

        bundle = RunBundle(run_root)
        legacy_reapproved: dict[str, str] = {}
        for artifact in bundle.artifacts():
            if (
                artifact.get("role") != "manifest"
                or artifact.get("reconstruction_role")
                != "legacy_source_reapproval_v1"
            ):
                continue
            manifest_rel = str(artifact.get("path_in_bundle") or "")
            manifest_path = (run_root / manifest_rel).resolve()
            try:
                manifest_path.relative_to(run_root)
                payload = parse_json_strict(
                    manifest_path.read_text(encoding="utf-8")
                )
            except (OSError, OrchestrationError) as exc:
                raise OrchestrationError(
                    "VERIFY_LEGACY_MANIFEST_INVALID",
                    str(manifest_path),
                ) from exc
            for row in payload.get("approved") or []:
                if isinstance(row, dict) and row.get("path") and row.get("sha256"):
                    legacy_reapproved[str(row["path"])] = str(row["sha256"])

        baseline: list[tuple[str, Path, str]] = []
        for artifact in bundle.artifacts():
            if artifact.get("role") != "in_project_file":
                continue
            metadata = artifact.get("metadata") or {}
            decision = str(metadata.get("policy_decision") or "")
            if decision and decision not in {"approved", "approved_asset"}:
                continue
            relative = str(
                metadata.get("relative_path")
                or artifact.get("reconstruction_role")
                or ""
            )
            expected = str(
                metadata.get("sha256")
                or artifact.get("sha256")
                or ""
            )
            if (
                decision not in {"approved", "approved_asset"}
                and legacy_reapproved.get(relative) != expected
            ):
                raise OrchestrationError(
                    "VERIFY_BASELINE_UNAPPROVED",
                    f"Legacy SourcePack nemá aktuální policy reapproval: {relative}",
                )
            bundle_path = str(artifact.get("path_in_bundle") or "")
            if not relative or not expected or not bundle_path:
                raise OrchestrationError(
                    "VERIFY_BASELINE_INVALID",
                    "Approved SourcePack evidence is incomplete.",
                )
            baseline.append(
                (relative, run_root / bundle_path, expected)
            )
        for relative, source, expected in sorted(baseline):
            copy_checked(relative, source, expected)

    # Overlay staged targets. A changed path deliberately replaces its approved
    # baseline copy, while a case-collision is rejected.
    staged_seen: set[str] = set()
    for row in staged:
        if not isinstance(row, dict):
            raise OrchestrationError("VERIFY_STAGING_INVALID", "Neplatný staged záznam.")
        relative = str(row.get("path") or "")
        staged_path = str(row.get("staged_path") or "")
        expected = str(row.get("sha256") or "")
        if not relative or not staged_path or not expected:
            raise OrchestrationError("VERIFY_STAGING_INVALID", relative or "unknown")
        key = os.path.normcase(relative.replace("\\", "/"))
        if key in staged_seen:
            raise OrchestrationError("VERIFY_PATH_COLLISION", relative)
        staged_seen.add(key)
        source = run_root / staged_path
        try:
            source = source.resolve(strict=True)
            source.relative_to(run_root)
        except (OSError, ValueError) as exc:
            raise OrchestrationError("VERIFY_STAGING_ESCAPE", relative) from exc
        if not source.is_file() or sha256_file(str(source)) != expected:
            raise OrchestrationError("VERIFY_STAGING_HASH", relative)
        destination = Path(safe_join_under_root(str(candidate), relative))
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        if sha256_file(str(destination)) != expected:
            raise OrchestrationError("VERIFY_COPY_HASH", relative)
        seen.add(key)

    return candidate


def unverified_delivery_report(staging_root, staged, *, target_id):
    """Eviduje dodání bez čtení, kopírování či testování obsahu produktu."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    profile = {"id": "delivery-no-product-tests", "commands": [], "required_checks": []}
    plan = VerificationPlan(target_id, canonical_sha256(staged), profile["id"],
                            canonical_sha256(profile), (), (), str(staging_root))
    return _report(
        plan, "unsupported", [{"id": "product-tests", "criterion_id": "product-tests",
            "kind": "deterministic", "status": "skipped", "evidence_hashes": [],
            "detail": "Dodání nespouští následné testování produktu."}],
        image_digest=None, started_at=now, finished_at=now,
        format_result="partial", functional_result="not_run",
    )


def candidate_verification_report(
    run_dir: str | Path,
    staged: list[dict[str, Any]],
    *,
    mode: str,
    target_id: str,
    profile_ids: list[str] | None = None,
) -> dict[str, Any]:
    candidate = Path(run_dir).resolve() / "staging"
    report = unverified_delivery_report(candidate, staged, target_id=target_id)
    report["candidate_mode"] = mode
    report["candidate_root"] = candidate.relative_to(
        Path(run_dir).resolve()
    ).as_posix()
    report["candidate_scope"] = "staged_outputs"
    _validate_verification_report(report, VERIFICATION_REPORT_V3_SCHEMA)
    return report


def technical_staging_report(
    staging_root: str | Path,
    *,
    target_id: str,
    profile_ids: list[str] | None = None,
) -> dict[str, Any]:
    root = Path(staging_root).resolve()
    profile, image = _stack_profile(root)
    requested = [str(value) for value in (profile_ids or []) if value]
    if requested:
        profile = {**profile, "requested_profile_ids": requested}
    sandbox = ContainerSandbox.discover(image) if image and profile["commands"] else None
    plan = plan_checks(root, profile, target_id=target_id)
    return run_checks(plan, sandbox=sandbox)
