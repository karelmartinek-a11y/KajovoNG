"""Fotografie všech referenčních variant jako označených vizuálních projekcí.

Události jsou deterministické fixture. Nástroj neprovádí jejich backendové
operace a nepředstavuje důkaz funkčnosti executorů ani vzdálené služby.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--size", default="1080,820")
    parser.add_argument("--scale", default="1")
    parser.add_argument("--native", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    from scripts.render_studio import native_platform, scroll_positions

    if args.native:
        os.environ.setdefault("QT_QPA_PLATFORM", native_platform(sys.platform))
    else:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ["QT_SCALE_FACTOR"] = args.scale
    os.environ.pop("OPENAI_API_KEY", None)
    from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop
    from PySide6.QtWidgets import QAbstractScrollArea, QApplication
    from kajovo.app.main import _load_fonts
    from kajovo.core.progress import ProgressEvent
    from kajovo.core.progress_catalog import coverage_for_variant
    from kajovo.studio.components import install_theme
    from kajovo.studio.progress_dialog import MultiProgressDialog

    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    source_path = root / "docs" / "progress" / "reference_inventory_182.json"
    source_bytes = source_path.read_bytes()
    catalog = json.loads(source_bytes)
    (output / "source-catalog.json").write_bytes(source_bytes)
    app = QApplication([])
    _load_fonts()
    install_theme(app)
    width, height = map(int, args.size.split(","))
    manifest = []

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Vizuální projekce nesmí přistupovat k síti.")

    def capture(dialog, variant, family, state, suffix=""):
        for _ in range(3):
            app.processEvents(QEventLoop.AllEvents, 40)
        name = f"variant-{variant['id']:03d}-{state}{suffix}"
        path = output / (name + ".png")
        if not dialog.grab().save(str(path)):
            raise RuntimeError("Vizuální projekci se nepodařilo uložit.")
        manifest.append({"name": name, "file": path.name, "variant_id": variant["id"],
                         "title": variant["title"], "family": family.key, "strategy": family.strategy,
                         "owners": list(family.owners), "class": type(dialog).__name__, "state": state,
                         "width": dialog.width(), "height": dialog.height(), "scale": args.scale,
                         "evidence_type": "reference_visual_projection", "backend_executed": False,
                         "source_steps": [step["title"] for step in variant["steps"]],
                         "projected_rows": dialog.inspector.model.rows(),
                         "scroll_areas": [{"type": type(area).__name__,
                                           "vertical_position": area.verticalScrollBar().value(),
                                           "vertical_maximum": area.verticalScrollBar().maximum()}
                                          for area in dialog.findChildren(QAbstractScrollArea) if area.isVisibleTo(dialog)]})

    def capture_scrolls(dialog, variant, family, state):
        capture(dialog, variant, family, state)
        for number, area in enumerate(dialog.findChildren(QAbstractScrollArea)):
            if not area.isVisibleTo(dialog):
                continue
            bar = area.verticalScrollBar()
            previous = bar.value()
            for position in scroll_positions(bar.maximum(), area.viewport().height()):
                if position != previous:
                    bar.setValue(position)
                    capture(dialog, variant, family, state, f"-scroll{number}-{position}")
            bar.setValue(previous)

    with patch("requests.sessions.Session.request", forbidden), \
            patch("socket.create_connection", forbidden), patch("socket.socket.connect", forbidden):
        for variant in catalog["variants"]:
            family = coverage_for_variant(variant["id"])
            dialog = MultiProgressDialog(variant["title"], reduced_motion=True)
            dialog.resize(width, height)
            dialog.show()
            dialog.inspector.title_label.setText(variant["title"])
            dialog.lifecycle_hint.setText("Vizuální projekce referenčního scénáře nad ukázkovými událostmi. Backend tohoto scénáře nebyl spuštěn.")
            stages = tuple(step["title"] for step in variant["steps"])
            dialog.on_event(ProgressEvent("PLAN", planned_steps=stages, timestamp=dialog.clock.started))
            middle = len(stages) // 2
            for index, stage in enumerate(stages[:middle]):
                dialog.on_event(ProgressEvent(stage, "completed", timestamp=dialog.clock.started + index + 1, source="validation"))
            if stages:
                detail = variant["steps"][middle]["proposal"].get("action", "")
                dialog.on_event(ProgressEvent(stages[middle], "active", detail=detail,
                                              timestamp=dialog.clock.started + middle + 1, source="local",
                                              next_step=stages[middle + 1] if middle + 1 < len(stages) else ""))
            capture_scrolls(dialog, variant, family, "active")
            for index, stage in enumerate(stages[middle:], start=middle):
                dialog.on_event(ProgressEvent(stage, "completed", timestamp=dialog.clock.started + index + 1, source="validation"))
            dialog.finish("completed")
            dialog.lifecycle_hint.setText("Vizuální projekce dokončeného referenčního scénáře. Stav je vytvořený fixture, není výsledkem spuštění backendu.")
            capture_scrolls(dialog, variant, family, "completed")
            dialog.timer.stop()
            dialog.close()
            dialog.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            app.processEvents()
            print(f"Vizuální projekce {variant['id']:03d}: {variant['title']}", flush=True)
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    coverage = {"schema_version": 1, "evidence_type": "reference_visual_projection", "backend_executed": False,
                "platform": os.environ["QT_QPA_PLATFORM"], "network": "blocked", "variant_count": len(catalog["variants"]),
                "step_count": sum(len(row["steps"]) for row in catalog["variants"]), "screenshots": len(manifest),
                "source": source_path.relative_to(root).as_posix(), "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
                "variant_ids": sorted({row["variant_id"] for row in manifest}),
                "note": "Všech 182 referenčních variant je fotografováno ve skutečném produkčním Qt dialogu. Jde o vizuální projekce; nezakládají důkaz vykonání 182 executorů ani 1284 backendových kroků."}
    (output / "coverage.json").write_text(json.dumps(coverage, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    cards = "\n".join(f'<figure><a href="{html.escape(row["file"])}"><img loading="lazy" src="{html.escape(row["file"])}" alt="{html.escape(row["title"])}"></a><figcaption>{row["variant_id"]}. {html.escape(row["title"])} · {row["state"]}</figcaption></figure>' for row in manifest)
    gallery = '<!doctype html><html lang="cs"><meta charset="utf-8"><title>Referenční vizuální projekce průběhu</title><style>body{background:#0b1220;color:#f3f7fc;font:16px system-ui;margin:24px}main{display:grid;grid-template-columns:repeat(auto-fit,minmax(360px,1fr));gap:20px}figure{margin:0}img{width:100%}figcaption{padding:12px}</style><h1>Referenční vizuální projekce průběhu</h1><p>Ukázkové události ve skutečném Qt dialogu; backendy ani vzdálené služby nebyly spuštěny.</p><main>' + cards + '</main></html>'
    (output / "gallery.html").write_text(gallery, encoding="utf-8")
    print(json.dumps(coverage, ensure_ascii=False))


if __name__ == "__main__":
    main()
