from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_config_dir, user_log_dir

from kajovospend.app.constants import APP_NAME, APP_SLUG, APP_VERSION


@dataclass(slots=True)
class AppConfig:
    app_name: str
    slug: str
    version: str
    organization: str
    environment: str
    settings_file: Path
    log_dir: Path

    @classmethod
    def load(cls) -> 'AppConfig':
        config_dir = Path(user_config_dir(APP_NAME, 'OpenAI'))
        log_dir = Path(user_log_dir(APP_NAME, 'OpenAI'))
        config_dir.mkdir(parents=True, exist_ok=True)
        log_dir.mkdir(parents=True, exist_ok=True)
        return cls(
            app_name=APP_NAME,
            slug=APP_SLUG,
            version=APP_VERSION,
            organization='OpenAI',
            environment='production',
            settings_file=config_dir / f'{APP_SLUG}.json',
            log_dir=log_dir,
        )
