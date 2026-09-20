"""Offline safety contracts for explicit live acceptance."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import live_acceptance


def test_gate_without_confirmation_blocks_before_key_access(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "secret-must-not-be-read")
    with pytest.raises(live_acceptance.Blocked, match="BLOCKED_AUTHORIZATION"):
        live_acceptance.safety_gate(False, live_acceptance.AUTHORIZATION)


def test_gate_rejects_wrong_authorization(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    with pytest.raises(live_acceptance.Blocked, match="BLOCKED_AUTHORIZATION"):
        live_acceptance.safety_gate(True, "wrong")


def test_gate_rejects_missing_api_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(live_acceptance.Blocked, match="BLOCKED_API_KEY"):
        live_acceptance.safety_gate(True, live_acceptance.AUTHORIZATION)


def test_case_order_is_normative():
    assert live_acceptance.CASES == (
        "generate-live",
        "generate-batch",
        "photo-batch",
        "comic-panels",
    )


def test_image_model_selection_is_fixed_to_allowed_candidates(monkeypatch):
    monkeypatch.setattr(
        "kajovo.core.photo_batch.image_edit_model_ids",
        lambda available: ["gpt-image-2.5-sunburst-2026-09-08"],
    )
    assert (
        live_acceptance.choose_image_model([])
        == "gpt-image-2.5-sunburst-2026-09-08"
    )
    monkeypatch.setattr(
        "kajovo.core.photo_batch.image_edit_model_ids",
        lambda available: [],
    )
    with pytest.raises(
        live_acceptance.Blocked,
        match="BLOCKED_IMAGE_MODEL_UNAVAILABLE",
    ):
        live_acceptance.choose_image_model(["gpt-image-2.5-terra"])


def test_summary_never_serializes_api_key(tmp_path: Path):
    report = live_acceptance._base_report(
        "generate-live", "sha", live_acceptance.MODEL
    )
    live_acceptance.write_evidence(tmp_path, [report], "sha")
    data = json.dumps(
        json.loads(
            (tmp_path / "live_acceptance_report.json").read_text(
                encoding="utf-8"
            )
        )
    )
    assert "OPENAI_API_KEY" not in data
    assert "secret" not in data


def test_raw_usage_is_archived_without_derived_value(tmp_path):
    evidence = tmp_path / "PHOTO" / "job-1"
    evidence.mkdir(parents=True)
    payload = {
        "provider_item_id": "batch-1:photo-1",
        "usage": {
            "input_tokens_details": {"text_tokens": 1, "image_tokens": 1},
            "output_tokens": 1,
        },
    }
    (evidence / "usage.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )
    (evidence / "usage-copy.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )
    report = live_acceptance._base_report(
        "photo-batch", "sha", "gpt-image-2.5-flare"
    )
    live_acceptance.collect_raw_usage(evidence, report)
    live_acceptance.collect_raw_usage(evidence, report)
    assert len(report["raw_usage"]) == 1
    assert report["raw_usage"][0]["usage"] == payload["usage"]
    assert set(report["raw_usage"][0]) == {"provider_item_id", "usage"}


def test_photo_pending_resume_does_not_submit_twice(tmp_path, monkeypatch):
    jobs = {}
    calls = {"prepare": 0}
    monkeypatch.setattr(
        live_acceptance,
        "_synthetic_png",
        lambda path: path.write_bytes(b"png"),
    )
    monkeypatch.setattr(
        "kajovo.core.photo_batch.image_edit_model_ids",
        lambda available: ["gpt-image-2.5-flare"],
    )

    def new_job(**kwargs):
        job = SimpleNamespace(
            job_id="job-1",
            batch_id="",
            input_file_id="",
            status="preparing",
            image_model=kwargs["image_model"],
            items=[SimpleNamespace(uploaded_file_id="source-1")],
        )
        jobs[job.job_id] = job
        return job

    def prepare(client, job, log_dir):
        calls["prepare"] += 1
        job.batch_id = "batch-1"
        job.input_file_id = "batch-input-1"
        job.status = "in_progress"
        return job

    monkeypatch.setattr("kajovo.core.photo_batch.new_job", new_job)
    monkeypatch.setattr(
        "kajovo.core.photo_batch.prepare_and_submit",
        prepare,
    )
    monkeypatch.setattr(
        "kajovo.core.photo_batch.load_jobs",
        lambda log_dir: list(jobs.values()),
    )
    monkeypatch.setattr(
        "kajovo.core.photo_batch.refresh_job",
        lambda client, job, log_dir: job,
    )
    monkeypatch.setattr(
        "kajovo.core.photo_batch.download_results",
        lambda client, job, log_dir: job,
    )
    settings = SimpleNamespace(log_dir=str(tmp_path / "LOG"))
    for _ in range(2):
        with pytest.raises(live_acceptance.RemotePending):
            live_acceptance.run_photo(
                tmp_path,
                object(),
                settings,
                "sha",
                ["gpt-image-2.5-flare"],
            )
    assert calls["prepare"] == 1
    state = json.loads(
        (tmp_path / "photo-batch" / "acceptance_state.json").read_text(
            encoding="utf-8"
        )
    )
    assert state["batch_id"] == "batch-1"


def test_photo_submission_unknown_resume_never_prepares_again(
    tmp_path, monkeypatch
):
    from kajovo.core import photo_batch

    source = tmp_path / "photo-batch" / "source.png"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"png")
    state_path = source.parent / "acceptance_state.json"
    prompt = "Change the red square to blue. Preserve the rest of the image."
    state_path.write_text(
        json.dumps(
            {
                "case": "photo-batch",
                "job_id": "job-unknown",
                "batch_id": "",
                "input_file_id": "input-1",
                "source_sha256": live_acceptance.sha256_file(source),
                "model": "gpt-image-2.5-flare",
                "prompt_hash": live_acceptance.hashlib.sha256(
                    prompt.encode()
                ).hexdigest(),
                "status": "submission_unknown",
            }
        ),
        encoding="utf-8",
    )
    job = SimpleNamespace(
        job_id="job-unknown",
        batch_id="",
        input_file_id="input-1",
        status="submission_unknown",
        image_model="gpt-image-2.5-flare",
        items=[],
    )
    calls = {"prepare": 0}
    monkeypatch.setattr(
        live_acceptance,
        "_synthetic_png",
        lambda path: path.write_bytes(b"png"),
    )
    monkeypatch.setattr(
        "kajovo.core.photo_batch.image_edit_model_ids",
        lambda available: ["gpt-image-2.5-flare"],
    )
    monkeypatch.setattr(photo_batch, "load_jobs", lambda log_dir: [job])
    monkeypatch.setattr(
        photo_batch,
        "prepare_and_submit",
        lambda *args: calls.__setitem__("prepare", calls["prepare"] + 1),
    )
    monkeypatch.setattr(
        photo_batch,
        "refresh_job",
        lambda client, value, log_dir: (
            _ for _ in ()
        ).throw(live_acceptance.RemotePending("unknown")),
    )
    with pytest.raises(live_acceptance.RemotePending):
        live_acceptance.run_photo(
            tmp_path,
            object(),
            SimpleNamespace(log_dir=str(tmp_path / "LOG")),
            "sha",
            ["gpt-image-2.5-flare"],
        )
    assert calls["prepare"] == 0


def test_cleanup_skips_pending_and_removes_completed_inputs():
    class Client:
        def __init__(self):
            self.deleted = []

        def delete_file(self, file_id):
            self.deleted.append(file_id)

    client = Client()
    live_acceptance.cleanup_remote_inputs(
        client,
        [
            {"status": "pending", "input_file_ids": ["no"]},
            {
                "status": "technical_pass_human_pending",
                "input_file_ids": ["source", "batch"],
            },
        ],
    )
    assert client.deleted == ["source", "batch"]


def test_comic_exact_snapshot_preflight_blocks_alias_before_service(
    monkeypatch, tmp_path
):
    created = []
    monkeypatch.setattr(
        "kajovo.core.comic_service.ComicService",
        lambda *args: created.append(args),
    )
    with pytest.raises(
        live_acceptance.Blocked,
        match="BLOCKED_IMAGE_MODEL_UNAVAILABLE",
    ):
        live_acceptance.run_comic(
            tmp_path,
            object(),
            SimpleNamespace(),
            "sha",
            ["gpt-image-2.5-sunburst"],
        )
    assert created == []


def test_comic_bible_pending_resume_reuses_operation(tmp_path, monkeypatch):
    class Store:
        statuses = {"bible-1": "preparing", "panel-op-1": "preparing"}

        def project(self, *args):
            return "project-1"

        def get(self, table, identifier):
            if table == "operations":
                return {"status": self.statuses[identifier]}
            raise AssertionError((table, identifier))

        def panel(self, project, name):
            return "panel-1"

        def save_panel(self, *args):
            return None

    class Service:
        starts_total = 0
        bible_runs_total = 0

        def __init__(self, settings, client):
            self.store = Store()

        def start_bible(self, project):
            type(self).starts_total += 1
            return "bible-1"

        def run(self, operation):
            if operation == "bible-1":
                type(self).bible_runs_total += 1
                if type(self).bible_runs_total == 1:
                    self.store.statuses[operation] = "response_pending"
                    return {"status": "response_pending"}
                self.store.statuses[operation] = "completed"
                return {"status": "completed"}
            self.store.statuses[operation] = "batch_pending"
            return {"status": "batch_pending"}

        def start_panels(self, project, panels):
            return "panel-op-1"

    monkeypatch.setattr("kajovo.core.comic_service.ComicService", Service)
    available = ["gpt-image-2.5-sunburst-2026-09-08"]
    for _ in range(2):
        with pytest.raises(live_acceptance.RemotePending):
            live_acceptance.run_comic(
                tmp_path, object(), object(), "sha", available
            )
    assert Service.starts_total == 1
    assert Service.bible_runs_total == 2
    state = json.loads(
        (tmp_path / "comic-panels" / "acceptance_state.json").read_text(
            encoding="utf-8"
        )
    )
    assert state["bible_operation_id"] == "bible-1"


def test_comic_panel_pending_resume_reuses_operation(tmp_path, monkeypatch):
    panel_path = tmp_path / "panel.png"
    panel_path.write_bytes(b"panel")

    class Store:
        statuses = {"bible-1": "completed", "panel-op-1": "preparing"}

        def project(self, *args):
            return "project-1"

        def get(self, table, identifier):
            if table == "operations":
                return {"status": self.statuses[identifier]}
            if table == "panels":
                return {"active_version": "version-1"}
            if table == "panel_versions":
                return {"asset_id": "asset-1"}
            raise AssertionError((table, identifier))

        def panel(self, project, name):
            return "panel-1"

        def save_panel(self, *args):
            return None

        def asset_path(self, asset):
            return panel_path

        def rows(self, table, where, params):
            return [{"provider_id": "batch-1"}]

    class Service:
        panel_starts_total = 0
        panel_runs_total = 0

        def __init__(self, settings, client):
            self.store = Store()

        def start_bible(self, project):
            return "bible-1"

        def run(self, operation):
            if operation == "panel-op-1":
                type(self).panel_runs_total += 1
                if type(self).panel_runs_total == 1:
                    self.store.statuses[operation] = "batch_pending"
                    return {"status": "batch_pending"}
                self.store.statuses[operation] = "completed"
            return {"status": "completed"}

        def start_panels(self, project, panels):
            type(self).panel_starts_total += 1
            return "panel-op-1"

    monkeypatch.setattr("kajovo.core.comic_service.ComicService", Service)
    available = ["gpt-image-2.5-sunburst-2026-09-08"]
    with pytest.raises(live_acceptance.RemotePending):
        live_acceptance.run_comic(
            tmp_path, object(), object(), "sha", available
        )
    result = live_acceptance.run_comic(
        tmp_path, object(), object(), "sha", available
    )
    assert Service.panel_starts_total == 1
    assert Service.panel_runs_total == 2
    assert result["status"] == "technical_pass_human_pending"


def _write_generate_state(
    tmp_path: Path,
    case: str,
    *,
    status: str,
    artifact_name: str | None = None,
    batch_id: str = "",
) -> Path:
    run_dir = live_acceptance.acceptance_run_dir(tmp_path, case)
    run_dir.mkdir(parents=True)
    if artifact_name:
        artifact = (
            run_dir / "staging" / "candidate" / "generated" / artifact_name
        )
        artifact.parent.mkdir(parents=True)
        artifact.write_text(
            (
                "def answer():\n    return 42\n"
                if artifact_name == "answer.py"
                else "def batch_answer():\n    return 7\n"
            ),
            encoding="utf-8",
        )
    state = {"status": status}
    if batch_id:
        state["batch_id"] = batch_id
    (run_dir / "run_state.json").write_text(
        json.dumps(state),
        encoding="utf-8",
    )
    return run_dir


def test_generate_live_completed_rerun_has_zero_new_submits(
    tmp_path, monkeypatch
):
    _write_generate_state(
        tmp_path,
        "generate-live",
        status="files_complete_unverified",
        artifact_name="answer.py",
    )
    calls = {"executor": 0}
    monkeypatch.setattr(
        live_acceptance,
        "_run_executor",
        lambda *args: calls.__setitem__(
            "executor", calls["executor"] + 1
        ),
    )
    settings = SimpleNamespace(log_dir=str(tmp_path / "_runtime" / "LOG"))
    first = live_acceptance.run_generate(
        "generate-live",
        tmp_path,
        object(),
        "key",
        settings,
        "sha",
        False,
    )
    second = live_acceptance.run_generate(
        "generate-live",
        tmp_path,
        object(),
        "key",
        settings,
        "sha",
        False,
    )
    assert first["artifact_sha256"] == second["artifact_sha256"]
    assert calls["executor"] == 0
    assert live_acceptance.case_needs_new_submit(
        tmp_path, "generate-live"
    ) is False


@pytest.mark.parametrize("status", ["response_pending", "submission_unknown"])
def test_generate_live_unresolved_rerun_never_creates_second_response(
    tmp_path, monkeypatch, status
):
    _write_generate_state(tmp_path, "generate-live", status=status)
    calls = {"executor": 0}
    monkeypatch.setattr(
        live_acceptance,
        "_run_executor",
        lambda *args: calls.__setitem__(
            "executor", calls["executor"] + 1
        ),
    )
    settings = SimpleNamespace(log_dir=str(tmp_path / "_runtime" / "LOG"))
    for _ in range(2):
        with pytest.raises(live_acceptance.RemotePending):
            live_acceptance.run_generate(
                "generate-live",
                tmp_path,
                object(),
                "key",
                settings,
                "sha",
                False,
            )
    assert calls["executor"] == 0


def test_generate_batch_completed_rerun_never_creates_second_batch(
    tmp_path, monkeypatch
):
    _write_generate_state(
        tmp_path,
        "generate-batch",
        status="completed",
        artifact_name="batch_answer.py",
        batch_id="batch-1",
    )
    calls = {"executor": 0}
    monkeypatch.setattr(
        live_acceptance,
        "_run_executor",
        lambda *args: calls.__setitem__(
            "executor", calls["executor"] + 1
        ),
    )
    monkeypatch.setattr(
        "kajovo.core.batch_completion.complete_saved_batch",
        lambda *args: (_ for _ in ()).throw(
            AssertionError("batch must not be polled after import")
        ),
    )
    settings = SimpleNamespace(log_dir=str(tmp_path / "_runtime" / "LOG"))
    result = live_acceptance.run_generate(
        "generate-batch",
        tmp_path,
        object(),
        "key",
        settings,
        "sha",
        True,
    )
    again = live_acceptance.run_generate(
        "generate-batch",
        tmp_path,
        object(),
        "key",
        settings,
        "sha",
        True,
    )
    assert result["batch_ids"] == again["batch_ids"] == ["batch-1"]
    assert calls["executor"] == 0


def test_run_config_has_no_economic_fields(tmp_path):
    cfg = live_acceptance.make_run_config(
        batch=False,
        prompt="x",
        out=tmp_path,
    )
    for name in (
        "max_cost_microusd",
        "max_paid_requests",
        "unknown_pricing",
        "max_input_tokens",
        "max_output_tokens",
    ):
        assert not hasattr(cfg, name)


def test_existing_pending_image_can_resume_without_new_submit(tmp_path):
    checkpoint = tmp_path / "photo-batch" / "acceptance_state.json"
    checkpoint.parent.mkdir(parents=True)
    checkpoint.write_text(
        json.dumps(
            {
                "case": "photo-batch",
                "status": "batch_pending",
                "job_id": "job-1",
            }
        ),
        encoding="utf-8",
    )
    assert (
        live_acceptance.case_needs_new_submit(tmp_path, "photo-batch")
        is False
    )


def test_workspace_authorization_mismatch_blocks(tmp_path):
    (tmp_path / "acceptance_state.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "authorization": "wrong",
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(
        live_acceptance.Blocked,
        match="BLOCKED_CHECKPOINT_MISMATCH",
    ):
        live_acceptance.ensure_workspace(tmp_path, "sha")
