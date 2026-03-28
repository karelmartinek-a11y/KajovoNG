from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from kajovospend.ocr.labeling import OcrLabelingMergeService


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Slouci vyplnenou OCR labeling davku do master labels CSV.')
    parser.add_argument('--master-labels', type=Path, required=True, help='Master labels CSV.')
    parser.add_argument('--batch', type=Path, required=True, help='Vyplneny batch.csv.')
    parser.add_argument('--output', type=Path, default=None, help='Volitelny vystupni labels CSV. Pokud chybi, prepise master labels.')
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    service = OcrLabelingMergeService()
    destination = service.merge_batch_into_master(args.master_labels, args.batch, args.output)
    summary = service.summarize_master_labels(destination)
    print(f"Merged labels: {destination}")
    print(f"Rows: {summary['rows']}")
    print(f"Labeled rows: {summary['labeled_rows']}")
    print(f"Expected finalize rows: {summary['expected_finalize_rows']}")
    print(f"ICO labels: {summary['expected_ico']}")
    print(f"Document number labels: {summary['expected_document_number']}")
    print(f"Issued at labels: {summary['expected_issued_at']}")
    print(f"Total labels: {summary['expected_total_with_vat']}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
