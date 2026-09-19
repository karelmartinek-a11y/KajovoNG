"""Evidence-based verification. Generated code never runs on the host."""
from __future__ import annotations

import hashlib
import platform
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .contracts import canonical_sha256
from .errors import OrchestrationError


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


def _report(
    plan: VerificationPlan,
    result: str,
    checks: list[dict[str, Any]],
    *,
    image_digest: str | None,
    started_at: str,
    finished_at: str,
) -> dict[str, Any]:
    return {
        "version": 2,
        "target_id": plan.target_id,
        "target_hash": plan.target_hash,
        "profile_id": plan.profile_id,
        "profile_hash": plan.profile_hash,
        "result": result,
        "checks": checks,
        "runner": {
            "image_digest": image_digest,
            "platform": platform.system().lower(),
            "started_at": started_at,
            "finished_at": finished_at,
        },
    }


def run_checks(plan: VerificationPlan, sandbox=None) -> dict[str, Any]:
    """Execute commands only through an administrator-provided sandbox handle."""
    from datetime import datetime, timezone

    started = datetime.now(timezone.utc).isoformat()
    if plan.commands and (
        sandbox is None
        or not isinstance(getattr(sandbox, "image_digest", None), str)
        or not getattr(sandbox, "image_digest", "")
        or not callable(getattr(sandbox, "run", None))
    ):
        finished = datetime.now(timezone.utc).isoformat()
        return _report(
            plan,
            "unsupported",
            [{
                "id": "sandbox",
                "criterion_id": "sandbox_available",
                "kind": "deterministic",
                "status": "unsupported",
                "evidence_hashes": [],
                "detail": (
                    "VERIFIER_UNAVAILABLE: schválený sandbox s immutable image "
                    "digestem není dostupný; host fallback je zakázán."
                ),
            }],
            image_digest=None,
            started_at=started,
            finished_at=finished,
        )

    checks: list[dict[str, Any]] = []
    overall = "passed"
    if not plan.commands:
        for index, path in enumerate(
            sorted(item for item in Path(plan.staging_root).rglob("*") if item.is_file()),
            1,
        ):
            raw = path.read_bytes()
            status = "passed"
            detail = "Immutable staged file is readable UTF-8."
            try:
                raw.decode("utf-8", errors="strict")
            except UnicodeDecodeError:
                status = "unsupported"
                detail = "Binary/non-UTF8 artifact requires a domain verifier."
                overall = "needs_human"
            checks.append({
                "id": f"static-{index:04d}",
                "criterion_id": "encoding",
                "kind": "deterministic",
                "status": status,
                "evidence_hashes": [hashlib.sha256(raw).hexdigest()],
                "detail": detail,
            })
        if not checks:
            overall = "failed"
            checks.append({
                "id": "static-empty",
                "criterion_id": "artifact_presence",
                "kind": "deterministic",
                "status": "failed",
                "evidence_hashes": [],
                "detail": "Staging neobsahuje žádný artefakt.",
            })
        elif overall == "passed":
            overall = "needs_human"
            checks.append({
                "id": "content-review",
                "criterion_id": "declared_assertions",
                "kind": "human",
                "status": "uncertain",
                "evidence_hashes": [],
                "detail": "Technická čitelnost neprokazuje obsahovou správnost.",
            })
    else:
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
                "image_digest": sandbox.image_digest,
            })
            status = "passed" if exit_code == 0 else "failed"
            if status == "failed":
                overall = "failed"
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
    finished = datetime.now(timezone.utc).isoformat()
    return _report(
        plan,
        overall,
        checks,
        image_digest=getattr(sandbox, "image_digest", None) if sandbox else None,
        started_at=started,
        finished_at=finished,
    )


def technical_staging_report(
    staging_root: str | Path,
    *,
    target_id: str,
) -> dict[str, Any]:
    profile = {
        "id": "static-text-v1",
        "commands": [],
        "required_checks": ["encoding", "format_parser", "declared_assertions"],
    }
    return run_checks(
        plan_checks(staging_root, profile, target_id=target_id),
        sandbox=None,
    )
