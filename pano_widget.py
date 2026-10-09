# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Cédric COCHART
# SPDX-License-Identifier: GPL-2.0-or-later
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
LIGHT_RED = QColor(255, 138, 128)  # réticule de visée (#ff8a80)
MEASURE_RED = QColor(255, 95, 82)  # trait de mesure (#ff5f52)


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
        self._measuring = False  # mesure par clics : curseur en croix fine
        # Mesures affichées : [{"pts": [(cap absolu, élévation)], "label": texte,
        # "beside": étiquette à côté du trait (hauteur), vers le centre}]
        self.marks = []

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

    def screen_pos(self, yaw, elev):
        """Position à l'écran d'une direction (cap, élévation), inverse de direction_at."""
        if self.pixmap is None or self.pixmap.isNull() or self.width() == 0:
            return None
        vw, vh = float(self.width()), float(self.height())
        if self.is360:
            H = float(self.pixmap.height())
            vfov = min(170.0, self.fov * vh / max(vw, 1.0))
            vfov = min(H, vfov / 180.0 * H) / H * 180.0
            dyaw = (yaw - self.heading + 540.0) % 360.0 - 180.0
            return QPointF(vw / 2.0 + dyaw / self.fov * vw, vh / 2.0 - elev / vfov * vh)
        pw, ph = float(self.pixmap.width()), float(self.pixmap.height())
        scale = min(vw / pw, vh / ph)
        dyaw = (yaw - self.azimuth + 540.0) % 360.0 - 180.0
        if abs(dyaw) >= 89.0:
            return None
        f = (pw / 2.0) / math.tan(math.radians(self.flat_fov) / 2.0)
        ix = f * math.tan(math.radians(dyaw))
        iy = -math.tan(math.radians(elev)) * math.hypot(ix, f)
        return QPointF(vw / 2.0 + ix * scale, vh / 2.0 + iy * scale)

    def set_measuring(self, on):
        self._measuring = bool(on)
        self.setCursor(self._base_cursor())
        self.update()

    def set_marks(self, measures):
        self.marks = [m for m in measures if m.get("pts")]
        self.update()

    def _base_cursor(self):
        return Qt.CursorShape.CrossCursor if self._measuring else Qt.CursorShape.OpenHandCursor

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
        self.setCursor(self._base_cursor())

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
        self._paint_marks(p)
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

    def _paint_marks(self, p):
        # Toutes les croix, puis tous les traits (devant les croix), puis les étiquettes
        for layer in ("cross", "line", "label"):
            for m in self.marks:
                self._paint_measure(p, m["pts"], m.get("label") or "", bool(m.get("beside")), layer)

    def _paint_measure(self, p, marks, label, beside, layer):
        pts = [self.screen_pos(y, e) for y, e in marks]
        pts = [q for q in pts if q is not None]
        if not pts:
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        # Points en croix blanches fines centrées sur le clic, le trait va d'un centre à l'autre
        # (couche "cross", "line" ou "label", voir _paint_marks).
        if layer == "cross":
            arm = 9.0
            white = QPen(QColor(255, 255, 255), 1.5)
            white.setCapStyle(Qt.PenCapStyle.FlatCap)
            p.setPen(white)
            for q in pts:
                p.drawLine(QPointF(q.x() - arm, q.y()), QPointF(q.x() + arm, q.y()))
                p.drawLine(QPointF(q.x(), q.y() - arm), QPointF(q.x(), q.y() + arm))
        if layer == "line" and len(pts) == 2:
            pen = QPen(MEASURE_RED, 2.5)
            pen.setCapStyle(Qt.PenCapStyle.FlatCap)
            p.setPen(pen)
            p.drawLine(pts[0], pts[1])
        if layer == "label" and label:
            at = QPointF((pts[0].x() + pts[-1].x()) / 2.0, (pts[0].y() + pts[-1].y()) / 2.0)
            font = QFont(self.font())
            font.setBold(True)
            p.setFont(font)
            fm = p.fontMetrics()
            # Flèche de tête allongée au double dans son sens : ↔ (largeur) en largeur,
            # ↕ (hauteur) en hauteur, l'étiquette grandit d'autant
            arrow = label[0] if label[0] in "↔↕" else ""
            rest = label[len(arrow):]
            sx, sy = (2.0, 1.0) if arrow == "↔" else (1.0, 2.0) if arrow else (1.0, 1.0)
            aw = sx * fm.horizontalAdvance(arrow) if arrow else 0
            glyph = fm.tightBoundingRect(arrow) if arrow else QRectF()
            tw = aw + fm.horizontalAdvance(rest)
            w = tw + 14
            h = max(fm.height(), sy * glyph.height()) + 4
            if beside and len(pts) == 2:
                # À côté du trait, du côté du centre de la vue, pour ne pas masquer l'objet
                x = at.x() - 14 - w if at.x() > self.width() / 2.0 else at.x() + 14
                rect = QRectF(x, at.y() - h / 2.0, w, h)
            else:
                rect = QRectF(at.x() - w / 2.0, at.y() - h - 10, w, h)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, 235))
            p.drawRoundedRect(rect, 4, 4)
            p.setPen(QPen(QColor(183, 28, 28)))
            x0 = rect.center().x() - tw / 2.0
            base = rect.top() + (h - fm.height()) / 2.0 + fm.ascent()
            p.drawText(QPointF(x0 + aw, base), rest)
            if arrow:
                # Étirement autour du centre de la flèche, centrée verticalement dans l'étiquette
                gy = glyph.top() + glyph.height() / 2.0  # centre de la flèche par rapport à la ligne de base
                p.save()
                p.translate(x0, rect.center().y())
                p.scale(sx, sy)
                p.drawText(QPointF(0, -gy), arrow)
                p.restore()

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
            # Réticule de visée (triangulation) en rouge clair, sinon repère orange
            if self.crosshair:  # cercle blanc autour du réticule, la croix entière à l'intérieur
                p.setPen(QPen(QColor(255, 255, 255), 1.5))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawEllipse(c, 9.5, 9.5)
            p.setPen(QPen(LIGHT_RED if self.crosshair else QColor(255, 111, 0, 200), 1.5))
            if self.crosshair:  # branches écartées du centre, point blanc au milieu
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    p.drawLine(QPointF(c.x() + 2.5 * dx, c.y() + 2.5 * dy), QPointF(c.x() + 8 * dx, c.y() + 8 * dy))
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(255, 255, 255))
                p.drawEllipse(c, 1.0, 1.0)
            else:
                p.drawLine(QPointF(c.x(), c.y() - 8), QPointF(c.x(), c.y() + 8))
                p.drawLine(QPointF(c.x() - 8, c.y()), QPointF(c.x() + 8, c.y()))
