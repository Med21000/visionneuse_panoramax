# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Cédric COCHART
# SPDX-License-Identifier: GPL-2.0-or-later
"""Profils d'altitude du terrain le long d'une visée (pour ground.intersect).

Sources, essayées dans l'ordre jusqu'à la première qui répond :
1. le service d'altimétrie de la Géoplateforme IGN (RGE ALTI 1 m, France),
   activé par défaut (seule option proposée dans le panneau) ;
2. des services en ligne de secours, plus grossiers : OpenTopoData EU-DEM 25 m
   (Europe) puis Open-Meteo Copernicus 90 m (monde) ;
3. à défaut, un sol plat et horizontal.
Un profil est une liste [(distance horizontale en m, altitude)], depuis la photo.

Pente de la route plutôt que profil brut : la caméra roule sur la chaussée et les
points mesurés sont à moins de 20 m. Un MNT de terrain nu (RGE ALTI) ignore les
ponts (il donne le fond du cours d'eau sous le tablier), et contient fossés,
talus et bruit : un creux d'un mètre sous la visée allonge les distances de
moitié. On retient donc la droite de pente du terrain autour de la photo, ajustée
de façon robuste (Theil-Sen : pente médiane, insensible aux creux et bosses
locaux), qui garde la vraie pente d'une rue.

Les services de secours ont des mailles de 25 à 90 m : seule leur pente générale
a un sens. Ce sont aussi des modèles de surface (bâtiments compris) : une pente
invraisemblable écarte la source.
Google Elevation n'est pas proposé : clé API et facturation obligatoires, et
ses conditions interdisent l'usage des données hors d'une carte Google.
"""

import math
from urllib.parse import urlencode

from . import api, ground, triangulation

# Fenêtre de la droite de pente : 30 m derrière la photo à 90 m devant, le long de la visée
LINE_FROM, LINE_TO = -30.0, 90.0
SAMPLES = 241  # tous les 0,5 m ; maximum accepté en une requête par le service IGN
IGN_URL = "https://data.geopf.fr/altimetrie/1.0/calcul/alti/rest/elevationLine.json"
IGN_RESOURCE = "ign_rge_alti_wld"
IGN_LABEL = "RGE ALTI (IGN), pente de la route"
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


class TerrainProvider:
    def __init__(self):
        self.use_ign = True  # service IGN en premier

    def profile(self, lon, lat, yaw, callback):
        """Profil le long de la visée. callback(profil, libellé de la source, avertissement | None).

        L'avertissement liste les sources écartées et pourquoi.
        """
        steps = [self._opentopodata_step, self._openmeteo_step]
        if self.use_ign:
            steps.insert(0, self._ign_step)
        reasons = []

        def next_step():
            if not steps:
                callback(ground.flat_profile(), ground.FLAT, "; ".join(reasons) or None)
                return
            step = steps.pop(0)

            def ok(prof, label):
                callback(prof, label, "; ".join(reasons) or None)

            def failed(reason):
                reasons.append(reason)
                next_step()

            step(lon, lat, yaw, ok, failed)

        next_step()

    # ------------------------------------------------------------------
    # Service d'altimétrie IGN
    # ------------------------------------------------------------------
    def _ign_step(self, lon, lat, yaw, ok, failed):
        start = triangulation.offset(lon, lat, yaw, LINE_FROM)
        end = triangulation.offset(lon, lat, yaw, LINE_TO)
        params = {
            "lon": "{:.8f}|{:.8f}".format(start[0], end[0]),
            "lat": "{:.8f}|{:.8f}".format(start[1], end[1]),
            "resource": IGN_RESOURCE,
            "sampling": SAMPLES,
            "profile_mode": "simple",
        }
        url = IGN_URL + "?" + urlencode(params, safe="|")

        def done(data, error):
            prof = self._parse_ign(data, lon, lat, yaw) if not error and isinstance(data, dict) else None
            if prof is not None:
                ok(prof, IGN_LABEL)
            else:
                failed("service d'altimétrie IGN indisponible ({})".format(error) if error else
                       "pas d'altitude IGN à cet endroit (hors de France ?)")

        api.fetch_json(url, done, timeout_ms=TIMEOUT_MS)

    @staticmethod
    def _parse_ign(data, lon, lat, yaw):
        """Altitudes IGN → droite de pente (None si hors couverture)."""
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
        return robust_profile(dists, values)

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

        api.fetch_json(url, done, timeout_ms=TIMEOUT_MS)

    def _openmeteo_step(self, lon, lat, yaw, ok, failed):
        dists, pts = self._coarse_points(lon, lat, yaw)
        url = OPENMETEO_URL + "?" + urlencode({
            "latitude": ",".join("{:.7f}".format(p[1]) for p in pts),
            "longitude": ",".join("{:.7f}".format(p[0]) for p in pts),
        }, safe=",")

        def done(data, error):
            values = data.get("elevation") if not error and isinstance(data, dict) else None
            self._coarse_done(dists, values, error, OPENMETEO_LABEL, "Open-Meteo", ok, failed)

        api.fetch_json(url, done, timeout_ms=TIMEOUT_MS)

    @staticmethod
    def _coarse_done(dists, values, error, label, name, ok, failed):
        if error:
            failed("{} indisponible ({})".format(name, error))
            return
        prof = robust_profile(dists, values)
        if prof is None:
            failed("pas d'altitude {} à cet endroit".format(name))
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


def _median(values):
    values = sorted(values)
    n = len(values)
    return values[n // 2] if n % 2 else (values[n // 2 - 1] + values[n // 2]) / 2.0
