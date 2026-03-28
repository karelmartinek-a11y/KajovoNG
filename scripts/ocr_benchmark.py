from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from kajovospend.ocr.benchmark import OcrBenchmarkRunner


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Spusti OCR baseline benchmark nad korpusem nebo ZIP archivem.')
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--zip', dest='zip_path', type=Path, help='Cesta k ZIP archivu s doklady.')
    group.add_argument('--corpus', dest='corpus_path', type=Path, help='Cesta k rozbalenemu korpusu.')
    parser.add_argument('--output-dir', type=Path, required=True, help='Adresar pro summary.json, samples.csv a labels_template.csv.')
    parser.add_argument('--max-files', type=int, default=None, help='Nepovinne omezeni poctu zpracovanych souboru.')
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    runner = OcrBenchmarkRunner()
    if args.zip_path is not None:
        report = runner.run_zip(args.zip_path, max_files=args.max_files)
    else:
        report = runner.run_corpus(args.corpus_path, max_files=args.max_files)
    outputs = runner.write_report(report, args.output_dir)
    metrics = report['metrics']
    print(f"Vzorku: {metrics['sample_count']}")
    print(f"Stran: {metrics['total_pages']}")
    print(f"Citelnych dokumentu: {metrics['readable_documents']}")
    print(f"Necitelnych dokumentu: {metrics['unreadable_documents']}")
    print(f"PDF text layer: {metrics['pdf_text_layer_documents']}")
    print(f"PDF scan: {metrics['pdf_scan_documents']}")
    print(f"PDF mixed: {metrics['pdf_mixed_documents']}")
    print(f"Avg ms: {metrics['avg_duration_ms']}")
    print(f"P95 ms: {metrics['p95_duration_ms']}")
    print(f"Summary: {outputs['summary']}")
    print(f"Samples: {outputs['samples']}")
    print(f"Labels: {outputs['labels']}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
