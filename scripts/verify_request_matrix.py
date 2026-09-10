"""Ověří a exportuje konečnou matici voleb běhu bez síťových volání."""

from __future__ import annotations

import argparse
import csv
from itertools import product
from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kajovo.core.request_rules import validate_run_options


def matrix_rows():
    for mode, flags in product(("GENERATE", "MODIFY", "QA", "QFILE"), product((False, True), repeat=8)):
        batch, previous, vector, file_search, win_in, ssh_in, win_out, ssh_out = flags
        expected = (not vector or file_search) and (
            not batch or mode == "GENERATE" and not (win_out or ssh_out)
            or mode == "MODIFY" and not any((previous, vector, win_in, ssh_in, win_out, ssh_out))
        )
        cfg = SimpleNamespace(
            mode=mode, model="gpt-4.1-nano", prompt="test", send_as_c=batch,
            response_id="resp_test" if previous else "",
            attached_vector_store_ids=["vs_test"] if vector else [],
            model_caps={"ok_basic": True, "supports_file_search": file_search},
            available_models=["gpt-4.1-nano"],
            diag_windows_in=win_in, diag_ssh_in=ssh_in,
            diag_windows_out=win_out, diag_ssh_out=ssh_out,
        )
        try:
            validate_run_options(cfg)
            actual = True
        except ValueError:
            actual = False
        if actual != expected:
            raise AssertionError((mode, flags, expected, actual))
        yield dict(mode=mode, batch=batch, previous=previous, vector=vector,
                   file_search=file_search, windows_in=win_in, ssh_in=ssh_in,
                   windows_out=win_out, ssh_out=ssh_out, allowed=expected)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    rows = list(matrix_rows())
    if args.output:
        with args.output.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    print(f"Ověřeno kombinací: {len(rows)}; povoleno: {sum(row['allowed'] for row in rows)}")


if __name__ == "__main__":
    main()
