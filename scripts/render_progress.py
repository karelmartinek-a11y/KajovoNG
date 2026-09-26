"""Snímky skutečného Qt okna nad událostmi executorů s nahrazenou sítí."""

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--size", default="1080,820")
    parser.add_argument("--scale", default="1")
    parser.add_argument("--replay", help="JSON událostí vytvořený předchozím spuštěním")
    args = parser.parse_args()
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ["QT_SCALE_FACTOR"] = args.scale
    root = Path(__file__).resolve().parents[1]
    os.environ["QT_QPA_FONTDIR"] = str(root / "resources")
    sys.path[:0] = [str(root), str(root / "tests")]
    from PySide6.QtWidgets import QApplication
    from change_v2_fixtures import run, scenario
    from kajovo.studio.progress_dialog import MultiProgressDialog
    from kajovo.studio.components import install_theme

    app = QApplication([])
    from kajovo.app.main import _load_fonts
    _load_fonts()
    install_theme(app)
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    width, height = map(int, args.size.split(","))
    traces = json.loads(Path(args.replay).read_text(encoding="utf-8")) if args.replay else {}
    from kajovo.core.progress import ProgressEvent

    def forbidden(*args, **kwargs):
        raise AssertionError("Snímkování nesmí přistupovat k síti.")

    with tempfile.TemporaryDirectory(prefix="kajovo-progress-") as temporary, \
            patch("requests.sessions.Session.request", forbidden), patch("socket.socket.connect", forbidden):
        for mode, options, slug in (
            ("GENERATE", {}, "tvorba"),
            ("MODIFY", {"stop_after_plan": True, "maximum_quality": True}, "plan-zmen"),
            ("GENERATE", {"batch": True}, "davka"),
        ):
            directory = Path(temporary) / slug
            directory.mkdir()
            if slug in traces:
                events = [ProgressEvent(**item) for item in traces[slug]["events"]]
                values = [traces[slug]["result"]]
            else:
                worker, client, _ = scenario(directory, mode, **options)
                events = []
                worker.progress_event.connect(events.append)
                values, errors = run(worker, client)
                if errors or not values:
                    raise AssertionError(errors)
                traces[slug] = {"events": [asdict(event) for event in events], "result": values[0]}
            title = {"tvorba": "Tvorba projektu · průběžné zpracování",
                     "plan-zmen": "Úprava projektu · příprava ověřeného plánu",
                     "davka": "Tvorba projektu · hromadné zpracování"}[slug]
            dialog = MultiProgressDialog(title)
            dialog.clock.started = events[0].timestamp
            dialog.resize(width, height)
            dialog.show()
            dialog.resize(width, height)
            captured = False
            for event in events:
                dialog.on_event(event)
                if event.state == "waiting" and not captured:
                    app.processEvents()
                    dialog.grab().save(str(output / (slug + "-ceka.png")))
                    captured = True
            dialog.result = values[0]
            dialog.finish(values[0].get("status", "completed"))
            app.processEvents()
            dialog.grab().save(str(output / (slug + "-vysledek.png")))
            print(slug, dialog.inspector.model.rows())
            dialog.close()
        from kajovo.core.user_errors import describe_error
        for state in ("submission_unknown", "failed", "cancelled", "partial", "response_pending"):
            dialog = MultiProgressDialog("Odpověď na otázku · kontrola koncového stavu")
            dialog.resize(width, height)
            dialog.show()
            dialog.resize(width, height)
            dialog.on_event(ProgressEvent("PLAN", planned_steps=("QA_INPUT", "QA_RESPONSE", "QA_VALIDATION")))
            dialog.on_event(ProgressEvent("QA_INPUT", "completed"))
            dialog.on_event(ProgressEvent("QA_RESPONSE", "waiting", source="api"))
            dialog.finish(state, describe_error(ValueError("Kontrolovaná testovací chyba")) if state == "failed" else None)
            app.processEvents()
            dialog.grab().save(str(output / (state + ".png")))
            dialog.close()
    (output / "traces.json").write_text(json.dumps(traces, ensure_ascii=False, indent=2), encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
