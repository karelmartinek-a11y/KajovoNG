from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from kajovospend.app.constants import PROD_DB_NAME, PROJECT_MARKER, WORK_DB_NAME


class ProjectPathError(ValueError):
    pass


@dataclass(slots=True)
class ProjectDatabasePaths:
    project_root: Path
    working_db: Path
    production_db: Path

    @classmethod
    def from_project_root(cls, project_root: str | Path) -> "ProjectDatabasePaths":
        root = Path(project_root).expanduser().resolve()
        marker = root / PROJECT_MARKER
        if marker.exists():
            import json

            data = json.loads(marker.read_text(encoding='utf-8'))
            db_paths = data.get('database_paths') or {}
            working = Path(db_paths.get('working', WORK_DB_NAME))
            production = Path(db_paths.get('production', PROD_DB_NAME))
        else:
            working = Path(WORK_DB_NAME)
            production = Path(PROD_DB_NAME)
        working = working if working.is_absolute() else (root / working)
        production = production if production.is_absolute() else (root / production)
        paths = cls(root, working.resolve(), production.resolve())
        paths.ensure_distinct()
        return paths

    def ensure_distinct(self) -> None:
        left = self.working_db.expanduser().resolve()
        right = self.production_db.expanduser().resolve()
        if left == right:
            raise ProjectPathError('Pracovní a produkční databáze nesmí ukazovat na stejný fyzický soubor.')
        if left.exists() and right.exists():
            try:
                if os.path.samefile(left, right):
                    raise ProjectPathError('Pracovní a produkční databáze jsou fyzicky totožné.')
            except FileNotFoundError:
                pass

    def create_parents(self) -> None:
        self.working_db.parent.mkdir(parents=True, exist_ok=True)
        self.production_db.parent.mkdir(parents=True, exist_ok=True)


class WorkingConnectionFactory:
    def __init__(self, paths: ProjectDatabasePaths) -> None:
        self.paths = paths

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.paths.working_db)
        conn.row_factory = sqlite3.Row
        self._configure(conn)
        return conn

    def _configure(self, conn: sqlite3.Connection) -> None:
        conn.execute('pragma foreign_keys=on')
        conn.execute('pragma journal_mode=wal')
        conn.execute('pragma synchronous=normal')
        conn.execute('pragma busy_timeout=5000')
        conn.execute('pragma temp_store=memory')
        conn.execute('pragma cache_size=-20000')


class ProductionConnectionFactory:
    def __init__(self, paths: ProjectDatabasePaths) -> None:
        self.paths = paths

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.paths.production_db)
        conn.row_factory = sqlite3.Row
        self._configure(conn)
        return conn

    def _configure(self, conn: sqlite3.Connection) -> None:
        conn.execute('pragma foreign_keys=on')
        conn.execute('pragma journal_mode=wal')
        conn.execute('pragma synchronous=normal')
        conn.execute('pragma busy_timeout=5000')
        conn.execute('pragma temp_store=memory')
        conn.execute('pragma cache_size=-20000')
