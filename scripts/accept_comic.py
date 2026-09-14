"""Výslovná placená akceptace komiksu; nikdy není součástí pytest ani startu."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kajovo.core.comic_service import ComicService
from kajovo.core.comic_types import PanelFormat
from kajovo.core.config import AppSettings
from kajovo.core.openai_client import OpenAIClient


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--live", action="store_true", help="Výslovně povolit placené pracovní požadavky")
    args = parser.parse_args()
    if not args.live:
        parser.error("Akceptace vyžaduje výslovné --live.")
    root = Path(args.workspace).resolve()
    root.mkdir(parents=True, exist_ok=True)
    settings = AppSettings(comic_library_dir=str(root / "COMICS"), log_dir=str(root / "LOG"))
    service = ComicService(settings, OpenAIClient(os.environ["OPENAI_API_KEY"], timeout_s=600))
    state_file = root / "acceptance.json"
    state = json.loads(state_file.read_text(encoding="utf-8")) if state_file.exists() else {}

    def save():
        temporary = state_file.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, state_file)

    def run_once(key, start):
        if key not in state:
            state[key] = start()
            save()
        result = service.run(state[key])
        print(json.dumps({"step": key, **result}), flush=True)
        return result["status"] == "completed"

    if "project" not in state:
        state["project"] = service.store.project("Akceptace – fiktivní postavy", style={"description": "Čistý evropský komiks, ruční inkoust, tlumené barvy, dospělé fiktivní postavy."})
        save()
    project = state["project"]
    if not run_once("bible", lambda: service.start_bible(project)):
        return
    from PIL import Image, ImageDraw
    for name, kind, color in (("Karel", "character", "navy"), ("Eva", "character", "maroon"), ("Kancelář", "environment", "teal")):
        if name not in state:
            state[name] = service.store.entity(project, kind, name, "Dospělá fiktivní postava podle syntetické kreslené předlohy; zachovej barvu oblečení a vlasů." if kind == "character" else "Kancelář s modrým stolem vlevo, oknem vpravo a kulatými hodinami na zdi.")
            save()
        if not service.store.references(project, state[name]):
            im = Image.new("RGB", (1024, 1024), "ivory")
            draw = ImageDraw.Draw(im)
            if kind == "character":
                draw.ellipse((310, 110, 720, 560), fill="peachpuff", outline="black", width=8)
                draw.pieslice((290, 65, 735, 450), 180, 360, fill="sienna" if name == "Eva" else "black")
                draw.rectangle((270, 565, 755, 1000), fill=color, outline="black", width=8)
                draw.ellipse((400, 280, 425, 305), fill="black")
                draw.ellipse((600, 280, 625, 305), fill="black")
                draw.arc((430, 380, 600, 460), 0, 180, fill="black", width=5)
            else:
                draw.rectangle((90, 560, 590, 770), fill="navy", outline="black", width=8)
                draw.rectangle((650, 190, 940, 490), fill="lightblue", outline="black", width=8)
                draw.ellipse((250, 150, 410, 310), fill="white", outline="black", width=8)
                draw.line((330, 230, 330, 170), fill="black", width=5)
            path = root / (name + ".png")
            im.save(path)
            service.import_references(project, [path], state[name])
        if not run_once("reference_" + name, lambda name=name: service.start_entity(state[name])):
            return
    if "panels" not in state:
        state["panels"] = []
        combinations = [[], ["Karel"], ["Karel", "Eva"], ["Kancelář"], ["Karel", "Kancelář"], ["Karel", "Eva", "Kancelář"]]
        for index, names in enumerate(combinations):
            panel = service.store.panel(project, f"Akceptace {index + 1}")
            nodes = [{"type": "text", "text": "Klidná večerní scéna. "}]
            nodes.extend({"type": "environment_ref" if n == "Kancelář" else "character_ref", "entity_id": state[n]} for n in names)
            service.store.save_panel(panel, 2, f"Akceptace {index + 1}", {"version": 1, "nodes": nodes}, asdict(PanelFormat(1024 if index != 5 else 731, 1024 if index != 5 else 987)), [])
            state["panels"].append(panel)
        save()
    if not run_once("batch", lambda: service.start_panels(project, state["panels"])):
        return
    panel = state["panels"][-1]
    if not run_once("edit", lambda: service.start_panels(project, [panel], "Změň kulaté hodiny na zelené. Ostatní zachovej.")):
        return
    versions = service.store.rows("panel_versions", "panel_id=?", (panel,))
    service.store.restore_version(panel, versions[-1]["id"])
    service.store.restore_version(panel, versions[0]["id"])
    state["backend_acceptance"] = "completed"
    save()
    print(json.dumps({"backend_acceptance": "completed", "workspace": str(root), "visual_review": "required"}), flush=True)


if __name__ == "__main__":
    main()
