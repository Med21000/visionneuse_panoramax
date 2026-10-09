# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Cédric COCHART
# SPDX-License-Identifier: GPL-2.0-or-later
"""Curseur de vue Panoramax dessiné sur la carte QGIS.

Point = position de la photo ; cône = direction et ouverture de la vue
actuellement affichée dans la visionneuse. Le curseur garde une taille
constante à l'écran et tient compte de la rotation de la carte et de la
convergence des méridiens (le nord est recalculé à chaque rafraîchissement).
"""

import math

from qgis.core import QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsPointXY, QgsProject
from qgis.gui import QgsMapCanvasItem
from qgis.PyQt.QtCore import QPointF, QRectF, Qt
from qgis.PyQt.QtGui import QBrush, QColor, QPainter, QPainterPath, QPen, QRadialGradient

WGS84 = QgsCoordinateReferenceSystem("EPSG:4326")
# Bleu vif, bien distinct de l'orange du filaire Panoramax
CURSOR_COLOR = QColor(0, 120, 255)
CURSOR_OUTLINE = QColor(0, 45, 120)


class ViewCursor(QgsMapCanvasItem):
    RADIUS = 46  # longueur du cône en pixels écran

    def __init__(self, canvas):
        super().__init__(canvas)
        self._canvas = canvas
        self.lon = None
        self.lat = None
        self.heading = None  # cap absolu en degrés (0 = nord)
        self.fov = 70.0  # ouverture horizontale en degrés
        self._screen_north = 0.0
        self.setZValue(1000)
        self.hide()

    # ------------------------------------------------------------------
    def set_position(self, lon, lat):
        self.lon, self.lat = lon, lat
        self.show()
        self.updatePosition()

    def set_view(self, heading, fov=None):
        self.heading = heading % 360 if heading is not None else None
        if fov:
            self.fov = max(5.0, min(float(fov), 360.0))
        self.update()

    # ------------------------------------------------------------------
    def updatePosition(self):  # noqa: N802 (appelé par QGIS à chaque changement d'emprise)
        if self.lon is None:
            return
        crs = self._canvas.mapSettings().destinationCrs()
        ct = QgsCoordinateTransform(WGS84, crs, QgsProject.instance())
        try:
            p0 = ct.transform(QgsPointXY(self.lon, self.lat))
            p1 = ct.transform(QgsPointXY(self.lon, self.lat + 0.0005))
        except Exception:  # point hors du domaine du SCR
            self.hide()
            return
        s0 = self.toCanvasCoordinates(p0)
        s1 = self.toCanvasCoordinates(p1)
        # Angle du nord à l'écran, en degrés dans le sens horaire depuis le haut
        self._screen_north = math.degrees(math.atan2(s1.x() - s0.x(), -(s1.y() - s0.y())))
        self.setPos(s0)
        self.update()

    def boundingRect(self):  # noqa: N802
        r = self.RADIUS + 6
        return QRectF(-r, -r, 2 * r, 2 * r)

    def paint(self, painter, option=None, widget=None):
        if self.lon is None:
            return
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = self.RADIUS

        if self.heading is not None:
            bearing = self._screen_north + self.heading  # direction à l'écran
            path = QPainterPath(QPointF(0, 0))
            rect = QRectF(-r, -r, 2 * r, 2 * r)
            if self.fov >= 359:
                path.addEllipse(rect)
            else:
                # Qt : angles anti-horaires depuis 3 h ; cap : horaire depuis 12 h
                path.arcTo(rect, 90 - bearing + self.fov / 2, -self.fov)
                path.closeSubpath()
            grad = QRadialGradient(QPointF(0, 0), r)
            c1 = QColor(CURSOR_COLOR)
            c1.setAlpha(200)
            c2 = QColor(CURSOR_COLOR)
            c2.setAlpha(40)
            grad.setColorAt(0.0, c1)
            grad.setColorAt(1.0, c2)
            painter.setBrush(QBrush(grad))
            pen = QPen(CURSOR_OUTLINE, 1.5)
            painter.setPen(pen)
            painter.drawPath(path)

            # Axe de visée
            a = math.radians(bearing)
            painter.setPen(QPen(QColor(255, 255, 255, 230), 1.6, Qt.PenStyle.DashLine))
            painter.drawLine(QPointF(0, 0), QPointF(r * 0.9 * math.sin(a), -r * 0.9 * math.cos(a)))

        # Position de la photo
        painter.setPen(QPen(QColor(255, 255, 255), 2.5))
        painter.setBrush(QBrush(CURSOR_COLOR))
        painter.drawEllipse(QPointF(0, 0), 6.5, 6.5)

    def remove(self):
        scene = self._canvas.scene()
        if scene is not None:
            scene.removeItem(self)
