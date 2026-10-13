# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Cédric COCHART
# SPDX-License-Identifier: GPL-2.0-or-later
"""Services que l'application hôte fournit au cœur de calcul (voir install).

- fetch(url, callback, prefer_cache=False, timeout_ms=None) : GET asynchrone,
  callback(octets | None, erreur | None) ; peut renvoyer un objet ayant abort() ;
- fetch_json(url, callback, prefer_cache=False, timeout_ms=None) : idem, callback(objet JSON
  | None, erreur | None) ;
- ngf_altitude(lon, lat, ellipsoïdale) : altitude NGF-IGN69 (grille RAF20), ou None ;
- decode_band(octets, largeur, élévation min, élévation max) : photo équirectangulaire
  décodée en niveaux de gris, réduite à `largeur` et rognée à la bande d'élévations
  (tableau numpy, première ligne, hauteur et largeur de l'image entière), ou None ;
- run_task(fonction, arguments, done) : calcul en arrière-plan, puis done(résultat ou None) ;
- api_url : API Panoramax de l'instance choisie.

Plugin QGIS : qgis_host.py ; application web : réseau du navigateur, décodage par canvas.
Les valeurs par défaut rendent le cœur utilisable sans hôte (tests) : pas de réseau, pas
d'altitude NGF, calcul immédiat.
"""

api_url = "https://api.panoramax.xyz/api"


def fetch(url, callback, prefer_cache=False, timeout_ms=None):
    callback(None, "pas d'accès réseau")


def fetch_json(url, callback, prefer_cache=False, timeout_ms=None):
    callback(None, "pas d'accès réseau")


def ngf_altitude(lon, lat, ellipsoidal):
    return None


def decode_band(data, width, elev_min, elev_max):
    return None


def run_task(function, args, done):
    try:
        result = function(*args)
    except Exception:  # calcul raté : même effet qu'un résultat vide
        result = None
    done(result)


SERVICES = ("fetch", "fetch_json", "ngf_altitude", "decode_band", "run_task", "api_url")


def install(**services):
    """Remplace les services par ceux de l'hôte (noms : SERVICES)."""
    unknown = set(services) - set(SERVICES)
    if unknown:
        raise ValueError("services inconnus : {}".format(", ".join(sorted(unknown))))
    globals().update(services)
