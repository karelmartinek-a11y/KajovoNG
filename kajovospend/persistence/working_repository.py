from __future__ import annotations

from pathlib import Path

from kajovospend.persistence.repository import ProjectRepository


class WorkingRepository(ProjectRepository):
    def __init__(self, project_path: Path):
        super().__init__(project_path)
