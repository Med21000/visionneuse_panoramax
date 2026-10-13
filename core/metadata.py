# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Cédric COCHART
# SPDX-License-Identifier: GPL-2.0-or-later
"""Fiches des photos Panoramax (STAC) : cap, précision et altitude GPS, liens, recherche.
Sans réseau ni QGIS (voir api.py pour l'accès à l'API dans le plugin)."""

import math
from urllib.parse import urlencode, urlsplit


def search_url(lon, lat, radius_m=25, limit=20, collection=None, api_url="https://api.panoramax.xyz/api"):
    """URL de recherche STAC des photos autour d'un point (bbox carrée), éventuellement d'une
    seule séquence."""
    dlat = radius_m / 111320.0
    dlon = radius_m / (111320.0 * max(math.cos(math.radians(lat)), 0.01))
    bbox = "{:.7f},{:.7f},{:.7f},{:.7f}".format(lon - dlon, lat - dlat, lon + dlon, lat + dlat)
    params = {"bbox": bbox, "limit": limit}
    if collection:
        params["collections"] = collection
    return "{}/search?{}".format(api_url, urlencode(params))


def nearest_feature(features, lon, lat):
    """Renvoie la photo (feature STAC) la plus proche du point."""
    best, best_d = None, None
    coslat = math.cos(math.radians(lat))
    for feat in features:
        try:
            flon, flat = feat["geometry"]["coordinates"][:2]
        except (KeyError, TypeError, ValueError):
            continue
        d = ((flon - lon) * coslat) ** 2 + (flat - lat) ** 2
        if best_d is None or d < best_d:
            best, best_d = feat, d
    return best


def item_heading(item):
    props = item.get("properties", {}) or {}
    for key in ("view:azimuth", "heading"):
        if props.get(key) is not None:
            try:
                return float(props[key])
            except (TypeError, ValueError):
                pass
    return None


def precise_heading(item):
    """Cap de la photo au centième de degré, tiré de l'EXIF (GPSImgDirection), ou None.

    L'API arrondit view:azimuth au degré : jusqu'à 0,5° d'erreur de visée, soit
    17 cm à 20 m. La valeur EXIF n'est retenue que si elle concorde avec
    view:azimuth (même mesure, non arrondie), pas si l'API a corrigé le cap.
    """
    rounded = item_heading(item)
    exif = ((item.get("properties", {}) or {}).get("exif") or {}).get("Exif.GPSInfo.GPSImgDirection")
    if rounded is None or not exif:
        return None
    try:
        num, _, den = str(exif).partition("/")
        value = float(num) / float(den or 1)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    return value % 360 if abs((value - rounded + 540) % 360 - 180) < 1.0 else None


def _exif_number(item, key):
    """Valeur numérique d'une balise EXIF de la fiche (rationnel « a/b » ou nombre), ou None."""
    value = ((item.get("properties", {}) or {}).get("exif") or {}).get(key)
    try:
        num, _, den = str(value).partition("/")
        return float(num) / float(den or 1)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def exif_accuracy(item):
    """Précision horizontale de la position (m) déclarée dans l'EXIF de la photo
    (GPSHPositioningError), ou None si la photo ne la déclare pas."""
    value = _exif_number(item, "Exif.GPSInfo.GPSHPositioningError")
    return value if value is not None and value > 0 else None


def gps_differential(item):
    """La position de la photo a-t-elle reçu une correction différentielle (EXIF GPSDifferential) ?
    DGPS ou RTK : la précision réelle reste à vérifier (voir precision.py)."""
    return _exif_number(item, "Exif.GPSInfo.GPSDifferential") == 1


def item_accuracy(item):
    """Précision horizontale de la position GPS de la photo (m), ou None si inconnue : celle
    déclarée dans l'EXIF, sinon celle annoncée par Panoramax (souvent générique)."""
    value = exif_accuracy(item)
    if value is not None:
        return value
    value = (item.get("properties", {}) or {}).get("quality:horizontal_accuracy")
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def item_altitude(item):
    """Altitude GPS ellipsoïdale de la caméra (m, EXIF GPSAltitude), ou None. Souvent imprécise :
    à n'utiliser que pour une position vérifiée (voir precision.py)."""
    altitude = _exif_number(item, "Exif.GPSInfo.GPSAltitude")
    if altitude is None:
        return None
    return -altitude if _exif_number(item, "Exif.GPSInfo.GPSAltitudeRef") == 1 else altitude


def item_image_url(item):
    assets = item.get("assets", {}) or {}
    for key in ("sd", "thumb", "hd"):
        href = (assets.get(key) or {}).get("href")
        if href:
            return href
    return None


def item_link(item, rel):
    for link in item.get("links", []) or []:
        if link.get("rel") == rel and link.get("href"):
            return link["href"]
    return None


def id_from_href(href):
    """Dernier segment d'une URL d'item STAC (= identifiant de la photo)."""
    return urlsplit(href).path.rstrip("/").rsplit("/", 1)[-1] if href else None


def sequence_axis(item, prev_item, next_item):
    """Direction de déplacement le long de la séquence (cap en degrés), ou None.

    Calculée entre les photos voisines (précédente et suivante, fiches STAC ou None) pour
    suivre l'axe de la voie, plutôt que l'orientation de la caméra.
    """
    def position(feat):
        try:
            return [float(v) for v in feat["geometry"]["coordinates"][:2]]
        except (KeyError, TypeError, ValueError):
            return None

    here = position(item)
    a = position(prev_item) if prev_item else None
    b = position(next_item) if next_item else None
    a, b = (a or here), (b or here)
    if a is None or b is None or a == b:
        return None
    dx = (b[0] - a[0]) * math.cos(math.radians((a[1] + b[1]) / 2))
    dy = b[1] - a[1]
    if math.hypot(dx, dy) * 111320.0 < 0.5:  # photos quasi confondues (arrêt) : direction peu fiable
        return None
    return math.degrees(math.atan2(dx, dy)) % 360
