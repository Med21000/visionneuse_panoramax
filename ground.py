# -*- coding: utf-8 -*-
"""Mesures sur une photo par l'hypothèse du sol plat.

La caméra est à une hauteur connue au-dessus d'un sol supposé plat et
horizontal. Un clic sous l'horizon donne une direction qui coupe le sol à une
distance d = hauteur / tan(dépression) : on en déduit la position du point au
sol, donc des distances entre points et la hauteur d'un objet (pied puis sommet).

La précision chute vite avec la distance (une erreur d'inclinaison de 0,5°
déplace un point situé à 15 m de plus d'un mètre) : réservé aux objets proches.
Module sans dépendance à QGIS, pour pouvoir être testé seul.
"""

import math

from .triangulation import _local, offset

PITCH_ERROR = 0.5  # erreur d'inclinaison supposée (degrés) pour l'incertitude affichée
MIN_DEPRESSION = 1.0  # en dessous (degrés sous l'horizon), le point au sol est trop lointain
MAX_DISTANCE = 60.0


class GroundError(ValueError):
    """Clic inexploitable (message destiné à l'utilisateur)."""


def view_direction(heading, pitch, vfov, dx, dy, height_px):
    """Direction (cap, élévation en degrés) d'un pixel de la vue.

    heading/pitch : orientation du centre de la vue ; vfov : champ vertical ;
    dx, dy : décalage du pixel par rapport au centre (dy vers le bas) ;
    height_px : hauteur de la vue en pixels. Même modèle que capture._view_rays.
    """
    f = (height_px / 2.0) / math.tan(math.radians(vfov) / 2.0)
    u, v = dx / f, -dy / f
    p = math.radians(pitch)
    y = v * math.cos(p) + math.sin(p)
    z = -v * math.sin(p) + math.cos(p)
    h = math.radians(heading)
    x2 = u * math.cos(h) + z * math.sin(h)
    z2 = -u * math.sin(h) + z * math.cos(h)
    return math.degrees(math.atan2(x2, z2)) % 360, math.degrees(math.atan2(y, math.hypot(x2, z2)))


def ground_distance(camera_height, elevation):
    """Distance horizontale du point au sol vu sous l'élévation donnée."""
    if elevation > -MIN_DEPRESSION:
        raise GroundError("Point au-dessus ou trop près de l'horizon : cliquez sur le sol.")
    d = camera_height / math.tan(math.radians(-elevation))
    if d > MAX_DISTANCE:
        raise GroundError("Point au sol à plus de {:.0f} m : trop loin pour être mesuré "
                          "de façon fiable.".format(MAX_DISTANCE))
    return d


def _ground_point(camera_height, click):
    d = ground_distance(camera_height, click["elev"])
    return d, offset(click["lon"], click["lat"], click["yaw"], d)


def _spread(fn, clicks):
    """Écart maximal du résultat quand chaque élévation varie de ±PITCH_ERROR."""
    ref = fn(clicks)
    worst = 0.0
    for i in range(len(clicks)):
        for sign in (-1, 1):
            moved = [dict(c) for c in clicks]
            moved[i]["elev"] += sign * PITCH_ERROR
            try:
                worst = max(worst, abs(fn(moved) - ref))
            except GroundError:
                return float("inf")
    return worst


def measure_distance(camera_height, a, b):
    """Distance au sol entre deux clics (dicts lon, lat, yaw, elev).

    Les clics peuvent venir de deux photos différentes : chacun est placé depuis
    la position de sa photo.
    """
    def dist(clicks):
        (_, p), (_, q) = (_ground_point(camera_height, c) for c in clicks)
        x, y = _local(q[0], q[1], p[0], p[1])
        return math.hypot(x, y)

    (d1, p1), (d2, p2) = _ground_point(camera_height, a), _ground_point(camera_height, b)
    return {"points": [p1, p2], "ranges": [d1, d2], "value": dist([a, b]),
            "uncertainty": _spread(dist, [a, b])}


def measure_height(camera_height, base, top):
    """Hauteur d'un objet : clic au pied (au sol) puis au sommet, sur la même photo."""
    if base.get("pic") != top.get("pic"):
        raise GroundError("Le pied et le sommet doivent être cliqués sur la même photo.")

    def height(clicks):
        b, t = clicks
        d = ground_distance(camera_height, b["elev"])
        return camera_height + d * math.tan(math.radians(t["elev"]))

    d, p = _ground_point(camera_height, base)
    h = height([base, top])
    if h <= 0:
        raise GroundError("Le sommet est sous le pied : cliquez d'abord au pied, puis au sommet.")
    return {"points": [p], "ranges": [d], "value": h, "uncertainty": _spread(height, [base, top])}
