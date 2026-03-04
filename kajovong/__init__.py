"""
Spouštěcí balíček pro běh `python -m kajovong`.

Pouze přesměrovává na hlavní vstup aplikace v modulu `kajovo.app.main`.
"""

from kajovo.app.main import main  # re-export pro konzistentní API

__all__ = ["main"]
