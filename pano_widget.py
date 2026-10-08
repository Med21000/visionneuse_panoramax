# -*- coding: utf-8 -*-
"""Mini-visionneuse native (mode sans QtWebEngine).

- Photo 360° (équirectangulaire) : on affiche une fenêtre de la sphère,
  orientable à la souris (glisser) et zoomable (molette).
- Photo classique : affichée entière, orientée selon son cap.
Émet viewChanged(cap_absolu, ouverture) pour animer le curseur sur la carte,
et clicked(cap, élévation) pour un clic sans glisser (mesures).
"""

import math

from qgis.PyQt.QtCore import QPointF, QRectF, Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor, QFont, QPainter, QPen
from qgis.PyQt.QtWidgets import QSizePolicy, QWidget

MIN_FOV, MAX_FOV = 25.0, 120.0


def _event_pos(event):
    """Position de la souris : position() en Qt6, localPos() en Qt5 (QGIS 3)."""
    return event.position() if hasattr(event, "position") else event.localPos()


class PanoWidget(QWidget):
    viewChanged = pyqtSignal(float, float)
    clicked = pyqtSignal(float, float)  # cap absolu, élévation (degrés)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(200)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.pixmap = None
        self.azimuth = 0.0  # cap du centre de l'image
        self.is360 = False
        self.flat_fov = 70.0
        self.heading = None  # cap absolu affiché (360°)
        self.fov = 90.0
        self.crosshair = False  # réticule de visée (triangulation), aussi sur les photos classiques
        self.message = "Cliquez sur « Choisir sur la carte » puis sur la carte."
        self._drag = None
        self._press = None

    # ------------------------------------------------------------------
    def set_message(self, text):
        self.pixmap = None
        self.message = text
        self.update()

    def set_image(self, pixmap, azimuth, is360, flat_fov=None):
        self.pixmap = pixmap
        self.azimuth = float(azimuth or 0.0)
        self.is360 = bool(is360) or (pixmap.width() >= 1.9 * pixmap.height())
        self.flat_fov = float(flat_fov or 70.0)
        if self.is360:
            # On conserve l'orientation en passant d'une photo à la suivante
            if self.heading is None:
                self.heading = self.azimuth
        self.message = ""
        self.update()
        self._emit()

    def current_view(self):
        if self.is360:
            return (self.heading if self.heading is not None else self.azimuth), self.fov
        return self.azimuth, self.flat_fov

    def direction_at(self, pos):
        """(cap, élévation) du point affiché en pos, ou None hors de l'image."""
        if self.pixmap is None or self.pixmap.isNull() or self.width() == 0:
            return None
        vw, vh = float(self.width()), float(self.height())
        if self.is360:
            # Même découpage que _paint_360 : fenêtre équirectangulaire centrée sur l'horizon
            H = float(self.pixmap.height())
            vfov = min(170.0, self.fov * vh / max(vw, 1.0))
            vfov = min(H, vfov / 180.0 * H) / H * 180.0
            yaw = self.heading + (pos.x() - vw / 2.0) / vw * self.fov
            return yaw % 360, -(pos.y() - vh / 2.0) / vh * vfov
        pw, ph = float(self.pixmap.width()), float(self.pixmap.height())
        scale = min(vw / pw, vh / ph)
        ix, iy = (pos.x() - vw / 2.0) / scale, (pos.y() - vh / 2.0) / scale
        if abs(ix) > pw / 2.0 or abs(iy) > ph / 2.0:
            return None
        f = (pw / 2.0) / math.tan(math.radians(self.flat_fov) / 2.0)  # caméra supposée horizontale
        return ((self.azimuth + math.degrees(math.atan2(ix, f))) % 360,
                math.degrees(math.atan2(-iy, math.hypot(ix, f))))

    def _emit(self):
        if self.pixmap is not None:
            h, f = self.current_view()
            self.viewChanged.emit(float(h % 360), float(f))

    # ------------------------------------------------------------------
    # Interaction
    # ------------------------------------------------------------------
    def mousePressEvent(self, event):  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._press = _event_pos(event)
        if self.is360 and event.button() == Qt.MouseButton.LeftButton:
            self._drag = _event_pos(event)
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, event):  # noqa: N802
        if self._drag is None or not self.is360 or self.width() == 0:
            return
        pos = _event_pos(event)
        dx = pos.x() - self._drag.x()
        self._drag = pos
        # Glisser vers la droite = regarder vers la gauche, comme dans la visionneuse web
        self.heading = (self.heading - dx * self.fov / self.width()) % 360
        self.update()
        self._emit()

    def mouseReleaseEvent(self, event):  # noqa: N802
        pos = _event_pos(event)
        if self._press is not None and abs(pos.x() - self._press.x()) + abs(pos.y() - self._press.y()) < 5:
            direction = self.direction_at(pos)
            if direction is not None:
                self.clicked.emit(*direction)
        self._press = None
        self._drag = None
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    def wheelEvent(self, event):  # noqa: N802
        if not self.is360:
            return
        steps = event.angleDelta().y() / 120.0
        self.fov = max(MIN_FOV, min(MAX_FOV, self.fov * (0.9 ** steps)))
        self.update()
        self._emit()

    def turn(self, delta):
        if self.is360 and self.heading is not None:
            self.heading = (self.heading + delta) % 360
            self.update()
            self._emit()

    # ------------------------------------------------------------------
    # Dessin
    # ------------------------------------------------------------------
    def paintEvent(self, event):  # noqa: N802
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(30, 30, 30))
        if self.pixmap is None or self.pixmap.isNull():
            p.setPen(QColor(220, 220, 220))
            p.drawText(self.rect().adjusted(10, 10, -10, -10),
                       Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, self.message)
            return
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        if self.is360:
            self._paint_360(p)
        else:
            self._paint_flat(p)
        self._paint_compass(p)

    def _paint_flat(self, p):
        pw, ph = self.pixmap.width(), self.pixmap.height()
        scale = min(self.width() / pw, self.height() / ph)
        w, h = pw * scale, ph * scale
        target = QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)
        p.drawPixmap(target, self.pixmap, QRectF(0, 0, pw, ph))

    def _paint_360(self, p):
        W, H = float(self.pixmap.width()), float(self.pixmap.height())
        vw, vh = float(self.width()), float(self.height())
        src_w = self.fov / 360.0 * W
        vfov = min(170.0, self.fov * vh / max(vw, 1.0))
        src_h = min(H, vfov / 180.0 * H)
        src_y = (H - src_h) / 2.0
        center_x = (W / 2.0 + (self.heading - self.azimuth) / 360.0 * W) % W
        x0 = center_x - src_w / 2.0
        # La fenêtre peut chevaucher le bord de l'image : on la dessine en deux morceaux
        pieces = []
        if x0 < 0:
            pieces = [(x0 + W, -x0), (0.0, src_w + x0)]
        elif x0 + src_w > W:
            pieces = [(x0, W - x0), (0.0, x0 + src_w - W)]
        else:
            pieces = [(x0, src_w)]
        tx = 0.0
        for sx, sw in pieces:
            tw = sw / src_w * vw
            p.drawPixmap(QRectF(tx, 0, tw, vh), self.pixmap, QRectF(sx, src_y, sw, src_h))
            tx += tw

    def _paint_compass(self, p):
        heading, fov = self.current_view()
        names = ["N", "NE", "E", "SE", "S", "SO", "O", "NO"]
        label = "{} {:.0f}°  ·  ouverture {:.0f}°".format(names[int(((heading % 360) + 22.5) // 45) % 8],
                                                           heading % 360, fov)
        font = QFont(self.font())
        font.setBold(True)
        p.setFont(font)
        rect = QRectF(8, 8, 220, 22)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 140))
        p.drawRoundedRect(rect, 6, 6)
        p.setPen(QPen(QColor(255, 255, 255)))
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter, label)
        if self.is360 or self.crosshair:
            # Repère central (axe de visée)
            c = QPointF(self.width() / 2.0, self.height() / 2.0)
            p.setPen(QPen(QColor(229, 57, 53) if self.crosshair else QColor(255, 111, 0, 200), 1.5))
            p.drawLine(QPointF(c.x(), c.y() - 8), QPointF(c.x(), c.y() + 8))
            p.drawLine(QPointF(c.x() - 8, c.y()), QPointF(c.x() + 8, c.y()))
