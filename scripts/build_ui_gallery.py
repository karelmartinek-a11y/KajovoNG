"""Vytvoří procházetelnou galerii snímků a volitelné kontaktní listy pro kontrolu."""

import argparse
import html
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--contacts")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1] / "docs" / "ui"
    rows = []
    for manifest in sorted((root / "after").glob("*/manifest.json")):
        for row in json.loads(manifest.read_text(encoding="utf-8")):
            rows.append((manifest.parent / row["file"], manifest.parent.name + " / " + row["name"]))
    cards = []
    for path, name in rows:
        source = html.escape(path.relative_to(root).as_posix())
        cards.append(f'<figure><a href="{source}"><img loading="lazy" src="{source}" alt="{html.escape(name)}"></a><figcaption>{html.escape(name)}</figcaption></figure>')
    document = '''<!doctype html><html lang="cs"><meta charset="utf-8"><title>Galerie řídicího studia</title>
<style>body{background:#0b1220;color:#f3f7fc;font:16px system-ui;margin:24px}main{display:grid;grid-template-columns:repeat(auto-fit,minmax(360px,1fr));gap:20px}figure{margin:0;padding:12px;background:#131f30;border-radius:12px}img{width:100%;height:auto}figcaption{overflow-wrap:anywhere;margin-top:10px}input{padding:12px;margin-bottom:24px;max-width:90%;width:600px}</style>
<h1>Řídicí studio · skutečné snímky Qt</h1><p>Ukázková data; kliknutí otevře plné rozlišení. Snímky nezahrnují placené síťové operace.</p><input aria-label="Filtrovat snímky" placeholder="Hledat sekci nebo velikost"><main>'''
    document += "\n".join(cards)
    document += '''</main><script>document.querySelector('input').addEventListener('input',e=>{const q=e.target.value.toLowerCase();document.querySelectorAll('figure').forEach(x=>x.hidden=!x.textContent.toLowerCase().includes(q))})</script></html>'''
    (root / "gallery.html").write_text(document, encoding="utf-8")
    if args.contacts:
        from PIL import Image, ImageDraw
        target = Path(args.contacts)
        target.mkdir(parents=True, exist_ok=True)
        for number in range(0, len(rows), 12):
            canvas = Image.new("RGB", (1600, 990), "#0b1220")
            draw = ImageDraw.Draw(canvas)
            for index, (path, name) in enumerate(rows[number:number + 12]):
                thumb = Image.open(path).convert("RGB")
                thumb.thumbnail((520, 285))
                x, y = (index % 3) * 535, (index // 3) * 245
                thumb.thumbnail((520, 216))
                canvas.paste(thumb, (x, y + 22))
                draw.text((x + 5, y + 3), name, fill="white")
            canvas.save(target / f"sheet-{number // 12:02}.png")
    print(f"Galerie obsahuje {len(rows)} snímků.")


if __name__ == "__main__":
    main()
