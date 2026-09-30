"""Rozvine explicitní ruční mapu na konkrétní API/UI/chat/writer projekce."""

from collections import defaultdict
import json
from pathlib import Path

from verify_recovery import write_once


ROOT = Path(__file__).resolve().parent
EXPOSURES = {"Q": "OWNER_QUERY", "C": "OWNER_COMMAND", "P": "PUBLIC_PROTOCOL", "D": "CATALOG_DISPATCH"}

# Nejde o odvozování side effects z prefixu. Zde se určuje pouze repository home
# jediného canonical handleru; jeho effect/state contract zůstává samostatný.
WRITERS = {
    "operation": "packages/domain/operation-catalog",
    "auth": "packages/domain/owner-identity",
    "ownerApiKey": "packages/domain/owner-api-credential",
    "dashboard": "packages/domain/topology",
    "component": "packages/domain/component",
    "secret": "packages/domain/secret",
    "mcp": "packages/domain/mcp",
    "agent": "packages/domain/agent",
    "chat": "packages/domain/central-chat",
    "ai": "packages/domain/model-call",
    "generation": "packages/domain/generation",
    "browser": "packages/domain/browser",
    "runtime": "packages/domain/runtime",
    "binding": "packages/domain/binding",
    "external": "packages/domain/external-target",
    "monitor": "packages/domain/monitoring",
    "audit": "packages/domain/audit",
    "log": "packages/observability/log-query",
    "configuration": "packages/domain/configuration",
    "deployment": "packages/domain/deployment",
    "backup": "packages/domain/backup",
    "acceptance": "packages/domain/acceptance",
    "system": "packages/domain/system-inspection",
    "selfTest": "packages/domain/self-test",
    "authority": "packages/domain/authority-parent-adapter",
    "provenance": "packages/domain/provenance-parent-adapter",
    "agentic": "packages/domain/agentic-security-parent-adapter",
}


def compile_rows(routes, text):
    sections = defaultdict(list)
    section = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            if section in sections:
                raise ValueError("Duplicitní sekce mapy.")
            sections[section] = []
        else:
            code, operation = line.split()
            if section is None or code not in EXPOSURES or operation.split(".")[0] not in WRITERS:
                raise ValueError("Neznámá operace/exposure/writer v mapě.")
            sections[section].append((code, operation))
    source = defaultdict(list)
    for route in routes:
        source[route["source_section"]].append(route)
    if source.keys() != sections.keys():
        raise ValueError("Sekce mapy neodpovídají source inventory.")
    rows = []
    for section, items in source.items():
        mappings = sections[section]
        if len(items) != len(mappings):
            raise ValueError(f"§{section}: {len(items)} cest, ale {len(mappings)} mapování.")
        for route, (code, operation) in zip(items, mappings, strict=True):
            if route["method"] == "GET" and code not in {"Q", "P"}:
                raise ValueError(f"GET nesmí vytvářet business command: {route['path']}")
            owner_surface = code in {"Q", "C"}
            rows.append({**route, "operation": operation, "exposure": EXPOSURES[code],
                         "canonical_writer": WRITERS[operation.split(".")[0]],
                         "api_operation_id": f"{route['method']} {route['path']}",
                         "ui_surface": f"operation:{operation}" if owner_surface else f"diagnostic:{operation}",
                         "chat_capability": operation if owner_surface else f"inspect:{operation}",
                         "self_test_case": f"parity:{route['method']}:{route['path']}",
                         "authority_source": f"SSOT.original.md#section-{section}"})
    return rows


def main():
    routes = json.loads((ROOT / "api-routes.inventory.json").read_text(encoding="utf-8"))
    rows = compile_rows(routes, (ROOT / "operation-map.txt").read_text(encoding="utf-8"))
    assert len(rows) == 503
    # Sentinel pro hlavní bezpečnostní a alias hranice.
    by_route = {(r["method"], r["path"]): r["operation"] for r in rows}
    for key, expected in {
        ("POST", "/auth/login"): "auth.login",
        ("POST", "/auth/api-key-session"): "ownerApiKey.session.exchange",
        ("GET", "/secrets/:id/value"): "secret.value.read",
        ("POST", "/self-tests/runs/:id/replay"): "selfTest.run.replay",
        ("PUT", "/monitoring/profiles/:componentId"): "monitor.profile.recompute",
        ("WSS", "/browser-sessions/:sessionId/preview/ws"): "browser.preview.stream",
        ("POST", "/operations/:operationKey/invoke"): "operation.catalog.invoke",
    }.items():
        assert by_route[key] == expected
    write_once(ROOT / "operation-map.expanded.json", rows)
    print(f"PASS: {len(rows)} cest má explicitní operaci, writer home a parity IDs.")
    print("Jde o návrhovou projekci, nikoli důkaz implementovaných handlerů nebo §55.6 schemas.")


if __name__ == "__main__":
    main()
