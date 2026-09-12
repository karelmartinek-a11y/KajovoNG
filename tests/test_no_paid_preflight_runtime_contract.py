"""Repo-wide invariant: runtime nesmí obsahovat aktivní placený preflight/probe workflow."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "kajovo"

# Historické preflight_batches smí být jen pasivně čitelné pro staré LOGy.
LEGACY_READ_ONLY_FILES = {
    "core/batch_completion.py",
    "desktop/application.py",
    "desktop/batch_view.py",
    "desktop/batches.py",
}


def _runtime_sources():
    for path in sorted(RUNTIME.rglob("*.py")):
        yield path, path.read_text(encoding="utf-8")


def test_active_preflight_api_is_absent_from_runtime():
    forbidden = {
        "PreflightPending",
        "preflight_run(",
        "preflight_response(",
        "record_preflight_batch",
        "continue_preflight",
        "can_continue_preflight(",
        "on_preflight_batch",
        "on_preflight_progress",
        "trial_live",
        "kajovong_preflight",
        "PREFLIGHT_BATCH_",
        "preflight_pending",
    }
    hits = []
    for path, source in _runtime_sources():
        rel = path.relative_to(RUNTIME).as_posix()
        for marker in forbidden:
            if marker in source:
                hits.append(f"{rel}: {marker}")
    assert hits == [], "Aktivní preflight/probe kontrakt se vrátil do runtime:\n" + "\n".join(hits)


def test_legacy_preflight_state_is_read_only_and_whitelisted():
    hits = set()
    for path, source in _runtime_sources():
        if "preflight_batches" in source or "preflight_ids" in source or "historický preflight" in source.lower():
            hits.add(path.relative_to(RUNTIME).as_posix())
    assert hits <= LEGACY_READ_ONLY_FILES, (
        "Historická preflight data unikla mimo read-only kompatibilní vrstvu: "
        + ", ".join(sorted(hits - LEGACY_READ_ONLY_FILES))
    )


def test_runtime_has_no_paid_validation_transport_markers():
    policy = (RUNTIME / "core" / "response_policy.py").read_text(encoding="utf-8")
    assert "_send_response(" not in policy
    assert 'POST", "/batches"' not in policy
    assert "POST', '/batches'" not in policy

    client = (RUNTIME / "core" / "openai_client.py").read_text(encoding="utf-8")
    # Generativní transporty smějí existovat pouze jako skutečné pracovní transporty,
    # ne v pomocných validačních metodách.
    assert "def validate_prepared_payload" in client
    assert "def prepare_run_validation" in client
    assert "def preflight_" not in client


def test_paid_live_probe_scripts_do_not_exist():
    forbidden = {
        "verify_openai_live.py",
        "verify_batch_live.py",
        "verify_generate_batch_live.py",
        "verify_response_contracts_live.py",
        "verify_workflows_live.py",
    }
    scripts = ROOT / "scripts"
    assert forbidden.isdisjoint({path.name for path in scripts.glob("*.py")})
