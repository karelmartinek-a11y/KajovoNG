"""Qt-nezávislé doménové kontrakty a orchestrace běhů KájovoNG.

Moduly v tomto balíčku nesmějí importovat PySide6 ani UI vrstvy. Přesun z
legacy `core.pipeline` probíhá inkrementálně a po každém kroku musí zůstat
zachováno původní runtime chování.
"""

from .config import UiRunConfig

__all__ = ["UiRunConfig"]
