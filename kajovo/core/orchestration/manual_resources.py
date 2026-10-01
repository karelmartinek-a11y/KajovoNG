"""Explicitní ruční podklady doplňují vazby, nikdy původní výrobní graf."""
from pathlib import Path
import hashlib

import jsonschema

from ..contracts import ContractError
from ..recoverable_artifacts import artifact_path, load_run_state
from ..runlog import RunLogger
from ..utils import safe_join_under_root
from ..resources import resource_path
from .contracts import canonical_sha256, parse_json_strict

MANUAL_RESOURCE_BINDINGS_V1_SCHEMA = parse_json_strict(resource_path(
    "orchestration/contracts/local/MANUAL_RESOURCE_BINDINGS_V1.schema.json"
).read_text(encoding="utf-8"))


def graph_from_state(state):
    graph = (state.get("preparation_snapshot") or {}).get("graph")
    if not graph:
        graph = ((state.get("generate_batch") or {}).get("snapshot") or {}).get("structure")
    if not isinstance(graph, dict) or graph.get("contract") != "IMPLEMENTATION_GRAPH_V3":
        raise ContractError("Ruční podklad vyžaduje původní IMPLEMENTATION_GRAPH_V3.")
    return graph


def validate_bindings(value, graph, run_id):
    jsonschema.Draft202012Validator(MANUAL_RESOURCE_BINDINGS_V1_SCHEMA).validate(value)
    if value["run_id"] != run_id or value["graph_hash"] != canonical_sha256(graph):
        raise ContractError("Ruční podklady nepatří tomuto běhu a grafu.")
    paths = [row["target_path"] for row in value["bindings"]]
    if len(paths) != len(set(paths)):
        raise ContractError("Duplicitní cíle ručních podkladů.")


def bind_manual_resource(run_dir, target_path, source_path):
    from ..runs.locking import ExecutionLock

    with ExecutionLock(Path(run_dir).resolve() / "execution.lock"):
        return _bind_manual_resource(run_dir, target_path, source_path)


def _bind_manual_resource(run_dir, target_path, source_path):
    from .resource_delivery import resource_delivery_index

    root = Path(run_dir).resolve()
    state = load_run_state(root)
    if state.get("submission_unknown") or state.get("status") == "submission_unknown":
        raise ContractError("Nejprve dohledejte neurčitý výsledek odeslání.")
    graph = graph_from_state(state)
    delivery = resource_delivery_index(graph).get(target_path)
    if not delivery or delivery.get("producer") != "manual_input":
        raise ContractError("Vybraný cíl nemá ručního producenta.")
    source = Path(source_path).resolve(strict=True)
    if not source.is_file():
        raise ContractError("Ruční podklad musí být soubor.")
    log = RunLogger(str(root.parent), root.name, project_name=state.get("project") or "NO_PROJECT",
                    resume=True, resume_import=True, resume_resource=True)
    value = state.get("manual_resource_bindings") or {
        "version": 1, "run_id": root.name, "graph_hash": canonical_sha256(graph), "bindings": [],
    }
    validate_bindings(value, graph, root.name)
    artifact = log.bundle.archive_artifact(source, role="manual_resource", kind="input_file", reusable=True,
                                          reconstruction_role=target_path)
    binding = {"target_path": target_path, "source_or_task_id": delivery["source_or_task_id"],
               "artifact_id": artifact["artifact_id"], "sha256": artifact["sha256"]}
    value["bindings"] = [row for row in value["bindings"] if row["target_path"] != target_path] + [binding]
    validate_bindings(value, graph, root.name)
    log.save_json("manifests", "manual_resource_bindings_v1", value)
    log.update_state({"manual_resource_bindings": value})
    if not state.get("batch_id"):
        snapshot = load_run_state(root)
        log.checkpoint("manual_resource_ready", state_snapshot=snapshot, safe_to_continue=True,
                       required_artifact_ids=[item["artifact_id"] for item in value["bindings"]],
                       reason="Doplněné explicitní podklady pro pokračování původního grafu.")
    log.bundle.seal()
    return binding


def manual_resource_bytes(worker, graph, target_path, source_id):
    state = load_run_state(worker.log.paths.run_dir)
    value = state.get("manual_resource_bindings")
    if not value:
        return None
    validate_bindings(value, graph, worker.log.run_id)
    row = next((item for item in value["bindings"] if item["target_path"] == target_path), None)
    if row is None:
        return None
    if row["source_or_task_id"] != source_id:
        raise ContractError("Ruční podklad má jinou zdrojovou identitu.")
    artifact = next((item for item in worker.log.bundle.artifacts() if item["artifact_id"] == row["artifact_id"]), None)
    if artifact is None:
        raise ContractError("Chybí archiv ručního podkladu.")
    path = Path(safe_join_under_root(worker.log.paths.run_dir, artifact["path_in_bundle"]))
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != row["sha256"]:
        raise ContractError("Ruční podklad má změněný hash.")
    return data


def recoverable_staged_files(parent_dir, state=None):
    """Doložený textový ArtifactRecord přežije pád před změnou read-modelu."""
    from ..run_bundle import LegacyRunAdapter, RunBundle
    state = load_run_state(parent_dir) if state is None else state
    staged = state.get("staged_files") or []
    rows = {row["path"]: row for row in staged}
    if len(rows) != len(staged):
        raise ContractError("Duplicitní cíle převzatého stagingu.")
    artifacts = [artifact for artifact in RunBundle(parent_dir).artifacts()
                 if artifact.get("role") == "staged_output"
                 and (artifact.get("metadata") or {}).get("resource_producer") == "validated_text"]
    if not artifacts and not (state.get("preparation_snapshot") or {}).get("graph"):
        return [rows[path] for path in sorted(rows)]
    graph = graph_from_state(state)
    file_targets = graph["spine"].get("files")
    if file_targets is None:
        if artifacts or state.get("production_expected_target_hashes"):
            raise ContractError("Textový artefakt vyžaduje původní seznam výrobních cílů.")
        return [rows[path] for path in sorted(rows)]
    targets = {row["path"]: row for row in file_targets}
    expected = state.get("production_expected_target_hashes") or {}
    adapter = LegacyRunAdapter(parent_dir)
    validations = adapter.validations()
    generated_by_path = {}
    for path, target in targets.items():
        if target.get("kind") != "text":
            continue
        generated_path = artifact_path(parent_dir, "generated/" + path)
        if not generated_path:
            continue
        generated = parse_json_strict(Path(generated_path).read_text("utf-8"))
        if (generated.get("path") != path or generated.get("validation_status") != "verified"
                or path not in expected or not isinstance(generated.get("content"), str)
                or not generated.get("response_id") or not generated.get("work_order_hash")
                or hashlib.sha256(generated["content"].encode("utf-8")).hexdigest() != generated.get("output_hash")
                or not any(record.get("status") == "passed" and record.get("target_type") == "file_contract"
                           and record.get("target_id") == path
                           and (record.get("evidence") or {}).get("sha256") == generated.get("output_hash")
                           and (record.get("evidence") or {}).get("response_id") == generated.get("response_id")
                           and (record.get("evidence") or {}).get("work_order_hash") == generated.get("work_order_hash")
                           for record in validations)):
            raise ContractError("Textový artefakt nepatří původnímu validovanému provider výsledku.")
        generated_by_path[path] = generated
    for artifact in artifacts:
        metadata = artifact.get("metadata") or {}
        path = metadata.get("relative_path")
        if path in rows and "implementation_graph_hash" not in metadata:
            if (rows[path].get("sha256") != artifact.get("sha256")
                    or path not in expected or metadata.get("expected_target_hash") != expected[path]):
                raise ContractError("Historický staged text změnil obsah nebo původní cíl.")
            continue
        if (path not in targets or targets[path].get("kind") != "text"
                or artifact.get("reconstruction_role") != path
                or metadata.get("implementation_graph_hash") != canonical_sha256(graph)
                or not artifact.get("source_response_id")
                or path not in expected or metadata.get("expected_target_hash") != expected[path]
                or metadata.get("sha256") != artifact.get("sha256")):
            raise ContractError("Textový artefakt nepatří původnímu výrobnímu grafu a výsledku.")
        generated = generated_by_path.get(path, {})
        if (generated.get("path") != path or generated.get("validation_status") != "verified"
                or generated.get("response_id") != artifact["source_response_id"]
                or generated.get("output_hash") != artifact["sha256"]
                or not isinstance(generated.get("content"), str)
                or hashlib.sha256(generated["content"].encode("utf-8")).hexdigest() != artifact["sha256"]):
            raise ContractError("Textový artefakt nepatří původnímu validovanému provider výsledku.")
        candidate = {
            "path": path, "staged_path": artifact["path_in_bundle"],
            "bytes": artifact["size_bytes"], "sha256": artifact["sha256"],
            "expected_target_hash": expected[path], "artifact_id": artifact["artifact_id"],
            "action": "modify" if targets[path].get("action") == "modify" else "add",
            "purpose": "validated_text", "resource_producer": "validated_text",
        }
        if path in rows and rows[path].get("sha256") != candidate["sha256"]:
            raise ContractError("Doložené textové artefakty mají kolidující obsah.")
        rows.setdefault(path, candidate)
    for path, generated in generated_by_path.items():
        if path in rows:
            if rows[path].get("sha256") != generated["output_hash"]:
                raise ContractError("Staging a validovaný text mají kolidující obsah.")
            continue
        rows[path] = {
            "path": path, "bytes": len(generated["content"].encode("utf-8")),
            "sha256": generated["output_hash"], "expected_target_hash": expected[path],
            "action": "modify" if targets[path].get("action") == "modify" else "add",
            "purpose": "validated_text", "resource_producer": "validated_text",
            "_generated_content": generated["content"],
            "_source_response_id": generated["response_id"],
        }
    return [rows[path] for path in sorted(rows)]


def inherit_resources(parent_dir, child_log):
    """Nová LIVE větev vlastní bajty, původní hash cíle i explicitní podklady."""
    import copy

    state = load_run_state(parent_dir)
    bindings = state.get("manual_resource_bindings")
    graph = graph_from_state(state)
    from ..run_bundle import RunBundle
    artifacts = {row["artifact_id"]: row for row in RunBundle(parent_dir).artifacts()}
    patch = {"inherited_staged_files": [], "inherited_graph_hash": canonical_sha256(graph)}
    if "production_expected_target_hashes" in state:
        patch["inherited_target_expectations"] = copy.deepcopy(state["production_expected_target_hashes"])

    def archive(relative, digest, role, target):
        path = Path(safe_join_under_root(parent_dir, relative))
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ContractError("Zdrojový artefakt větve změnil hash.")
        return child_log.bundle.archive_artifact(path, role=role, kind="output_file" if role == "staged_output" else "input_file",
                                                reconstruction_role=target, reusable=True)

    for source in recoverable_staged_files(parent_dir, state):
        row = copy.deepcopy(source)
        if "_generated_content" in row:
            from ..utils import atomic_write_text
            destination = Path(safe_join_under_root(child_log.paths.run_dir, "staging/inherited/" + row["path"]))
            destination.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(str(destination), row.pop("_generated_content"))
            artifact = child_log.bundle.archive_artifact(
                destination, role="staged_output", kind="output_file", reusable=True,
                reconstruction_role=row["path"], source_response_id=row.pop("_source_response_id"),
            )
        else:
            artifact = archive(row["staged_path"], row["sha256"], "staged_output", row["path"])
        row.update(staged_path=artifact["path_in_bundle"], artifact_id=artifact["artifact_id"])
        patch["inherited_staged_files"].append(row)
    if bindings:
        validate_bindings(bindings, graph, Path(parent_dir).name)
        value = copy.deepcopy(bindings)
        value["run_id"] = child_log.run_id
        for row in value["bindings"]:
            old = artifacts[row["artifact_id"]]
            artifact = archive(old["path_in_bundle"], row["sha256"], "manual_resource", row["target_path"])
            row["artifact_id"] = artifact["artifact_id"]
        patch["manual_resource_bindings"] = value
    child_log.update_state(patch)
