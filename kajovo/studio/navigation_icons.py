"""Jemné piktogramy navigace; značku aplikace dodává samostatný rastrový PNG."""

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap


def navigation_icon(key):
    """Ikona obsahuje normální i aktivní podobu pro výlučnou navigaci."""
    icon = QIcon()
    for mode, color in ((QIcon.Normal, "#B8C7D9"), (QIcon.Active, "#5EEAD4")):
        pixmap = QPixmap(48, 48)
        pixmap.fill(Qt.transparent)
        pixmap.setDevicePixelRatio(2)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor(color), 1.65, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        painter.setBrush(Qt.NoBrush)
        if key in {"run", "converter"}:
            painter.drawRoundedRect(QRectF(5, 3, 14, 18), 2, 2)
            for y in (8, 12, 16):
                painter.drawLine(QPointF(9, y), QPointF(15, y))
        elif key == "photos":
            painter.drawRoundedRect(QRectF(3, 4, 18, 16), 2, 2)
            painter.drawEllipse(QPointF(8, 9), 1.5, 1.5)
            path = QPainterPath(QPointF(4, 18))
            path.lineTo(10, 12)
            path.lineTo(14, 16)
            path.lineTo(17, 13)
            path.lineTo(20, 17)
            painter.drawPath(path)
        elif key == "comics":
            path = QPainterPath()
            path.moveTo(8, 19)
            path.cubicTo(1, 17, 1, 5, 8, 4)
            path.cubicTo(24, 0, 26, 19, 13, 19)
            path.lineTo(6, 22)
            path.lineTo(8, 19)
            painter.drawPath(path)
        elif key == "cascade":
            path = QPainterPath(QPointF(7, 3))
            path.lineTo(20, 12)
            path.lineTo(7, 21)
            path.closeSubpath()
            painter.drawPath(path)
        elif key == "resources":
            path = QPainterPath(QPointF(3, 7))
            for x, y in ((3, 4), (10, 4), (13, 7), (21, 7), (21, 20), (3, 20), (3, 7)):
                path.lineTo(x, y)
            painter.drawPath(path)
        elif key == "batch":
            for x, y in ((3, 8), (7, 4)):
                painter.drawRoundedRect(QRectF(x, y, 14, 13), 2, 2)
        elif key == "search":
            painter.drawEllipse(QPointF(10, 10), 6, 6)
            painter.drawLine(QPointF(15, 15), QPointF(21, 21))
        elif key == "history":
            painter.drawEllipse(QPointF(12, 12), 9, 9)
            painter.drawLine(QPointF(12, 6), QPointF(12, 12))
            painter.drawLine(QPointF(12, 12), QPointF(17, 14))
        elif key == "versions":
            for x, y in ((6, 4), (6, 20), (18, 6)):
                painter.drawEllipse(QPointF(x, y), 2.2, 2.2)
            painter.drawLine(QPointF(6, 7), QPointF(6, 17))
            path = QPainterPath(QPointF(6, 15))
            path.cubicTo(6, 8, 18, 16, 18, 9)
            painter.drawPath(path)
        elif key == "models":
            painter.drawRoundedRect(QRectF(6, 6, 12, 12), 2, 2)
            painter.drawRect(QRectF(9, 9, 6, 6))
            for axis in (8, 12, 16):
                for a, b in (((axis, 3), (axis, 6)), ((axis, 18), (axis, 21)),
                             ((3, axis), (6, axis)), ((18, axis), (21, axis))):
                    painter.drawLine(QPointF(*a), QPointF(*b))
        elif key == "settings":
            path = QPainterPath()
            for index in range(32):
                angle = index * math.tau / 32
                radius = 10 if index % 4 in (1, 2) else 8
                point = QPointF(12 + math.cos(angle) * radius, 12 + math.sin(angle) * radius)
                if index:
                    path.lineTo(point)
                else:
                    path.moveTo(point)
            path.closeSubpath()
            painter.drawPath(path)
            painter.drawEllipse(QPointF(12, 12), 3, 3)
        elif key == "help":
            painter.drawEllipse(QPointF(12, 12), 9, 9)
            font = QFont("Montserrat")
            font.setPixelSize(17)
            painter.setFont(font)
            painter.drawText(QRectF(3, 1, 18, 22), Qt.AlignCenter, "?")
        else:
            painter.drawEllipse(QPointF(12, 12), 4, 4)
        painter.end()
        icon.addPixmap(pixmap, mode, QIcon.Off)
        icon.addPixmap(pixmap, mode, QIcon.On)
    return icon
