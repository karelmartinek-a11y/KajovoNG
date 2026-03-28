from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from kajovospend.ocr.evaluation import OcrEvaluationRunner


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Vyhodnoti presnost offline OCR extrakce nad oznacenym korpusem.')
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--zip', dest='zip_path', type=Path, help='Cesta k ZIP archivu s doklady.')
    group.add_argument('--corpus', dest='corpus_path', type=Path, help='Cesta k rozbalenemu korpusu.')
    parser.add_argument('--labels', type=Path, default=None, help='CSV s expected_* poli a relative_path.')
    parser.add_argument('--output-dir', type=Path, required=True, help='Adresar pro summary.json a predictions.csv.')
    parser.add_argument('--max-files', type=int, default=None, help='Nepovinne omezeni poctu zpracovanych souboru.')
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    runner = OcrEvaluationRunner()
    if args.zip_path is not None:
        report = runner.run_zip(args.zip_path, labels_path=args.labels, max_files=args.max_files)
    else:
        report = runner.run_corpus(args.corpus_path, labels_path=args.labels, max_files=args.max_files)
    outputs = runner.write_report(report, args.output_dir)
    metrics = report['metrics']
    print(f"Vzorku: {metrics['sample_count']}")
    print(f"Oznacenych vzorku: {metrics['labeled_sample_count']}")
    print(f"Document exact match: {metrics['document_exact_match_rate']}")
    print(f"ICO exact match: {metrics['ico_exact_match_rate']}")
    print(f"Document number exact match: {metrics['document_number_exact_match_rate']}")
    print(f"Issued at exact match: {metrics['issued_at_exact_match_rate']}")
    print(f"Total exact match: {metrics['total_with_vat_exact_match_rate']}")
    print(f"Finalize accuracy: {metrics['finalize_accuracy_rate']}")
    print(f"Summary: {outputs['summary']}")
    print(f"Predictions: {outputs['predictions']}")
    print(f"Review pack: {outputs['review_pack']}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
