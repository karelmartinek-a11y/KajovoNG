from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from kajovospend.ocr.labeling import OcrAiPrefillService


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Predvyplni OCR labeling batch z AI/OCR predikci.')
    parser.add_argument('--batch', type=Path, required=True, help='Vstupni batch.csv.')
    parser.add_argument('--output', type=Path, required=True, help='Vystupni AI-predvyplneny batch.csv.')
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    service = OcrAiPrefillService()
    output = service.prefill_batch(args.batch, args.output)
    summary = service.summarize_prefill(output)
    print(f"AI-prefilled batch: {output}")
    print(f"Rows: {summary['rows']}")
    print(f"Prefilled ICO: {summary['prefilled_ico']}")
    print(f"Prefilled document number: {summary['prefilled_document_number']}")
    print(f"Prefilled issued at: {summary['prefilled_issued_at']}")
    print(f"Prefilled total: {summary['prefilled_total']}")
    print(f"Prefilled finalize: {summary['prefilled_finalize']}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
