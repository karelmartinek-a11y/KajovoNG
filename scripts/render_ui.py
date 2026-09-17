#!/usr/bin/env python3
"""Kompatibilní vstup pro snímkování UI.

Kompatibilní renderer používá jedinou produkční implementaci Studio UI.
Všechny argumenty se beze změny předávají kanonickému produkčnímu rendereru
`scripts/render_studio.py`, takže staré automatizace dál fungují a současně
ověřují skutečné `kajovo.studio` UI.
"""

from __future__ import annotations

from render_studio import main


if __name__ == "__main__":
    main()
