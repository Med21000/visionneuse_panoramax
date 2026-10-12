# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Cédric COCHART
# SPDX-License-Identifier: GPL-2.0-or-later
"""Inclinaison d'une photo 360° calculée d'après ses verticales, sans clic.

Une caméra montée sur un véhicule penche avec lui (pente de la route, dévers, virages) : 1 à
3° sur les photos imajbox du CD21, autant d'erreur de visée. Sur une photo équirectangulaire
de niveau, les arêtes verticales du monde (angles de façades, poteaux, encadrements) restent
verticales dans l'image ; la caméra penchée, elles s'inclinent selon une loi connue.

1. Bandes de BAND_DEG d'élévation, au-dessus de l'horizon (le sol, les véhicules et la
   voiture de prise de vue sont en dessous) : pour chaque pente possible, somme du gradient
   horizontal le long de droites de cette pente (transformée de Radon locale). Une arête
   rectiligne y fait un pic net : sa position et sa pente donnent un segment.
2. Chaque segment impose, comme un objet vertical cliqué (calibration.tilt_from_verticals),
   que la vraie verticale soit dans le plan qui contient la caméra et le segment. Moindres
   carrés sur des centaines de segments, rejet itératif des segments qui ne sont pas vraiment
   verticaux (arbres, toits, objets penchés).

Validé à Pouilly-en-Auxois (photos imajbox) : une inclinaison imposée de 1° est retrouvée à
0,05° près ; il faut des bâtiments ou des poteaux tout autour (en campagne : pas assez de
verticales, voir terrain_tilt pour l'estimation par le LiDAR HD).

Calculs numpy seuls (pas d'OpenCV dans QGIS) ; la photo est décodée réduite et rognée à la
bande utile (voir read_band), en arrière-plan.
"""

import importlib.util
import math

from . import api, calibration, precision

HAS_NUMPY = importlib.util.find_spec("numpy") is not None  # calcul des verticales

ELEV_MIN, ELEV_MAX = 3.0, 45.0  # bande analysée (degrés au-dessus de l'horizon)
BAND_DEG = 4.0  # hauteur d'une bande d'analyse (degrés)
MAX_SLOPE = 0.06  # pente maximale retenue (pixels par pixel, ~3,4°)
SLOPE_RANGE = 0.1  # pentes calculées, de part et d'autre (marge pour le test de netteté)
SLOPES = 101
WIDTH = 6144  # largeur de travail (photo HD imajbox : 12 288, décodée au demi)
MIN_SEGMENTS = 40
MIN_QUADRANTS = 3  # quarts d'horizon avec au moins QUADRANT_SEGMENTS segments
QUADRANT_SEGMENTS = 5
MAX_SIGMA = 0.25  # incertitude maximale acceptée (degrés)
FLOOR = math.radians(0.05)  # dispersion minimale des segments retenus (radians)


def read_band(data, width=WIDTH):
    """Photo (octets JPEG) décodée en niveaux de gris, réduite à `width` de large et rognée à
    la bande ELEV_MIN..ELEV_MAX. Retourne (tableau numpy, première ligne, hauteur et largeur
    de l'image entière) ou None. Utilisable hors du fil principal (pas de widget)."""
    import numpy as np
    from qgis.PyQt.QtCore import QBuffer, QByteArray, QIODevice, QRect, QSize
    from qgis.PyQt.QtGui import QImage, QImageReader

    buf = QBuffer()
    buf.setData(QByteArray(data))
    buf.open(QIODevice.OpenModeFlag.ReadOnly)
    reader = QImageReader(buf)
    size = reader.size()
    if not size.isValid() or size.width() < 2 * size.height() - 2:
        return None  # pas une photo équirectangulaire
    w = min(width, size.width())
    h = round(w * size.height() / size.width())
    row0, row1 = int((90 - ELEV_MAX) / 180 * h), int((90 - ELEV_MIN) / 180 * h)
    reader.setScaledSize(QSize(w, h))
    reader.setScaledClipRect(QRect(0, row0, w, row1 - row0))
    img = reader.read()
    if img.isNull():
        return None
    img = img.convertToFormat(QImage.Format.Format_Grayscale8)
    bpl = img.bytesPerLine()
    ptr = img.constBits()
    ptr.setsize(img.height() * bpl)
    band = np.frombuffer(ptr, np.uint8).reshape(img.height(), bpl)[:, :img.width()].astype(np.float32)
    return band, row0, h, w


def segments(band, row0, height, width, azimuth):
    """Segments quasi verticaux de la bande : liste de ((cap, élév.) bas, (cap, élév.) haut),
    directions brutes (cap de la photo `azimuth` ajouté, comme les clics)."""
    import numpy as np

    deg = 180.0 / height
    rows = max(8, int(round(BAND_DEG / deg)))
    gx = np.zeros_like(band)
    gx[:, 1:-1] = band[:, 2:] - band[:, :-2]
    slopes = np.linspace(-SLOPE_RANGE, SLOPE_RANGE, SLOPES)
    offs = np.arange(rows) - (rows - 1) / 2.0
    out = []

    def direction(px, py):
        return ((px + 0.5) / width * 360.0 - 180.0 + azimuth) % 360, 90.0 - (py + 0.5) * deg

    for top in range(0, band.shape[0] - rows + 1, rows // 2):
        win = gx[top:top + rows]
        acc = np.empty((SLOPES, width), np.float32)
        for k, b in enumerate(slopes):
            shift = b * offs
            base = np.floor(shift).astype(int)
            frac = (shift - base).astype(np.float32)
            s = np.zeros(width, np.float32)
            for r in range(rows):  # somme le long de x + b·(r − milieu), interpolée
                row = win[r]
                s += (1 - frac[r]) * np.roll(row, -base[r]) + frac[r] * np.roll(row, -base[r] - 1)
            acc[k] = s
        acc = np.abs(acc)
        best = acc.argmax(axis=0)
        peak = acc[best, np.arange(width)]
        thr = max(5.0 * rows, 4.0 * float(np.median(peak)))
        local = peak >= np.maximum.reduce([np.roll(peak, d) for d in range(-4, 5)])
        yc = row0 + top + (rows - 1) / 2.0
        half = rows / 2.0
        for x in np.nonzero(local & (peak > thr))[0]:
            if abs(slopes[best[x]]) > MAX_SLOPE:
                continue  # pente hors de la plage cherchée
            line = _refine(win, x, slopes[best[x]], offs)
            if line is None:
                continue
            xc, b = line
            out.append((direction(xc + b * half, yc + half), direction(xc - b * half, yc - half)))
    return out


def _refine(win, x, slope, offs, reach=5):
    """Position et pente précises d'une arête détectée en x avec la pente approchée `slope` :
    centre du gradient sur chaque ligne (autour de la droite approchée), puis droite des
    moindres carrés. (x au milieu de la bande, pente) ou None si l'arête n'est pas rectiligne
    ou pas continue sur toute la bande."""
    import numpy as np

    width = win.shape[1]
    a, b = float(x), float(slope)
    for _ in range(3):  # recentrée sur la droite trouvée : moins d'effet de la grille des pixels
        pred = a + b * offs
        cols = np.round(pred).astype(int)[:, None] + np.arange(-reach, reach + 1)[None, :]
        g = np.take_along_axis(win, cols % width, axis=1)
        sign = 1.0 if g.sum() >= 0 else -1.0
        wts = np.clip(sign * g, 0, None)
        total = wts.sum(axis=1)
        strong = total > 0.3 * np.median(total) if total.any() else total > 0
        if strong.mean() < 0.8:
            return None  # arête interrompue
        centre = (wts * cols).sum(axis=1)[strong] / total[strong]
        o = offs[strong]
        b, a = np.polyfit(o, centre, 1)
    if np.std(centre - (a + b * o)) > 0.4:
        return None  # pas rectiligne
    return a, b


def fit(pairs):
    """Verticale vraie d'après des segments (voir calibration.tilt_from_verticals), avec rejet
    itératif des segments qui s'en écartent. Retourne le dict de tilt_from_verticals
    complété de sigma (incertitude, degrés), segments (retenus), auto=True, ou None si les
    segments sont trop peu nombreux ou mal répartis."""
    normals, yaws = [], []
    for bottom, top in pairs:
        n = calibration._cross(calibration.vector(*bottom), calibration.vector(*top))
        norm = math.sqrt(calibration._dot(n, n))
        if norm > 0:
            normals.append((n[0] / norm, n[1] / norm, n[2] / norm))
            yaws.append(bottom[0])
    keep = list(range(len(normals)))
    up = (0.0, 0.0, 1.0)
    for _ in range(8):
        if len(keep) < 3:
            return None
        a11 = a12 = a22 = r1 = r2 = 0.0
        for i in keep:
            nx, ny, nz = normals[i]
            a11 += nx * nx
            a12 += nx * ny
            a22 += ny * ny
            r1 -= nx * nz
            r2 -= ny * nz
        det = a11 * a22 - a12 * a12
        if det <= 0:
            return None
        a, b = (a22 * r1 - a12 * r2) / det, (a11 * r2 - a12 * r1) / det
        up = calibration._unit((a, b, 1.0))
        res = [calibration._dot(normals[i], up) for i in range(len(normals))]
        kept = sorted(abs(res[i]) for i in keep)
        sigma = max(1.4826 * kept[len(kept) // 2], FLOOR)
        new = [i for i in range(len(normals)) if abs(res[i]) < 3 * sigma]
        if new == keep:
            break
        keep = new
    if len(keep) < MIN_SEGMENTS:
        return None
    # Répartition autour de la photo : sinon une composante de l'inclinaison est mal connue
    quadrants = [0] * 4
    for i in keep:
        quadrants[int(yaws[i] % 360 // 90)] += 1
    if sum(q >= QUADRANT_SEGMENTS for q in quadrants) < MIN_QUADRANTS:
        return None
    # Incertitude : écart-type des segments sur l'information apportée (plus grand axe)
    lam = (a11 + a22) / 2 - math.sqrt(((a11 - a22) / 2) ** 2 + a12 * a12)
    sigma_deg = math.degrees(sigma / math.sqrt(max(lam, 1e-12)))
    if sigma_deg > MAX_SIGMA:
        return None
    tilt = math.degrees(math.acos(min(1.0, up[2])))
    if tilt > calibration.MAX_TILT:
        return None
    return {"up": up, "tilt": tilt, "toward": math.degrees(math.atan2(up[0], up[1])) % 360,
            "residual": math.degrees(sigma), "partial": False, "count": len(keep), "sigma": sigma_deg,
            "auto": True}


def estimate(data, azimuth):
    """Inclinaison de la photo (octets JPEG, cap du centre de l'image) : dict de fit, ou None."""
    band = read_band(data)
    if band is None:
        return None
    return fit(segments(*band, azimuth))


def lidar_url(lon, lat, heading):
    """URL des altitudes LiDAR HD sous le véhicule (voir calibration.lidar_points)."""
    return precision.lidar_url(calibration.lidar_points(lon, lat, heading))


def is_360(item):
    """Photo équirectangulaire (360°) ?"""
    props = item.get("properties", {}) or {}
    try:
        if float((props.get("pers:interior_orientation") or {}).get("field_of_view") or 0) >= 360:
            return True
    except (TypeError, ValueError):
        pass
    return (props.get("exif") or {}).get("Xmp.GPano.ProjectionType") == "equirectangular"


def _run_task(function, args, done):
    """function(*args) en arrière-plan (tâche QGIS), puis done(résultat ou None) dans le fil
    principal."""
    from qgis.core import QgsApplication, QgsTask

    def work(task, *a):
        return function(*a)

    def finished(exception, result=None):
        _tasks.discard(task)
        done(None if exception else result)

    task = QgsTask.fromFunction("Visionneuse Panoramax : inclinaison de la photo", work, *args,
                                on_finished=finished, flags=QgsTask.Flag.Silent)
    _tasks.add(task)  # gardée tant qu'elle tourne
    QgsApplication.taskManager().addTask(task)


_tasks = set()


class TiltChecker:
    """Inclinaison automatique des photos : verticales de la photo HD (en arrière-plan) et
    pente et dévers du LiDAR HD sous le véhicule. Le résultat est rangé dans le calage
    (Calibration.set_auto), qui le conserve d'une session à l'autre ; une photo déjà
    analysée ne l'est pas à nouveau."""

    def __init__(self):
        self._pending = {}  # photo -> rappels en attente
        self.run = _run_task  # remplaçable dans les tests

    def pending(self, pic):
        return pic in self._pending

    def check(self, item, cal, done=None):
        """Lance l'analyse de la photo (fiche STAC) si elle n'a ni inclinaison calée à la main
        ni inclinaison automatique ; done() quand elle est rangée dans `cal` (ou abandonnée)."""
        pic = item.get("id")
        callbacks = [done] if done else []
        if not pic or pic in cal.verticals or pic in cal.auto:
            for cb in callbacks:
                cb()
            return
        if pic in self._pending:
            self._pending[pic] += callbacks
            return
        heading = api.precise_heading(item)
        heading = api.item_heading(item) if heading is None else heading
        try:
            lon, lat = (float(v) for v in item["geometry"]["coordinates"][:2])
        except (KeyError, TypeError, ValueError):
            lon = lat = None
        if heading is None or lon is None or not is_360(item):
            for cb in callbacks:
                cb()
            return
        self._pending[pic] = callbacks
        state = {}

        def finish():
            if "lidar" not in state or "verticals" not in state:
                return  # l'autre moitié n'est pas encore arrivée
            if state["lidar_error"] and state["verticals"] is None:
                entry = None  # service injoignable : à refaire plus tard
            else:
                entry = {"sequence": item.get("collection"), "heading": float(heading),
                         "verticals": state["verticals"], "lidar": state["lidar"]}
            for cb in [None] + self._pending.pop(pic, []):
                if cb is None:
                    if entry is not None:
                        cal.set_auto(pic, entry)
                else:
                    cb()

        def got_lidar(data, error):
            z = [] if error else [g for g in _elevations(data)]
            ok = len(z) == len(calibration.LIDAR_GRID) and None not in z
            state["lidar"] = list(calibration.lidar_slopes(z)) if ok else None
            state["lidar_error"] = bool(error)
            finish()

        def got_image(data, error):
            if error or not data:
                analysed(None)
            else:
                self.run(estimate, (bytes(data), float(heading)), analysed)

        def analysed(res):
            state["verticals"] = ({"up": list(res["up"]), "count": res["count"], "sigma": res["sigma"]}
                                  if res else None)
            finish()

        api.fetch_json(lidar_url(lon, lat, heading), got_lidar)
        href = ((item.get("assets") or {}).get("hd") or {}).get("href")
        if href and HAS_NUMPY:
            api.fetch(href, got_image, prefer_cache=True)
        else:
            analysed(None)


def _elevations(data):
    return precision._ground(data)
