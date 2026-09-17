"""Qt-free kompatibilní fasáda kanonické run orchestrace."""

from .runs.config import UiRunConfig
from .runs.executor import RunExecutor, split_text

__all__ = ["RunExecutor", "UiRunConfig", "split_text"]
