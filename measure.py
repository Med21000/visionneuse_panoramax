# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Cédric COCHART
# SPDX-License-Identifier: GPL-2.0-or-later
"""Mesures dans QGIS : triangulation, largeur et hauteur.

- Triangulation : viser un objet au centre de la visionneuse, changer de photo,
  le viser à nouveau ; le point d'intersection s'affiche sur la carte et peut
  être enregistré dans la couche « Panoramax – points triangulés ».
- Largeur / hauteur : deux clics dans la photo, prolongés jusqu'au terrain
  (voir ground.py et terrain.py) ; les points s'affichent sur la carte.
"""

from datetime import datetime

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsFeature,
    QgsGeometry,
    QgsMarkerSymbol,
    QgsPointXY,
    QgsProject,
    QgsVectorLayer,
)
from qgis.gui import QgsMapCanvasItem
from qgis.PyQt.QtCore import QPointF, QRectF, Qt
from qgis.PyQt.QtGui import QBrush, QColor, QFontMetricsF, QPainter, QPen

from . import ground, triangulation

WGS84 = QgsCoordinateReferenceSystem("EPSG:4326")
COLOR = QColor(229, 57, 53)  # rouge, distinct du bleu du curseur et de l'orange du filaire
LAYER_NAME = "Panoramax – points triangulés"
LAYER_KEY = "visionneuse_panoramax/kind"
LAYER_FIELDS = (
    "field=nb_visees:integer&field=angle:double&field=incert_m:double&field=ecart_m:double"
    "&field=dist_max_m:double&field=photos:string&field=date_mesure:string&field=commentaire:string")
DEFAULT_CAMERA_HEIGHT = 1.9  # hauteur de caméra par défaut (m)
LONE_RAY = 60.0  # longueur (m) d'une visée tant qu'elle n'en croise aucune autre


def _num(value, digits=1):
    return "{:.{}f}".format(value, digits).replace(".", ",")


class SightingsItem(QgsMapCanvasItem):
    """Visées (pointillés) et point triangulé dessinés sur la carte."""

    def __init__(self, canvas):
        super().__init__(canvas)
        self._canvas = canvas
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
        dash = QPen(COLOR, 2, Qt.PenStyle.DashLine)
        for p0, p1 in self._lines:
            painter.setPen(halo)
            painter.drawLine(p0, p1)
            painter.setPen(dash)
            painter.drawLine(p0, p1)
        painter.setPen(QPen(QColor(255, 255, 255), 1.5))
        painter.setBrush(QBrush(COLOR))
        for p in self._origins:
            painter.drawEllipse(p, 4, 4)
        if self._target is not None:
            c = self._target
            painter.setPen(QPen(QColor(255, 255, 255), 2.5))
            painter.drawEllipse(c, 7, 7)
            painter.setPen(QPen(COLOR, 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(c, 7, 7)
            painter.drawLine(QPointF(c.x() - 11, c.y()), QPointF(c.x() + 11, c.y()))
            painter.drawLine(QPointF(c.x(), c.y() - 11), QPointF(c.x(), c.y() + 11))

    def remove(self):
        scene = self._canvas.scene()
        if scene is not None:
            scene.removeItem(self)


class Triangulator:
    """Visées en cours, résultat et enregistrement dans la couche."""

    def __init__(self, canvas):
        self.canvas = canvas
        self.sightings = []  # dicts : pic, lon, lat, heading
        self.result = None
        self.error = None
        self.item = None

    # ------------------------------------------------------------------
    def add(self, sighting):
        """Ajoute une visée (remplace celle déjà faite depuis la même photo)."""
        self.sightings = [s for s in self.sightings if s["pic"] != sighting["pic"]] + [sighting]
        self._compute()
        return self.status()

    def clear(self):
        self.sightings, self.result, self.error = [], None, None
        self._draw()

    def remove(self):
        if self.item is not None:
            self.item.remove()
            self.item = None

    def _compute(self):
        self.result, self.error = None, None
        if len(self.sightings) >= 2:
            try:
                self.result = triangulation.solve(self.sightings)
            except triangulation.TriangulationError as exc:
                self.error = str(exc)
        self._draw()

    def _draw(self):
        if self.item is None:
            if not self.sightings:
                return
            self.item = SightingsItem(self.canvas)
        rays = []
        for i, s in enumerate(self.sightings):
            length = self.result["distances"][i] * 1.15 + 3 if self.result else LONE_RAY
            end = triangulation.offset(s["lon"], s["lat"], s["heading"], length)
            rays.append((s["lon"], s["lat"]) + end)
        self.item.set_data(rays, (self.result["lon"], self.result["lat"]) if self.result else None)

    # ------------------------------------------------------------------
    def status(self):
        n = len(self.sightings)
        if n == 0:
            return ("Placez le réticule sur l'objet puis « Viser ». Recommencez depuis une autre "
                    "photo, sur le côté de l'objet.")
        if n == 1:
            return "1 visée. Changez de photo, visez le même objet puis « Viser » à nouveau."
        if self.error:
            return "{} visées : {}".format(n, self.error)
        r = self.result
        text = "{} visées : point à {} m · croisement {}° · incertitude ±{} m".format(
            n, " / ".join(_num(d) for d in r["distances"]), _num(r["angle"], 0), _num(r["uncertainty"]))
        if n > 2:
            text += " · écart des visées {} m".format(_num(r["rms"]))
        if r["angle"] < 15:
            text += ". Croisement faible : une visée plus latérale améliorerait le point."
        return text

    def save(self):
        """Enregistre le point courant. Retourne (succès, message)."""
        if self.result is None:
            return False, self.error or "Il faut au moins deux visées qui se croisent."
        layer = self._layer()
        r = self.result
        geom = QgsGeometry.fromPointXY(QgsPointXY(r["lon"], r["lat"]))
        geom.transform(QgsCoordinateTransform(WGS84, layer.crs(), QgsProject.instance()))
        values = {
            "nb_visees": len(self.sightings),
            "angle": round(r["angle"], 1),
            "incert_m": round(r["uncertainty"], 2),
            "ecart_m": round(r["rms"], 2),
            "dist_max_m": round(max(r["distances"]), 1),
            "photos": ",".join(s["pic"] for s in self.sightings),
            "date_mesure": datetime.now().isoformat(timespec="seconds"),
        }
        feat = QgsFeature(layer.fields())
        feat.setGeometry(geom)
        for name, value in values.items():
            if layer.fields().indexOf(name) >= 0:
                feat.setAttribute(name, value)
        ok = layer.addFeature(feat) if layer.isEditable() else layer.dataProvider().addFeatures([feat])[0]
        if not ok:
            return False, "Impossible d'ajouter le point à la couche « {} ».".format(layer.name())
        layer.updateExtents()
        layer.triggerRepaint()
        self.clear()
        return True, "Point enregistré dans « {} » (incertitude ±{} m).".format(layer.name(), _num(r["uncertainty"]))

    @staticmethod
    def _layer():
        project = QgsProject.instance()
        for layer in project.mapLayers().values():
            if isinstance(layer, QgsVectorLayer) and layer.isValid() and layer.customProperty(LAYER_KEY) == "triangulation":
                return layer
        crs = project.crs() if project.crs().isValid() else WGS84
        layer = QgsVectorLayer("Point?crs={}&{}".format(crs.authid() or "EPSG:4326", LAYER_FIELDS), LAYER_NAME, "memory")
        layer.setCustomProperty(LAYER_KEY, "triangulation")
        layer.renderer().setSymbol(QgsMarkerSymbol.createSimple(
            {"name": "circle", "color": COLOR.name(), "outline_color": "white", "outline_width": "0.4", "size": "2.8"}))
        project.addMapLayer(layer)
        return layer


# --------------------------------------------------------------------------
# Largeur et hauteur
# --------------------------------------------------------------------------
class MeasureItem(QgsMapCanvasItem):
    """Points mesurés au sol, segment éventuel et étiquette du résultat."""

    def __init__(self, canvas):
        super().__init__(canvas)
        self._canvas = canvas
        self.points, self.label = [], ""
        self.extra = None  # point cliqué non relié (largeur : second bord)
        self._screen = []
        self._extra = None
        self._rect = QRectF()
        self.setZValue(990)

    def set_data(self, points, label, extra=None):
        self.points, self.label, self.extra = points, label, extra
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
            self._extra = self.toCanvasCoordinates(ct.transform(QgsPointXY(*self.extra))) if self.extra else None
        except Exception:
            self._screen, self._extra = [], None
        self.prepareGeometryChange()
        self.setPos(0, 0)
        if self._screen:
            pts = self._screen + ([self._extra] if self._extra is not None else [])
            xs, ys = [p.x() for p in pts], [p.y() for p in pts]
            rect = QRectF(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)).adjusted(-10, -10, 10, 10)
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
        if len(self._screen) == 2:
            painter.setPen(QPen(QColor(255, 255, 255, 220), 5))
            painter.drawLine(*self._screen)
            painter.setPen(QPen(COLOR, 2.5))
            painter.drawLine(*self._screen)
        painter.setPen(QPen(QColor(255, 255, 255), 2))
        painter.setBrush(QBrush(COLOR))
        for p in self._screen:
            painter.drawEllipse(p, 5, 5)
        if self._extra is not None:
            # Second bord cliqué, relié en pointillés au pied de la perpendiculaire
            painter.setPen(QPen(COLOR, 1.5, Qt.PenStyle.DotLine))
            painter.drawLine(self._screen[-1], self._extra)
            painter.setPen(QPen(COLOR, 2))
            painter.setBrush(QColor(255, 255, 255))
            painter.drawEllipse(self._extra, 4, 4)
        rect = self._label_rect()
        if not rect.isNull():
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(255, 255, 255, 230))
            painter.drawRoundedRect(rect, 4, 4)
            painter.setPen(QPen(COLOR.darker(130)))
            painter.setFont(self._canvas.font())
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self.label)

    def remove(self):
        scene = self._canvas.scene()
        if scene is not None:
            scene.removeItem(self)


class GroundMeasure:
    """Deux clics dans la photo : largeur perpendiculaire à la route ("width") ou
    hauteur d'un objet ("height").

    Chaque clic qui doit toucher le sol porte le profil du terrain le long de sa
    visée (clés "profile" et "terrain", voir terrain.py) ; sans profil, sol plat.
    """

    def __init__(self, canvas):
        self.canvas = canvas
        self.mode = "width"
        self.camera_height = DEFAULT_CAMERA_HEIGHT
        self.clicks = []  # dicts : pic, lon, lat, yaw, elev, profile, terrain
        self.result = None
        self.error = None
        self.notice = None  # avertissement sur la source d'altitude
        self.item = None

    def set_mode(self, mode):
        self.mode = mode
        self.clear()

    def set_camera_height(self, value):
        self.camera_height = float(value)
        self._compute()

    def needs_profile(self):
        """Le prochain clic doit-il toucher le sol ? (pas le sommet d'un objet)"""
        return not (self.mode == "height" and len(self.clicks) == 1)

    def add_click(self, click, notice=None):
        if len(self.clicks) >= 2:
            self.clicks = []  # troisième clic : nouvelle mesure
        if self.needs_profile():
            self.notice = notice
        self.clicks.append(click)
        self._compute()
        return self.status()

    def clear(self):
        self.clicks, self.result, self.error, self.notice = [], None, None, None
        self._draw()

    def remove(self):
        if self.item is not None:
            self.item.remove()
            self.item = None

    def _compute(self):
        self.result, self.error = None, None
        try:
            if len(self.clicks) == 2:
                if self.mode == "width":
                    axis = self.clicks[0].get("axis")
                    if axis is None:
                        raise ground.GroundError("Axe de la route inconnu pour cette photo : largeur "
                                                 "impossible à calculer.")
                    self.result = ground.measure_width(self.camera_height, *self.clicks, axis)
                else:
                    self.result = ground.measure_height(self.camera_height, *self.clicks)
            elif len(self.clicks) == 1:
                ground._ground_point(self.camera_height, self.clicks[0])  # contrôle du premier clic
        except ground.GroundError as exc:
            self.error = str(exc)
            if len(self.clicks) == 1:
                self.clicks = []  # premier clic refusé : on le refait
        self._draw()

    def _draw(self):
        if self.item is None:
            if not self.clicks:
                return
            self.item = MeasureItem(self.canvas)
        points, label, extra = [], "", None
        if self.result:
            points = self.result["points"]
            label = "{} m".format(_num(self.result["value"], 2))
            extra = self.result.get("clicked")
        elif len(self.clicks) == 1 and not self.error:
            points = [ground._ground_point(self.camera_height, self.clicks[0])[1]]
        self.item.set_data(points, label, extra)

    def viewer_marks(self):
        """Repères à afficher dans la visionneuse (voir PanoramaxDock.set_measure_marks)."""
        points = []
        for c in self.clicks:
            pos = c.get("pos") or [None, None]
            points.append({"pic": c.get("pic"), "yaw": pos[0], "pitch": pos[1],
                           "abs_yaw": c["yaw"], "elev": c["elev"]})
        # Visionneuse web : seuls les clics dont la position dans la photo est connue
        label = ""
        if self.result:
            symbol = {"width": "↔ ", "height": "↕ "}.get(self.mode, "")
            label = "{}{} m ± {} m".format(symbol, _num(self.result["value"], 2), _num(self.result["uncertainty"], 2))
        # Hauteur : trait vertical sur l'objet, l'étiquette se met à côté (vers le centre de la vue)
        return {"points": points, "label": label, "beside": self.mode == "height"}

    def _terrain_label(self):
        labels = []
        for c in self.clicks:
            if c.get("profile") is not None or not labels:
                label = c.get("terrain", ground.FLAT)
                if label not in labels:
                    labels.append(label)
        return " + ".join(labels) or ground.FLAT

    def status(self):
        n = len(self.clicks)
        prefix = (self.error + " ") if self.error else ""
        if self.mode == "width":
            if n == 0:
                return prefix + "Cliquez au pied du premier bord (bordure, marquage, limite de chaussée…)."
            if n == 1 and not self.error:
                return ("Cliquez au pied du bord opposé, pas forcément juste en face : la largeur est "
                        "prise perpendiculairement à la route.")
        else:
            if n == 0:
                return prefix + "Cliquez dans la photo au pied de l'objet (au sol)."
            if n == 1 and not self.error:
                return "Cliquez au sommet de l'objet, sur la même photo."
        if self.error:
            return self.error + " Cliquez à nouveau pour recommencer."
        r = self.result
        if self.mode == "width":
            text = "Largeur : {} m (±{} m) perpendiculairement à la route · en biais {} m · axe {}° ({})".format(
                _num(r["value"], 2), _num(r["uncertainty"], 2), _num(r["oblique"], 2), _num(r["axis"], 0),
                self.clicks[0].get("axis_source") or "?")
        else:
            text = "Hauteur : {} m (±{} m) · objet à {} m".format(
                _num(r["value"], 2), _num(r["uncertainty"], 2), _num(r["ranges"][0]))
        text += ". Terrain : {}, caméra à {} m.".format(self._terrain_label(), _num(self.camera_height, 2))
        if self.notice:
            text += " ({}.)".format(self.notice)
        return text
