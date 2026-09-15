"""Opakovatelné měření místního indexu na dočasné kanonické evidenci bez sítě."""

import argparse
import json
from pathlib import Path
import sys
import tempfile
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kajovo.core.run_bundle import HistoryIndex
from kajovo.core.runlog import RunLogger
from kajovo.studio.history_models import RunTableModel, build_run


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=3000)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="kajovo-history-benchmark-") as root:
        for number in range(args.runs):
            logger = RunLogger(root, f"RUN_{number:06d}", f"Projekt {number % 20}")
            logger.bundle.update_run({"mode": "QA", "status": "completed", "model_summary": ["gpt-4.1"],
                "created_at": "2026-09-15T10:00:00Z", "finished_at": "2026-09-15T10:01:00Z"})
            step = logger.bundle.ensure_step("QA")
            logger.bundle.update_step(step["step_id"], status="completed", started_at="2026-09-15T10:00:00Z",
                                       finished_at="2026-09-15T10:01:00Z")
        index = HistoryIndex(root)
        start = perf_counter()
        records = index.refresh()
        cold = perf_counter() - start
        start = perf_counter()
        index.refresh()
        index.reverse_lineage()
        warm = perf_counter() - start
        start = perf_counter()
        runs = [build_run(record, steps=record["timeline_steps"]) for record in records]
        model = perf_counter() - start
        start = perf_counter()
        matches = RunTableModel.filter_runs(runs, {"search": "Projekt 12"})
        search = perf_counter() - start
        result = {"runs": args.runs, "cold_index_seconds": cold, "warm_index_seconds": warm,
                  "view_model_seconds": model, "search_seconds": search, "matches": len(matches),
                  "note": "Dočasné běhy s jedním doloženým krokem; nejde o měření cizích provozních dat."}
        target = Path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
