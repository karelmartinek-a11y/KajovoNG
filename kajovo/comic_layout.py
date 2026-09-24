"""Společná sazba textu pro přípravu storyboardu i skutečné vykreslení."""
from copy import deepcopy
import math

from PySide6.QtCore import QRectF, Qt, QTextBoundaryFinder
from PySide6.QtGui import QColor, QFont, QGuiApplication, QTextCharFormat, QTextCursor, QTextDocument

from .core.comic_types import ComicError, DEFAULT_PANEL_FORMAT
from .core.orchestration.image_slots import image_policy


def text_document(layer, width, height):
    if QGuiApplication.instance() is None:
        raise ComicError("text_layout", "Sazba komiksu vyžaduje inicializované grafické prostředí aplikace.")
    rect = QRectF(0, 0, layer["w"] * width, layer["h"] * height)
    inset = .18 if layer["kind"] in ("dialog", "thought") else .06
    inner = rect.adjusted(rect.width() * inset, rect.height() * inset, -rect.width() * inset, -rect.height() * inset)
    doc = QTextDocument()
    font = QFont("Montserrat")
    font.setPixelSize(max(1, round(layer["font_size"] * height)))
    font.setBold(layer["kind"] == "sfx" or layer.get("bold", False))
    doc.setDefaultFont(font)
    doc.setDefaultStyleSheet("body { color: black; }")
    doc.setPlainText(layer["text"])
    cursor = QTextCursor(doc)
    cursor.select(QTextCursor.Document)
    ink = QTextCharFormat()
    ink.setForeground(QColor("black"))
    cursor.mergeCharFormat(ink)
    option = doc.defaultTextOption()
    option.setAlignment(Qt.AlignHCenter if layer["kind"] in ("dialog", "thought", "sfx") else Qt.AlignLeft)
    doc.setDefaultTextOption(option)
    doc.setTextWidth(inner.width())
    return doc, inner


def _graphemes(text):
    finder = QTextBoundaryFinder(QTextBoundaryFinder.Grapheme, text)
    encoded = text.encode("utf-16-le")
    start = 0
    while (end := finder.toNextBoundary()) != -1:
        yield encoded[start * 2:end * 2].decode("utf-16-le")
        start = end


def prepare_storyboard_layout(storyboard, fmt=None, *, style=None):
    """Bezeztrátově rozdělí jen text; scénu, mluvčí a pořadí nepřepisuje."""
    fmt = dict(fmt or DEFAULT_PANEL_FORMAT)
    dialogue_kind = {"dialogová": "dialog", "myšlenková": "thought", "narativní": "caption"}[
        (style or {}).get("balloon", "dialogová")
    ]
    policy = image_policy()["comic_overlay"]
    points = min(policy["max_font_points"], max(policy["min_font_points"], policy["default_font_points"]))
    font_size = points * fmt["dpi"] / 72 / fmt["height"]
    result, layouts, fragments = [], {}, []
    used_ids = {row["id"] for row in storyboard["panels"]}
    for original in sorted(storyboard["panels"], key=lambda row: row["position"]):
        pieces = []
        for index, line in enumerate(original["dialogue"]):
            pieces.append(("dialog", index, line["speaker"], line["text"]))
        if original["caption"]:
            pieces.append(("caption", None, "", original["caption"]))
        local_panels = []
        def new_panel(original=original, local_panels=local_panels):
            panel = deepcopy(original)
            panel["dialogue"], panel["caption"] = [], ""
            identifier = original["id"]
            if local_panels:
                suffix = len(local_panels) + 1
                identifier = original["id"] + f"__text_{suffix}"
                while identifier in used_ids:
                    suffix += 1
                    identifier = original["id"] + f"__text_{suffix}"
            used_ids.add(identifier)
            panel["id"] = identifier
            panel["position"] = len(result) + 1
            layouts[identifier] = []
            result.append(panel)
            local_panels.append(panel)
            return panel
        panel = new_panel()
        y = .08
        for kind, line_index, speaker, text in pieces:
            units = list(_graphemes(text))
            offset = 0
            while units:
                if y >= .90 or len(layouts[panel["id"]]) >= 5:
                    panel, y = new_panel(), .08
                prefix = speaker + ": " if kind == "dialog" else ""
                def measure(count, units=units, kind=kind, prefix=prefix, y=y):
                    fragment = "".join(units[:count])
                    layer = {"kind": dialogue_kind if kind == "dialog" else kind, "text": prefix + fragment, "x": .08, "y": y,
                             "w": .84, "h": .84, "tail_x": .5, "tail_y": .95, "font_size": font_size}
                    doc, _ = text_document(layer, fmt["width"], fmt["height"])
                    inset = .18 if layer["kind"] in ("dialog", "thought") else .06
                    layer["h"] = math.ceil(doc.size().height() / (1 - 2 * inset) + 2) / fmt["height"]
                    return layer
                low, high = 0, len(units)
                while low < high:
                    middle = (low + high + 1) // 2
                    candidate = measure(middle)
                    if candidate["h"] <= .92 - y and len(candidate["text"]) <= policy["max_text_characters"]:
                        low = middle
                    else:
                        high = middle - 1
                if not low:
                    if y == .08:
                        raise ComicError("text_layout", "Jediný znak nebo mluvčí přesahuje tiskovou plochu panelu.")
                    panel, y = new_panel(), .08
                    continue
                fragment = "".join(units[:low])
                layer = measure(low)
                if layer["text"].strip():
                    layouts[panel["id"]].append(layer)
                if kind == "dialog":
                    panel["dialogue"].append({"speaker": speaker, "text": fragment})
                else:
                    panel["caption"] += fragment
                fragments.append({"source_panel_id": original["id"], "panel_id": panel["id"],
                                  "field": kind, "dialogue_index": line_index,
                                  "start": offset, "end": offset + len(fragment)})
                offset += len(fragment)
                units = units[low:]
                y += layer["h"] + .02
    return {"panels": result}, {"version": 1, "format": fmt, "overlays": layouts, "fragments": fragments}
