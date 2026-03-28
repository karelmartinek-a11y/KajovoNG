from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path


@dataclass
class AppSettings:
    last_project_path: str = ''
    input_directory: str = ''
    output_directory: str = ''
    openai_enabled: bool = False
    openai_model: str = ''
    openai_api_key_masked: str = ''  # legacy compatibility field; UI now reads the real key from SecretStore
    openai_usage_policy: str = 'manual_only'
    automatic_retry_limit: int = 2
    manual_retry_limit: int = 2
    openai_retry_limit: int = 1
    block_without_ares: bool = True
    quarantine_duplicate: bool = True
    quarantine_missing_identification: bool = True
    allow_manual_openai_retry: bool = True
    require_valid_input_directory: bool = True
    pattern_match_fields: list[str] = field(default_factory=lambda: ['ico', 'anchor_text'])
    reduced_motion: bool = False
    confirm_destructive_actions: bool = True


class SettingsStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path

    def load(self) -> AppSettings:
        if not self.path.exists():
            return self._normalize(AppSettings())
        data = json.loads(self.path.read_text(encoding='utf-8'))
        allowed = {item.name for item in fields(AppSettings)}
        normalized = {key: value for key, value in data.items() if key in allowed}
        return self._normalize(AppSettings(**normalized))

    def save(self, settings: AppSettings) -> None:
        self.path.write_text(json.dumps(asdict(settings), indent=2, ensure_ascii=False), encoding='utf-8')

    def _normalize(self, settings: AppSettings) -> AppSettings:
        settings.openai_usage_policy = (
            settings.openai_usage_policy
            if settings.openai_usage_policy in {'manual_only', 'openai_only'}
            else 'manual_only'
        )
        settings.automatic_retry_limit = max(1, int(settings.automatic_retry_limit or 0))
        settings.manual_retry_limit = max(1, int(settings.manual_retry_limit or 0))
        settings.openai_retry_limit = max(1, int(settings.openai_retry_limit or 0))
        settings.pattern_match_fields = [item for item in settings.pattern_match_fields if str(item).strip()] or ['ico', 'anchor_text']
        return settings
