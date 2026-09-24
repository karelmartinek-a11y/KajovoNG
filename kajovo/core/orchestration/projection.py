"""Explicit artifact projection without fuzzy relevance decisions."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .contracts import canonical_sha256
from .errors import OrchestrationError


@dataclass(frozen=True)
class Binding:
    artifact_id: str
    sha256: str
    selector: str
    role: str
    reason: str


@dataclass(frozen=True)
class Projection:
    stage: str
    target_id: str
    components: tuple[Binding, ...]
    payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "target_id": self.target_id,
            "components": [asdict(item) for item in self.components],
            "payload": self.payload,
        }

    @property
    def hash(self) -> str:
        return canonical_sha256(self.to_dict())


@dataclass(frozen=True)
class FrozenRegistry:
    artifacts: dict[str, dict[str, Any]]
    required_by_target: dict[str, tuple[str, ...]]


def project(
    stage: str,
    target: str,
    registry: FrozenRegistry,
    bindings: tuple[Binding, ...],
) -> Projection:
    required = set(registry.required_by_target.get(target, ()))
    selected = {item.artifact_id for item in bindings}
    missing = required - selected
    if missing:
        raise OrchestrationError(
            "CONTEXT_INCOMPLETE",
            f"{target}: chybí explicitní komponenty {sorted(missing)}.",
        )
    payload: dict[str, Any] = {}
    for binding in bindings:
        artifact = registry.artifacts.get(binding.artifact_id)
        if artifact is None:
            raise OrchestrationError("CONTEXT_SOURCE_MISSING", binding.artifact_id)
        payload[binding.artifact_id] = artifact.get("value")
    result = Projection(stage, target, bindings, payload)
    verify_projection(result, registry)
    return result


def verify_projection(p: Projection, store) -> None:
    seen: set[tuple[str, str, str, str]] = set()
    artifacts = getattr(store, "artifacts", None)
    if isinstance(store, FrozenRegistry):
        artifacts = store.artifacts
    for component in p.components:
        key = (
            component.artifact_id,
            component.sha256,
            component.selector,
            component.role,
        )
        if key in seen:
            raise OrchestrationError(
                "CONTEXT_DUPLICATE",
                f"Duplicitní komponenta projekce: {component.artifact_id}",
            )
        seen.add(key)
        if not component.reason:
            raise OrchestrationError(
                "CONTEXT_REASON_MISSING",
                component.artifact_id,
            )
        if isinstance(artifacts, dict):
            artifact = artifacts.get(component.artifact_id)
            if artifact is None:
                raise OrchestrationError("CONTEXT_SOURCE_MISSING", component.artifact_id)
            digest = canonical_sha256(artifact.get("value"))
            if digest != component.sha256 or artifact.get("sha256", digest) != digest:
                raise OrchestrationError("CONTEXT_HASH_MISMATCH", component.artifact_id)


def projection_from_file_context(
    stage: str,
    target: str,
    compiled: dict[str, Any],
) -> Projection:
    """Adapter from the existing ContextCompiler to the canonical Projection."""
    working = compiled.get("working_context") or {}
    components: list[Binding] = []
    payload: dict[str, Any] = {}
    for role, source_key in (
        ("target_file", "target_file"),
        ("implementation_contract", "implementation_contract"),
        ("requirements", "relevant_requirements"),
        ("interfaces", "shared_type_contracts"),
        ("obligations", "applicable_invariants"),
        ("sources", "relevant_source_excerpts"),
        ("source_segments", "source_segments"),
        ("dependencies", "dependency_contracts"),
        ("architecture", "architecture_contracts"),
        ("project", "project_contract"),
        ("assumptions", "applicable_assumptions"),
        ("decisions", "architecture_decisions"),
        ("integration_rules", "integration_rules"),
        ("packages", "packages"),
        ("acceptance", "acceptance_contracts"),
        ("verified_artifacts", "verified_dependency_artifacts"),
        ("cycles", "cycle_contracts"),
        ("modify_obligations", "modify_obligations"),
    ):
        value = working.get(source_key)
        if value in (None, [], {}):
            continue
        artifact_id = f"{target}:{role}"
        digest = canonical_sha256(value)
        components.append(
            Binding(
                artifact_id=artifact_id,
                sha256=digest,
                selector=f"/working_context/{source_key}",
                role=role,
                reason=f"ContextCompiler explicitně vybral {role} pro {target}.",
            )
        )
        payload[role] = value
    projection = Projection(stage, target, tuple(components), payload)
    verify_projection(projection, None)
    return projection
