"""Jednorázový codemod: vector-store polling z pipeline do core.runs.polling."""

from __future__ import annotations

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
PIPELINE = ROOT / "kajovo" / "core" / "pipeline.py"
SELF = ROOT / "scripts" / "_refactor_pipeline.py"
WORKFLOW = ROOT / ".github" / "workflows" / "refactor-codemod.yml"


def main() -> None:
    text = PIPELINE.read_text(encoding="utf-8")
    anchor = "from .runs.delivery import DeliveryContext, save_out_files\n"
    if text.count(anchor) != 1:
        raise SystemExit("Refaktor odmítnut: delivery import nemá očekávaný tvar.")
    polling_import = "from .runs.polling import VectorStorePollingContext, wait_vector_store_files\n"
    if polling_import not in text:
        text = text.replace(anchor, anchor + polling_import, 1)

    pattern = re.compile(
        r"    def _wait_vector_store_files\(self, client: OpenAIClient, vs_id: str, vs_file_ids: List\[str\], timeout_s: int = 180\) -> None:\n.*?(?=    def _in_dir_fallback_note)",
        re.DOTALL,
    )
    replacement = '''    def _wait_vector_store_files(self, client: OpenAIClient, vs_id: str, vs_file_ids: List[str], timeout_s: int = 180) -> None:
        context = VectorStorePollingContext(
            retrieve=lambda vector_store_id, file_id: with_retry(
                lambda: client.retrieve_vector_store_file(vector_store_id, file_id),
                self.settings.retry,
                self.breaker,
            ),
            check_stop=self._check_stop,
            progress_emit=self.progress_event.emit,
            evidence_emit=lambda event, payload: self.log.event(event, payload),
            timeout_s=timeout_s,
        )
        wait_vector_store_files(context, vs_id, vs_file_ids)

'''
    updated, count = pattern.subn(replacement, text, count=1)
    if count != 1:
        raise SystemExit(f"Refaktor odmítnut: očekáván 1 polling blok, nalezeno {count}.")
    PIPELINE.write_text(updated, encoding="utf-8", newline="\n")
    SELF.unlink()
    WORKFLOW.unlink()


if __name__ == "__main__":
    main()
