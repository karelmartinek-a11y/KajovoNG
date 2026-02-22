# Contributing

## Setup
1. Create virtual env and install deps: `scripts/install.ps1` or `pip install -r requirements.txt`.
2. Run app: `python -m kajovo.app.main`.

## Local checks
- Tests: `python -m unittest discover -s tests -v`
- CI mirrors lint + tests.

## Coding standards
- PEP8, snake_case for functions, PascalCase for classes.
- Keep UI code in `kajovo/ui` and orchestration in `kajovo/core`.
