# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Cédric COCHART
# SPDX-License-Identifier: GPL-2.0-or-later
"""Façade du cadastre traversée par une visée, contrôlée par le MNS du LiDAR HD.

Pour une hauteur dont le pied est caché (voiture, haie, muret), ou pour définir une façade
d'un seul clic : le mur est le premier bord de bâtiment du cadastre (Parcellaire Express,
service WFS de la Géoplateforme) que coupe la visée horizontale depuis la photo.

Tests du 12/10/2026 (façades triangulées entre photos RTK du CD21 ; erreur sur la distance au
mur) : à Nuits-Saint-Georges et Semur-en-Auxois (90 façades), cadastre à 0,18 m en médiane,
90 % sous 0,8 m ; autour de Dijon, 0,08 m ; mais 0,8 m à Pouilly-en-Auxois, et des erreurs
grossières (2 à 18 m) quand le mur visé manque au cadastre (annexe, clôture). Le MNS du LiDAR
HD (bâti compris) place le mur au bord du toit, 0,8 m trop près en médiane (0 à 2,2 m selon le
débord) : il sert de contrôle. Près de la façade du cadastre (à partir de SEARCH_BEFORE m
avant : arbres et véhicules intermédiaires exclus), le bâti commence là où le MNS dépasse le
MNT de BUILT m ; le cadastre est confirmé s'il est de LIDAR_BEFORE m avant à LIDAR_AFTER m
après ce point (débord de toit), sinon un avertissement invite à contrôler la façade sur la
carte. Sans LiDAR publié, le cadastre est utilisé sans contrôle (signalé).

Lecture par maille d'environ 100 m, une fois par session ; une panne n'est pas gardée.
"""

import array
import math
import time
from urllib.parse import urlencode

from . import api, roads, triangulation

WFS_URL = roads.WFS_URL
LAYER = "CADASTRALPARCELS.PARCELLAIRE_EXPRESS:batiment"
WMS_URL = "https://data.geopf.fr/wms-r/wms"
MNS = "IGNF_LIDAR-HD_MNS_ELEVATION.ELEVATIONGRIDCOVERAGE.WGS84G"
MNT = "IGNF_LIDAR-HD_MNT_ELEVATION.ELEVATIONGRIDCOVERAGE.WGS84G"
CELL = roads.CELL
MARGIN = 60.0  # m autour de la maille : bâtiments visés depuis une photo de la maille
PIXEL = 0.5  # m : pas de la grille LiDAR demandée
MIN_RANGE, MAX_RANGE = 1.0, 60.0  # m : façades cherchées le long de la visée
BUILT = 2.0  # m de MNS au-dessus du MNT : bâti
LIDAR_BEFORE, LIDAR_AFTER = 0.5, 2.0  # m : écart toléré entre cadastre et bâti du LiDAR
SEARCH_BEFORE = 3.0  # m avant la façade du cadastre où chercher le bâti du LiDAR
NODATA_BELOW = -1000.0
RETRY = roads.RETRY
PENDING = roads.PENDING


def _cell(lon, lat):
    return roads._cell(lon, lat)


def _bounds(cell):
    """(lon0, lat0, lon1, lat1) de la maille et de MARGIN m autour."""
    lon0, lat0 = cell[0] * CELL, cell[1] * CELL
    dlat = MARGIN / 111320.0
    dlon = MARGIN / (111320.0 * math.cos(math.radians(lat0 + CELL / 2)))
    return lon0 - dlon, lat0 - dlat, lon0 + CELL + dlon, lat0 + CELL + dlat


def wfs_url(cell):
    lon0, lat0, lon1, lat1 = _bounds(cell)
    return WFS_URL + "?" + urlencode({
        "SERVICE": "WFS", "VERSION": "2.0.0", "REQUEST": "GetFeature", "TYPENAMES": LAYER,
        "OUTPUTFORMAT": "application/json", "SRSNAME": "EPSG:4326", "COUNT": 2000,
        "BBOX": "{:.7f},{:.7f},{:.7f},{:.7f},urn:ogc:def:crs:EPSG::4326".format(lat0, lon0, lat1, lon1)})


def grid_size(cell):
    """(largeur, hauteur) en pixels de la grille LiDAR de la maille, au pas de PIXEL m."""
    lon0, lat0, lon1, lat1 = _bounds(cell)
    w = (lon1 - lon0) * 111320.0 * math.cos(math.radians((lat0 + lat1) / 2)) / PIXEL
    h = (lat1 - lat0) * 111320.0 / PIXEL
    return int(round(w)), int(round(h))


def wms_url(cell, layer):
    lon0, lat0, lon1, lat1 = _bounds(cell)
    w, h = grid_size(cell)
    return WMS_URL + "?" + urlencode({
        "SERVICE": "WMS", "VERSION": "1.3.0", "REQUEST": "GetMap", "LAYERS": layer, "STYLES": "",
        "CRS": "EPSG:4326", "BBOX": "{:.7f},{:.7f},{:.7f},{:.7f}".format(lat0, lon0, lat1, lon1),
        "WIDTH": w, "HEIGHT": h, "FORMAT": "image/x-bil;bits=32"})


def parse_edges(data):
    """Bords des bâtiments d'une réponse WFS (GeoJSON) : [((lon, lat), (lon, lat))]."""
    out = []
    for feat in (data or {}).get("features") or []:
        geom = feat.get("geometry") or {}
        if geom.get("type") == "Polygon":
            polys = [geom.get("coordinates") or []]
        elif geom.get("type") == "MultiPolygon":
            polys = geom.get("coordinates") or []
        else:
            continue
        for poly in polys:
            for ring in poly:
                try:
                    pts = [(float(c[0]), float(c[1])) for c in ring]
                except (TypeError, ValueError, IndexError):
                    continue
                out += [(a, b) for a, b in zip(pts, pts[1:]) if a != b]
    return out


class Grid:
    """Grille d'altitudes (flottants 32 bits, ligne 0 au nord) couvrant `bounds`."""

    def __init__(self, data, cell):
        self.values = array.array("f")
        self.values.frombytes(bytes(data)[:len(data) - len(data) % 4])
        if array.array("f", [1.0]).tobytes() != b"\x00\x00\x80\x3f":
            self.values.byteswap()  # fichier petit-boutiste
        self.w, self.h = grid_size(cell)
        if len(self.values) != self.w * self.h:
            raise ValueError("grille LiDAR de taille inattendue")
        self.lon0, self.lat0, self.lon1, self.lat1 = _bounds(cell)

    def at(self, lon, lat):
        """Altitude au point (plus proche pixel), ou None hors de la grille ou sans données."""
        c = int((lon - self.lon0) / (self.lon1 - self.lon0) * self.w)
        r = int((self.lat1 - lat) / (self.lat1 - self.lat0) * self.h)
        if not (0 <= c < self.w and 0 <= r < self.h):
            return None
        z = self.values[r * self.w + c]
        return z if z > NODATA_BELOW else None


def wall_hit(edges, lon, lat, yaw):
    """Premier bord de bâtiment coupé par la visée horizontale depuis (lon, lat) au cap yaw :
    dict distance (m), points ((lon, lat) des extrémités du bord), ou None."""
    a = math.radians(yaw)
    dx, dy = math.sin(a), math.cos(a)
    best = None
    for p, q in edges:
        x1, y1 = triangulation._local(p[0], p[1], lon, lat)
        x2, y2 = triangulation._local(q[0], q[1], lon, lat)
        ex, ey = x2 - x1, y2 - y1
        den = dx * ey - dy * ex
        if abs(den) < 1e-9:
            continue
        t = (x1 * ey - y1 * ex) / den
        s = (x1 * dy - y1 * dx) / den
        if MIN_RANGE <= t <= MAX_RANGE and 0.0 <= s <= 1.0 and (best is None or t < best["distance"]):
            best = {"distance": t, "points": [p, q]}
    return best


def lidar_built(mns, mnt, lon, lat, yaw, start, until):
    """Distance le long de la visée horizontale où commence le bâti du LiDAR (MNS au-dessus
    du MNT de BUILT m), cherchée de `start` à `until` m ; None sans bâti, False sans LiDAR
    publié."""
    seen = False
    t = max(0.5, start)
    while t <= until:
        p = triangulation.offset(lon, lat, yaw, t)
        s, g = mns.at(*p), mnt.at(*p)
        if s is not None and g is not None:
            seen = True
            if s - g > BUILT:
                return t
        t += PIXEL / 2
    return None if seen else False


def check(distance, built):
    """Contrôle du mur du cadastre par le LiDAR : (texte, avertissement)."""
    if built is False:
        return "LiDAR HD non publié ici : façade du cadastre non contrôlée", True
    if built is None:
        return ("pas de bâti du LiDAR HD à la façade du cadastre : contrôlez-la sur la carte "
                "(mur absent du LiDAR, ou cadastre décalé)"), True
    gap = distance - built
    if -LIDAR_BEFORE <= gap <= LIDAR_AFTER:
        return "façade confirmée par le LiDAR HD", False
    return ("cadastre et LiDAR HD divergent de {:.1f} m : contrôlez la façade sur la carte".format(
        abs(gap)).replace(".", ",")), True


class BuildingProvider:
    """Façade du cadastre visée depuis une photo (voir le module), avec lectures en cache.

    lookup est synchrone : il rend la façade si les données sont là, PENDING si une lecture
    vient d'être lancée (ready() sera appelé à son arrivée), None sinon."""

    def __init__(self):
        self._cache = {}  # (type, maille) -> arêtes ou grille (None : rien d'utilisable)
        self._pending = set()
        self._failed = {}
        self.ready = None

    def lookup(self, lon, lat, yaw):
        cell = _cell(lon, lat)
        edges = self._get(("cadastre", cell), lambda: wfs_url(cell), parse_edges)
        mns = self._get(("mns", cell), lambda: wms_url(cell, MNS), lambda data: Grid(data, cell), raw=True)
        mnt = self._get(("mnt", cell), lambda: wms_url(cell, MNT), lambda data: Grid(data, cell), raw=True)
        if PENDING in (edges, mns, mnt):
            return PENDING
        hit = wall_hit(edges or [], lon, lat, yaw)
        if hit is None:
            return None
        # Bâti cherché seulement près du mur : un arbre, une haie ou un véhicule entre la photo
        # et la façade dépasse aussi du MNT
        d = hit["distance"]
        built = lidar_built(mns, mnt, lon, lat, yaw, d - SEARCH_BEFORE, d + LIDAR_AFTER) if mns and mnt else False
        hit["check"], hit["warning"] = check(hit["distance"], built)
        return hit

    def _get(self, key, url, parse, raw=False):
        if key in self._cache:
            return self._cache[key]
        if key in self._pending:
            return PENDING
        if time.monotonic() - self._failed.get(key, -RETRY) < RETRY:
            return None
        self._pending.add(key)

        def done(data, error):
            self._pending.discard(key)
            if error:
                self._failed[key] = time.monotonic()
            else:
                try:
                    self._cache[key] = parse(data)
                except (ValueError, TypeError):
                    self._cache[key] = None
            if self.ready is not None:
                self.ready()

        (api.fetch if raw else api.fetch_json)(url(), done, timeout_ms=20000)
        return PENDING if key in self._pending else self._cache.get(key)
