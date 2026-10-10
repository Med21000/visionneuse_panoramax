# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Cédric COCHART
# SPDX-License-Identifier: GPL-2.0-or-later
"""Points 3D à partir des clics, pour les mesures libres dans un plan quelconque.

Un clic ne donne qu'une direction ; le point 3D est l'intersection du rayon de
visée avec une surface :
- "ground" : le terrain (même calcul que les mesures au sol, voir ground.py) ;
- "facade" : un plan vertical défini par deux points au pied d'un mur ;
- "vertical" : le plan vertical face à la caméra passant par le premier point
  de la mesure (généralisation de la mesure de hauteur) ;
- "horizontal" : un plan horizontal à une hauteur donnée au-dessus du sol ;
- "tri3d" : sans surface, par intersection de visées depuis deux photos.

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
MIN_CROSSING = 3.0  # angle minimal entre deux visées de triangulation 3D (degrés)


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


def on_ground(click, camera_height, origin):
    """Point au sol (rayon ∩ terrain), comme ground._ground_point."""
    s, z = ground.intersect(click.get("profile") or ground.flat_profile(), camera_height, click["elev"])
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


def triangulate(rays):
    """Point le plus proche de plusieurs visées 3D (moindres carrés) : (point, distances,
    croisement en degrés). rays : [(centre, direction unitaire)]."""
    a = [[0.0] * 3 for _ in range(3)]
    b = [0.0, 0.0, 0.0]
    for c, u in rays:
        for i in range(3):
            for j in range(3):
                m = (1.0 if i == j else 0.0) - u[i] * u[j]
                a[i][j] += m
                b[i] += m * c[j]
    crossing = max(_angle(u1, u2) for k, (_, u1) in enumerate(rays) for _, u2 in rays[k + 1:])
    if crossing < MIN_CROSSING:
        raise GeometryError("Visées presque parallèles ({:.0f}°) : visez le point depuis une photo plus "
                            "décalée.".format(crossing))
    x = _solve3(a, b)
    ranges = [sum((x[i] - c[i]) * u[i] for i in range(3)) for c, u in rays]
    if min(ranges) < 0.5:
        raise GeometryError("Les visées ne se croisent pas devant les photos : visez bien le même point.")
    return tuple(x), ranges, crossing


def _angle(u, v):
    """Angle entre deux droites (0 à 90°)."""
    c = abs(sum(a * b for a, b in zip(u, v)))
    return math.degrees(math.acos(min(1.0, c)))


def _solve3(a, b):
    (a11, a12, a13), (a21, a22, a23), (a31, a32, a33) = a
    det = (a11 * (a22 * a33 - a23 * a32) - a12 * (a21 * a33 - a23 * a31) + a13 * (a21 * a32 - a22 * a31))
    if abs(det) < 1e-12:
        raise GeometryError("Visées parallèles : impossible de les croiser.")
    out = []
    for k in range(3):
        m = [row[:] for row in a]
        for i in range(3):
            m[i][k] = b[i]
        (m11, m12, m13), (m21, m22, m23), (m31, m32, m33) = m
        out.append((m11 * (m22 * m33 - m23 * m32) - m12 * (m21 * m33 - m23 * m31)
                    + m13 * (m21 * m32 - m22 * m31)) / det)
    return out


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
