# Repository Guidelines

## Project Structure & Module Organization
- `kajovo/` holds the Python package: `app/` for CLI entry points, `core/` for business logic, and `ui/` for the PySide6 desktop components. Treat each folder as a cohesive module—keep GUI logic in `ui`, orchestration in `core`, and startup scripts in `app`.
- `scripts/` bundles platform helpers: `install.{bat,ps1}` installs dependencies, `run.{bat,ps1}` launches the desktop app, and `fetch_fonts.ps1` grabs the `Montserrat` assets referenced in `resources/`.
- `doc/` stores the project brief (`Zadani_Kajovo_Master.md`) and is the go-to for domain requirements; copy or link excerpts when documenting new features.
- `.venv/`, `requirements.txt`, `kajovo_program.zip`, and `resources/` (font binaries and usage notes) sit at the root—keep tooling there so install scripts can assume consistent paths.

## Build, Test, and Development Commands
- `scripts\install.bat` / `scripts\install.ps1`: create the `.venv`, install `requirements.txt`, and set up any runtime hooks. Run this once after cloning.
- `scripts\run.bat` / `scripts\run.ps1`: starts the PySide6 app through the `kajovo.app.main` entry point, respecting the configured API key and logging paths.
- `scripts\fetch_fonts.ps1`: downloads `Montserrat` font files referenced by the UI and stores them in `resources/`. Run it if the fonts are missing locally.

## Coding Style & Naming Conventions
- Python modules follow PEP 8: 4-space indentation, snake_case for functions/variables, PascalCase for widget classes. Keep imports grouped as standard, third-party, then local.
- UI files mirror the package layout, so name new dialogs after their purpose (`session_dialog.py`, `qa_panel.py`).
- Keep strings that appear in the UI or logs concise and update related `.ui` resources if the label text changes.

## Testing Guidelines
- No automated test suite exists yet; describe regressions in `doc/` and pair new features with manual QA steps (mode selection, OUT folder generation, LOG inspection).
- If you add Pytest/Unit tests, place them under a new `tests/` folder mirroring the package layout and name them `test_<module>.py`.

## Commit & Pull Request Guidelines
- Use descriptive, imperative commits such as `core: add retry logic for modify mode` or `ui: clarify API key prompt`.
- Pull requests should include a short summary, testing steps run (e.g., `Ran scripts\run.ps1 in GENERATE mode`), and links to relevant doc issues or bugs; attach screenshots when UI changes are involved.

## Security & Configuration Tips
- API keys are written via the app’s **API-KEY** button, which exports to `OPENAI_API_KEY` in the user environment. Never hard-code secrets in source files or checked-in configs.
- Logs live under `LOG/RUN_<timestamp>_/`; use them to trace dispatched prompts, API responses, and receipts stored in `kajovo.sqlite`.
