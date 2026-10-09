# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Cédric COCHART
# SPDX-License-Identifier: GPL-2.0-or-later
"""Accès à l'API Panoramax (méta-catalogue national) et utilitaires réseau."""

import json
import math
import time
from collections import OrderedDict
from urllib.parse import parse_qs, urlencode, urlsplit

from qgis.core import QgsNetworkAccessManager, QgsSettings
from qgis.PyQt.QtCore import QUrl
from qgis.PyQt.QtNetwork import QNetworkReply, QNetworkRequest

META_API_URL = "https://api.panoramax.xyz/api"
META_EXPLORE_URL = "https://explore.panoramax.fr/fr/index"
INSTANCES_URL = META_API_URL + "/instances"
TILES_MIN_ZOOM = 0
TILES_MAX_ZOOM = 15

# Instance courante (modifiée par set_instance). Par défaut : méta-catalogue,
# qui agrège toutes les instances.
INSTANCE_NAME = "meta"
API_URL = META_API_URL
TILES_URL = API_URL + "/map/{z}/{x}/{y}.mvt"
EXPLORE_URL = META_EXPLORE_URL


def set_instance(name, base_url=None):
    """Bascule sur une instance. base_url=None -> méta-catalogue.

    base_url est l'adresse du site de l'instance (ex. https://panoramax.ign.fr) ;
    l'API est servie sous /api et la visionneuse à la racine du site.
    """
    global INSTANCE_NAME, API_URL, TILES_URL, EXPLORE_URL
    if not base_url:
        INSTANCE_NAME, API_URL, EXPLORE_URL = "meta", META_API_URL, META_EXPLORE_URL
    else:
        base = base_url.rstrip("/")
        if base.endswith("/api"):
            base = base[:-4]
        INSTANCE_NAME, API_URL, EXPLORE_URL = name or base, base + "/api", base + "/"
    TILES_URL = API_URL + "/map/{z}/{x}/{y}.mvt"
    clear_cache()


def parse_instances(data):
    """Liste (nom, url) triée à partir de la réponse de /api/instances."""
    result = []
    for inst in (data or {}).get("instances", []) or []:
        url = inst.get("url")
        if url:
            result.append((inst.get("name") or url, url))
    return sorted(result, key=lambda t: t[0].lower())

# Références vers les réponses en cours, pour éviter qu'elles soient
# détruites par le ramasse-miettes Python avant la fin du téléchargement.
_pending = set()


def explore_url(pic_id=None, lat=None, lon=None, zoom=18, focus=None):
    """Construit l'URL de la visionneuse explore.panoramax.fr."""
    params = {}
    if lat is not None and lon is not None:
        params["map"] = "{:.2f}/{:.7f}/{:.7f}".format(zoom, lat, lon)
    if pic_id:
        params["pic"] = pic_id
    params["focus"] = focus or ("pic" if pic_id else "map")
    params["speed"] = "250"
    return EXPLORE_URL + "?" + urlencode(params, safe="/")


def parse_viewer_url(url):
    """Extrait pic et map (zoom, lat, lon) d'une URL de visionneuse.

    La visionneuse peut mettre ses paramètres dans la query ou dans le hash :
    on regarde les deux.
    """
    parts = urlsplit(url)
    params = {}
    for chunk in (parts.query, parts.fragment.lstrip("/")):
        if "=" in chunk:
            for key, values in parse_qs(chunk).items():
                if values:
                    params[key] = values[0]
    result = {"pic": params.get("pic")}
    if params.get("xyz"):
        # x = cap absolu (0 = nord, 90 = est), y = inclinaison, z = zoom 0-100
        try:
            vals = [float(v) for v in params["xyz"].split("/")[:3]]
            while len(vals) < 3:
                vals.append(0.0)
            result["xyz"] = tuple(vals)
        except ValueError:
            pass
    if params.get("map"):
        try:
            z, lat, lon = (float(v) for v in params["map"].split("/")[:3])
            result["map"] = (z, lat, lon)
        except ValueError:
            pass
    return result


def _request(url, prefer_cache=False, timeout_ms=None):
    req = QNetworkRequest(QUrl(url))
    req.setRawHeader(b"User-Agent", b"QGIS-Visionneuse-Panoramax/1.0")
    if timeout_ms:
        req.setTransferTimeout(int(timeout_ms))  # sans réponse dans ce délai : requête abandonnée
    if prefer_cache:
        # Utilise le cache disque réseau de QGIS dès qu'une copie existe
        req.setAttribute(QNetworkRequest.Attribute.CacheLoadControlAttribute,
                         QNetworkRequest.CacheLoadControl.PreferCache)
    return req


def fetch(url, callback, prefer_cache=False, timeout_ms=None):
    """GET asynchrone. callback(bytes | None, message_erreur | None)."""
    reply = QgsNetworkAccessManager.instance().get(_request(url, prefer_cache, timeout_ms))
    _pending.add(reply)

    def done():
        _pending.discard(reply)
        err = reply.error()
        timeout_errors = (QNetworkReply.NetworkError.OperationCanceledError,
                          getattr(QNetworkReply.NetworkError, "TimeoutError", None))
        if timeout_ms and err in timeout_errors:
            callback(None, "pas de réponse en {:.0f} s".format(timeout_ms / 1000.0))
        elif err != QNetworkReply.NetworkError.NoError:
            callback(None, reply.errorString())
        else:
            callback(bytes(reply.readAll()), None)
        reply.deleteLater()

    reply.finished.connect(done)
    return reply


def fetch_json(url, callback, prefer_cache=False, timeout_ms=None):
    """GET asynchrone d'un JSON. callback(dict | None, message_erreur | None)."""

    def done(data, error):
        if error:
            callback(None, error)
            return
        try:
            callback(json.loads(data.decode("utf-8")), None)
        except (ValueError, UnicodeDecodeError) as exc:
            callback(None, "Réponse JSON invalide : {}".format(exc))

    return fetch(url, done, prefer_cache, timeout_ms)


def search_url(lon, lat, radius_m=25, limit=20):
    """URL de recherche STAC des photos autour d'un point (bbox carrée)."""
    dlat = radius_m / 111320.0
    dlon = radius_m / (111320.0 * max(math.cos(math.radians(lat)), 0.01))
    bbox = "{:.7f},{:.7f},{:.7f},{:.7f}".format(lon - dlon, lat - dlat, lon + dlon, lat + dlat)
    return "{}/search?{}".format(API_URL, urlencode({"bbox": bbox, "limit": limit}))


def picture_url(pic_id):
    return "{}/pictures/{}".format(API_URL, pic_id)


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


# --------------------------------------------------------------------------
# Cache minimal : photo courante + 1 photo avant + 1 photo après
# --------------------------------------------------------------------------
INSTANCES_TTL = 7 * 24 * 3600

_items = OrderedDict()        # id photo -> fiche STAC (3 au maximum)
_waiting = {}                 # id photo -> callbacks en attente (requête déjà partie)
stats = {"api_calls": 0, "cache_hits": 0}


def clear_cache():
    _items.clear()
    _waiting.clear()


def remember_items(features):
    """Met en cache des fiches STAC (au plus 3 conservées ensuite)."""
    for feat in features or []:
        pid = feat.get("id") if isinstance(feat, dict) else None
        if pid and feat.get("geometry"):
            _items[pid] = feat


def cached_item(pic_id):
    return _items.get(pic_id)


def id_from_href(href):
    """Dernier segment d'une URL d'item STAC (= identifiant de la photo)."""
    return urlsplit(href).path.rstrip("/").rsplit("/", 1)[-1] if href else None


def _neighbour_ids(item):
    return [pid for pid in (id_from_href(item_link(item, "prev")),
                            id_from_href(item_link(item, "next"))) if pid]


def _set_current(item):
    """Ne garde que la photo courante et ses voisines, puis précharge les voisines manquantes."""
    keep = {item["id"], *_neighbour_ids(item)}
    for pid in list(_items):
        if pid not in keep:
            del _items[pid]
    _items[item["id"]] = item
    for pid in _neighbour_ids(item):
        if pid not in _items and pid not in _waiting:
            _fetch_item(pid, None)


def _fetch_item(pic_id, callback):
    _waiting[pic_id] = [callback] if callback else []

    def done(data, error):
        callbacks = _waiting.pop(pic_id, [])
        if data:
            _items[pic_id] = data
        for cb in callbacks:
            cb(data, error)

    stats["api_calls"] += 1
    fetch_json(picture_url(pic_id), done)


def get_item(pic_id, callback):
    """Fiche d'une photo : cache d'abord, API seulement si nécessaire.

    callback(item | None, erreur | None). La photo devient la photo courante :
    seules elle et ses deux voisines (avant/après) restent en cache.
    """
    def deliver(item, error):
        if item is not None and item.get("id") == pic_id:
            _set_current(item)
        callback(item, error)

    item = _items.get(pic_id)
    if item is not None:
        stats["cache_hits"] += 1
        deliver(item, None)
    elif pic_id in _waiting:  # déjà demandée (préchargement en cours) : même réponse
        _waiting[pic_id].append(deliver)
    else:
        _fetch_item(pic_id, deliver)


def get_instances(callback, force=False):
    """Liste des instances, mise en cache 7 jours dans les paramètres QGIS."""
    settings = QgsSettings()
    if not force:
        try:
            stamp = float(settings.value("visionneuse_panoramax/instances_time", 0) or 0)
            cached = json.loads(settings.value("visionneuse_panoramax/instances_json", "") or "null")
        except (TypeError, ValueError):
            stamp, cached = 0, None
        if cached and time.time() - stamp < INSTANCES_TTL:
            callback(cached, None)
            return

    def done(data, error):
        if data:
            data = {"instances": [{"name": n, "url": u} for n, u in parse_instances(data)]}
            settings.setValue("visionneuse_panoramax/instances_json", json.dumps(data))
            settings.setValue("visionneuse_panoramax/instances_time", time.time())
        callback(data, error)

    stats["api_calls"] += 1
    fetch_json(INSTANCES_URL, done)


def nearest_cached(lon, lat, max_m):
    """Photo en cache la plus proche à moins de max_m mètres (None sinon)."""
    feat = nearest_feature(_items.values(), lon, lat)
    if feat is None:
        return None
    flon, flat = feat["geometry"]["coordinates"][:2]
    dx = (flon - lon) * 111320.0 * math.cos(math.radians(lat))
    dy = (flat - lat) * 111320.0
    return feat if math.hypot(dx, dy) <= max_m else None


def sequence_axis(item):
    """Direction de déplacement le long de la séquence (cap en degrés), ou None.

    Calculée entre les photos voisines (précédente et suivante, déjà en cache)
    pour suivre l'axe de la voie, plutôt que l'orientation de la caméra.
    """
    def position(feat):
        try:
            return [float(v) for v in feat["geometry"]["coordinates"][:2]]
        except (KeyError, TypeError, ValueError):
            return None

    here = position(item)
    prev_item = cached_item(id_from_href(item_link(item, "prev")))
    next_item = cached_item(id_from_href(item_link(item, "next")))
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
