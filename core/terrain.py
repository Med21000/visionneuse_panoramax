# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Cédric COCHART
# SPDX-License-Identifier: GPL-2.0-or-later
"""Profils d'altitude du terrain le long d'une visée (pour ground.intersect).

Sources, essayées dans l'ordre jusqu'à la première qui répond :
1. le service d'altimétrie de la Géoplateforme IGN : MNT du LiDAR HD (50 cm) là où il
   est publié, sinon RGE ALTI (1 m, France) ;
2. des services en ligne de secours, plus grossiers : OpenTopoData EU-DEM 25 m
   (Europe) puis Open-Meteo Copernicus 90 m (monde) ;
3. à défaut, un sol plat et horizontal.
Un profil est une liste [(distance horizontale en m, altitude)], depuis la photo.

Pente de la route plutôt que profil brut : la caméra roule sur la chaussée et les
points mesurés sont à moins de 20 m. Un MNT de terrain nu (RGE ALTI) contient fossés,
talus et bruit : un creux d'un mètre sous la visée allonge les distances de moitié.
On retient donc la droite de pente du terrain autour de la photo, ajustée de façon
robuste (Theil-Sen : pente médiane, insensible aux creux et bosses locaux), qui garde
la vraie pente d'une rue. La fenêtre (10 m derrière la photo à 25 m devant) reste
proche des points mesurés : sur 120 m, la droite suivait le relief lointain, et le sol
modélisé à 6 m pouvait être 0,3 m trop haut (mesures 15 % trop courtes, Pouilly-en-
Auxois). Un fossé ou une bosse de moins de 10 m environ reste ignoré.

Sur un pont, le MNT de terrain nu donne le fond du cours d'eau sous le tablier :
l'option « Mesure sur un pont » du panneau prend alors un sol plat et horizontal
(bridge), sans interroger les services.

Les services de secours ont des mailles de 25 à 90 m : seule leur pente générale
a un sens. Ce sont aussi des modèles de surface (bâtiments compris) : une pente
invraisemblable écarte la source.
Google Elevation n'est pas proposé : clé API et facturation obligatoires, et
ses conditions interdisent l'usage des données hors d'une carte Google.
"""

import math
from collections import OrderedDict
from urllib.parse import urlencode

from . import ground, host, triangulation

# Fenêtre de la droite de pente : 10 m derrière la photo à 25 m devant, le long de la visée
LINE_FROM, LINE_TO = -10.0, 25.0
SAMPLES = 71  # tous les 0,5 m (241 au plus en une requête pour le service IGN)
IGN_URL = "https://data.geopf.fr/altimetrie/1.0/calcul/alti/rest/elevationLine.json"
IGN_RESOURCE = "ign_rge_alti_wld"
IGN_LABEL = "RGE ALTI (IGN), pente de la route"
LIDAR_RESOURCE = "ign_lidar_hd_mnt_mono_wld"  # MNT LiDAR HD 50 cm, publié département par département
LIDAR_LABEL = "LiDAR HD (IGN), profil de la chaussée"
LIDAR_LINE_LABEL = "LiDAR HD (IGN), pente de la route"  # profil incomplet : droite de pente
# Sol sous la caméra assez exact pour en déduire la hauteur de caméra par l'altitude GPS : LiDAR
# HD seulement (le RGE ALTI, au mètre et plus ancien, peut être décalé de 0,2 à 0,5 m, soit 10 à
# 25 % d'erreur sur toutes les distances)
PRECISE_LABELS = (LIDAR_LABEL, LIDAR_LINE_LABEL)
MIN_COVERAGE = 0.8  # part minimale d'altitudes valides pour suivre le profil réel
NODATA_BELOW = -9999.0  # le service IGN renvoie -99999 hors de sa couverture
TIMEOUT_MS = 5000  # au-delà, on passe à la source suivante

# Services de secours : altitudes ponctuelles, ajustées par une droite (pente générale).
# Échantillons de 60 m derrière la photo à 120 m devant : assez large pour qu'une
# maille de 90 m donne une pente, et sous la limite de 100 points par requête.
COARSE_FROM, COARSE_TO, COARSE_STEP = -60.0, 120.0, 7.5
OPENTOPODATA_URL = "https://api.opentopodata.org/v1/eudem25m"
OPENTOPODATA_LABEL = "EU-DEM 25 m (OpenTopoData), pente générale"
OPENMETEO_URL = "https://api.open-meteo.com/v1/elevation"
OPENMETEO_LABEL = "Copernicus 90 m (Open-Meteo), pente générale"
# Ces modèles sont des modèles de surface (bâtiments, arbres compris) : en ville, la
# pente peut être faussée. Au-delà de cette pente, jugée invraisemblable pour une
# rue, la source est écartée.
MAX_COARSE_SLOPE = 0.12
BRIDGE_LABEL = "sol plat (mesure sur un pont)"

# Cache de la session, pour limiter les appels aux services : profils déjà calculés (même
# point de vue, même direction au dixième de degré près : visée reprise, point recalé ; plus
# grossier, le profil réel du LiDAR HD pourrait passer de l'autre côté d'une bordure)
# et sources sans données autour d'un point (LiDAR HD pas encore publié, hors de France),
# qui ne sont plus interrogées dans cette zone. Un service injoignable n'est pas mémorisé.
CACHE_SIZE = 64
COVERAGE_CELL = 0.001  # degré (~100 m) : maille des zones sans données mémorisées


def _profile_key(lon, lat, yaw):
    return round(lon, 6), round(lat, 6), int(round(yaw * 10)) % 3600


def _cell(lon, lat):
    return int(math.floor(lon / COVERAGE_CELL)), int(math.floor(lat / COVERAGE_CELL))


class TerrainProvider:
    def __init__(self):
        self.bridge = False  # mesure sur un pont : sol plat, sans interroger les services
        self._profiles = OrderedDict()  # clé (voir _profile_key) -> (profil, libellé, avertissement)
        self._waiting = {}  # clé -> rappels en attente (même profil déjà demandé)
        self._uncovered = set()  # (source, maille) : source sans données dans cette zone

    def profile(self, lon, lat, yaw, callback):
        """Profil le long de la visée. callback(profil, libellé de la source, avertissement | None).

        L'avertissement liste les sources écartées et pourquoi.
        """
        if self.bridge:
            callback(ground.flat_profile(), BRIDGE_LABEL, None)
            return
        key = _profile_key(lon, lat, yaw)
        if key in self._profiles:
            self._profiles.move_to_end(key)
            callback(*self._profiles[key])
            return
        if key in self._waiting:
            self._waiting[key].append(callback)
            return
        self._waiting[key] = [callback]
        cell = _cell(lon, lat)
        steps = [(name, step) for name, step in (("lidar", self._lidar_step), ("ign", self._ign_step),
                                                  ("opentopodata", self._opentopodata_step),
                                                  ("openmeteo", self._openmeteo_step))
                 if (name, cell) not in self._uncovered]
        reasons = []

        def finish(prof, label, cache):
            res = (prof, label, "; ".join(reasons) or None)
            if cache:
                self._profiles[key] = res
                while len(self._profiles) > CACHE_SIZE:
                    self._profiles.popitem(last=False)
            for cb in self._waiting.pop(key, []):
                cb(*res)

        def next_step():
            if not steps:
                finish(ground.flat_profile(), ground.FLAT, False)  # à retenter : un service peut revenir
                return
            name, step = steps.pop(0)

            def ok(prof, label):
                finish(prof, label, True)

            def failed(reason=None, uncovered=False):
                if uncovered:  # pas de données ici : source inutile dans cette zone pour la session
                    self._uncovered.add((name, cell))
                if reason:  # LiDAR HD pas encore publié ici : repli silencieux sur le RGE ALTI
                    reasons.append(reason)
                next_step()

            step(lon, lat, yaw, ok, failed)

        next_step()

    # ------------------------------------------------------------------
    # Service d'altimétrie IGN
    # ------------------------------------------------------------------
    def _lidar_step(self, lon, lat, yaw, ok, failed):
        """MNT LiDAR HD : profil réel de la chaussée (voir real_profile), ou à défaut droite de pente."""
        def parsed(dists, values):
            prof = real_profile(dists, values)
            if prof is not None:
                return prof, LIDAR_LABEL
            prof = robust_profile(dists, values)
            return (prof, LIDAR_LINE_LABEL) if prof is not None else None

        self._ign_line(lon, lat, yaw, ok, lambda error: failed(uncovered=error is None), LIDAR_RESOURCE, parsed)

    def _ign_step(self, lon, lat, yaw, ok, failed):
        def no_data(error):
            failed("service d'altimétrie IGN indisponible ({})".format(error) if error else
                   "pas d'altitude IGN à cet endroit (hors de France ?)", uncovered=error is None)

        def parsed(dists, values):
            prof = robust_profile(dists, values)
            return (prof, IGN_LABEL) if prof is not None else None

        self._ign_line(lon, lat, yaw, ok, no_data, IGN_RESOURCE, parsed)

    def _ign_line(self, lon, lat, yaw, ok, no_data, resource, parse):
        """Profil d'une ressource du service IGN ; parse(distances, altitudes) → (profil, libellé)
        ou None ; no_data(erreur ou None) si rien d'exploitable."""
        start = triangulation.offset(lon, lat, yaw, LINE_FROM)
        end = triangulation.offset(lon, lat, yaw, LINE_TO)
        params = {
            "lon": "{:.8f}|{:.8f}".format(start[0], end[0]),
            "lat": "{:.8f}|{:.8f}".format(start[1], end[1]),
            "resource": resource,
            "sampling": SAMPLES,
            "profile_mode": "simple",
        }
        url = IGN_URL + "?" + urlencode(params, safe="|")

        def done(data, error):
            samples = self._ign_samples(data, lon, lat, yaw) if not error and isinstance(data, dict) else None
            res = parse(*samples) if samples else None
            if res is not None:
                ok(*res)
            else:
                no_data(error)

        host.fetch_json(url, done, timeout_ms=TIMEOUT_MS)

    @staticmethod
    def _parse_ign(data, lon, lat, yaw):
        """Altitudes IGN → droite de pente (None si hors couverture)."""
        samples = TerrainProvider._ign_samples(data, lon, lat, yaw)
        return robust_profile(*samples) if samples else None

    @staticmethod
    def _ign_samples(data, lon, lat, yaw):
        """Altitudes IGN → (distances signées le long de la visée, altitudes), ou None."""
        dx, dy = math.sin(math.radians(yaw)), math.cos(math.radians(yaw))
        dists, values = [], []
        for pt in data.get("elevations") or []:
            try:
                z = float(pt["z"])
                x, y = triangulation._local(float(pt["lon"]), float(pt["lat"]), lon, lat)
            except (KeyError, TypeError, ValueError):
                return None
            dists.append(x * dx + y * dy)  # position signée le long de la visée
            values.append(z)
        return dists, values

    # ------------------------------------------------------------------
    # Services de secours (mailles de 25 à 90 m) : pente générale
    # ------------------------------------------------------------------
    @staticmethod
    def _coarse_points(lon, lat, yaw):
        n = int(round((COARSE_TO - COARSE_FROM) / COARSE_STEP)) + 1
        dists = [COARSE_FROM + i * COARSE_STEP for i in range(n)]
        return dists, [triangulation.offset(lon, lat, yaw, s) for s in dists]

    def _opentopodata_step(self, lon, lat, yaw, ok, failed):
        dists, pts = self._coarse_points(lon, lat, yaw)
        url = OPENTOPODATA_URL + "?" + urlencode({
            "locations": "|".join("{:.7f},{:.7f}".format(p[1], p[0]) for p in pts),
            "interpolation": "bilinear",
        }, safe="|,")

        def done(data, error):
            values = None
            if not error and isinstance(data, dict) and data.get("status") == "OK":
                values = [r.get("elevation") for r in data.get("results") or []]
            self._coarse_done(dists, values, error, OPENTOPODATA_LABEL, "OpenTopoData", ok, failed)

        host.fetch_json(url, done, timeout_ms=TIMEOUT_MS)

    def _openmeteo_step(self, lon, lat, yaw, ok, failed):
        dists, pts = self._coarse_points(lon, lat, yaw)
        url = OPENMETEO_URL + "?" + urlencode({
            "latitude": ",".join("{:.7f}".format(p[1]) for p in pts),
            "longitude": ",".join("{:.7f}".format(p[0]) for p in pts),
        }, safe=",")

        def done(data, error):
            values = data.get("elevation") if not error and isinstance(data, dict) else None
            self._coarse_done(dists, values, error, OPENMETEO_LABEL, "Open-Meteo", ok, failed)

        host.fetch_json(url, done, timeout_ms=TIMEOUT_MS)

    @staticmethod
    def _coarse_done(dists, values, error, label, name, ok, failed):
        if error:
            failed("{} indisponible ({})".format(name, error))
            return
        prof = robust_profile(dists, values)
        if prof is None:
            failed("pas d'altitude {} à cet endroit".format(name), uncovered=True)
            return
        slope = (prof[1][1] - prof[0][1]) / (prof[1][0] - prof[0][0])
        if abs(slope) > MAX_COARSE_SLOPE:
            failed("pente {} invraisemblable ({:+.0f} %, bâtiments ?)".format(name, slope * 100))
        else:
            ok(prof, label)


def robust_profile(dists, values):
    """Droite de pente z = a + b·s (Theil-Sen), renvoyée comme profil de 0 à MAX_DISTANCE.

    Pente = médiane des pentes entre paires de points, ordonnée = médiane des
    résidus : un creux ou une bosse sur moins d'un tiers de la fenêtre (pont,
    fossé, talus) ne la déforme pas. None s'il manque trop d'altitudes.
    """
    pts = []
    for s, z in zip(dists, values or []):
        try:
            z = float(z)
        except (TypeError, ValueError):
            continue
        if z == z and z > NODATA_BELOW:
            pts.append((float(s), z))
    if len(pts) < max(3, len(dists) // 2):
        return None  # trop de trous (mer, hors couverture)
    pts.sort()
    pts = pts[::max(1, len(pts) // 120)]  # 120 points suffisent (≈ 7 000 paires)
    slopes = sorted((z2 - z1) / (s2 - s1) for i, (s1, z1) in enumerate(pts)
                    for s2, z2 in pts[i + 1:] if s2 - s1 > 1e-6)
    slope = _median(slopes) if slopes else 0.0
    z0 = _median([z - slope * s for s, z in pts])
    return [(0.0, z0), (ground.MAX_DISTANCE, z0 + slope * ground.MAX_DISTANCE)]


def real_profile(dists, values):
    """Profil réel le long de la visée, depuis la photo (MNT LiDAR HD) : bordures, caniveau et
    dévers y figurent, contrairement à la droite de pente.

    Altitudes lissées par une médiane glissante sur trois échantillons (1,5 m) contre le bruit,
    profil commençant sous la caméra (altitude interpolée à 0), prolongé au-delà des données par
    la droite de pente jusqu'à MAX_DISTANCE. None s'il manque plus de 20 % des altitudes, ou
    s'il n'y en a pas de part et d'autre de la photo.
    """
    pts = []
    for s, z in zip(dists, values or []):
        try:
            z = float(z)
        except (TypeError, ValueError):
            continue
        if z == z and z > NODATA_BELOW:
            pts.append((float(s), z))
    line = robust_profile(dists, values)
    if line is None or len(pts) < MIN_COVERAGE * len(dists):
        return None
    pts.sort()
    zs = [z for _, z in pts]
    smooth = [(s, _median(zs[max(0, i - 1):i + 2])) for i, (s, _) in enumerate(pts)]
    before = [p for p in smooth if p[0] <= 0.0]
    after = [p for p in smooth if p[0] > 0.0]
    if not before or not after or after[0][0] - before[-1][0] > 2.0:
        return None
    (s1, z1), (s2, z2) = before[-1], after[0]
    prof = [(0.0, z1 + (z2 - z1) * (0.0 - s1) / (s2 - s1))] + after
    last_s, last_z = prof[-1]
    if last_s < ground.MAX_DISTANCE:
        slope = (line[1][1] - line[0][1]) / (line[1][0] - line[0][0])
        prof.append((ground.MAX_DISTANCE, last_z + slope * (ground.MAX_DISTANCE - last_s)))
    return prof


def _median(values):
    values = sorted(values)
    n = len(values)
    return values[n // 2] if n % 2 else (values[n // 2 - 1] + values[n // 2]) / 2.0
