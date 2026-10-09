# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Cédric COCHART
# SPDX-License-Identifier: GPL-2.0-or-later
"""Mesures dans QGIS : triangulation, largeur et hauteur.

- Triangulation : viser un objet au centre de la visionneuse, changer de photo,
  le viser à nouveau ; le point d'intersection s'affiche sur la carte et peut
  être enregistré dans la couche « Panoramax – points triangulés ».
- Largeur / hauteur : deux clics dans la photo, prolongés jusqu'au terrain
  (voir ground.py et terrain.py) ; les points s'affichent sur la carte.
- Calage : inclinaison de la photo et cap de la séquence (voir calibration.py),
  appliqués ensuite à toutes les mesures.
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

from . import calibration, geometry, ground, triangulation

WGS84 = QgsCoordinateReferenceSystem("EPSG:4326")
COLOR = QColor(229, 57, 53)  # rouge, distinct du bleu du curseur et de l'orange du filaire
LAYER_NAME = "Panoramax – points triangulés"
LAYER_KEY = "visionneuse_panoramax/kind"
LAYER_FIELDS = (
    "field=nb_visees:integer&field=angle:double&field=incert_m:double&field=ecart_m:double"
    "&field=dist_max_m:double&field=gps_m:double&field=photos:string&field=date_mesure:string&field=commentaire:string")
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
        self.sightings = []  # dicts : pic, lon, lat, heading, accuracy, precise
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
        text += " · GPS ±{} m".format(_num(r["gps"]))
        if r["gps_assumed"]:
            text += " (précision inconnue pour certaines photos : {} m supposés)".format(
                _num(triangulation.GPS_ACCURACY, 0))
        if r["angle"] < 15:
            text += ". Croisement faible : une visée plus latérale améliorerait le point."
        if r["gps"] > triangulation.GPS_WARNING:
            text += (". Position GPS des photos imprécise (±{} m) : visez depuis des photos mieux "
                     "positionnées, ou d'autres séquences.".format(_num(r["gps"])))
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
            "gps_m": round(r["gps"], 1),
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
    """Deux clics dans la photo : largeur parallèle à la route, distance directe entre deux points
    au sol ("width"), largeur perpendiculaire à la chaussée ("road") ou
    hauteur d'un objet ("height").

    Chaque clic qui doit toucher le sol porte le profil du terrain le long de sa
    visée (clés "profile" et "terrain", voir terrain.py) ; sans profil, sol plat.
    """

    def __init__(self, canvas):
        self.canvas = canvas
        self.mode = "width"
        self.camera_height = DEFAULT_CAMERA_HEIGHT  # hauteur saisie dans le panneau
        self.calibration = None  # calibration.Calibration : hauteur calée par séquence
        self.clicks = []  # dicts : pic, lon, lat, yaw, elev, profile, terrain
        self.result = None
        self.error = None
        self.notice = None  # avertissement sur la source d'altitude
        self.item = None
        self.done = []  # mesures terminées gardées à l'écran : dicts marks (visionneuse), item (carte)

    def set_mode(self, mode):
        self.finish()
        self.mode = mode
        self._draw()

    def set_camera_height(self, value):
        self.camera_height = float(value)
        self._compute()

    def needs_profile(self):
        """Le prochain clic doit-il toucher le sol ? (pas le sommet d'un objet)"""
        return not (self.mode == "height" and len(self.clicks) == 1)

    def _camera(self):
        """(hauteur de caméra, calée ?) : celle de la séquence si elle a été calée."""
        sequence = self.clicks[0].get("sequence") if self.clicks else None
        cam = self.calibration.camera(sequence) if self.calibration and sequence else None
        return (cam["height"], True) if cam else (self.camera_height, False)

    def add_click(self, click, notice=None):
        if len(self.clicks) >= 2:
            self.finish()  # troisième clic : nouvelle mesure, la précédente reste affichée
        if self.needs_profile():
            self.notice = notice
        self.clicks.append(click)
        self._compute()
        return self.status()

    def finish(self):
        """Termine la mesure en cours : réussie, elle reste affichée (visionneuse et carte)
        jusqu'à clear_all ; sinon elle est abandonnée."""
        if self.result and self.item is not None:
            self.done.append({"marks": self.viewer_marks(), "item": self.item})
            self.item = None
        self.clicks, self.result, self.error, self.notice = [], None, None, None
        self._draw()

    def clear(self):
        """Efface la mesure en cours (les mesures terminées restent affichées)."""
        self.clicks, self.result, self.error, self.notice = [], None, None, None
        self._draw()

    def clear_all(self):
        """Efface aussi toutes les mesures terminées."""
        self.clear()
        for d in self.done:
            d["item"].remove()
        self.done = []

    def done_marks(self):
        """Repères des mesures terminées, pour la visionneuse."""
        return [d["marks"] for d in self.done]

    def remove(self):
        for d in self.done:
            d["item"].remove()
        self.done = []
        if self.item is not None:
            self.item.remove()
            self.item = None

    def _compute(self):
        self.result, self.error = None, None
        try:
            if len(self.clicks) == 2:
                if self.mode == "width":
                    self.result = ground.measure_distance(self._camera()[0], *self.clicks)
                elif self.mode == "road":
                    axis = self.clicks[0].get("axis")
                    if axis is None:
                        raise ground.GroundError("Axe de la route inconnu pour cette photo : largeur "
                                                 "impossible à calculer.")
                    self.result = ground.measure_width(self._camera()[0], *self.clicks, axis)
                else:
                    self.result = ground.measure_height(self._camera()[0], *self.clicks)
            elif len(self.clicks) == 1:
                ground._ground_point(self._camera()[0], self.clicks[0])  # contrôle du premier clic
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
            points = [ground._ground_point(self._camera()[0], self.clicks[0])[1]]
        self.item.set_data(points, label, extra)

    def viewer_marks(self):
        """Repères à afficher dans la visionneuse (voir PanoramaxDock.set_measure_marks)."""
        points = []
        for c in self.clicks:
            pos = c.get("pos") or [None, None]
            raw = c.get("raw") or [c["yaw"], c["elev"]]  # repère à l'endroit cliqué, avant calage
            points.append({"pic": c.get("pic"), "yaw": pos[0], "pitch": pos[1],
                           "abs_yaw": raw[0], "elev": raw[1]})
        # Visionneuse web : seuls les clics dont la position dans la photo est connue
        label = ""
        if self.result:
            symbol = {"width": "↔ ", "road": "↔ ", "height": "↕ "}.get(self.mode, "")
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
                return prefix + "Cliquez au sol à une extrémité de l'objet."
            if n == 1 and not self.error:
                return "Cliquez au sol à l'autre extrémité : la distance directe entre les deux points est mesurée."
        elif self.mode == "road":
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
            text = "Largeur : {} m (±{} m) entre les deux points · à {} et {} m de la photo".format(
                _num(r["value"], 2), _num(r["uncertainty"], 2), _num(r["ranges"][0]), _num(r["ranges"][1]))
            if abs(r["rise"]) >= 0.05:
                text += " · dénivelé {} m".format(_num(r["rise"], 2))
        elif self.mode == "road":
            text = ("Largeur perpendiculaire à la chaussée : {} m (±{} m) · en biais {} m "
                    "· axe {}° ({})").format(
                _num(r["value"], 2), _num(r["uncertainty"], 2), _num(r["oblique"], 2), _num(r["axis"], 0),
                self.clicks[0].get("axis_source") or "?")
        else:
            text = "Hauteur : {} m (±{} m) · objet à {} m".format(
                _num(r["value"], 2), _num(r["uncertainty"], 2), _num(r["ranges"][0]))
        height, calibrated = self._camera()
        text += ". Terrain : {}, caméra à {} m{}.".format(self._terrain_label(), _num(height, 2),
                                                        " (calée)" if calibrated else "")
        if self.notice:
            text += " ({}.)".format(self.notice)
        return text


# --------------------------------------------------------------------------
# Calage : inclinaison et cap
# --------------------------------------------------------------------------
class CalibrationTool:
    """Clics de calage (voir calibration.py) : pied et sommet d'objets verticaux
    (inclinaison de la photo), ou repère cliqué dans la photo puis sur la carte
    (cap de la séquence)."""

    def __init__(self, canvas, calibration, current, reference=None):
        self.canvas = canvas
        self.calibration = calibration
        self.current = current  # () -> (photo, séquence) affichées
        self.reference = reference  # () -> ("ground" | "height", longueur connue en m)
        self.mode = "tilt"
        self.clicks = []  # clics en attente (dicts de PanoramaxDock.photoClicked)
        self.error = None
        self.last = None  # résultat du dernier calage (texte)
        self.new_camera = None  # hauteur de caméra de la séquence, juste calée (à reporter dans le panneau)
        self.item = None

    def set_mode(self, mode):
        self.mode = mode
        self.reset()

    def needs_profile(self):
        """Le prochain clic doit-il toucher le sol ? (hauteur de caméra, sauf sommet d'un objet)"""
        if self.mode != "camera":
            return False
        return self._reference()[0] == "ground" or not self.clicks

    def _reference(self):
        return self.reference() if self.reference else ("ground", 0.0)

    def waiting_map(self):
        """Le repère cliqué dans la photo attend son clic sur la carte."""
        return self.mode == "heading" and len(self.clicks) == 1

    def add_click(self, click, notice=None):
        self.error, self.last = None, None
        if self.mode == "camera":
            self._add_camera_click(click)
        elif self.mode == "heading":
            if not click.get("sequence"):
                self.error = "Séquence de la photo inconnue : recalage du cap impossible."
            else:
                self.clicks = [click]
        else:
            if self.clicks and self.clicks[0]["pic"] != click["pic"]:
                self.clicks = []  # le sommet doit être sur la même photo que le pied
            self.clicks.append(click)
            if len(self.clicks) == 2:
                bottom, top = (tuple(c["raw"]) for c in self.clicks)
                self.clicks = []
                try:
                    r = self.calibration.add_vertical(click["pic"], bottom, top)
                except calibration.CalibrationError as exc:
                    self.error = str(exc)
                else:
                    self.last = "Inclinaison de la photo : {}° vers {}°{}.".format(
                        _num(r["tilt"], 2), _num(r["toward"], 0),
                        "" if r["residual"] is None else ", écart des objets {}°".format(_num(r["residual"], 2)))
                    if r["partial"]:
                        self.last += (" Correction partielle : cliquez un autre objet vertical, à environ 90° "
                                      "du premier, pour corriger toute l'inclinaison.")
        self._draw()
        return self.status()

    def _add_camera_click(self, click):
        if not click.get("sequence"):
            self.error = "Séquence de la photo inconnue : calage de la hauteur impossible."
            return
        if self.clicks and self.clicks[0]["pic"] != click["pic"]:
            self.clicks = []  # les deux clics se font sur la même photo
        self.clicks.append(click)
        if len(self.clicks) < 2:
            return
        clicks, self.clicks = self.clicks, []
        kind, known = self._reference()
        try:
            r = self.calibration.add_camera_height(click["sequence"], kind, clicks, known)
        except calibration.CalibrationError as exc:
            self.error = str(exc)
        else:
            self.new_camera = self.calibration.camera(click["sequence"])["height"]
            self.last = "Caméra à {} m (±{} m) d'après cette référence de {} m.".format(
                _num(r["height"], 2), _num(r["sigma"], 2), _num(known, 2))
            if self.calibration.camera(click["sequence"])["count"] > 1:
                self.last += " Avec les références précédentes : {} m.".format(_num(self.new_camera, 2))
            self.last += " Hauteur reportée dans le réglage « Caméra à »."
            if r["sigma"] > 0.25:
                self.last += " Référence trop loin ou trop courte pour être précise : prenez-en une plus proche."

    def add_map_point(self, mlon, mlat):
        """Repère cliqué sur la carte (WGS84), après son clic dans la photo."""
        if not self.waiting_map():
            return self.status()
        c, self.clicks = self.clicks[0], []
        # Position et précision GPS d'origine : le recalage ne doit pas partir d'une position déjà recalée
        lon, lat = c.get("gps") or (c["lon"], c["lat"])
        accuracy = c.get("gps_accuracy", c.get("accuracy"))
        survey = bool(c.get("precise")) and accuracy is not None and accuracy <= triangulation.SURVEY_ACCURACY
        try:
            r = self.calibration.add_landmark(c["sequence"], c["pic"], lon, lat, c["raw"][0],
                                              c["raw"][1], mlon, mlat, accuracy, survey)
        except calibration.CalibrationError as exc:
            self.error = str(exc)
        else:
            pose = self.calibration.pose(c["sequence"])
            n = pose["count"]
            self.last = "Repère {} à {} m (écart de cap brut {}°).".format(
                n, _num(r["distance"], 0), _num(r["offset"], 2))
            if pose["positioned"]:
                self.last += " Position recalée à ±{} m, cap à ±{}°.".format(
                    _num(pose["shift_sigma"], 2), _num(pose["sigma"], 2))
            elif n >= 3:
                self.last += (" Repères mal répartis pour recaler la position : ajoutez-en dans d'autres "
                              "directions (devant, derrière, sur les côtés).")
            else:
                self.last += (" Cap corrigé à ±{}°. À partir de 3 repères bien répartis autour de la photo, "
                              "la position est recalée aussi.".format(_num(pose["sigma"], 2)))
        self._draw()
        return self.status()

    def reset(self):
        """Abandonne les clics en attente (le calage déjà fait est conservé)."""
        self.clicks, self.error, self.last = [], None, None
        self._draw()

    def clear(self):
        """Efface le calage du mode : inclinaison de la photo ou cap de la séquence affichée."""
        pic, sequence = self.current()
        if self.mode == "tilt" and pic:
            self.calibration.clear_tilt(pic)
        elif self.mode == "heading" and sequence:
            self.calibration.clear_heading(sequence)
        elif self.mode == "camera" and sequence:
            self.calibration.clear_camera(sequence)
        self.reset()

    def remove(self):
        if self.item is not None:
            self.item.remove()
            self.item = None

    def _draw(self):
        """Carte : visées vers les repères de la séquence affichée (mode cap)."""
        _, sequence = self.current()
        obs = self.calibration.landmarks.get(sequence, []) if self.mode == "heading" else []
        if self.item is None:
            if not obs:
                return
            self.item = SightingsItem(self.canvas)
        rays = [(o["lon"], o["lat"], o["mlon"], o["mlat"]) for o in obs]
        self.item.set_data(rays, (obs[-1]["mlon"], obs[-1]["mlat"]) if obs else None)

    def viewer_marks(self):
        points = []
        for c in self.clicks:
            pos = c.get("pos") or [None, None]
            points.append({"pic": c.get("pic"), "yaw": pos[0], "pitch": pos[1],
                           "abs_yaw": c["raw"][0], "elev": c["raw"][1]})
        return {"points": points, "label": "", "beside": self.mode in ("tilt", "camera")}

    def status(self):
        n = len(self.clicks)
        text = ""
        if self.error:
            text = self.error + " "
        elif self.last:
            text = self.last + " "
        if self.mode == "camera":
            kind, known = self._reference()
            if kind == "height":
                if n == 1:
                    return text + "Cliquez le sommet de l'objet, sur la même photo."
                return text + ("Cliquez au pied d'un objet de hauteur connue ({} m), puis à son sommet. "
                               "Choisissez-le proche (moins de 10–15 m).".format(_num(known, 2)))
            if n == 1:
                return text + "Cliquez la seconde extrémité, au sol, sur la même photo."
            return text + ("Cliquez au sol les deux extrémités d'une longueur connue ({} m) : trait de marquage, "
                           "place de stationnement, ou longueur mesurée sur la carte. Choisissez-la proche "
                           "(moins de 10–15 m) et plutôt en travers de la vue : dans l'axe, elle est moins précise.".format(_num(known, 2)))
        if self.mode == "tilt":
            if n == 1:
                return text + "Cliquez le sommet du même objet."
            return text + ("Cliquez le pied puis le sommet d'un objet bien vertical (poteau, angle de façade). "
                           "Deux objets à environ 90° l'un de l'autre corrigent toute l'inclinaison de la photo.")
        if n == 1:
            return text + ("Cliquez maintenant ce même repère sur la carte QGIS (glisser pour déplacer la carte, "
                           "molette pour zoomer, Échap pour annuler).")
        return text + ("Cliquez dans la photo un repère net, visible aussi sur la carte (poteau, angle de bâtiment). "
                       "Un repère seul corrige le cap (choisissez-le lointain) ; trois repères ou plus, bien "
                       "répartis autour de la photo, recalent aussi sa position. Le recalage vaut pour les photos "
                       "de la séquence à moins de {} m.".format(_num(calibration.REACH, 0)))


# --------------------------------------------------------------------------
# Mesure libre 3D
# --------------------------------------------------------------------------
FREE_SURFACES = (
    ("Sol", "ground"),
    ("Façade (plan vertical)", "facade"),
    ("Plan vertical face à la caméra", "vertical"),
    ("Plan horizontal", "horizontal"),
    ("Triangulation 3D (deux photos)", "tri3d"),
)
SURFACE_NAMES = {key: label.lower() for label, key in FREE_SURFACES}


PLANE_HEIGHT_ERROR = 0.05  # incertitude de la hauteur saisie du plan horizontal (m)


class FreeMeasure(GroundMeasure):
    """Mesure libre entre deux points 3D, chacun pris sur une surface au choix (voir
    geometry.py) : sol, façade, plan vertical face à la caméra, plan horizontal, ou
    triangulation depuis deux photos. Même modèle de caméra que les autres mesures
    (calages, hauteur de caméra, terrain) : un point est le même quel que soit le mode.
    Le résultat est la distance 3D et sa décomposition.
    """

    def __init__(self, canvas):
        super().__init__(canvas)
        self.mode = "free"
        self.surface = "ground"
        self.plane_height = 1.0  # hauteur du plan horizontal au-dessus du sol (m)
        self.facade = None  # deux points (lon, lat, altitude) au pied du mur
        self.facade_source = None  # les deux clics qui l'ont définie (pour l'incertitude)
        self.facade_clicks = []  # clics de définition de la façade en cours
        self.defining = False  # les prochains clics définissent la façade
        self.facade_item = None
        self.incidence = None  # angle d'incidence le plus rasant sur le plan (degrés)

    # Réglages ---------------------------------------------------------------
    def set_mode(self, mode):
        pass  # un seul mode

    def set_surface(self, surface):
        self.finish()
        self.surface = surface
        self.defining = surface == "facade" and self.facade is None
        self._draw()

    def set_plane_height(self, value):
        self.plane_height = float(value)
        self._compute()

    def start_facade(self):
        """Les deux prochains clics, au pied du mur, définissent la façade."""
        self.finish()
        self.defining = True
        self._draw()

    def needs_profile(self):
        return True  # altitude de la caméra (et du sol) pour chaque clic

    def _height(self, click):
        """Hauteur de caméra du clic : calée pour sa séquence, sinon celle du panneau."""
        sequence = click.get("sequence")
        cam = self.calibration.camera(sequence) if self.calibration and sequence else None
        return cam["height"] if cam else self.camera_height

    # Clics --------------------------------------------------------------------
    def add_click(self, click, notice=None):
        if notice:
            self.notice = notice
        if self.defining:
            self.error = None
            self.facade_clicks.append(click)
            if len(self.facade_clicks) == 2:
                clicks, self.facade_clicks = self.facade_clicks, []
                try:
                    self.facade = self._facade_from(clicks)
                    self.facade_source = clicks
                    self.defining = False
                except (geometry.GeometryError, ground.GroundError) as exc:
                    self.error = str(exc)
            self._draw()
            return self.status()
        if len(self.clicks) >= self._needed():
            self.finish()  # nouvelle mesure, la précédente reste affichée
        self.clicks.append(click)
        self._compute()
        return self.status()

    def _facade_from(self, clicks):
        """Façade (deux points lon, lat, altitude) à partir de deux clics au pied du mur."""
        origin = (clicks[0]["lon"], clicks[0]["lat"])
        a, b = (geometry.on_ground(c, self._height(c), origin)[0] for c in clicks)
        geometry.vertical_plane(a, b)  # contrôle
        return [geometry.to_geographic(a, origin), geometry.to_geographic(b, origin)]

    def _needed(self):
        return 4 if self.surface == "tri3d" else 2

    def _points(self, clicks, facade=None, plane_height=None):
        """Points 3D (repère local centré sur le premier clic) des clics complets, et
        l'angle d'incidence le plus rasant sur le plan. `facade` remplace la façade définie."""
        facade = facade or self.facade
        if not clicks:
            return [], None, None
        origin = (clicks[0]["lon"], clicks[0]["lat"])
        h = self._height
        incidence = None
        # Altitude du sol sous chaque photo : celle du premier clic fait sur la photo, la même
        # ensuite pour tous ses clics (le profil d'un clic dépend de la direction visée)
        ground_z = {}
        for c in clicks:
            ground_z.setdefault(c.get("pic"), (c.get("profile") or ground.flat_profile())[0][1])

        def zof(click):
            return ground_z[click.get("pic")]
        if self.surface == "ground":
            pts = [geometry.on_ground(c, h(c), origin)[0] for c in clicks]
        elif self.surface == "tri3d":
            pts = []
            for k in range(0, len(clicks) - 1, 2):
                pair = clicks[k:k + 2]
                if pair[0].get("pic") == pair[1].get("pic"):
                    raise geometry.GeometryError("Changez de photo pour viser le même point sous un autre angle.")
                pts.append(geometry.triangulate([geometry.camera(c, h(c), origin, zof(c)) for c in pair])[0])
        else:
            if self.surface == "facade":
                if not facade:
                    raise geometry.GeometryError("Façade non définie : cliquez « Définir la façade ».")
                plane = geometry.vertical_plane(*(geometry.to_local(*p, origin) for p in facade))
                planar = clicks
                pts = []
            elif self.surface == "vertical":
                first = geometry.on_ground(clicks[0], h(clicks[0]), origin)[0]
                plane = geometry.facing_plane(geometry.camera(clicks[0], h(clicks[0]), origin, zof(clicks[0]))[0], first)
                planar, pts = clicks[1:], [first]
            else:  # horizontal
                plane = ((0.0, 0.0, zof(clicks[0]) + (self.plane_height if plane_height is None else plane_height)),
                         (0.0, 0.0, 1.0))
                planar, pts = clicks, []
            for c in planar:
                p, angle = geometry.on_plane(c, h(c), origin, *plane, ground_z=zof(c))
                pts.append(p)
                incidence = angle if incidence is None else min(incidence, angle)
        return pts, incidence, origin

    def _quantity(self, d):
        """Grandeur mesurée : la hauteur (composante verticale) sur le plan vertical face à la
        caméra, comme le préréglage « Hauteur d'un objet » ; sinon la distance 3D."""
        return abs(d["vertical"]) if self.surface == "vertical" else d["d3"]

    def _distance(self, clicks, facade=None, plane_height=None):
        pts, _, _ = self._points(clicks, facade, plane_height)
        return self._quantity(geometry.decompose(pts[0], pts[1]))

    def _spread(self, clicks):
        """Écart maximal de la distance quand l'élévation de chaque clic varie de
        ±ground.PITCH_ERROR (et, en triangulation, le cap de l'erreur de visée). Sur une
        façade, ses deux clics de définition varient aussi : la distance du mur fausse
        toutes les mesures faites dessus, en proportion."""
        ref = self._distance(clicks)
        worst = 0.0
        if self.surface == "horizontal":
            for sign in (-1, 1):  # hauteur du plan saisie à ±PLANE_HEIGHT_ERROR
                try:
                    worst = max(worst, abs(self._distance(
                        clicks, plane_height=self.plane_height + sign * PLANE_HEIGHT_ERROR) - ref))
                except (geometry.GeometryError, ground.GroundError):
                    return float("inf")
        if self.surface == "facade" and self.facade_source:
            for i in range(len(self.facade_source)):
                for sign in (-1, 1):
                    moved = [dict(x) for x in self.facade_source]
                    moved[i]["elev"] += sign * ground.PITCH_ERROR
                    try:
                        worst = max(worst, abs(self._distance(clicks, self._facade_from(moved)) - ref))
                    except (geometry.GeometryError, ground.GroundError):
                        return float("inf")
        for i, c in enumerate(clicks):
            moves = [("elev", ground.PITCH_ERROR)]
            if self.surface == "tri3d":
                moves.append(("yaw", triangulation.heading_error(c)))
            for key, step in moves:
                for sign in (-1, 1):
                    moved = [dict(x) for x in clicks]
                    moved[i][key] += sign * step
                    try:
                        worst = max(worst, abs(self._distance(moved) - ref))
                    except (geometry.GeometryError, ground.GroundError):
                        return float("inf")
        return worst

    def _compute(self):
        self.result, self.error, self.incidence = None, None, None
        try:
            pts, self.incidence, origin = self._points(self.clicks)
            if len(pts) == 2 and len(self.clicks) == self._needed():
                d = geometry.decompose(pts[0], pts[1], self.clicks[0].get("axis"))
                self.result = {"points": [geometry.to_geographic(p, origin)[:2] for p in pts],
                               "value": self._quantity(d), "decomposition": d,
                               "uncertainty": self._spread(self.clicks)}
        except (geometry.GeometryError, ground.GroundError) as exc:
            self.error = str(exc)
            self.clicks.pop()  # clic refusé : on le refait
        self._draw()

    # Effacement ---------------------------------------------------------------
    def finish(self):
        self.facade_clicks = []
        super().finish()

    def clear(self):
        self.facade_clicks = []
        super().clear()

    def clear_all(self):
        """Efface aussi la façade, à redéfinir."""
        super().clear_all()
        self.facade = self.facade_source = None
        self.defining = self.surface == "facade"
        self._draw()

    def remove(self):
        super().remove()
        if self.facade_item is not None:
            self.facade_item.remove()
            self.facade_item = None

    # Affichage ----------------------------------------------------------------
    def _draw(self):
        points, label = [], ""
        if self.result:
            points = self.result["points"]
            label = "{} m".format(_num(self.result["value"], 2))
        elif self.clicks and not self.defining:
            try:
                pts, _, origin = self._points(self.clicks)
                points = [geometry.to_geographic(p, origin)[:2] for p in pts]
            except (geometry.GeometryError, ground.GroundError):
                pass
        if self.item is None and points:
            self.item = MeasureItem(self.canvas)
        if self.item is not None:
            self.item.set_data(points, label)
        # Façade : trait au pied du mur
        if self.facade and self.surface == "facade":
            if self.facade_item is None:
                self.facade_item = MeasureItem(self.canvas)
            self.facade_item.set_data([p[:2] for p in self.facade], "façade")
        elif self.facade_item is not None:
            self.facade_item.set_data([], "")

    def viewer_marks(self):
        clicks = self.facade_clicks if self.defining else self.clicks
        points = []
        for c in clicks:
            pos = c.get("pos") or [None, None]
            raw = c.get("raw") or [c["yaw"], c["elev"]]
            points.append({"pic": c.get("pic"), "yaw": pos[0], "pitch": pos[1], "abs_yaw": raw[0], "elev": raw[1]})
        label, beside = "", False
        if self.result and not self.defining:
            d = self.result["decomposition"]
            beside = self.surface == "vertical" or abs(d["vertical"]) > d["horizontal"]  # trait vertical
            label = "{}{} m ± {} m".format("↕ " if beside else "↔ ", _num(self.result["value"], 2),
                                           _num(self.result["uncertainty"], 2))
        return {"points": points, "label": label, "beside": beside}

    def status(self):
        prefix = (self.error + " ") if self.error else ""
        if self.defining:
            if not self.facade_clicks:
                return prefix + ("Façade : cliquez au pied du mur, à une extrémité (le plan vertical passera "
                                 "par les deux points).")
            return "Façade : cliquez au pied du mur, à l'autre extrémité."
        n = len(self.clicks)
        if self.surface == "tri3d" and n < 4:
            steps = ("Point 1 : cliquez-le sur cette photo.",
                     "Point 1 : changez de photo et cliquez le même point, sous un autre angle.",
                     "Point 2 : cliquez-le (sur cette photo ou une autre).",
                     "Point 2 : changez de photo et cliquez le même point, sous un autre angle.")
            return prefix + steps[n]
        if n < 2:
            hints = {
                "ground": ("Cliquez le premier point, au sol.", "Cliquez le second point, au sol."),
                "facade": ("Cliquez le premier point sur la façade.", "Cliquez le second point sur la façade."),
                "vertical": ("Cliquez le premier point, au sol (pied de l'objet).",
                             "Cliquez le second point, dans le plan vertical face à la caméra qui passe par le "
                             "premier (sommet, angle…)."),
                "horizontal": ("Cliquez le premier point sur le plan horizontal à {} m du sol.".format(
                    _num(self.plane_height, 2)), "Cliquez le second point sur le même plan."),
            }[self.surface]
            return prefix + hints[n]
        if self.error:
            return self.error + " Cliquez à nouveau."
        r, d = self.result, self.result["decomposition"]
        if self.surface == "vertical":
            text = "Hauteur : {} m (±{} m) · distance 3D {} · horizontale {}".format(
                _num(r["value"], 2), _num(r["uncertainty"], 2), _num(d["d3"], 2), _num(d["horizontal"], 2))
        else:
            text = "Distance 3D : {} m (±{} m) · horizontale {} · verticale {}".format(
                _num(d["d3"], 2), _num(r["uncertainty"], 2), _num(d["horizontal"], 2), _num(d["vertical"], 2))
        if d["along"] is not None:
            text += " · le long de la route {} · en travers {}".format(_num(d["along"], 2), _num(d["across"], 2))
        text += " · surface : {}".format(SURFACE_NAMES[self.surface])
        if self.surface == "horizontal":
            gap = self._height(self.clicks[0]) - self.plane_height
            text += " · plan à {} m sous la caméra, incidence {}°".format(
                _num(gap, 2), _num(self.incidence or 0, 0))
        if self.incidence is not None and self.incidence < geometry.GRAZING:
            text += ". Visée rasante sur le plan ({}°) : mesure imprécise, prenez une photo plus en face".format(
                _num(self.incidence, 0))
        labels = {c.get("terrain") for c in self.clicks if c.get("terrain")}
        height = self._height(self.clicks[0])
        text += ". Terrain : {}, caméra à {} m.".format(" + ".join(sorted(labels)) or ground.FLAT, _num(height, 2))
        if len(labels) > 1:
            text += " Altitudes de sources différentes : la composante verticale peut être faussée."
        if self.notice:
            text += " ({}.)".format(self.notice)
        return text
