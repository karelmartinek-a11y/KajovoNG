"""Atomické reference zadání a společné vykreslování textových vrstev."""
from __future__ import annotations

import copy
import json

from PySide6.QtCore import QByteArray, QMimeData, QPointF, QRectF, QUrl, Qt, Signal
from PySide6.QtGui import (QColor, QFont, QImage, QPainter, QPainterPath, QPen, QPixmap,
                          QTextCharFormat, QTextCursor, QTextDocument, QTextFormat, QTextImageFormat, QFontMetricsF)
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDoubleSpinBox, QFormLayout, QGraphicsItem,
                              QGraphicsObject, QGraphicsScene, QGraphicsView, QHBoxLayout, QListWidget,
                              QPlainTextEdit, QPushButton, QTextEdit, QVBoxLayout, QWidget)

from kajovo.core.comic_types import ComicError, validate_document, validate_overlays

TOKEN_TYPE = QTextFormat.ImageObject
ENTITY_PROPERTY = QTextFormat.UserProperty + 20
MIME = "application/x-kajovo-comic-prompt-v1"


class EntityPromptEdit(QTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptRichText(False)
        self.setAccessibleName("Zadání panelu s atomickými odkazy")
        self.entities = {}

    def insert_entity(self, entity):
        self.entities[entity["id"]] = entity
        font = self.document().defaultFont()
        metrics = QFontMetricsF(font)
        width, height = int(metrics.horizontalAdvance(entity["name"]) + 24), int(metrics.height() + 10)
        raster = QImage(width * 2, height * 2, QImage.Format_ARGB32)
        raster.setDevicePixelRatio(2)
        raster.fill(Qt.transparent)
        painter = QPainter(raster)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setFont(font)
        painter.setPen(QPen(QColor("#5EEAD4")))
        painter.setBrush(QColor("#24465b"))
        rect = QRectF(1, 1, width - 2, height - 2)
        painter.drawRoundedRect(rect, 6, 6)
        painter.setPen(QColor("#F3F7FC"))
        painter.drawText(rect, Qt.AlignCenter, entity["name"])
        painter.end()
        resource = QUrl("comic-token:" + entity["id"])
        self.document().addResource(QTextDocument.ImageResource, resource, raster)
        fmt = QTextImageFormat()
        fmt.setName(resource.toString())
        fmt.setWidth(width)
        fmt.setHeight(height)
        fmt.setProperty(ENTITY_PROPERTY, {"entity_id": entity["id"], "type": entity["kind"] + "_ref", "label": entity["name"]})
        cursor = self.textCursor()
        cursor.insertText("\ufffc", fmt)
        cursor.setCharFormat(QTextCharFormat())
        self.setTextCursor(cursor)
        self.setFocus()

    def prompt_document(self, start=0, end=None):
        end = self.document().characterCount() - 1 if end is None else end
        nodes, text = [], ""
        cursor = QTextCursor(self.document())
        for pos in range(start, end):
            cursor.setPosition(pos)
            cursor.setPosition(pos + 1, QTextCursor.KeepAnchor)
            fmt = cursor.charFormat()
            if cursor.selectedText() == "\ufffc" and fmt.objectType() == TOKEN_TYPE:
                if text:
                    nodes.append({"type": "text", "text": text})
                    text = ""
                data = fmt.property(ENTITY_PROPERTY)
                nodes.append({"type": data["type"], "entity_id": data["entity_id"]})
            else:
                text += cursor.selectedText().replace("\u2029", "\n").replace("\ufffc", "")
        if text:
            nodes.append({"type": "text", "text": text})
        return validate_document({"version": 1, "nodes": nodes})

    def load_document(self, value, entities):
        validate_document(value)
        self.entities = {e["id"]: e for e in entities}
        self.clear()
        self._insert_document(value)
        self.document().clearUndoRedoStacks()

    def _insert_document(self, value):
        cursor = self.textCursor()
        cursor.beginEditBlock()
        self.setTextCursor(cursor)
        for node in value["nodes"]:
            if node["type"] == "text":
                self.insertPlainText(node["text"])
            else:
                entity = self.entities.get(node["entity_id"], {"id": node["entity_id"], "kind": node["type"].removesuffix("_ref"), "name": "Nedostupná entita"})
                self.insert_entity(entity)
        cursor.endEditBlock()

    def createMimeDataFromSelection(self):
        cursor = self.textCursor()
        data = QMimeData()
        document = self.prompt_document(cursor.selectionStart(), cursor.selectionEnd())
        data.setData(MIME, QByteArray(json.dumps(document, ensure_ascii=False).encode("utf-8")))
        data.setText("".join(n.get("text", "[" + self.entities.get(n.get("entity_id"), {}).get("name", "Entita") + "]") for n in document["nodes"]))
        return data

    def canInsertFromMimeData(self, source):
        return source.hasFormat(MIME) or source.hasText()

    def insertFromMimeData(self, source):
        if source.hasFormat(MIME):
            try:
                value = validate_document(json.loads(bytes(source.data(MIME)).decode("utf-8")))
                self._insert_document(value)
            except (ValueError, UnicodeError):
                self.insertPlainText(source.text())
        else:
            self.insertPlainText(source.text())


def paint_layer(painter, layer, width, height):
    rect = QRectF(0, 0, layer["w"] * width, layer["h"] * height)
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(QPen(QColor("black"), max(1, width / 600)))
    painter.setBrush(QColor("white"))
    if layer["kind"] == "dialog":
        tail = QPointF((layer["tail_x"] - layer["x"]) * width, (layer["tail_y"] - layer["y"]) * height)
        path = QPainterPath()
        path.moveTo(rect.width() * .4, rect.height() * .7)
        path.lineTo(tail)
        path.lineTo(rect.width() * .6, rect.height() * .7)
        path.closeSubpath()
        painter.drawPath(path)
        painter.drawEllipse(rect)
    elif layer["kind"] == "thought":
        painter.drawEllipse(rect)
        tail = QPointF((layer["tail_x"] - layer["x"]) * width, (layer["tail_y"] - layer["y"]) * height)
        painter.drawEllipse(tail, width * .009, width * .009)
    elif layer["kind"] == "caption":
        painter.drawRect(rect)
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
    overflow = doc.size().height() > inner.height()
    painter.translate(inner.topLeft())
    doc.drawContents(painter, QRectF(0, 0, inner.width(), inner.height()))
    painter.restore()
    return overflow


def render_panel(data, layers):
    validate_overlays(layers)
    image = QImage.fromData(data)
    if image.isNull():
        raise ComicError("corrupt_image", "Panel nelze vykreslit.")
    image = image.convertToFormat(QImage.Format_ARGB32)
    painter = QPainter(image)
    overflowing = []
    try:
        for index, layer in enumerate(layers):
            painter.save()
            painter.translate(layer["x"] * image.width(), layer["y"] * image.height())
            if paint_layer(painter, layer, image.width(), image.height()):
                overflowing.append(index + 1)
            painter.restore()
    finally:
        painter.end()
    if overflowing:
        raise ComicError("text_overflow", "Text se nevejde do prvků: " + ", ".join(map(str, overflowing)) + ". Zvětšete bublinu nebo zmenšete písmo.")
    return image


class BalloonItem(QGraphicsObject):
    moved = Signal()

    def __init__(self, layer, size):
        super().__init__()
        self.layer, self.size = layer, size
        self.setFlag(QGraphicsItem.ItemIsMovable)
        self.setFlag(QGraphicsItem.ItemIsSelectable)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges)
        self.setPos(layer["x"] * size.width(), layer["y"] * size.height())

    def boundingRect(self):
        rect = QRectF(0, 0, self.layer["w"] * self.size.width(), self.layer["h"] * self.size.height())
        if self.layer["kind"] in ("dialog", "thought"):
            tail = QPointF((self.layer["tail_x"] - self.layer["x"]) * self.size.width(),
                           (self.layer["tail_y"] - self.layer["y"]) * self.size.height())
            rect = rect.united(QRectF(tail.x() - 4, tail.y() - 4, 8, 8))
        return rect.adjusted(-4, -4, 4, 4)

    def paint(self, painter, option, widget=None):
        overflow = paint_layer(painter, self.layer, self.size.width(), self.size.height())
        if self.isSelected() or overflow:
            painter.setPen(QPen(QColor("red" if overflow else "#5EEAD4"), 2, Qt.DashLine))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(self.boundingRect())

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionChange:
            return QPointF(max(0, min(value.x(), (1 - self.layer["w"]) * self.size.width())),
                           max(0, min(value.y(), (1 - self.layer["h"]) * self.size.height())))
        return super().itemChange(change, value)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        self.layer["x"] = self.x() / self.size.width()
        self.layer["y"] = self.y() / self.size.height()
        self.moved.emit()


class OverlayEditor(QWidget):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.layers, self.loading = [], False
        self.default_bold = False
        self.image = QImage(1000, 700, QImage.Format_RGB32)
        self.image.fill(QColor("#ddd"))
        root = QVBoxLayout(self)
        self.scene = QGraphicsScene(self)
        self.view = QGraphicsView(self.scene)
        self.view.setMinimumHeight(260)
        self.view.setAccessibleName("Náhled panelu a posun bublin")
        root.addWidget(self.view, 1)
        row = QHBoxLayout()
        self.listing = QListWidget()
        self.listing.setMaximumHeight(100)
        self.listing.setSelectionMode(QAbstractItemView.SingleSelection)
        row.addWidget(self.listing)
        controls = QVBoxLayout()
        self.kind = QComboBox()
        for label, kind in (("Dialog", "dialog"), ("Myšlenka", "thought"), ("Titulek", "caption"), ("SFX", "sfx")):
            self.kind.addItem(label, kind)
        controls.addWidget(self.kind)
        for text, callback in (("Přidat text", self.add_layer), ("Odstranit text", self.remove_layer)):
            button = QPushButton(text)
            button.clicked.connect(callback)
            controls.addWidget(button)
        row.addLayout(controls)
        root.addLayout(row)
        self.text = QPlainTextEdit()
        self.text.setPlaceholderText("Přesné znění včetně diakritiky")
        self.text.setMaximumHeight(85)
        root.addWidget(self.text)
        form = QFormLayout()
        self.spins = {}
        for key, label in (("w", "Šířka %"), ("h", "Výška %"), ("font_size", "Písmo % výšky"), ("tail_x", "Hrot X %"), ("tail_y", "Hrot Y %")):
            spin = QDoubleSpinBox()
            spin.setRange(.1 if key in ("w", "h", "font_size") else 0, 100)
            spin.setDecimals(1)
            spin.valueChanged.connect(self.update_layer)
            form.addRow(label, spin)
            self.spins[key] = spin
        root.addLayout(form)
        self.text.textChanged.connect(self.update_layer)
        self.listing.currentRowChanged.connect(self.select_layer)
        self.redraw()

    def load(self, data, layers):
        self.loading = True
        self.layers = copy.deepcopy(layers)
        if data:
            self.image = QImage.fromData(data)
        else:
            self.image = QImage(1000, 700, QImage.Format_RGB32)
            self.image.fill(QColor("#ddd"))
        self.listing.clear()
        self.listing.addItems([f"{i + 1}. {v['text'][:40]}" for i, v in enumerate(self.layers)])
        self.loading = False
        self.redraw()
        self.listing.setCurrentRow(0 if self.layers else -1)

    def redraw(self):
        self.scene.clear()
        self.scene.addPixmap(QPixmap.fromImage(self.image))
        self.scene.setSceneRect(QRectF(self.image.rect()))
        for layer in self.layers:
            item = BalloonItem(layer, self.image.size())
            item.moved.connect(self.changed.emit)
            self.scene.addItem(item)
        self.view.fitInView(self.scene.sceneRect(), Qt.KeepAspectRatio)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.view.fitInView(self.scene.sceneRect(), Qt.KeepAspectRatio)

    def add_layer(self):
        self.layers.append({"kind": self.kind.currentData(), "text": "Text", "x": .08, "y": .08, "w": .4, "h": .22, "tail_x": .3, "tail_y": .4, "font_size": .028, "bold": self.default_bold})
        self.listing.addItem(f"{len(self.layers)}. Text")
        self.listing.setCurrentRow(len(self.layers) - 1)
        self.redraw()
        self.changed.emit()

    def remove_layer(self):
        index = self.listing.currentRow()
        if index >= 0:
            self.layers.pop(index)
            self.listing.takeItem(index)
            self.redraw()
            self.changed.emit()

    def select_layer(self, index):
        self.loading = True
        self.text.setEnabled(index >= 0)
        if index >= 0 and index < len(self.layers):
            layer = self.layers[index]
            self.text.setPlainText(layer["text"])
            for key, spin in self.spins.items():
                spin.setValue(layer[key] * 100)
        else:
            self.text.clear()
        self.loading = False

    def update_layer(self, *_):
        index = self.listing.currentRow()
        if self.loading or not 0 <= index < len(self.layers):
            return
        layer = self.layers[index]
        layer["text"] = self.text.toPlainText()
        for key, spin in self.spins.items():
            layer[key] = spin.value() / 100
        layer["w"] = min(layer["w"], 1 - layer["x"])
        layer["h"] = min(layer["h"], 1 - layer["y"])
        self.listing.item(index).setText(f"{index + 1}. {layer['text'][:40]}")
        self.redraw()
        self.changed.emit()
