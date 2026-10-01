"""Malé skutečné Qt pohledy včetně PDF musí dokončit obsluhu událostí."""

import json
import os
from pathlib import Path
import subprocess
import sys


def test_small_ui_snapshot_render_finishes_pdf_layout_without_event_loop_hang(tmp_path):
    root = Path(__file__).resolve().parents[1]
    output = tmp_path / "snimky"
    environment = dict(os.environ)
    environment.pop("OPENAI_API_KEY", None)
    result = subprocess.run(
        [sys.executable, str(root / "scripts/render_studio.py"), "--output", str(output), "--size", "640,360"],
        cwd=root, env=environment, capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stderr[-4000:] + result.stdout[-1000:]
    views = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    by_name = {view["name"]: view for view in views}
    for name in ("run_studio_qfile_tabs0_0", "run_studio_qfile_tabs0_1", "comic_reference", "history_filters"):
        assert name in by_name
        assert (output / by_name[name]["file"]).stat().st_size > 0
    assert by_name["run_studio_qfile_tabs0_0"]["width"] == 640
    assert by_name["run_studio_qfile_tabs0_0"]["height"] == 360
    assert not any(view["label_clipping"] for view in views)
