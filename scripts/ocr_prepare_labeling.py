from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from kajovospend.ocr.labeling import OcrLabelingBatchGenerator


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Pripravi labeling batch z OCR review packu.')
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--zip', dest='zip_path', type=Path, help='Cesta k ZIP archivu s doklady.')
    group.add_argument('--corpus', dest='corpus_path', type=Path, help='Cesta k rozbalenemu korpusu.')
    parser.add_argument('--review-pack', type=Path, required=True, help='CSV review pack vygenerovany OCR evaluaci.')
    parser.add_argument('--output-dir', type=Path, required=True, help='Adresar pro batch.csv a preview artefakty.')
    parser.add_argument('--limit', type=int, default=50, help='Kolik nejvyse prioritnich vzorku zaradit do davky.')
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    generator = OcrLabelingBatchGenerator()
    if args.zip_path is not None:
        outputs = generator.prepare_from_zip(args.zip_path, args.review_pack, args.output_dir, limit=args.limit)
    else:
        outputs = generator.prepare_from_corpus(args.corpus_path, args.review_pack, args.output_dir, limit=args.limit)
    print(f"Batch: {outputs['batch']}")
    print(f"Previews: {outputs['previews_dir']}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
