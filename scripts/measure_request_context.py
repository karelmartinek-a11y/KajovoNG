"""Změří uložený Responses payload lokálně, bez přístupu k API."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kajovo.core.context_limits import measure_request  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("payload", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--exact-input-tokens", type=int)
    args = parser.parse_args()
    value = json.loads(args.payload.read_text("utf-8"))
    payload = value.get("payload", value.get("body", value))
    compiled = None
    if isinstance(payload.get("input"), str):
        try:
            context, _ = json.JSONDecoder().raw_decode(payload["input"])
            compiled = context.get("file_context") if isinstance(context, dict) else None
        except ValueError:
            pass
    report = measure_request(payload, compiled=compiled, exact_input_tokens=args.exact_input_tokens,
                             batch="custom_id" in value)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Odhad/přesný vstup: {report['input_tokens']}; blokace: {len(report['blockers'])}.")


if __name__ == "__main__":
    main()
