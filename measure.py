# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Cédric COCHART
# SPDX-License-Identifier: GPL-2.0-or-later
"""Dessin des mesures sur la carte QGIS et enregistrement des points triangulés.

La logique des mesures est celle de la session du cœur (core/session.py) :
- visées de triangulation (pointillés rouges) et visées du calage du cap (pointillés noirs) ;
- mesures (hauteur, largeur) : points, trait et étiquette du résultat ;
- points triangulés gardés (disque rouge et nom), distances entre eux, droite de mesure
  d'une largeur (bande bleue) ;
- points et distances enregistrés dans les couches « Panoramax – points triangulés » et
  « Panoramax – mesures ».
"""

import math

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsFeature,
    QgsGeometry,
    QgsMarkerSymbol,
    QgsPointXY,
    QgsProject,
)
from qgis.gui import QgsMapCanvasItem
from qgis.PyQt.QtCore import QPointF, QRectF, Qt
from qgis.PyQt.QtGui import QBrush, QColor, QFont, QFontMetricsF, QPainter, QPen

from . import output
from .core import tools

WGS84 = QgsCoordinateReferenceSystem("EPSG:4326")
COLOR = QColor(229, 57, 53)  # rouge, distinct du bleu du curseur et de l'orange du filaire
CROSS = QColor(255, 235, 0)  # croix jaune vif des points cliqués, comme dans la visionneuse (#ffeb00)
# Trait perpendiculaire à la route (largeur perpendiculaire) : il ne relie pas les points
# cliqués, d'où une autre couleur que le trait rouge de la visionneuse
TRACE = QColor(0, 188, 230)
TRACE_LABEL = "perpendiculaire à la route"
WALL = QColor(156, 39, 176)  # façade (définie dans la photo ou d'après le cadastre) : tirets violets
CALIBRATION_COLOR = QColor(0, 0, 0)  # visées du calage du cap : noires, distinctes de la triangulation
LAYER_NAME = "Panoramax – points triangulés"
LAYER_FIELDS = (
    "field=nom:string&field=nb_visees:integer&field=angle:double&field=incert_m:double&field=ecart_m:double"
    "&field=dist_max_m:double&field=gps_m:double&field=photos:string&field=date_mesure:string&field=commentaire:string")




class SightingsItem(QgsMapCanvasItem):
    """Visées (pointillés) et point triangulé dessinés sur la carte ; en noir pour les visées du
    calage du cap, à ne pas prendre pour une triangulation."""

    def __init__(self, canvas, color=COLOR):
        super().__init__(canvas)
        self._canvas = canvas
        self.color = color
        self.rays = []  # [(lon0, lat0, lon1, lat1)]
        self.point = None  # (lon, lat)
        self._lines, self._origins, self._target = [], [], None
        self._rect = QRectF()
        self.setZValue(990)  # sous le curseur de vue

    def set_data(self, rays, point):
        self.rays, self.point = rays, point
        self.updatePosition()

    def updatePosition(self):  # noqa: N802 (appelé par QGIS à chaque changement d'emprise)
        ct = QgsCoordinateTransform(WGS84, self._canvas.mapSettings().destinationCrs(), QgsProject.instance())

        def screen(lon, lat):
            return self.toCanvasCoordinates(ct.transform(QgsPointXY(lon, lat)))

        try:
            self._lines = [(screen(a, b), screen(c, d)) for a, b, c, d in self.rays]
            self._target = screen(*self.point) if self.point else None
        except Exception:  # hors du domaine du SCR de la carte
            self._lines, self._target = [], None
        self._origins = [p for p, _ in self._lines]
        pts = [p for line in self._lines for p in line] + ([self._target] if self._target else [])
        self.prepareGeometryChange()
        self.setPos(0, 0)
        if pts:
            xs, ys = [p.x() for p in pts], [p.y() for p in pts]
            self._rect = QRectF(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)).adjusted(-12, -12, 12, 12)
        else:
            self._rect = QRectF()
        self.update()

    def boundingRect(self):  # noqa: N802
        return self._rect

    def paint(self, painter, option=None, widget=None):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        halo = QPen(QColor(255, 255, 255, 200), 4)
        dash = QPen(self.color, 2, Qt.PenStyle.DashLine)
        for p0, p1 in self._lines:
            painter.setPen(halo)
            painter.drawLine(p0, p1)
            painter.setPen(dash)
            painter.drawLine(p0, p1)
        painter.setPen(QPen(QColor(255, 255, 255), 1.5))
        painter.setBrush(QBrush(self.color))
        for p in self._origins:
            painter.drawEllipse(p, 4, 4)
        if self._target is not None:
            c = self._target
            painter.setPen(QPen(QColor(255, 255, 255), 2.5))
            painter.drawEllipse(c, 7, 7)
            painter.setPen(QPen(self.color, 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(c, 7, 7)
            painter.drawLine(QPointF(c.x() - 11, c.y()), QPointF(c.x() + 11, c.y()))
            painter.drawLine(QPointF(c.x(), c.y() - 11), QPointF(c.x(), c.y() + 11))

    def remove(self):
        scene = self._canvas.scene()
        if scene is not None:
            scene.removeItem(self)


class Triangulator(tools.Triangulator):
    """Visées dessinées sur la carte QGIS."""

    def __init__(self, canvas):
        super().__init__()
        self.canvas = canvas

    def new_item(self):
        return SightingsItem(self.canvas)


def save_points(points, saved, parent=None):
    """Enregistre dans la couche « Panoramax – points triangulés » les points gardés de la session
    (lon, lat, attributs) qui ne le sont pas encore ; saved : ensemble des noms déjà enregistrés,
    complété. Retourne (nombre ajouté, message d'erreur ou None ; None aussi si l'utilisateur a
    renoncé à créer la couche, alors (0, "")."""
    todo = [p for p in points if p[2]["nom"] not in saved]
    if not todo:
        return 0, None
    layer, error = output.ensure_layer("triangulation", LAYER_NAME, "points_triangules", "Point",
                                       LAYER_FIELDS, _style_points, parent)
    if layer is None:
        return 0, error or ""
    to_layer = QgsCoordinateTransform(WGS84, layer.crs(), QgsProject.instance())
    for lon, lat, values in todo:
        geom = QgsGeometry.fromPointXY(QgsPointXY(lon, lat))
        geom.transform(to_layer)
        feat = QgsFeature(layer.fields())
        feat.setGeometry(geom)
        for name, value in values.items():
            if layer.fields().indexOf(name) >= 0:
                feat.setAttribute(name, value)
        if output.write_feature(layer, feat) is None:
            return 0, "Impossible d'ajouter le point {} à la couche « {} ».".format(values["nom"], layer.name())
        saved.add(values["nom"])
    return len(todo), None


def _style_points(layer):
    layer.renderer().setSymbol(QgsMarkerSymbol.createSimple(
        {"name": "circle", "color": COLOR.name(), "outline_color": "white", "outline_width": "0.4", "size": "2.8"}))


# --------------------------------------------------------------------------
# Largeur et hauteur
# --------------------------------------------------------------------------
def _draw_cross(painter, p, size=6.0):
    """Croix jaune d'un point cliqué, comme dans la visionneuse, cernée de sombre pour rester
    lisible sur l'orthophoto."""
    for pen in (QPen(QColor(0, 0, 0, 170), 4), QPen(CROSS, 2)):
        painter.setPen(pen)
        painter.drawLine(QPointF(p.x() - size, p.y()), QPointF(p.x() + size, p.y()))
        painter.drawLine(QPointF(p.x(), p.y() - size), QPointF(p.x(), p.y() + size))


class MeasureItem(QgsMapCanvasItem):
    """Points mesurés au sol, segment éventuel et étiquette du résultat."""

    def __init__(self, canvas, wall=False):
        super().__init__(canvas)
        self._canvas = canvas
        self.wall = wall  # façade : tirets, sans croix (ce ne sont pas des points cliqués)
        self.points, self.label = [], ""
        self.extras = []  # points cliqués reliés en pointillés aux bouts du trait (largeur perpendiculaire)
        self._screen = []
        self._extras = []
        self._rect = QRectF()
        self.setZValue(990)

    def set_data(self, points, label, extras=None):
        """extras : pour chaque point, le point cliqué à relier en pointillés (ou None)."""
        self.points, self.label, self.extras = points, label, list(extras or [])
        self.updatePosition()

    def _label_rect(self):
        if not self.label or not self._screen:
            return QRectF()
        xs = [p.x() for p in self._screen]
        ys = [p.y() for p in self._screen]
        size = QFontMetricsF(self._canvas.font()).size(0, self.label)
        cx, top = (min(xs) + max(xs)) / 2.0, min(ys) - 14
        return QRectF(cx - size.width() / 2 - 5, top - size.height() - 4, size.width() + 10, size.height() + 4)

    def updatePosition(self):  # noqa: N802
        ct = QgsCoordinateTransform(WGS84, self._canvas.mapSettings().destinationCrs(), QgsProject.instance())
        try:
            self._screen = [self.toCanvasCoordinates(ct.transform(QgsPointXY(*p))) for p in self.points]
            self._extras = [(i, self.toCanvasCoordinates(ct.transform(QgsPointXY(*e))))
                            for i, e in enumerate(self.extras) if e is not None and i < len(self._screen)]
        except Exception:
            self._screen, self._extras = [], []
        self.prepareGeometryChange()
        self.setPos(0, 0)
        if self._screen:
            pts = self._screen + [e for _, e in self._extras]
            xs, ys = [p.x() for p in pts], [p.y() for p in pts]
            margin = 30 if self._extras else 10  # étiquette « perpendiculaire à la route » le long du trait
            rect = QRectF(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)).adjusted(
                -margin, -margin, margin, margin)
            self._rect = rect.united(self._label_rect())
        else:
            self._rect = QRectF()
        self.update()

    def boundingRect(self):  # noqa: N802
        return self._rect

    def paint(self, painter, option=None, widget=None):
        if not self._screen:
            return
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        # Largeur perpendiculaire : trait tracé à mi-chemin des bords cliqués (couleur TRACE),
        # bords cliqués reliés en pointillés. Sinon le trait relie les points cliqués, en rouge
        # comme dans la visionneuse.
        if self.wall:
            self._paint_wall(painter)
            return
        trace = bool(self._extras)
        color = TRACE if trace else COLOR
        if len(self._screen) == 2:
            painter.setPen(QPen(QColor(255, 255, 255, 220), 5))
            painter.drawLine(*self._screen)
            painter.setPen(QPen(color, 2.5))
            painter.drawLine(*self._screen)
        for i, extra in self._extras:
            painter.setPen(QPen(color, 1.5, Qt.PenStyle.DotLine))
            painter.drawLine(self._screen[i], extra)
        if trace:
            painter.setPen(QPen(QColor(255, 255, 255), 1.5))
            painter.setBrush(QBrush(color))
            for p in self._screen:
                painter.drawEllipse(p, 3.5, 3.5)
        if trace and len(self._screen) == 2:
            self._paint_trace_label(painter, color)
        clicked = [e for _, e in self._extras] if trace else self._screen
        for p in clicked:
            _draw_cross(painter, p)
        rect = self._label_rect()
        if not rect.isNull():
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(255, 255, 255, 230))
            painter.drawRoundedRect(rect, 4, 4)
            painter.setPen(QPen(color.darker(150 if trace else 130)))
            painter.setFont(self._canvas.font())
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self.label)

    def _paint_wall(self, painter):
        if len(self._screen) == 2:
            painter.setPen(QPen(QColor(255, 255, 255, 220), 5))
            painter.drawLine(*self._screen)
            painter.setPen(QPen(WALL, 2.5, Qt.PenStyle.DashLine))
            painter.drawLine(*self._screen)
        painter.setPen(QPen(QColor(255, 255, 255), 1.5))
        painter.setBrush(QBrush(WALL))
        for p in self._screen:
            painter.drawRect(QRectF(p.x() - 3.5, p.y() - 3.5, 7, 7))
        rect = self._label_rect()
        if not rect.isNull():
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(255, 255, 255, 230))
            painter.drawRoundedRect(rect, 4, 4)
            painter.setPen(QPen(WALL.darker(130)))
            painter.setFont(self._canvas.font())
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self.label)

    def _paint_trace_label(self, painter, color):
        """« perpendiculaire à la route » le long du trait de la largeur perpendiculaire, du côté
        opposé à l'étiquette de la valeur, lisible de gauche à droite."""
        a, b = self._screen
        if a.x() > b.x():
            a, b = b, a
        length = math.hypot(b.x() - a.x(), b.y() - a.y())
        font = QFont(self._canvas.font())
        font.setPointSizeF(max(6.0, font.pointSizeF() * 0.85))
        font.setItalic(True)
        width = QFontMetricsF(font).horizontalAdvance(TRACE_LABEL)
        if length < width + 16:
            return  # trait trop court à cette échelle
        painter.save()
        painter.translate((a.x() + b.x()) / 2, (a.y() + b.y()) / 2)
        painter.rotate(math.degrees(math.atan2(b.y() - a.y(), b.x() - a.x())))
        height = QFontMetricsF(font).height()
        rect = QRectF(-width / 2 - 4, 4, width + 8, height + 2)  # sous le trait
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, 230))
        painter.drawRoundedRect(rect, 3, 3)
        painter.setPen(QPen(color.darker(150)))
        painter.setFont(font)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, TRACE_LABEL)
        painter.restore()

    def remove(self):
        scene = self._canvas.scene()
        if scene is not None:
            scene.removeItem(self)


# --------------------------------------------------------------------------
# Calage : inclinaison et cap
# --------------------------------------------------------------------------
class CalibrationTool(tools.CalibrationTool):
    """Visées vers les repères dessinées sur la carte QGIS, en noir."""

    MAP_NAME = "la carte QGIS"

    def __init__(self, canvas, calibration, current, reference=None):
        super().__init__(calibration, current, reference)
        self.canvas = canvas

    def new_item(self):
        return SightingsItem(self.canvas, CALIBRATION_COLOR)


# --------------------------------------------------------------------------
# Points triangulés gardés, distances, droite de mesure
# --------------------------------------------------------------------------
POINT_LABEL = QColor(142, 27, 27)  # fond de l'étiquette d'un point triangulé (bordeaux, comme son disque rouge)


class PointsItem(QgsMapCanvasItem):
    """Points triangulés gardés par la session (disque rouge, nom dessous), distances entre eux
    (trait rouge et étiquette) et droite de mesure de la largeur (bande bleue)."""

    def __init__(self, canvas):
        super().__init__(canvas)
        self._canvas = canvas
        self.points, self.links, self.guide = [], [], None
        self._points, self._links, self._guide = [], [], None
        self._rect = QRectF()
        self.setZValue(985)  # sous les mesures et les visées

    def set_data(self, points, links, guide=None):
        """points : [(lon, lat, nom)] ; links : [((lon, lat), (lon, lat), étiquette)] ; guide :
        extrémités (lon, lat) de la droite de mesure, ou None."""
        self.points, self.links, self.guide = list(points), list(links), guide
        self.updatePosition()

    def updatePosition(self):  # noqa: N802
        ct = QgsCoordinateTransform(WGS84, self._canvas.mapSettings().destinationCrs(), QgsProject.instance())

        def screen(lon, lat):
            return self.toCanvasCoordinates(ct.transform(QgsPointXY(lon, lat)))

        try:
            self._points = [(screen(lon, lat), name) for lon, lat, name in self.points]
            self._links = [(screen(*a), screen(*b), label) for a, b, label in self.links]
            self._guide = [screen(*p) for p in self.guide] if self.guide else None
        except Exception:  # hors du domaine du SCR de la carte
            self._points, self._links, self._guide = [], [], None
        pts = [p for p, _ in self._points] + [p for a, b, _ in self._links for p in (a, b)] + (self._guide or [])
        self.prepareGeometryChange()
        self.setPos(0, 0)
        if pts:
            xs, ys = [p.x() for p in pts], [p.y() for p in pts]
            self._rect = QRectF(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)).adjusted(-90, -40, 90, 50)
        else:
            self._rect = QRectF()
        self.update()

    def boundingRect(self):  # noqa: N802
        return self._rect

    def _label(self, painter, centre, text, background, color):
        size = QFontMetricsF(self._canvas.font()).size(0, text)
        rect = QRectF(centre.x() - size.width() / 2 - 5, centre.y() - size.height() / 2 - 2,
                      size.width() + 10, size.height() + 4)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(background)
        painter.drawRoundedRect(rect, 3, 3)
        painter.setPen(QPen(color))
        painter.setFont(self._canvas.font())
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)

    def paint(self, painter, option=None, widget=None):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if self._guide:
            pen = QPen(QColor(TRACE.red(), TRACE.green(), TRACE.blue(), 205), 7)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.drawLine(*self._guide)
        for a, b, label in self._links:
            painter.setPen(QPen(QColor(255, 255, 255, 220), 5))
            painter.drawLine(a, b)
            painter.setPen(QPen(COLOR, 2.5))
            painter.drawLine(a, b)
            self._label(painter, QPointF((a.x() + b.x()) / 2, (a.y() + b.y()) / 2 - 16), label,
                        QColor(21, 76, 121, 235), QColor(255, 255, 255))
        for p, name in self._points:
            painter.setPen(QPen(COLOR, 1.5))
            painter.setBrush(QColor(COLOR.red(), COLOR.green(), COLOR.blue(), 153))
            painter.drawEllipse(p, 6.5, 6.5)
            self._label(painter, QPointF(p.x(), p.y() + 26), name, QColor(POINT_LABEL.red(), POINT_LABEL.green(),
                                                                            POINT_LABEL.blue(), 235), QColor(255, 255, 255))

    def remove(self):
        scene = self._canvas.scene()
        if scene is not None:
            scene.removeItem(self)
