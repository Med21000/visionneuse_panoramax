# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Cédric COCHART
# SPDX-License-Identifier: GPL-2.0-or-later
"""Position d'un objet par intersection de visées (triangulation).

Chaque visée part de la position d'une photo dans une direction (cap absolu,
0 = nord, sens horaire). Le point retenu est celui qui minimise la somme
pondérée des carrés des distances aux demi-droites de visée (moindres carrés) :
deux visées suffisent, chaque visée supplémentaire améliore et contrôle le
résultat. Le poids d'une visée est l'inverse du carré de son écart latéral
probable, qui cumule l'erreur de visée (croissante avec la distance) et la
précision de la position GPS de la photo : une visée lointaine ou une photo mal
positionnée compte moins.

Calcul dans un plan tangent local (quelques centaines de mètres au plus) :
l'erreur due à la projection est négligeable devant la précision du GPS.
Module sans dépendance à QGIS, pour pouvoir être testé seul.
"""

import math

EARTH_RADIUS = 6371008.8
HEADING_ERROR = 1.0  # erreur de visée supposée (degrés) pour l'incertitude affichée
# Matériel de relevé (GPS corrigé à SURVEY_ACCURACY m ou mieux, cap EXIF au centième) :
# sur les photos imajbox du CD21, le cap suit la trajectoire à 0,33° près (écart-type).
SURVEY_HEADING_ERROR = 0.5
SURVEY_ACCURACY = 2.0
MIN_ANGLE = 3.0  # en dessous, les visées sont jugées parallèles
MAX_DISTANCE = 500.0  # au-delà, le résultat n'a plus de sens avec des photos de rue
GPS_ACCURACY = 3.0  # précision GPS supposée (m) quand la photo ne l'indique pas
GPS_WARNING = 5.0  # au-delà, la position des photos limite nettement la précision du point
ITERATIONS = 4  # les poids dépendent des distances, donc du point : quelques passes suffisent


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


def heading_error(sighting):
    """Erreur de visée supposée (degrés) : celle du recalage du cap s'il y en a un
    ("heading_error", voir calibration.py), plus faible pour le matériel de relevé."""
    if sighting.get("heading_error") is not None:
        return float(sighting["heading_error"])
    accuracy = sighting.get("accuracy")
    if sighting.get("precise") and accuracy is not None and accuracy <= SURVEY_ACCURACY:
        return SURVEY_HEADING_ERROR
    return HEADING_ERROR


def _sigma(distance, accuracy, error=HEADING_ERROR):
    """Écart latéral probable d'une visée (m) : erreur de visée et position GPS."""
    lateral = max(distance, 0.0) * math.tan(math.radians(error))
    return max(math.hypot(lateral, accuracy), 0.05)


def _normal(rays, weights):
    """Normales pondérées  Σ w (I - d dᵀ) : (a11, a12, a22, b1, b2)."""
    a11 = a12 = a22 = b1 = b2 = 0.0
    for (px, py, dx, dy), w in zip(rays, weights):
        m11, m12, m22 = w * (1 - dx * dx), w * (-dx * dy), w * (1 - dy * dy)
        a11 += m11
        a12 += m12
        a22 += m22
        b1 += m11 * px + m12 * py
        b2 += m12 * px + m22 * py
    return a11, a12, a22, b1, b2


def _distances(rays, x, y):
    """Position du point le long de chaque visée (m)."""
    return [(x - px) * dx + (y - py) * dy for px, py, dx, dy in rays]


def solve(sightings):
    """Intersection des visées.

    sightings : liste de dicts avec au moins "lon", "lat", "heading", et
    éventuellement "accuracy" (précision de la position GPS en m, None si inconnue)
    "precise" (cap au centième de degré) et "heading_error" (voir heading_error).
    Retourne un dict : lon, lat, distances (m, une par visée), angle (° de
    croisement le plus favorable), rms (écart moyen des visées au point, m),
    uncertainty (rayon d'incertitude en m pour l'erreur de visée de chaque photo et la
    précision GPS), gps (précision GPS la moins bonne, m), gps_assumed (vrai si
    au moins une photo n'indique pas sa précision, GPS_ACCURACY est alors supposé).
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

    rays, accuracies, errors = [], [], []
    for s in sightings:
        px, py = _local(s["lon"], s["lat"], lon0, lat0)
        h = math.radians(s["heading"])
        rays.append((px, py, math.sin(h), math.cos(h)))
        accuracies.append(s.get("accuracy"))
        errors.append(heading_error(s))
    gps_assumed = any(a is None for a in accuracies)
    accuracies = [GPS_ACCURACY if a is None else float(a) for a in accuracies]

    # Normales  Σ w (I - d dᵀ) X = Σ w (I - d dᵀ) P ; première passe sans pondération,
    # puis poids recalculés à partir des distances au point trouvé.
    weights = [1.0] * len(rays)
    for _ in range(ITERATIONS):
        a11, a12, a22, b1, b2 = _normal(rays, weights)
        det = a11 * a22 - a12 * a12
        if abs(det) < 1e-9 * max(a11 + a22, 1e-12) ** 2:
            raise TriangulationError("Visées parallèles : impossible de les croiser.")
        x = (a22 * b1 - a12 * b2) / det
        y = (a11 * b2 - a12 * b1) / det
        weights = [1.0 / _sigma(t, acc, e) ** 2 for t, acc, e in zip(_distances(rays, x, y), accuracies, errors)]

    distances = _distances(rays, x, y)
    residuals = [abs((x - px) * dy - (y - py) * dx) for px, py, dx, dy in rays]  # écart perpendiculaire
    if min(distances) < 0.5:
        raise TriangulationError(
            "Les visées ne se croisent pas devant les photos : vérifiez que vous visez "
            "bien le même objet.")
    if max(distances) > MAX_DISTANCE:
        raise TriangulationError(
            "Point trouvé à plus de {:.0f} m : visées trop peu sécantes ou objets "
            "différents.".format(MAX_DISTANCE))

    # Incertitude : chaque visée est décalée latéralement d'environ _sigma (erreur de cap
    # et position GPS). Covariance du point = (Σ w (I - d dᵀ))⁻¹, w = 1 / décalage² ;
    # on garde le grand axe.
    weights = [1.0 / _sigma(t, acc, e) ** 2 for t, acc, e in zip(distances, accuracies, errors)]
    c11, c12, c22, _, _ = _normal(rays, weights)
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
        "gps": max(accuracies),
        "gps_assumed": gps_assumed,
    }
