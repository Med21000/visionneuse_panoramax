# -*- coding: utf-8 -*-
"""Triangulation dans QGIS : visées dessinées sur la carte et couche des points.

Usage : viser un objet au centre de la visionneuse, changer de photo, le viser
à nouveau ; le point d'intersection s'affiche sur la carte et peut être
enregistré dans la couche « Panoramax – points triangulés ».
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
from qgis.PyQt.QtGui import QBrush, QColor, QPainter, QPen

from . import triangulation

WGS84 = QgsCoordinateReferenceSystem("EPSG:4326")
COLOR = QColor(229, 57, 53)  # rouge, distinct du bleu du curseur et de l'orange du filaire
LAYER_NAME = "Panoramax – points triangulés"
LAYER_KEY = "visionneuse_panoramax/kind"
LAYER_FIELDS = (
    "field=nb_visees:integer&field=angle:double&field=incert_m:double&field=ecart_m:double"
    "&field=dist_max_m:double&field=photos:string&field=date_mesure:string&field=commentaire:string")
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
