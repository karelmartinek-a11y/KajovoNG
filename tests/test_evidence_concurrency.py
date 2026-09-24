"""Sdílená evidence a odvozená historie bez síťových služeb."""
import json
import multiprocessing
import os
from concurrent.futures import ThreadPoolExecutor

from kajovo.core.run_bundle import HistoryIndex, RunBundle


def _append_events(root, count):
    bundle = RunBundle(root, "RUN_EVENTS")
    for index in range(count):
        bundle.append_event("worker", {"index": index})


def test_sequence_survives_interleaved_bundle_instances(tmp_path):
    first = RunBundle(tmp_path / "RUN_EVENTS", "RUN_EVENTS", create=True)
    second = RunBundle(first.root, first.run_id)
    records = [first.append_event("first"), second.append_event("second"), first.append_event("third")]
    sequences = [item["sequence"] for item in records]
    assert sequences == list(range(sequences[0], sequences[0] + 3))


def test_sequence_serializes_processes_and_threads(tmp_path):
    bundle = RunBundle(tmp_path / "RUN_EVENTS", "RUN_EVENTS", create=True)
    context = multiprocessing.get_context("spawn")
    processes = [context.Process(target=_append_events, args=(bundle.root, 10)) for _ in range(2)]
    for process in processes:
        process.start()
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: _append_events(bundle.root, 10), range(2)))
    for process in processes:
        process.join(30)
        if process.is_alive():
            process.terminate()
            process.join()
        assert process.exitcode == 0
    records = [json.loads(line) for line in bundle.events_path.read_text(encoding="utf-8").splitlines()]
    sequences = [record["sequence"] for record in records]
    assert sequences == list(range(1, len(records) + 1))
    assert sum(record["type"] == "worker" for record in records) == 40


def test_transport_poll_preserves_work_step_parameters(tmp_path):
    bundle = RunBundle(tmp_path / "RUN_EVENTS", "RUN_EVENTS", create=True)
    step = bundle.ensure_step("A3", kind="api")
    bundle.record_request({"model": "gpt-5.6-luna", "reasoning": {"effort": "high"}}, step_id=step["step_id"])
    bundle.record_request({"method": "GET", "json_body": None}, method="GET", request_role="transport", step_id=step["step_id"])
    stored = next(row for row in bundle.steps() if row["step_id"] == step["step_id"])
    assert stored["model"] == "gpt-5.6-luna"
    assert stored["reasoning_effort"] == "high"
    assert len(stored["request_ids"]) == 2


def test_history_refresh_detects_nested_rewrite_and_keeps_unchanged_index(tmp_path):
    bundle = RunBundle(tmp_path / "RUN_EVENTS", "RUN_EVENTS", create=True)
    response = bundle.responses_dir / "_record_answer.json"
    response.write_text('{"response_record_id":"record_test","response_id":"resp_first","output_text":"first"}', encoding="utf-8")
    index = HistoryIndex(tmp_path)
    first = index.refresh()
    index_time = index.path.stat().st_mtime_ns
    assert index.refresh() == first
    assert index.path.stat().st_mtime_ns == index_time
    folder_stat = bundle.responses_dir.stat()
    response.write_text('{"response_record_id":"record_test","response_id":"resp_second","output_text":"second-longer"}', encoding="utf-8")
    os.utime(bundle.responses_dir, ns=(folder_stat.st_atime_ns, folder_stat.st_mtime_ns))
    second = index.refresh()
    assert first[0]["source_signature"] != second[0]["source_signature"]
    assert "resp_second" in second[0]["response_ids"]


def test_empty_legacy_index_is_rebuilt_once(tmp_path):
    index = HistoryIndex(tmp_path)
    index.path.write_text('{"schema_version":4,"runs":{}}', encoding="utf-8")
    assert index.refresh() == []
    assert json.loads(index.path.read_text(encoding="utf-8"))["schema_version"] == 5
    stamp = index.path.stat().st_mtime_ns
    assert index.refresh() == []
    assert index.path.stat().st_mtime_ns == stamp
