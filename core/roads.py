# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Cédric COCHART
# SPDX-License-Identifier: GPL-2.0-or-later
"""Axe de la route à l'endroit mesuré, pour la largeur perpendiculaire à la chaussée.

La direction du véhicule à la caméra n'est pas celle de la route au point mesuré, 10 à 20 m
plus loin : en courbe, l'écart y atteint 3 à 5° (8 à 16° en courbe serrée), et aux carrefours
où le véhicule tourne, la trajectoire coupe la route. Tests sur 419 points de la séquence imajbox
de Pouilly-en-Auxois (12/10/2026) : les tronçons de la BD TOPO s'accordent avec la trajectoire
à 0,4° en ligne droite et 0,8° en courbe, et suivent la route aux carrefours.

1. BD TOPO (IGN, service WFS de la Géoplateforme, sans clé) : parmi les tronçons à moins de
   MAX_DISTANCE m du milieu des deux bords cliqués, ceux plutôt perpendiculaires au segment
   cliqué (une largeur traverse la route : à un carrefour, cela désigne la rue mesurée) ; le
   plus proche donne l'axe, direction locale du tronçon sur ±TANGENT m.
2. À défaut (hors de France, service injoignable, aucun tronçon) : direction de la trajectoire
   de la séquence au plus près du point mesuré (photos voisines du milieu).
3. À défaut : direction du véhicule à la caméra (axe porté par les clics).

Les tronçons sont lus par maille d'environ 100 m, une fois par session ; une panne n'est pas
gardée (nouvel essai après RETRY s).
"""

import math
import time
from urllib.parse import urlencode

from . import host, metadata, triangulation

WFS_URL = "https://data.geopf.fr/wfs/ows"
LAYER = "BDTOPO_V3:troncon_de_route"
CELL = 0.001  # degré (~100 m) : maille de lecture des tronçons
MARGIN = 30.0  # m autour de la maille : tronçons proches d'un point du bord de la maille
MAX_DISTANCE = 15.0  # m entre le milieu des bords cliqués et le tronçon
TANGENT = 10.0  # m de part et d'autre : direction locale du tronçon
MIN_CROSSING = 45.0  # degrés minimum entre le tronçon et le segment cliqué
TRACK_RADIUS = 20.0  # m : photos de la séquence autour du point mesuré
TRACK_PHOTOS = 3
RETRY = 30.0  # s avant de réinterroger un service en panne
PENDING = "pending"  # résultat de lookup quand une lecture est en cours


def _axis_diff(a, b):
    """Écart entre deux directions de droite (modulo 180°)."""
    d = abs((a - b) % 180)
    return min(d, 180 - d)


def _cell(lon, lat):
    return int(math.floor(lon / CELL)), int(math.floor(lat / CELL))


def wfs_url(cell):
    """URL des tronçons de route de la maille (et de MARGIN m autour)."""
    lon0, lat0 = cell[0] * CELL, cell[1] * CELL
    dlat = MARGIN / 111320.0
    dlon = MARGIN / (111320.0 * math.cos(math.radians(lat0 + CELL / 2)))
    return WFS_URL + "?" + urlencode({
        "SERVICE": "WFS", "VERSION": "2.0.0", "REQUEST": "GetFeature", "TYPENAMES": LAYER,
        "OUTPUTFORMAT": "application/json", "SRSNAME": "EPSG:4326", "COUNT": 500,
        "BBOX": "{:.7f},{:.7f},{:.7f},{:.7f},urn:ogc:def:crs:EPSG::4326".format(
            lat0 - dlat, lon0 - dlon, lat0 + CELL + dlat, lon0 + CELL + dlon)})


def parse_sections(data):
    """Tronçons d'une réponse WFS (GeoJSON) : liste de dicts line ([(lon, lat)]), name."""
    out = []
    for feat in (data or {}).get("features") or []:
        geom = feat.get("geometry") or {}
        props = feat.get("properties") or {}
        lines = geom.get("coordinates") or []
        if geom.get("type") == "LineString":
            lines = [lines]
        elif geom.get("type") != "MultiLineString":
            continue
        name = props.get("cpx_numero") or props.get("nom_collaboratif_gauche") or props.get("nature") or ""
        for line in lines:
            try:
                pts = [(float(c[0]), float(c[1])) for c in line]
            except (TypeError, ValueError, IndexError):
                continue
            if len(pts) >= 2:
                out.append({"line": pts, "name": str(name)})
    return out


def _project(line, x, y):
    """(distance, abscisse curviligne) du point de la polyligne le plus proche de (x, y)."""
    best, s = (float("inf"), 0.0), 0.0
    for (ax, ay), (bx, by) in zip(line, line[1:]):
        length = math.hypot(bx - ax, by - ay)
        if length == 0:
            continue
        t = max(0.0, min(1.0, ((x - ax) * (bx - ax) + (y - ay) * (by - ay)) / length ** 2))
        d = math.hypot(x - ax - t * (bx - ax), y - ay - t * (by - ay))
        if d < best[0]:
            best = (d, s + t * length)
        s += length
    return best


def _at(line, s):
    """Point de la polyligne à l'abscisse curviligne s (bornée)."""
    acc = 0.0
    for (ax, ay), (bx, by) in zip(line, line[1:]):
        length = math.hypot(bx - ax, by - ay)
        if length > 0 and acc + length >= s:
            t = max(0.0, (s - acc) / length)
            return ax + t * (bx - ax), ay + t * (by - ay)
        acc += length
    return line[-1]


def road_axis(sections, lon, lat, crossing):
    """Axe de la route (dict axis, source, distance) au point (lon, lat), d'après les tronçons :
    le plus proche à moins de MAX_DISTANCE m parmi ceux qui coupent la direction `crossing`
    (cap du segment cliqué) d'au moins MIN_CROSSING°, ou None."""
    best = None
    for sec in sections:
        line = [triangulation._local(p[0], p[1], lon, lat) for p in sec["line"]]
        d, s = _project(line, 0.0, 0.0)
        if d > MAX_DISTANCE or (best and d >= best["distance"]):
            continue
        a, b = _at(line, s - TANGENT), _at(line, s + TANGENT)
        if math.hypot(b[0] - a[0], b[1] - a[1]) < 1.0:
            continue
        axis = math.degrees(math.atan2(b[0] - a[0], b[1] - a[1])) % 360
        if crossing is not None and _axis_diff(axis, crossing) < MIN_CROSSING:
            continue  # rue parallèle au segment cliqué : pas celle qu'il traverse
        source = "BD TOPO" + (", " + sec["name"] if sec["name"] else "")
        best = {"axis": axis, "source": source, "distance": d}
    return best


def trajectory_axis(features, sequence, lon, lat):
    """Direction de la trajectoire de la séquence près de (lon, lat) : axe principal des
    positions de ses photos à moins de TRACK_RADIUS m (dict axis, source), ou None."""
    pts = []
    for feat in (features or {}).get("features") or []:
        if sequence and feat.get("collection", sequence) != sequence:
            continue
        try:
            x, y = triangulation._local(*(float(v) for v in feat["geometry"]["coordinates"][:2]), lon, lat)
        except (KeyError, TypeError, ValueError):
            continue
        if math.hypot(x, y) <= TRACK_RADIUS:
            pts.append((x, y))
    if len(pts) < TRACK_PHOTOS:
        return None
    mx, my = sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)
    sxx = sum((p[0] - mx) ** 2 for p in pts)
    syy = sum((p[1] - my) ** 2 for p in pts)
    sxy = sum((p[0] - mx) * (p[1] - my) for p in pts)
    if sxx + syy < 4.0:
        return None  # photos quasi confondues (arrêt)
    angle = 0.5 * math.atan2(2 * sxy, sxx - syy)  # depuis l'est, sens trigonométrique
    return {"axis": (90.0 - math.degrees(angle)) % 360, "source": "trajectoire au point mesuré"}


class RoadAxisProvider:
    """Axe de la route au point mesuré (voir le module), avec lectures en cache.

    lookup est synchrone : il rend l'axe si les données sont là, PENDING si une lecture
    vient d'être lancée (ready() sera appelé à son arrivée), None sinon."""

    def __init__(self):
        self._sections = {}  # maille -> tronçons
        self._tracks = {}  # (séquence, maille) -> réponse de la recherche des photos
        self._pending = set()
        self._failed = {}  # clé -> heure de la panne
        self.ready = None  # appelé quand une lecture arrive

    def lookup(self, lon, lat, crossing, sequence=None):
        cell = _cell(lon, lat)
        sections = self._get(("bdtopo", cell), self._sections, cell, lambda: wfs_url(cell), parse_sections)
        if sections is PENDING:
            return PENDING
        res = road_axis(sections or [], lon, lat, crossing)
        if res or not sequence:
            return res
        key = (sequence, cell)
        track = self._get(("track",) + key, self._tracks, key,
                          lambda: metadata.search_url(lon, lat, TRACK_RADIUS + 10, 20, sequence, host.api_url), lambda data: data)
        if track is PENDING:
            return PENDING
        return trajectory_axis(track, sequence, lon, lat) if track else None

    def _get(self, key, cache, cache_key, url, parse):
        """Données en cache, PENDING (lecture lancée ou en cours), ou None (panne récente)."""
        if cache_key in cache:
            return cache[cache_key]
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
                cache[cache_key] = parse(data)
            if self.ready is not None:
                self.ready()

        host.fetch_json(url(), done, timeout_ms=15000)
        return PENDING if key in self._pending else self._get(key, cache, cache_key, url, parse)
