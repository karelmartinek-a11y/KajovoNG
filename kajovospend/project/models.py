from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class ProjectStatus:
    path: Path | None
    is_connected: bool
    name: str = 'Projekt nepřipojen'
    message: str = 'Projekt není připojen.'
    queue_size: int = 0
    last_success: str = '—'
    last_error: str = '—'
    processing_running: bool = False
    input_dir_status: str = 'Nevybrán'
