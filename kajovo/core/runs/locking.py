from __future__ import annotations

import os
from pathlib import Path
from typing import BinaryIO


class ExecutionLock:
    """Nečekající procesní zámek jednoho běhu bez závislosti na Qt."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._stream: BinaryIO | None = None

    def acquire(self) -> bool:
        if self._stream is not None:
            return True
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            stream = self.path.open("a+b")
        except OSError:
            return False
        try:
            if os.name == "nt":
                import msvcrt

                stream.seek(0, os.SEEK_END)
                if stream.tell() == 0:
                    stream.write(b"\0")
                    stream.flush()
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            stream.close()
            return False
        self._stream = stream
        return True

    def release(self) -> None:
        stream = self._stream
        if stream is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        finally:
            stream.close()
            self._stream = None

    def __enter__(self) -> ExecutionLock:
        if not self.acquire():
            raise BlockingIOError(f"Zámek je již obsazen: {self.path}")
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()
