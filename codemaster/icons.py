"""Icon pixmap helpers."""

import hashlib

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QFont, QPainter, QPixmap

from .constants import ICON_SIZE

PALETTE = ["#4f8cff", "#ff6b6b", "#1ec98b", "#ffa94d", "#9775fa", "#22b8cf"]


def pixmap_from_bytes(data, size=ICON_SIZE):
    if not data:
        return None
    pm = QPixmap()
    if pm.loadFromData(data):
        return pm.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    try:  # vector fallback (e.g. JadivCalc ships an SVG icon)
        from PyQt5.QtSvg import QSvgRenderer
        from PyQt5.QtCore import QByteArray
        renderer = QSvgRenderer(QByteArray(data))
        if renderer.isValid():
            img = QPixmap(size, size)
            img.fill(Qt.transparent)
            painter = QPainter(img)
            renderer.render(painter)
            painter.end()
            return img
    except Exception:
        pass
    return None


def placeholder_pixmap(name, size=ICON_SIZE):
    """A coloured rounded tile with the app's initial – used when no icon."""
    digest = int(hashlib.md5(name.encode("utf-8")).hexdigest(), 16)
    color = QColor(PALETTE[digest % len(PALETTE)])

    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setBrush(color)
    painter.setPen(Qt.NoPen)
    radius = size * 0.22
    painter.drawRoundedRect(0, 0, size, size, radius, radius)
    painter.setPen(QColor("white"))
    font = QFont()
    font.setPixelSize(int(size * 0.5))
    font.setBold(True)
    painter.setFont(font)
    letter = (name.strip()[:1] or "?").upper()
    painter.drawText(pm.rect(), Qt.AlignCenter, letter)
    painter.end()
    return pm
