"""Jednorázový codemod: output delivery z pipeline do core.runs.delivery."""

from __future__ import annotations

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
PIPELINE = ROOT / "kajovo" / "core" / "pipeline.py"
RENDER_STUDIO = ROOT / "scripts" / "render_studio.py"
SELF = ROOT / "scripts" / "_refactor_pipeline.py"
WORKFLOW = ROOT / ".github" / "workflows" / "refactor-codemod.yml"


def patch_pipeline() -> None:
    text = PIPELINE.read_text(encoding="utf-8")
    anchor = "from .runs.config import UiRunConfig\n"
    if text.count(anchor) != 1:
        raise SystemExit("Refaktor odmítnut: UiRunConfig import nemá očekávaný tvar.")
    delivery_import = "from .runs.delivery import DeliveryContext, save_out_files\n"
    if delivery_import not in text:
        text = text.replace(anchor, anchor + delivery_import, 1)

    pattern = re.compile(
        r"    def _save_out_files\(self, files: List\[Dict\[str, Any\]\]\) -> Dict\[str, Any\]:\n.*?(?=    def _finish_file_delivery)",
        re.DOTALL,
    )
    replacement = '''    def _save_out_files(self, files: List[Dict[str, Any]]) -> Dict[str, Any]:
        # Tenký Qt adaptér. Vlastní validace, hash guardy a durable zápis jsou
        # v Qt-nezávislé doménové vrstvě core.runs.delivery.
        context = DeliveryContext(
            cfg=self.cfg,
            settings=self.settings,
            log=self.log,
            set_progress=self._set,
            create_snapshot=self._create_snapshot,
            check_stop=self._check_stop,
            finish_delivery=self._finish_file_delivery,
            progress_emit=self.progress_event.emit,
            subprogress_emit=self.subprogress.emit,
            overwrite_guard_enabled=hasattr(self, "_delivery_overwrite_hashes"),
            overwrite_hashes=getattr(self, "_delivery_overwrite_hashes", None),
        )
        self._progress_stage = "Ukládání"
        return save_out_files(context, files)

'''
    updated, count = pattern.subn(replacement, text, count=1)
    if count != 1:
        raise SystemExit(f"Refaktor odmítnut: očekáván 1 delivery blok, nalezeno {count}.")
    PIPELINE.write_text(updated, encoding="utf-8", newline="\n")


def patch_renderer() -> None:
    text = RENDER_STUDIO.read_text(encoding="utf-8")
    import_anchor = "    from kajovo.core.config import AppSettings\n"
    secret_import = "    from kajovo.core import secret_store\n"
    if import_anchor not in text:
        raise SystemExit("Render refaktor odmítnut: chybí AppSettings import.")
    if secret_import not in text:
        text = text.replace(import_anchor, import_anchor + secret_import, 1)

    old = '                 patch("kajovo.core.secret_store._read_persisted_api_key", return_value=None), \\\n                 patch("kajovo.core.secret_store.get_secret", return_value=None):'
    new = '                 patch("kajovo.core.secret_store._read_persisted_api_key", return_value=None), \\\n                 patch("kajovo.core.secret_store._read_keyring_api_key_record", return_value=secret_store._MISSING), \\\n                 patch("kajovo.core.secret_store.get_secret", return_value=None):'
    if old not in text:
        raise SystemExit("Render refaktor odmítnut: credential patch blok nemá očekávaný tvar.")
    text = text.replace(old, new, 1)
    RENDER_STUDIO.write_text(text, encoding="utf-8", newline="\n")


def main() -> None:
    patch_pipeline()
    patch_renderer()
    SELF.unlink()
    WORKFLOW.unlink()


if __name__ == "__main__":
    main()
