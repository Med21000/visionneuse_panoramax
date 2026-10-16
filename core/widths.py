# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Cédric COCHART
# SPDX-License-Identifier: GPL-2.0-or-later
"""Largeur perpendiculaire à la route depuis un point triangulé (application web).

Le premier bord A est un point triangulé (voir triangulation.py). La largeur se prend sur
la droite passant par A, perpendiculaire à l'axe de la route. Le second bord B est là où la
visée d'un clic dans une photo coupe cette droite : seule la direction horizontale du clic
compte, ni le terrain ni la hauteur de caméra. La visée doit couper la droite franchement :
depuis une photo prise quelques mètres avant ou après A, pas juste en face.

Calcul dans un plan tangent local centré sur A (voir triangulation._local). Module sans
dépendance à QGIS.
"""

import math

from . import geometry, measures, triangulation
from .calibration import AIM_ERROR

# Incertitude de la direction de l'axe de la route (degrés) : tronçon de la BD TOPO (direction
# locale sur ±10 m, voir roads.py), sinon trajectoire de la séquence (le véhicule ne roule pas
# toujours parallèlement à la route)
AXIS_ERROR_BDTOPO = 2.0
AXIS_ERROR_TRACK = 3.0
MIN_ANGLE = 10.0  # angle minimal (degrés) entre la visée et la droite perpendiculaire
GRAZING_ANGLE = 25.0  # en dessous, visée signalée comme rasante
GUIDE_LENGTH = 15.0  # m de part et d'autre de A : droite tracée sur la carte et dans la photo


def axis_error(source):
    """Incertitude (degrés) de l'axe de la route selon sa source (voir roads.py)."""
    return AXIS_ERROR_BDTOPO if source and source.startswith("BD TOPO") else AXIS_ERROR_TRACK


def heading_error(click):
    """Erreur de cap (degrés) d'un clic : visée dans la photo et cap de la photo (plus faible
    pour le matériel de relevé, voir triangulation.heading_error)."""
    return math.hypot(AIM_ERROR, triangulation.heading_error(click))


def perpendicular(axis):
    """Direction (est, nord) de la droite perpendiculaire à l'axe `axis` (degrés)."""
    p = math.radians(axis + 90.0)
    return math.sin(p), math.cos(p)


def cross(a, axis, cam, heading):
    """Intersection de la visée partant de cam (est, nord) au cap `heading` (degrés) et de la
    droite passant par a (est, nord), perpendiculaire à l'axe `axis` (degrés).

    Retourne (point, t, s, angle) : point d'intersection (est, nord), son abscisse signée t
    sur la droite depuis a, la distance s depuis cam, et l'angle (degrés) entre la visée et la
    droite. Lève geometry.GeometryError si la visée ne coupe pas la droite devant la caméra
    ou la coupe trop en biais (MIN_ANGLE)."""
    ux, uy = perpendicular(axis)
    h = math.radians(heading)
    dx, dy = math.sin(h), math.cos(h)
    # cam + s d = a + t u  :  s d - t u = a - cam
    det = uy * dx - ux * dy
    angle = math.degrees(math.asin(min(1.0, abs(det))))
    if angle < MIN_ANGLE:
        raise geometry.GeometryError(
            "Visée presque parallèle à la droite de mesure ({:.0f}°) : cliquez le bord depuis une photo prise "
            "quelques mètres avant ou après le point de référence.".format(angle))
    rx, ry = a[0] - cam[0], a[1] - cam[1]
    s = (ry * ux - rx * uy) / -det
    t = (dx * ry - dy * rx) / -det
    if s <= 0:
        raise geometry.GeometryError("La droite de mesure est derrière la visée.")
    return (cam[0] + s * dx, cam[1] + s * dy), t, s, angle


class PerpendicularWidth(measures.Measure):
    """Largeur entre le point triangulé A (edge : lon, lat, uncertainty, name) et le bord B
    cliqué (voir le module). Rangée avec les mesures de MeasureTool, dont elle a l'interface
    (liste, export, repères de la visionneuse, dessin sur la carte) ; clicks : repère de A
    dans la photo de B (sans direction brute : non recorrigé), puis le clic de B."""

    def __init__(self, tool, edge, road, click):
        super().__init__(tool, "road", "ground")
        self.edge = edge
        self.road = road  # axe de la route en A : dict axis, source (voir roads.py)
        self.clicks = [self._edge_mark(click), click]
        self.angle = None
        self.compute()

    def _edge_mark(self, click):
        """Repère de A dans la photo du clic de B : direction de A depuis la caméra, au sol
        plat (repère seulement, la mesure n'en dépend pas)."""
        x, y = triangulation._local(self.edge["lon"], self.edge["lat"], click["lon"], click["lat"])
        bearing = math.degrees(math.atan2(x, y))
        raw = bearing - (click["yaw"] - click["raw"][0])  # même correction du calage que le clic
        elev = -math.degrees(math.atan2(self.tool.camera_for(click), max(math.hypot(x, y), 0.1)))
        pos = click.get("pos") or [None, None]
        if pos[0] is not None:  # repère de la photo : décalé du clic comme les directions brutes
            pos = [pos[0] + math.radians(raw - click["raw"][0]), math.radians(elev)]
        return {"pic": click.get("pic"), "lon": click["lon"], "lat": click["lat"], "yaw": bearing, "elev": elev,
                "pos": pos, "sequence": click.get("sequence"), "axis": self.road["axis"],
                "axis_source": self.road["source"]}

    def needed(self):
        return 2

    def picks(self):
        return []  # ni A (point triangulé) ni B (sur sa visée) ne se glissent sur la carte

    def reaim(self, index, click):
        if index == 0:
            self.error = "Le point de référence ne se vise pas ici : « Nouveau point de référence »."
            return
        state = self.clicks[1]
        self.clicks[1] = click
        self.compute()
        if self.error:
            error = self.error
            self.clicks[1] = state
            self.compute()
            self.error = error

    def _width(self, da=(0.0, 0.0), daxis=0.0, dheading=0.0, dcam=(0.0, 0.0)):
        click = self.clicks[1]
        cam = triangulation._local(click["lon"], click["lat"], self.edge["lon"], self.edge["lat"])
        return cross(da, self.road["axis"] + daxis, (cam[0] + dcam[0], cam[1] + dcam[1]), click["yaw"] + dheading)

    def compute(self):
        self.result, self.error, self.angle = None, None, None
        try:
            (bx, by), t, s, self.angle = self._width()
            click = self.clicks[1]
            sigma_a, sigma_cam = self.edge["uncertainty"], click.get("accuracy") or triangulation.GPS_ACCURACY
            groups = [[{"da": (k * sigma_a, 0.0)} for k in (-1, 1)], [{"da": (0.0, k * sigma_a)} for k in (-1, 1)],
                      [{"daxis": k * axis_error(self.road["source"])} for k in (-1, 1)],
                      [{"dheading": k * heading_error(click)} for k in (-1, 1)],
                      [{"dcam": (k * sigma_cam, 0.0)} for k in (-1, 1)], [{"dcam": (0.0, k * sigma_cam)} for k in (-1, 1)]]
            total = 0.0
            for variants in groups:
                worst = 0.0
                for variant in variants:
                    try:
                        worst = max(worst, abs(abs(self._width(**variant)[1]) - abs(t)))
                    except geometry.GeometryError:
                        worst = float("inf")
                total += worst * worst
            a = (self.edge["lon"], self.edge["lat"])
            b = triangulation._geographic(bx, by, *a)
            cam = triangulation._local(click["lon"], click["lat"], *a)
            self.result = {"value": abs(t), "uncertainty": math.sqrt(total),
                           "decomposition": {"horizontal": abs(t), "vertical": 0.0, "along": 0.0, "across": abs(t)},
                           "ranges": [math.hypot(*cam), s], "points": [a, b], "extra": None, "picks": [a, b],
                           "shifts": [(0.0, 0.0), (0.0, 0.0)]}
        except geometry.GeometryError as exc:
            self.error = str(exc)
        self._draw()

    def guide(self):
        """Extrémités (lon, lat) de la droite de mesure tracée sur la carte."""
        return guide(self.edge, self.road["axis"])

    def status(self):
        r = self.result
        text = ("Largeur perpendiculaire à la chaussée : {} m (±{} m) · depuis le point triangulé {} · axe {}° ({}) "
                "· visée à {}° de la droite").format(
            measures._num(r["value"], 2), measures._num(r["uncertainty"], 2), self.edge.get("name") or "",
            measures._num(self.road["axis"] % 180, 0), self.road["source"], measures._num(self.angle, 0))
        if self.angle < GRAZING_ANGLE:
            text += ". Visée rasante : un clic depuis une photo plus éloignée le long de la route sera plus précis"
        return text + ". Terrain et hauteur de caméra sans effet."


def guide(edge, axis, length=GUIDE_LENGTH):
    """Extrémités (lon, lat) de la droite passant par le point edge (lon, lat),
    perpendiculaire à l'axe `axis`, sur `length` m de part et d'autre."""
    ux, uy = perpendicular(axis)
    return [triangulation._geographic(k * length * ux, k * length * uy, edge["lon"], edge["lat"]) for k in (-1, 1)]
