from __future__ import annotations

import json
import logging

from kajovospend.diagnostics.logging_setup import JsonFormatter


def test_json_formatter_emits_forensic_fields() -> None:
    record = logging.LogRecord('kajovospend', logging.INFO, __file__, 1, 'message', (), None)
    record.event_name = 'openai.call_completed'
    record.correlation_id = 'corr-1'
    record.request_id = 'req-1'
    record.document_id = 10
    record.file_id = 20
    record.endpoint = 'https://api.openai.com/v1/responses'
    record.model = 'gpt-4.1-mini'
    record.request_fingerprint = 'abc123'
    record.duration_ms = 512
    record.payload = {'phase': 'openai'}
    payload = json.loads(JsonFormatter().format(record))
    assert payload['schema_version'] == 2
    assert payload['correlation_id'] == 'corr-1'
    assert payload['request_id'] == 'req-1'
    assert payload['endpoint'] == 'https://api.openai.com/v1/responses'
    assert payload['request_fingerprint'] == 'abc123'
    assert payload['payload']['phase'] == 'openai'



def test_project_decisions_log_uses_schema_v2_and_top_level_forensic_fields(tmp_path) -> None:
    from pathlib import Path
    import json as _json
    from kajovospend.project.project_service import ProjectService
    from kajovospend.persistence.repository import ProjectRepository

    project_root = ProjectService().create_project(tmp_path / 'project', 'Logs Project')
    repo = ProjectRepository(project_root)
    repo.log_event('info', 'openai.call_completed', 'done', 'corr-1', file_id=1, document_id=2, attempt_id=3, payload={'endpoint': 'https://api.openai.com/v1/responses', 'model': 'gpt-4.1-mini', 'request_fingerprint': 'abc123', 'duration_ms': 50, 'status_code': 200, 'source_sha256': 'deadbeef'})
    entry = _json.loads((project_root / 'logs' / 'decisions.jsonl').read_text(encoding='utf-8').splitlines()[-1])
    assert entry['schema_version'] == 2
    assert entry['event_name'] == 'openai.call_completed'
    assert entry['project_path'] == str(Path(project_root))
    assert entry['endpoint'] == 'https://api.openai.com/v1/responses'
    assert entry['request_fingerprint'] == 'abc123'
