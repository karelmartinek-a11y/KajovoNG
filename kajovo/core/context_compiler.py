"""Deterministický výběr implementačních povinností s přesným původem.

Compiler neodvozuje význam ze slov ani přípon. Rozsah globálních povinností
určuje přípravná fáze; chybějící rozhodnutí blokuje pracovní kontext.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json

from .contracts import ContractError, validate_paths


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def content_hash(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def file_index(snapshot):
    structure = snapshot["structure"]
    if structure.get("contract") == "IMPLEMENTATION_GRAPH_V3":
        files = list((structure.get("spine") or {}).get("files") or [])
    else:
        files = structure.get("files", structure.get("touched_files", []))
        files = [*files, *structure.get("preserved_files", [])]
    validate_paths(files)
    return {file["path"]: file for file in files}


def global_obligations(snapshot):
    """Atomické položky, jejichž působnost musí příprava výslovně rozhodnout."""
    result = {}
    for group in ("requirements", "plan", "structure"):
        for key, value in (snapshot.get(group) or {}).items():
            if key in {"contract", "version", "files", "touched_files", "preserved_files",
                       "interfaces", "implementation", "architecture_items"}:
                continue
            if key.endswith("requirements") and isinstance(value, list) and all(
                isinstance(item, dict) and "id" in item for item in value
            ):
                continue
            if isinstance(value, list):
                result.update({f"/{group}/{key}/{i}": item for i, item in enumerate(value)})
            elif value:
                result[f"/{group}/{key}"] = value
    return result


def dependency_groups(snapshot):
    """SCC a topologické vlny kondenzovaného grafu, nezávislé na pořadí vstupu."""
    files = file_index(snapshot)
    graph = {p: sorted(set(f.get("dependencies", []))) for p, f in files.items()}
    for path, dependencies in graph.items():
        if set(dependencies) - files.keys():
            raise ContractError(f"{path}: neznámé závislosti {sorted(set(dependencies) - files.keys())}")
    # Iterativní Kosaraju nepřeteče na projektech s tisíci soubory.
    visited, order = set(), []
    for start in sorted(graph):
        stack = [(start, False)]
        while stack:
            node, expanded = stack.pop()
            if expanded:
                order.append(node)
            elif node not in visited:
                visited.add(node)
                stack.append((node, True))
                stack.extend((dep, False) for dep in reversed(graph[node]) if dep not in visited)
    reverse = {p: [] for p in graph}
    for node, dependencies in graph.items():
        for dep in dependencies:
            reverse[dep].append(node)
    assigned, groups = set(), []
    for start in reversed(order):
        if start in assigned:
            continue
        stack, group = [start], []
        while stack:
            node = stack.pop()
            if node in assigned:
                continue
            assigned.add(node)
            group.append(node)
            stack.extend(reverse[node])
        groups.append(sorted(group))
    groups.sort()
    owner = {p: i for i, group in enumerate(groups) for p in group}
    pending = {i: {owner[d] for p in group for d in graph[p]} - {i}
               for i, group in enumerate(groups)}
    waves = []
    while pending:
        ready = sorted(i for i, dependencies in pending.items() if not dependencies)
        if not ready:
            raise ContractError("Neplatná kondenzace závislostí.")
        waves.append([groups[i] for i in ready])
        pending = {i: dependencies - set(ready) for i, dependencies in pending.items() if i not in ready}
    return {"waves": waves, "cycles": [g for g in groups if len(g) > 1 or g[0] in graph[g[0]]]}


def implementation_schema():
    """Řídké facet kontrakty: detaily pouze pro skutečně použitou oblast."""
    from .structured_output import obj, array
    text = {"type": "string"}
    strings = array(text)
    facets = array(obj({
        "kind": {"type": "string", "enum": ["types", "serialization", "storage", "api", "events",
            "errors", "retry", "timeouts", "idempotency", "transactions", "concurrency", "locking",
            "persistence", "auth", "security", "lifecycle", "invariants", "framework", "versions"]},
        "definition": text, "source_refs": strings,
    }))
    return obj({
        "version": {"type": "integer", "enum": [1]},
        "scopes": array(obj({"source": text, "paths": strings, "reason": text})),
        "interfaces": array(obj({"id": text, "version": text, "signature": text,
                                  "error_semantics": text, "lifecycle": text})),
        "files": array(obj({
            "path": text, "facets": facets,
            "interface_bindings": array(obj({"id": text, "version": text})),
            "acceptance_criteria": strings, "test_scenarios": strings,
            "assumptions": strings,
            "unresolved_questions": array(obj({"question": text, "critical": {"type": "boolean"}})),
            "required_facets": strings,
            "expected_output_tokens": {"type": "integer", "minimum": 1},
            "allow_empty": {"type": "boolean"},
        })),
    })


def validate_implementation(snapshot):
    import jsonschema
    structure = snapshot["structure"]
    if structure.get("contract") == "IMPLEMENTATION_GRAPH_V3":
        from .orchestration.waves import build_execution_dag
        dag = build_execution_dag(structure)
        return {
            "waves": [[list(wave)] for wave in dag.waves],
            "cycles": [],
            "content_dependencies": {
                key: list(value) for key, value in dag.content_dependencies.items()
            },
        }
    implementation = structure.get("implementation")
    if implementation is None:
        raise ContractError("Příprava vyžaduje implementační kontrakt v1; legacy struktura není samostatně implementovatelná.")
    try:
        jsonschema.validate(implementation, implementation_schema())
    except jsonschema.ValidationError as exc:
        raise ContractError(f"Neplatný implementační kontrakt: {exc.message}") from exc
    files = file_index(snapshot)
    contracts = implementation["files"]
    validate_paths(contracts)
    if {c["path"] for c in contracts} != set(files):
        raise ContractError("Implementační kontrakty musí pokrýt všechny soubory včetně zachovaných providerů.")
    obligations = global_obligations(snapshot)
    scopes = implementation["scopes"]
    if len({s["source"] for s in scopes}) != len(scopes) or {s["source"] for s in scopes} != set(obligations):
        raise ContractError("Příprava musí určit působnost každé globální povinnosti právě jednou.")
    for scope in scopes:
        if not scope["reason"].strip() or not set(scope["paths"]) <= files.keys():
            raise ContractError("Působnost povinnosti vyžaduje známé cesty a zdůvodnění.")
        if not scope["paths"]:
            raise ContractError("Povinnost nemá implementačního vlastníka; nesmí být tiše vynechána.")
    interfaces = implementation["interfaces"]
    by_id = {i["id"]: i for i in interfaces}
    if len(by_id) != len(interfaces) or set(by_id) != {i["id"] for i in structure.get("interfaces", [])}:
        raise ContractError("Přesné kontrakty rozhraní neodpovídají struktuře.")
    for interface in interfaces:
        if any(not interface[k].strip() for k in interface):
            raise ContractError(f"Rozhraní {interface['id']} postrádá signaturu, verzi, chyby nebo lifecycle.")
    for contract in contracts:
        file = files[contract["path"]]
        symbols = set(file.get("provides", []) + file.get("requires", []))
        bindings = contract["interface_bindings"]
        if len({b["id"] for b in bindings}) != len(bindings) or {b["id"] for b in bindings} != symbols:
            raise ContractError(f"{file['path']}: neúplné vazby veřejných symbolů.")
        for binding in bindings:
            if binding["id"] not in by_id or binding["version"] != by_id[binding["id"]]["version"]:
                raise ContractError(f"{file['path']}: neznámý symbol nebo konfliktní verze {binding['id']}.")
        for symbol in file.get("requires", []):
            providers = [p for p in [file["path"], *file.get("dependencies", [])]
                         if p in files and symbol in files[p].get("provides", [])]
            if not providers:
                raise ContractError(f"{file['path']}: chybí dependency provider {symbol}.")
        if any(q["critical"] for q in contract["unresolved_questions"]):
            raise ContractError(f"{file['path']}: nevyřešená blokující otázka.")
        if not contract["acceptance_criteria"] or not contract["test_scenarios"]:
            raise ContractError(f"{file['path']}: chybí akceptace nebo ověřovací scénář.")
        if any(not value.strip() for key in ("acceptance_criteria", "test_scenarios", "assumptions")
               for value in contract[key]):
            raise ContractError(f"{file['path']}: prázdné implementační kritérium.")
        kinds = {f["kind"] for f in contract["facets"]}
        if not set(contract["required_facets"]) <= kinds:
            raise ContractError(f"{file['path']}: chybí povinná implementační oblast.")
        for facet in contract["facets"]:
            if not facet["definition"].strip() or not facet["source_refs"]:
                raise ContractError(f"{file['path']}: detail vyžaduje definici a zdroj.")
            if not set(facet["source_refs"]) <= obligations.keys():
                raise ContractError(f"{file['path']}: neznámý původ implementačního detailu.")
            applicable = {s["source"] for s in scopes if file["path"] in s["paths"]}
            if not set(facet["source_refs"]) <= applicable:
                raise ContractError(f"{file['path']}: zdroj detailu nemá odpovídající působnost.")
    return dependency_groups(snapshot)


class ContextCompiler:
    """Auditní hash identifikuje celek; working hash identifikuje jen účinná data."""

    def __init__(self, snapshot):
        self.snapshot = deepcopy(snapshot)
        self.files = file_index(self.snapshot)
        self.snapshot_hash = content_hash(self.snapshot)
        structure = self.snapshot["structure"]
        self.is_v3 = structure.get("contract") == "IMPLEMENTATION_GRAPH_V3"
        if self.is_v3:
            from .orchestration.waves import build_execution_dag
            dag = build_execution_dag(structure)
            self.graph = {
                "waves": [list(wave) for wave in dag.waves],
                "cycles": [],
                "content_dependencies": {
                    key: list(value)
                    for key, value in dag.content_dependencies.items()
                },
            }
            self.implementation = structure
            self.contracts = {
                row["path"]: row["spec"]
                for row in structure.get("file_specs", [])
            }
            self.interfaces = list((structure.get("spine") or {}).get("interfaces", []))
        else:
            self.graph = validate_implementation(self.snapshot)
            self.implementation = structure["implementation"]
            self.contracts = {c["path"]: c for c in self.implementation["files"]}
            self.interfaces = list(self.implementation["interfaces"])

    def _compile_v3(self, path, *, originals=None, verified_artifacts=None):
        if path not in self.files:
            raise ContractError(f"Neznámý cílový soubor {path}.")
        if path not in self.contracts:
            raise ContractError(f"{path}: chybí A2/B2 DETAIL kontrakt.")
        file = self.files[path]
        spec = deepcopy(self.contracts[path])
        requirements = self.snapshot.get("requirements") or {}
        plan = self.snapshot.get("plan") or {}
        requirement_index = {
            row["id"]: row for row in requirements.get("requirements", [])
        }
        selected_requirements = [
            requirement_index[key]
            for key in file.get("requirement_ids", [])
            if key in requirement_index
        ]
        if {row["id"] for row in selected_requirements} != set(file.get("requirement_ids", [])):
            raise ContractError(f"{path}: neznámý requirement.")
        components = [
            row for row in plan.get("components", [])
            if row.get("id") == file.get("component_id")
        ]
        if len(components) != 1:
            raise ContractError(f"{path}: chybí jednoznačná component.")

        symbols = set(file.get("provides", []) + file.get("requires", []))
        shared_interfaces = sorted(
            [row for row in self.interfaces if row["id"] in symbols],
            key=lambda row: row["id"],
        )
        dependencies = sorted(set(file.get("dependencies", [])))
        content_dependencies = sorted(set(file.get("content_dependencies", [])))
        obligations = []
        for owner in self.snapshot["structure"]["spine"].get("obligation_owners", []):
            if path in owner.get("paths", []):
                obligations.append(deepcopy(owner))

        relevant_sources = []
        for source_path, content in sorted((originals or {}).items()):
            if source_path == path or source_path in dependencies:
                if not isinstance(content, str):
                    raise ContractError("Původní obsah musí být přesný text.")
                relevant_sources.append({
                    "path": source_path,
                    "content": content,
                    "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                    "reason": (
                        "Původní měněný soubor"
                        if source_path == path
                        else "Přímá zdrojová závislost"
                    ),
                })
        if file.get("action") == "modify" and path not in (originals or {}):
            raise ContractError(f"{path}: chybí původní obsah měněného souboru.")

        verified = []
        for dependency in content_dependencies:
            artifact = (verified_artifacts or {}).get(dependency)
            if artifact is None:
                raise ContractError(
                    f"{path}: content dependency {dependency} nemá ověřený provider artefakt."
                )
            if artifact.get("validation_status") != "verified":
                raise ContractError(
                    f"{path}: content dependency {dependency} není na požadované ověřovací úrovni."
                )
            if artifact.get("contract_hash") != self.provider_hash(dependency):
                raise ContractError(
                    f"{path}: content dependency {dependency} má zastaralý contract hash."
                )
            content = artifact.get("content")
            if not isinstance(content, str) or hashlib.sha256(content.encode("utf-8")).hexdigest() != artifact.get("output_hash"):
                raise ContractError(f"{path}: neplatný hash dependency artefaktu {dependency}.")
            verified.append({**artifact, "path": dependency})

        implementation_contract = deepcopy(spec)
        implementation_contract["expected_output_tokens"] = int(
            implementation_contract.get("expected_visible_tokens") or 0
        )
        context = {
            "contract_version": 3,
            "target_file": deepcopy(file),
            "implementation_contract": implementation_contract,
            "relevant_requirements": sorted(
                selected_requirements, key=lambda row: row["id"]
            ),
            "architecture_contracts": components,
            "applicable_invariants": sorted(
                obligations, key=lambda row: row["obligation_id"]
            ),
            "shared_type_contracts": shared_interfaces,
            "dependency_contracts": [
                {
                    "path": dependency,
                    "provides": self.files[dependency].get("provides", []),
                    "interfaces": [
                        row for row in self.interfaces
                        if row["id"] in set(self.files[dependency].get("provides", []))
                    ],
                }
                for dependency in dependencies
            ],
            "relevant_source_excerpts": relevant_sources,
            "verified_dependency_artifacts": verified,
            "cycle_contracts": [],
        }
        context["context_provenance"] = [
            {
                "component": key,
                "value_hash": content_hash(value),
                "source": "IMPLEMENTATION_GRAPH_V3",
                "reason": "Explicitní vazba z validovaného SPINE/DETAIL grafu.",
            }
            for key, value in context.items()
            if key != "context_provenance"
        ]
        return {
            "source_snapshot_hash": self.snapshot_hash,
            "file_context_hash": content_hash(context),
            "contract_hash": content_hash(spec),
            "dependency_hashes": {
                dependency: self.provider_hash(dependency)
                for dependency in dependencies
            },
            "working_context": deepcopy(context),
        }

    def compile(self, path, *, originals=None, verified_artifacts=None):
        if self.is_v3:
            return self._compile_v3(
                path,
                originals=originals,
                verified_artifacts=verified_artifacts,
            )
        if path not in self.files:
            raise ContractError(f"Neznámý cílový soubor {path}.")
        file = self.files[path]
        dependencies = sorted(set(file.get("dependencies", [])))
        requirements = self.snapshot.get("requirements") or {}
        all_requirements = [r for k, values in requirements.items() if k.endswith("requirements")
                            and isinstance(values, list) for r in values]
        selected_requirements = [r for r in all_requirements if r["id"] in file.get("requirement_ids", [])]
        if {r["id"] for r in selected_requirements} != set(file.get("requirement_ids", [])):
            raise ContractError(f"{path}: neznámý požadavek.")
        architecture = [a for a in (self.snapshot.get("plan") or {}).get("architecture_items", [])
                        if a["id"] in file.get("architecture_item_ids", [])]
        if {a["id"] for a in architecture} != set(file.get("architecture_item_ids", [])):
            raise ContractError(f"{path}: neznámá architektura.")
        obligations = global_obligations(self.snapshot)
        scoped = [{"source": s["source"], "reason": s["reason"], "value": obligations[s["source"]]}
                  for s in self.implementation["scopes"] if path in s["paths"]]
        symbols = set(file.get("provides", []) + file.get("requires", []))
        context = {
            "contract_version": 1, "target_file": file,
            "implementation_contract": self.contracts[path],
            "relevant_requirements": sorted(selected_requirements, key=lambda r: r["id"]),
            "architecture_contracts": sorted(architecture, key=lambda a: a["id"]),
            "applicable_invariants": sorted(scoped, key=lambda s: s["source"]),
            "shared_type_contracts": sorted([i for i in self.implementation["interfaces"] if i["id"] in symbols], key=lambda i: i["id"]),
            "dependency_contracts": [{"path": p, "provides": self.files[p].get("provides", []),
                "interfaces": [i for i in self.implementation["interfaces"]
                               if i["id"] in symbols & set(self.files[p].get("provides", []))]}
                for p in dependencies],
            "relevant_source_excerpts": [], "verified_dependency_artifacts": [],
            "cycle_contracts": [g for g in self.graph["cycles"] if path in g],
        }
        for p, content in sorted((originals or {}).items()):
            if p == path or p in dependencies:
                if not isinstance(content, str):
                    raise ContractError("Původní obsah musí být přesný text.")
                context["relevant_source_excerpts"].append({"path": p, "content": content,
                    "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                    "reason": "Původní měněný soubor" if p == path else "Přímá zdrojová závislost"})
        if file.get("action") == "modify" and path not in (originals or {}):
            raise ContractError(f"{path}: chybí původní obsah měněného souboru.")
        for p in dependencies:
            artifact = (verified_artifacts or {}).get(p)
            if artifact is None:
                continue
            if artifact.get("validation_status") != "verified" or artifact.get("contract_hash") != self.provider_hash(p):
                raise ContractError(f"{p}: neověřený nebo zastaralý dependency artefakt.")
            if hashlib.sha256(artifact["content"].encode("utf-8")).hexdigest() != artifact.get("output_hash"):
                raise ContractError(f"{p}: neplatný hash dependency artefaktu.")
            context["verified_dependency_artifacts"].append({**artifact, "path": p})
        provenance = []
        origins = {
            "target_file": (f"structure/files[path={path}]", "Kontrakt cílového souboru"),
            "implementation_contract": (f"structure/implementation/files[path={path}]", "Samostatná implementační povinnost cíle"),
            "relevant_requirements": ("requirements/*requirements[id]", "Výslovná vazba target_file.requirement_ids"),
            "architecture_contracts": ("plan/architecture_items[id]", "Výslovná vazba target_file.architecture_item_ids"),
            "applicable_invariants": ("structure/implementation/scopes[source]", "Cesta je výslovně uvedena v působnosti; jednotlivé důvody jsou u položek"),
            "shared_type_contracts": ("structure/implementation/interfaces[id]", "Symbol je poskytován nebo vyžadován cílem"),
            "dependency_contracts": ("structure/files[path]/provides + implementation/interfaces[id]", "Přímá závislost cíle a jeho využívané symboly"),
            "relevant_source_excerpts": ("originals[path]", "Přesný původní cíl nebo přímá závislost; integritu dokládá SHA-256"),
            "verified_dependency_artifacts": ("verified_artifacts[path]", "Ověřený provider přímé závislosti se shodným kontraktem"),
            "cycle_contracts": ("structure/files[path]/dependencies", "SCC zahrnující cílový soubor"),
        }
        for key, (source, reason) in origins.items():
            provenance.append({"component": key, "value_hash": content_hash(context[key]),
                               "source": source, "reason": reason})
        context["context_provenance"] = provenance
        # Auditní otisk nesmí invalidovat consumer při nesouvisející změně projektu.
        return {"source_snapshot_hash": self.snapshot_hash, "file_context_hash": content_hash(context),
                "contract_hash": content_hash(self.contracts[path]),
                "dependency_hashes": {p: self.provider_hash(p) for p in dependencies},
                "working_context": deepcopy(context)}

    def provider_hash(self, path):
        symbols = set(self.files[path].get("provides", []))
        interfaces = self.interfaces if self.is_v3 else self.implementation["interfaces"]
        return content_hash({
            "path": path,
            "provides": sorted(symbols),
            "interfaces": sorted(
                [row for row in interfaces if row["id"] in symbols],
                key=lambda row: row["id"],
            ),
        })

    def invalidated(self, previous, *, originals=None, previous_originals=None):
        """Přímé změny kontraktů a tranzitivní konzumenti; bez nesouvisejících souborů."""
        changed = {p for p in self.files if p not in previous.files or
                   self.compile(p, originals=originals)["file_context_hash"] !=
                   previous.compile(p, originals=previous_originals)["file_context_hash"]}
        removed = set(previous.files) - self.files.keys()
        pending = changed | removed
        while pending:
            consumers = {p for p, f in self.files.items() if set(f.get("dependencies", [])) & pending} - changed
            changed.update(consumers)
            pending = consumers
        return sorted(changed)
