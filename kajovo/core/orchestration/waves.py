"""Execution DAG over IMPLEMENTATION_GRAPH_V3 content dependencies."""
from __future__ import annotations

from dataclasses import dataclass

from .errors import OrchestrationError


@dataclass(frozen=True)
class ExecutionDag:
    waves: tuple[tuple[str, ...], ...]
    content_dependencies: dict[str, tuple[str, ...]]
    contract_dependencies: dict[str, tuple[str, ...]]


def build_execution_dag(graph: dict) -> ExecutionDag:
    spine = graph.get("spine") or {}
    files = spine.get("files") or []
    by_path = {str(item.get("path") or ""): item for item in files}
    if not by_path or "" in by_path:
        raise OrchestrationError("IMPLEMENTATION_GRAPH_EMPTY", "Graf neobsahuje platné soubory.")
    if len(by_path) != len(files):
        raise OrchestrationError("PATH_COLLISION", "Graf obsahuje duplicitní cestu.")

    content: dict[str, set[str]] = {}
    contract: dict[str, set[str]] = {}
    for path, item in by_path.items():
        deps = {str(value) for value in item.get("dependencies", [])}
        cdeps = {str(value) for value in item.get("content_dependencies", [])}
        if path in deps or path in cdeps:
            raise OrchestrationError("UNKNOWN_DEPENDENCY", f"{path}: self dependency.")
        unknown = (deps | cdeps) - by_path.keys()
        if unknown:
            raise OrchestrationError(
                "UNKNOWN_DEPENDENCY",
                f"{path}: neznámé závislosti {sorted(unknown)}",
            )
        if not cdeps <= deps:
            raise OrchestrationError(
                "CONTENT_DEPENDENCY_INVALID",
                f"{path}: content dependency musí být i obecná dependency.",
            )
        mode = str(item.get("dependency_content_mode") or "contract")
        if (mode == "verified_content") != bool(cdeps):
            raise OrchestrationError(
                "CONTENT_MODE_INVALID",
                f"{path}: dependency_content_mode neodpovídá content dependencies.",
            )
        content[path] = cdeps
        contract[path] = deps - cdeps

    # Execution order is over every declared dependency, not only verified-content
    # edges. Content-vs-contract semantics remain separate for context compilation.
    pending = {
        path: set(content[path]) | set(contract[path])
        for path in by_path
    }
    waves: list[tuple[str, ...]] = []
    while pending:
        ready = tuple(sorted(path for path, deps in pending.items() if not deps))
        if not ready:
            raise OrchestrationError(
                "CONTENT_DEPENDENCY_CYCLE",
                "Cyklická content dependency nemá bezpečný první krok.",
            )
        waves.append(ready)
        completed = set(ready)
        pending = {
            path: deps - completed
            for path, deps in pending.items()
            if path not in completed
        }
    return ExecutionDag(
        tuple(waves),
        {key: tuple(sorted(value)) for key, value in content.items()},
        {key: tuple(sorted(value)) for key, value in contract.items()},
    )


def ready_tasks(dag: ExecutionDag, evidence) -> tuple[str, ...]:
    """Vrátí tasks, jejichž verified-content evidence je aktuální."""
    available = set()
    if isinstance(evidence, dict):
        available = {
            str(key)
            for key, value in evidence.items()
            if value is True or (
                isinstance(value, dict)
                and value.get("status") in {"passed", "completed_verified", "verified"}
            )
        }
    elif hasattr(evidence, "verified_targets"):
        available = set(evidence.verified_targets())
    for wave in dag.waves:
        ready = tuple(
            path
            for path in wave
            if set(dag.content_dependencies.get(path, ())) <= available
        )
        if ready:
            return ready
    return ()
