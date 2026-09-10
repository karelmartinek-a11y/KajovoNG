"""Vytvoření distribuovaného ceníku z lokálně uložených oficiálních Markdown zdrojů."""
import argparse
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from kajovo.core.price_sources import parse_prices, OPENAI_PRICING


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docs-directory", required=True, type=Path)
    parser.add_argument("--output", default="kajovo/core/openai_prices.json", type=Path)
    args = parser.parse_args()
    sources = {p.stem: p.read_text(encoding="utf-8") for p in args.docs_directory.glob("*.md")}
    rows = parse_prices(sources["pricing"], sources)
    used = {"pricing": sources["pricing"], **{m: sources[m] for m in rows if m in sources}}
    result = {"schema_version": 2, "verified_date": datetime.now(timezone.utc).date().isoformat(),
        "last_updated": time.time(), "source": OPENAI_PRICING,
        "sources_sha256": {name: hashlib.sha256(text.encode()).hexdigest() for name, text in used.items()},
        "rows": [vars(row) for row in rows.values()]}
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(f"Zapsáno {len(rows)} modelových sazeb do {args.output}.")


if __name__ == "__main__":
    main()
