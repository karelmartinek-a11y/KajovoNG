from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            'schema_version': 2,
            'timestamp': datetime.now(UTC).isoformat(),
            'level': record.levelname,
            'logger': record.name,
            'message': record.getMessage(),
            'event_name': getattr(record, 'event_name', record.msg),
        }
        for field in (
            'correlation_id',
            'request_id',
            'project_path',
            'project_id',
            'document_id',
            'file_id',
            'attempt_id',
            'app_slug',
            'app_version',
            'environment',
            'duration_ms',
            'status_code',
            'endpoint',
            'model',
            'request_fingerprint',
            'phase',
            'result_code',
        ):
            value = getattr(record, field, None)
            if value not in (None, ''):
                payload[field] = value
        extras = getattr(record, 'payload', None)
        if isinstance(extras, dict) and extras:
            payload['payload'] = extras
        if record.exc_info:
            payload['exception'] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(log_dir: Path | None = None) -> None:
    formatter = JsonFormatter()
    handlers: list[logging.Handler] = []
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    handlers.append(stream_handler)
    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_dir / 'kajovospend.log', encoding='utf-8')
        file_handler.setFormatter(formatter)
        handlers.append(file_handler)
    logging.basicConfig(level=logging.INFO, handlers=handlers, force=True)
    logging.getLogger('pypdf').setLevel(logging.ERROR)
