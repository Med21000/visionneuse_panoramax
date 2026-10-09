# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Cédric COCHART
# SPDX-License-Identifier: GPL-2.0-or-later
"""Position d'un objet par intersection de visées (triangulation).

Chaque visée part de la position d'une photo dans une direction (cap absolu,
0 = nord, sens horaire). Le point retenu est celui qui minimise la somme des
carrés des distances aux demi-droites de visée (moindres carrés) : deux
visées suffisent, chaque visée supplémentaire améliore et contrôle le résultat.

Calcul dans un plan tangent local (quelques centaines de mètres au plus) :
l'erreur due à la projection est négligeable devant la précision du GPS.
Module sans dépendance à QGIS, pour pouvoir être testé seul.
"""

import math

EARTH_RADIUS = 6371008.8
HEADING_ERROR = 1.0  # erreur de visée supposée (degrés) pour l'incertitude affichée
MIN_ANGLE = 3.0  # en dessous, les visées sont jugées parallèles
MAX_DISTANCE = 500.0  # au-delà, le résultat n'a plus de sens avec des photos de rue


class TriangulationError(ValueError):
    """Visées inexploitables (message destiné à l'utilisateur)."""


def _local(lon, lat, lon0, lat0):
    """Coordonnées métriques (est, nord) autour de (lon0, lat0)."""
    return (EARTH_RADIUS * math.radians(lon - lon0) * math.cos(math.radians(lat0)),
            EARTH_RADIUS * math.radians(lat - lat0))


def _geographic(x, y, lon0, lat0):
    return (lon0 + math.degrees(x / (EARTH_RADIUS * math.cos(math.radians(lat0)))),
            lat0 + math.degrees(y / EARTH_RADIUS))


def offset(lon, lat, heading, distance):
    """Point situé à `distance` mètres de (lon, lat) dans la direction `heading`."""
    a = math.radians(heading)
    return _geographic(distance * math.sin(a), distance * math.cos(a), lon, lat)


def crossing_angle(h1, h2):
    """Angle entre deux droites de visée, ramené entre 0 et 90°."""
    a = abs(h1 - h2) % 180.0
    return min(a, 180.0 - a)


def solve(sightings):
    """Intersection des visées.

    sightings : liste de dicts avec au moins "lon", "lat", "heading".
    Retourne un dict : lon, lat, distances (m, une par visée), angle (° de
    croisement le plus favorable), rms (écart moyen des visées au point, m),
    uncertainty (rayon d'incertitude en m pour ±HEADING_ERROR° de visée).
    Lève TriangulationError si les visées ne permettent pas de conclure.
    """
    if len(sightings) < 2:
        raise TriangulationError("Il faut au moins deux visées.")
    lon0 = sum(s["lon"] for s in sightings) / len(sightings)
    lat0 = sum(s["lat"] for s in sightings) / len(sightings)

    angle = max(crossing_angle(a["heading"], b["heading"])
                for i, a in enumerate(sightings) for b in sightings[i + 1:])
    if angle < MIN_ANGLE:
        raise TriangulationError(
            "Visées presque parallèles ({:.0f}°) : visez l'objet depuis une photo plus "
            "décalée sur le côté.".format(angle))

    # Normales  Σ (I - d dᵀ) X = Σ (I - d dᵀ) P
    rays = []
    a11 = a12 = a22 = b1 = b2 = 0.0
    for s in sightings:
        px, py = _local(s["lon"], s["lat"], lon0, lat0)
        h = math.radians(s["heading"])
        dx, dy = math.sin(h), math.cos(h)
        m11, m12, m22 = 1 - dx * dx, -dx * dy, 1 - dy * dy
        a11 += m11
        a12 += m12
        a22 += m22
        b1 += m11 * px + m12 * py
        b2 += m12 * px + m22 * py
        rays.append((px, py, dx, dy))
    det = a11 * a22 - a12 * a12
    if abs(det) < 1e-9:
        raise TriangulationError("Visées parallèles : impossible de les croiser.")
    x = (a22 * b1 - a12 * b2) / det
    y = (a11 * b2 - a12 * b1) / det

    distances, residuals = [], []
    for px, py, dx, dy in rays:
        vx, vy = x - px, y - py
        distances.append(vx * dx + vy * dy)  # position le long de la visée
        residuals.append(abs(vx * dy - vy * dx))  # écart perpendiculaire
    if min(distances) < 0.5:
        raise TriangulationError(
            "Les visées ne se croisent pas devant les photos : vérifiez que vous visez "
            "bien le même objet.")
    if max(distances) > MAX_DISTANCE:
        raise TriangulationError(
            "Point trouvé à plus de {:.0f} m : visées trop peu sécantes ou objets "
            "différents.".format(MAX_DISTANCE))

    # Incertitude : chaque visée est décalée latéralement d'environ t·tan(erreur de cap).
    # Covariance du point = (Σ w (I - d dᵀ))⁻¹, w = 1 / décalage² ; on garde le grand axe.
    c11 = c12 = c22 = 0.0
    tan_err = math.tan(math.radians(HEADING_ERROR))
    for (px, py, dx, dy), t in zip(rays, distances):
        w = 1.0 / max(t * tan_err, 0.05) ** 2
        c11 += w * (1 - dx * dx)
        c12 += w * (-dx * dy)
        c22 += w * (1 - dy * dy)
    cdet = c11 * c22 - c12 * c12
    if cdet > 0:
        # Valeurs propres de l'inverse = inverses des valeurs propres : on prend la plus petite
        half = (c11 + c22) / 2.0
        smallest = half - math.sqrt(max(half * half - cdet, 0.0))
        uncertainty = 1.0 / math.sqrt(smallest) if smallest > 0 else float("inf")
    else:
        uncertainty = float("inf")

    lon, lat = _geographic(x, y, lon0, lat0)
    return {
        "lon": lon,
        "lat": lat,
        "distances": distances,
        "angle": angle,
        "rms": math.sqrt(sum(r * r for r in residuals) / len(residuals)),
        "uncertainty": uncertainty,
    }
