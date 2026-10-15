# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Cédric COCHART
# SPDX-License-Identifier: GPL-2.0-or-later
"""Points 3D à partir des clics, pour les mesures (voir measures.py).

Un clic ne donne qu'une direction ; le point 3D est l'intersection du rayon de
visée avec une surface :
- le terrain (même calcul que les mesures au sol, voir ground.py) ;
- un plan vertical : face à la caméra et passant par le pied de l'objet (hauteur),
  ou celui d'une façade du cadastre (voir buildings.py).

Repère local (est, nord, altitude) en mètres, centré sur un point de référence
(lon0, lat0). La caméra est à la position (recalée) de la photo, à la hauteur de
caméra au-dessus du sol sous la photo, donné par le profil de terrain du clic.
Les mesures sont ensuite des calculs sur ces points : distance 3D et sa
décomposition (horizontale, verticale, le long de la route et en travers).
Module sans dépendance à QGIS, pour pouvoir être testé seul.
"""

import math

from . import ground
from .triangulation import _geographic, _local

MAX_RANGE = 100.0  # portée maximale d'un point sur un plan (m)
GRAZING = 15.0  # en dessous de cet angle d'incidence sur un plan, la mesure devient imprécise


class GeometryError(ValueError):
    """Clic inexploitable (message destiné à l'utilisateur)."""


def direction(yaw, elev):
    """Vecteur unitaire (est, nord, haut) d'une direction en degrés."""
    y, e = math.radians(yaw), math.radians(elev)
    return (math.sin(y) * math.cos(e), math.cos(y) * math.cos(e), math.sin(e))


def to_local(lon, lat, z, origin):
    x, y = _local(lon, lat, origin[0], origin[1])
    return (x, y, z)


def to_geographic(p, origin):
    lon, lat = _geographic(p[0], p[1], origin[0], origin[1])
    return lon, lat, p[2]


def camera(click, camera_height, origin, ground_z=None):
    """Centre optique (repère local) et direction de visée d'un clic. ground_z : altitude
    du sol sous la photo, à donner pour qu'elle soit la même pour tous les clics d'une
    photo (celle du profil du clic dépend de la direction visée)."""
    if ground_z is None:
        ground_z = (click.get("profile") or ground.flat_profile())[0][1]
    x, y = _local(click["lon"], click["lat"], origin[0], origin[1])
    return (x, y, ground_z + camera_height), direction(click["yaw"], click["elev"])


def on_ground(click, camera_height, origin, ground_z=None):
    """Point au sol (rayon ∩ terrain), comme ground._ground_point. ground_z : altitude du sol
    sous la photo, la même pour tous ses clics (voir ground.anchored et camera)."""
    profile = ground.anchored(click.get("profile") or ground.flat_profile(), ground_z)
    s, z = ground.intersect(profile, camera_height, click["elev"])
    x, y = _local(click["lon"], click["lat"], origin[0], origin[1])
    a = math.radians(click["yaw"])
    return (x + s * math.sin(a), y + s * math.cos(a), z), 90.0


def on_plane(click, camera_height, origin, point, normal, ground_z=None):
    """Point sur le plan (point, normale). Retourne (point, angle d'incidence en degrés)."""
    c, u = camera(click, camera_height, origin, ground_z)
    nu = sum(n * v for n, v in zip(normal, u))
    if abs(nu) < 1e-6:
        raise GeometryError("Visée parallèle au plan : cliquez un point plus face à la caméra.")
    t = sum(n * (p - q) for n, p, q in zip(normal, point, c)) / nu
    if t <= 0.1:
        raise GeometryError("Le point visé est derrière le plan de mesure : vérifiez la surface choisie.")
    if t * math.hypot(u[0], u[1]) > MAX_RANGE:
        raise GeometryError("Point à plus de {:.0f} m sur le plan de mesure : visez plus près.".format(MAX_RANGE))
    norm = math.sqrt(sum(n * n for n in normal))
    return tuple(q + t * v for q, v in zip(c, u)), math.degrees(math.asin(min(1.0, abs(nu) / norm)))


def vertical_plane(a, b):
    """Plan vertical passant par deux points (repère local) : (point, normale horizontale)."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    if math.hypot(dx, dy) < 0.2:
        raise GeometryError("Les deux points de la façade sont trop proches : cliquez ses deux extrémités.")
    return a, (-dy, dx, 0.0)


def facing_plane(cam, point):
    """Plan vertical passant par `point`, face à la caméra (normale horizontale vers elle)."""
    dx, dy = point[0] - cam[0], point[1] - cam[1]
    if math.hypot(dx, dy) < 0.1:
        raise GeometryError("Point trop proche de la caméra.")
    return point, (dx, dy, 0.0)


def decompose(p, q, axis=None):
    """Distance de p à q : dict d3, horizontal, vertical (q − p), along et across (le long
    de l'axe de la route et en travers, si l'axe est connu)."""
    dx, dy, dz = q[0] - p[0], q[1] - p[1], q[2] - p[2]
    out = {"d3": math.sqrt(dx * dx + dy * dy + dz * dz), "horizontal": math.hypot(dx, dy), "vertical": dz,
           "along": None, "across": None}
    if axis is not None:
        ax, ay = math.sin(math.radians(axis)), math.cos(math.radians(axis))
        out["along"] = abs(dx * ax + dy * ay)
        out["across"] = abs(dx * ay - dy * ax)
    return out
