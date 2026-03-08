"""
Entry point to run the Kajovo desktop app via ``python -m kajovong``.

This delegates to ``kajovo.app.main.main`` to keep a single canonical
launcher while offering the short module name the user requested.
"""

from kajovo.app.main import main


if __name__ == "__main__":
    main()
