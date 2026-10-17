# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Cédric COCHART
# SPDX-License-Identifier: GPL-2.0-or-later
"""Précision de la position GPS des photos, vérifiée d'après leurs métadonnées.

Panoramax annonce souvent une précision générique (2 m pour le matériel de relevé). Une
photo est jugée précise (au décimètre) :
- si son EXIF déclare une précision (GPSHPositioningError) d'au plus PRECISE_ACCURACY ;
- ou si sa position a reçu une correction différentielle (GPSDifferential) et que cela se
  vérifie sur sa séquence : pour les photos voisines, l'altitude GPS (convertie en NGF)
  moins le sol LiDAR HD sous la photo, c'est-à-dire la hauteur de la caméra, est constante
  à MAX_SPREAD près, sans dériver de plus de MAX_DRIFT. Un GPS non recalé (DGPS, GPS
  simple) varie de plusieurs décimètres. Si la portion de séquence autour de la photo est
  refusée (pont, défaut du LiDAR), d'autres portions de la séquence sont essayées : une
  seule vérifiée suffit pour toute la séquence.
  C'est le cas des photos imajbox 360 HD (RTK et centrale inertielle : 4,7 cm sur 100
  photos à Pouilly-en-Auxois).

Une photo précise donne la hauteur de caméra par son altitude (voir
measures.MeasureTool.camera_info). Les mesures ne sont pleinement fiables qu'avec à la
fois une position vérifiée précise et le LiDAR HD sous la photo ; sinon l'outil est en
mode dégradé (hauteur de caméra et inclinaison à caler, sol approximatif sans LiDAR) :
une fenêtre l'annonce à l'activation de la mesure (voir viewer_dock).

Une vérification par séquence (deux requêtes : fiches des photos voisines, altitudes
LiDAR), en arrière-plan ; le résultat est gardé pour la session.
"""

import math
import statistics
from urllib.parse import urlencode

from . import host, metadata, terrain

PRECISE_ACCURACY = 0.20  # m : précision déclarée suffisante pour la hauteur de caméra par l'altitude
VERIFIED_ACCURACY = 0.10  # m : précision retenue pour une séquence vérifiée
CHECK_RADIUS = 60.0  # m autour de la photo pour les photos voisines de la séquence
CHECK_PHOTOS = 20
# Portion autour de la photo refusée : autres portions de la même séquence essayées (une
# seule vérifiée suffit, la séquence est relevée avec le même matériel)
OTHER_PORTIONS = 2
OTHER_RADIUS = 1000.0  # m
OTHER_SEARCH = 100  # photos de la séquence parmi lesquelles choisir les autres portions
MIN_PHOTOS = 8
# Hauteur de caméra entre photos voisines : écart-type et dérive maximaux. Le sol LiDAR HD
# (±10 cm), les bordures, le dévers et la suspension du véhicule la font varier de plus
# de 10 cm sur des séquences RTK (Pouilly-en-Auxois, 2026-10-15 : 5 points sur 72 refusés
# à 10 cm, de 10,5 à 19,8 cm, ou dérive de 11 cm) ; un GPS non recalé varie de plusieurs
# décimètres.
MAX_SPREAD = 0.20  # m : écart-type
MAX_DRIFT = 0.15  # m : écart entre les deux moitiés de la portion vérifiée
OUTLIER = 4.0  # photos écartées au-delà de OUTLIER écarts médians absolus (corrigés)
MIN_MAD = 0.02  # m : écart médian minimal (photos très régulières)
CAMERA_RANGE = (0.5, 4.0)  # hauteur de caméra plausible (m)
ELEVATION_URL = "https://data.geopf.fr/altimetrie/1.0/calcul/alti/rest/elevation.json"
DEGRADED_ADVICE = ("Les mesures dépendent alors de la hauteur de caméra, de l'inclinaison de la caméra et, sans "
                   "LiDAR HD, d'un sol approximatif : calez la hauteur de caméra sur une longueur connue et "
                   "l'inclinaison sur deux objets verticaux, puis contrôlez les résultats.")


def lidar_url(points):
    """URL des altitudes LiDAR HD (MNT) sous des points [(lon, lat)]."""
    return ELEVATION_URL + "?" + urlencode({
        "lon": "|".join("{:.7f}".format(lon) for lon, _ in points),
        "lat": "|".join("{:.7f}".format(lat) for _, lat in points),
        "resource": terrain.LIDAR_RESOURCE, "zonly": "true"}, safe="|")


def _ground(data):
    """Altitudes LiDAR d'une réponse du service (None hors couverture)."""
    out = []
    for z in (data or {}).get("elevations") or []:
        try:
            z = float(z["z"] if isinstance(z, dict) else z)
        except (KeyError, TypeError, ValueError):
            z = None
        out.append(z if z is not None and z > terrain.NODATA_BELOW else None)
    return out


def result(precise, accuracy=None, lidar=None, reason="", count=0, spread=None, height=None, transient=False):
    """Résultat d'une vérification : precise, accuracy (m, ou None : celle de la photo), lidar (LiDAR
    HD publié sous la photo, None si inconnu), reason (texte), count et spread pour une séquence
    vérifiée, height : hauteur de caméra calculée (altitude GPS moins sol LiDAR HD ; médiane de la
    séquence vérifiée, ou celle de la photo de précision déclarée), None si inconnue. transient :
    obtenu malgré un service injoignable, donc pas gardé pour la session (vérification refaite)."""
    return {"precise": precise, "accuracy": accuracy, "lidar": lidar, "reason": reason, "count": count,
            "spread": spread, "height": height, "transient": transient}


def degraded(res):
    """Mode dégradé : il manque la position vérifiée précise ou le LiDAR HD sous la photo
    (False tant que la vérification n'est pas faite)."""
    return bool(res) and not (res["precise"] and res["lidar"] is True)


def degraded_reasons(res):
    """Ce qui manque pour des mesures pleinement fiables (liste de textes)."""
    out = []
    if not res["precise"]:
        out.append("position GPS non vérifiée au décimètre ({})".format(res["reason"]))
    if res["lidar"] is False:
        out.append("pas de LiDAR HD publié à cet endroit")
    elif res["lidar"] is None:
        out.append("présence du LiDAR HD non vérifiée (service d'altimétrie indisponible)")
    return out


def camera_suggestion(res):
    """Hauteur de caméra calculée à proposer dans le panneau : (hauteur, explication), ou None."""
    if not res or not res["precise"] or res["lidar"] is not True or res.get("height") is None:
        return None
    if res["count"]:
        how = "médiane de {} photos de la séquence, écart-type {:.0f} cm".format(res["count"], res["spread"] * 100)
    else:
        how = "photo de précision déclarée ±{:.0f} cm".format(res["accuracy"] * 100)
    return res["height"], ("Hauteur de caméra calculée : altitude GPS de la caméra (NGF) moins le sol LiDAR HD "
                           "({}). Cliquer pour remplir le champ « Caméra à ».".format(how))


def state_text(res, accuracy=None, tilt=None):
    """Ligne d'état du panneau : précision de la position, présence du LiDAR HD et, si
    `tilt` (texte, manquante) est donné, inclinaison de la photo."""
    tilt_text, tilt_missing = tilt or ("", False)
    if not res:
        text = "Position GPS et LiDAR HD : vérification en cours…"
        return "⚠ {} · {}".format(text, tilt_text) if tilt_missing else (
            "{} · {}".format(text, tilt_text) if tilt_text else text)
    if res["precise"]:
        position = "Position GPS vérifiée (±{} m)".format("{:.2f}".format(res["accuracy"]).replace(".", ","))
    elif accuracy is not None:
        position = "Position GPS non vérifiée (±{} m annoncés)".format("{:g}".format(accuracy).replace(".", ","))
    else:
        position = "Position GPS non vérifiée"
    lidar = {True: "LiDAR HD", False: "pas de LiDAR HD", None: "LiDAR HD non vérifié"}[res["lidar"]]
    text = " · ".join(t for t in (position, lidar, tilt_text) if t)
    if degraded(res):
        return "⚠ " + text + " : mode de mesure dégradé"
    if tilt_missing:
        return "⚠ " + text + " : calez-la sur deux objets verticaux"
    return text


def verify_heights(pts):
    """Vérification d'une séquence d'après la hauteur de caméra (altitude GPS NGF moins sol
    LiDAR HD) de photos voisines [(lon, lat, hauteur)] : résultat (voir result).

    Robuste : les photos dont la hauteur s'écarte de plus de OUTLIER écarts (écart médian
    absolu) sont écartées (pont, défaut du LiDAR, arrêt sous un arbre) ; la dispersion des
    autres doit rester sous MAX_SPREAD. La hauteur doit aussi être la même sur les deux
    moitiés de la portion vérifiée (à MAX_DRIFT près) : un GPS non recalé peut être lisse
    sur quelques photos, mais il dérive."""
    heights = [h for _, _, h in pts]
    median = statistics.median(heights)
    mad = 1.4826 * statistics.median(abs(h - median) for h in heights)
    kept = [p for p in pts if abs(p[2] - median) <= OUTLIER * max(mad, MIN_MAD)]
    if len(kept) < MIN_PHOTOS:
        return result(False, lidar=True, reason="hauteur de caméra trop dispersée ({} photos sur {})".format(
            len(kept), len(pts)))
    spread, height = statistics.pstdev([h for _, _, h in kept]), statistics.median([h for _, _, h in kept])
    # Deux moitiés le long de la trajectoire (axe principal des positions)
    lon0, lat0 = kept[0][0], kept[0][1]
    xy = [((lon - lon0) * 111320.0 * math.cos(math.radians(lat0)), (lat - lat0) * 111320.0) for lon, lat, _ in kept]
    mx, my = sum(x for x, _ in xy) / len(xy), sum(y for _, y in xy) / len(xy)
    sxx = sum((x - mx) ** 2 for x, _ in xy)
    syy = sum((y - my) ** 2 for _, y in xy)
    sxy = sum((x - mx) * (y - my) for x, y in xy)
    a = 0.5 * math.atan2(2 * sxy, sxx - syy)
    order = sorted(range(len(kept)), key=lambda i: xy[i][0] * math.cos(a) + xy[i][1] * math.sin(a))
    half = len(order) // 2
    drift = abs(statistics.median(kept[i][2] for i in order[:half]) -
                statistics.median(kept[i][2] for i in order[half:]))
    precise = spread <= MAX_SPREAD and drift <= MAX_DRIFT and CAMERA_RANGE[0] <= height <= CAMERA_RANGE[1]
    dropped = len(pts) - len(kept)
    note = " ({} écartée{})".format(dropped, "s" if dropped > 1 else "") if dropped else ""
    if precise:
        reason = "position vérifiée : hauteur de caméra constante à {:.0f} cm près sur {} photos{}".format(
            spread * 100, len(kept), note)
    elif drift > MAX_DRIFT and spread <= MAX_SPREAD:
        reason = "position non vérifiée : hauteur de caméra qui dérive de {:.0f} cm le long de la séquence".format(
            drift * 100)
    else:
        reason = "position non vérifiée : hauteur de caméra variable de {:.0f} cm sur {} photos".format(
            spread * 100, len(kept))
    return result(precise, VERIFIED_ACCURACY if precise else None, True, reason, len(kept), spread, height)


class PrecisionChecker:
    """Vérifications par séquence, gardées pour la session."""

    def __init__(self):
        self._results = {}  # séquence -> résultat
        self._pending = {}  # séquence -> rappels en attente du résultat

    def known(self, item):
        """Résultat déjà connu pour la séquence de la photo, ou None."""
        return self._results.get((item or {}).get("collection"))

    def check(self, item, callback):
        """Précision de la photo (fiche STAC) : callback(résultat) dès qu'il est connu."""
        sequence = item.get("collection")
        if sequence in self._results:
            callback(self._results[sequence])
            return
        # Photo sans séquence : vérifiée seule (deux photos sans séquence ne partagent rien)
        key = sequence if sequence is not None else ("photo", item.get("id"))
        if key in self._pending:
            self._pending[key].append(callback)
            return
        self._pending[key] = [callback]

        def done(res):
            if sequence is not None and not res.get("transient"):
                self._results[sequence] = res
            for cb in self._pending.pop(key, []):
                cb(res)

        try:
            lon, lat = (float(v) for v in item["geometry"]["coordinates"][:2])
        except (KeyError, TypeError, ValueError):
            done(result(False, reason="position de la photo inconnue"))
            return
        accuracy = metadata.exif_accuracy(item)
        if accuracy is not None and accuracy <= PRECISE_ACCURACY:
            self._check_lidar(lon, lat, done, "précision déclarée dans les métadonnées de la photo", accuracy,
                              host.ngf_altitude(lon, lat, metadata.item_altitude(item)))
        elif metadata.gps_differential(item) and sequence:
            self._check_sequence(item, sequence, lon, lat, done)
        else:
            self._check_lidar(lon, lat, done, "pas de correction différentielle déclarée")

    def _check_lidar(self, lon, lat, done, reason, accuracy=None, camera_alt=None, transient=False):
        """Le LiDAR HD est-il publié sous la photo ? accuracy : précision déclarée suffisante, ou None ;
        camera_alt : altitude NGF de la caméra, pour la hauteur de caméra d'une photo précise ;
        transient : une étape précédente n'a pas pu joindre son service."""
        def got(data, error):
            ground = _ground(data) if not error else []
            lidar = None if error else bool(ground and ground[0] is not None)
            height = None
            if lidar and accuracy is not None and camera_alt is not None:
                height = camera_alt - ground[0]
                if not CAMERA_RANGE[0] <= height <= CAMERA_RANGE[1]:
                    height = None
            done(result(accuracy is not None, accuracy, lidar, reason, height=height,
                        transient=transient or bool(error)))

        host.fetch_json(lidar_url([(lon, lat)]), got, timeout_ms=terrain.TIMEOUT_MS)

    def _check_sequence(self, item, sequence, lon, lat, done):
        """Portion de la séquence autour de la photo ; en cas d'échec, d'autres portions (voir
        _other_portions) : une seule vérifiée suffit pour toute la séquence."""
        def first(kind, value):
            if kind == "photos":
                self._check_lidar(lon, lat, done, "photos voisines indisponibles ({})".format(value), transient=True)
            elif kind == "few":
                self._check_lidar(lon, lat, done, "altitude GPS inexploitable ({} photos voisines)".format(value))
            elif kind == "ground":
                done(result(False, reason="service d'altimétrie IGN indisponible ({})".format(value), transient=True))
            elif value["precise"] or value["lidar"] is not True:
                done(value)  # vérifiée, ou pas de LiDAR HD ici : rien à chercher ailleurs
            else:
                self._other_portions(sequence, lon, lat, value, done)

        self._portion(sequence, lon, lat, first)

    def _portion(self, sequence, lon, lat, callback):
        """Vérifie la portion de séquence autour de (lon, lat) : callback(genre, valeur), genre
        « photos » ou « ground » (service injoignable, valeur : l'erreur), « few » (trop peu
        d'altitudes GPS, valeur : leur nombre) ou « result » (valeur : résultat)."""
        def got_photos(data, error):
            if error:
                callback("photos", error)
                return
            photos = []
            for feat in (data or {}).get("features") or []:
                try:
                    plon, plat = (float(v) for v in feat["geometry"]["coordinates"][:2])
                except (KeyError, TypeError, ValueError):
                    continue
                ngf = host.ngf_altitude(plon, plat, metadata.item_altitude(feat))
                if ngf is not None and feat.get("collection", sequence) == sequence:
                    photos.append((plon, plat, ngf))
            if len(photos) < MIN_PHOTOS:
                callback("few", len(photos))
                return
            host.fetch_json(lidar_url([(p[0], p[1]) for p in photos]),
                           lambda data, error: got_ground(photos, data, error), timeout_ms=terrain.TIMEOUT_MS)

        def got_ground(photos, data, error):
            if error:
                callback("ground", error)
                return
            pts = [(plon, plat, ngf - g) for (plon, plat, ngf), g in zip(photos, _ground(data)) if g is not None]
            if len(pts) < MIN_PHOTOS:
                callback("result", result(False, lidar=False, reason="pas de LiDAR HD pour vérifier la position"))
                return
            callback("result", verify_heights(pts))

        host.fetch_json(metadata.search_url(lon, lat, CHECK_RADIUS, CHECK_PHOTOS, sequence, host.api_url), got_photos)

    def _other_portions(self, sequence, lon, lat, failed, done):
        """Portion autour de la photo refusée (failed) : jusqu'à OTHER_PORTIONS autres portions de
        la séquence, à moins de OTHER_RADIUS, essayées l'une après l'autre (le service d'altimétrie
        limite les rafales). La première vérifiée vaut pour toute la séquence ; si aucune ne
        l'est, failed est retenu."""
        def got_photos(data, error):
            if error:
                done(dict(failed, transient=True))  # à refaire plus tard
                return
            centers, taken = [], [(lon, lat)]
            candidates = []
            for feat in (data or {}).get("features") or []:
                try:
                    plon, plat = (float(v) for v in feat["geometry"]["coordinates"][:2])
                except (KeyError, TypeError, ValueError):
                    continue
                if feat.get("collection", sequence) == sequence:
                    candidates.append((plon, plat))
            # Les plus éloignées d'abord, assez loin les unes des autres pour ne pas se recouvrir
            candidates.sort(key=lambda p: -_metres(p, (lon, lat)))
            for p in candidates:
                if len(centers) < OTHER_PORTIONS and all(_metres(p, q) >= 2 * CHECK_RADIUS for q in taken):
                    centers.append(p)
                    taken.append(p)
            try_next(centers, 0)

        def try_next(centers, tried):
            if not centers:
                if tried:
                    plural = "s" if tried > 1 else ""
                    done(dict(failed, reason="{} ; {} autre{p} portion{p} de la séquence non vérifiée{p}".format(
                        failed["reason"], tried, p=plural)))
                else:
                    done(failed)
                return
            (clon, clat), rest = centers[0], centers[1:]

            def got(kind, value):
                if kind in ("photos", "ground"):
                    done(dict(failed, transient=True))
                elif kind == "result" and value["precise"]:
                    distance = _metres((clon, clat), (lon, lat))
                    done(dict(value, reason="{}, à {:.0f} m de la photo (même séquence)".format(
                        value["reason"], round(distance, -1))))
                else:
                    try_next(rest, tried + 1)

            self._portion(sequence, clon, clat, got)

        host.fetch_json(metadata.search_url(lon, lat, OTHER_RADIUS, OTHER_SEARCH, sequence, host.api_url), got_photos)


def _metres(a, b):
    """Distance (m) entre deux (lon, lat) proches."""
    dx = (b[0] - a[0]) * 111320.0 * math.cos(math.radians((a[1] + b[1]) / 2))
    return math.hypot(dx, (b[1] - a[1]) * 111320.0)
