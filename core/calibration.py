# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Cédric COCHART
# SPDX-License-Identifier: GPL-2.0-or-later
"""Calage des photos : correction d'inclinaison et recalage du cap.

- Inclinaison (par photo) : une caméra 360 est rarement parfaitement de niveau.
  On clique le pied puis le sommet d'objets verticaux (poteau, angle de façade) :
  chacun impose que la vraie verticale soit dans le plan qui contient la caméra
  et l'objet. Un objet corrige la pente vue de côté dans sa direction ; deux
  objets à peu près à angle droit corrigent l'inclinaison complète. Sans clic,
  l'inclinaison est calculée d'après les verticales de la photo (autotilt.py), ou à
  défaut estimée par la pente et le dévers du LiDAR HD sous le véhicule, quand la
  séquence a montré que la caméra penche avec lui (voir lidar_model).
- Hauteur de caméra (par séquence) : on mesure une longueur connue, au sol
  (trait de marquage, place de stationnement) ou en hauteur (objet de hauteur
  connue). Les longueurs mesurées étant à peu près proportionnelles à la
  hauteur de la caméra, on cherche celle qui redonne la longueur connue.
- Cap et position (par séquence, autour des photos utilisées) : on clique dans
  la photo un repère visible sur la carte, puis ce repère sur la carte. Les
  repères donnent, par relèvement, le décalage de cap et de position GPS des
  photos, chacun gardant son a priori (cap et position d'origine à leur
  précision) : un repère seul corrige surtout le cap, sans jamais le dégrader ;
  trois repères ou plus bien répartis autour de la photo recalent la position.

Directions : (cap absolu en degrés, 0 = nord, sens horaire ; élévation en
degrés). Les corrections s'appliquent dans cet ordre : inclinaison, puis cap.
Calage conservé dans le profil QGIS de l'utilisateur, d'une session et d'un projet à
l'autre, jusqu'à ce qu'il soit refait ou effacé (voir plugin.py). Module sans dépendance à QGIS.
"""

import math

from . import ground
from .triangulation import _local, offset

OBJECT_ERROR = 0.2  # écart d'un objet vertical cliqué à la verticale vraie (degrés : clics, objet)
UNCALIBRATED = 2.0  # inclinaison d'une photo non calée (degrés) : 1 à 3° mesurés sur l'imajbox
MAX_TILT = 10.0  # au-delà, les clics sont sûrement faux (objet penché, pied/sommet inversés)
MIN_SPAN = 3.0  # écart d'élévation minimal entre pied et sommet (degrés)
MAX_LEAN = 30.0  # écart de cap maximal entre pied et sommet d'un même objet vertical
MAX_OFFSET = 20.0  # au-delà, le repère cliqué sur la carte n'est sûrement pas le bon
MIN_LANDMARK = 5.0  # distance minimale du repère (m)
AIM_ERROR = 0.1  # précision d'un clic dans la photo zoomée (degrés)
MAP_ERROR = 0.5  # précision de position du repère sur la carte (m)
DEFAULT_ACCURACY = 3.0  # précision GPS supposée quand la photo ne l'indique pas (m)
SURVEY_PRIOR = 0.5  # précision du cap d'origine, matériel de relevé (degrés)
PRIOR = 3.0  # précision du cap d'origine, autres appareils (boussole, trajectoire GPS)
REACH = 300.0  # portée du recalage (m) autour des photos qui l'ont servi : le GPS dérive au-delà
OUTLIER = 4.0  # un repère qui s'écarte de plus de OUTLIER écarts-types des autres est refusé
# Inclinaison estimée par le LiDAR HD : plan du sol ajusté sous le véhicule, sur une grille de
# LIDAR_ALONG m devant et derrière la caméra et LIDAR_ACROSS m à droite et à gauche (sous les
# roues) ; la grille atténue l'arrondi au centimètre des altitudes du service. Validé à
# Pouilly-en-Auxois (imajbox, 18 photos) : 0,4° d'écart en long et 0,5° en travers avec les
# verticales.
LIDAR_ALONG, LIDAR_ACROSS = 1.5, 0.8
LIDAR_GRID = [(a * LIDAR_ALONG, c * LIDAR_ACROSS) for a in (-1, -0.5, 0, 0.5, 1) for c in (-1, 0, 1)]
LIDAR_MIN_PHOTOS = 3  # photos de la séquence calées par leurs verticales pour valider le modèle
LIDAR_MAX_SIGMA = 0.8  # au-delà, la caméra ne suit pas le véhicule (portée à pied, stabilisée)
LIDAR_MIN_SIGMA = 0.3


CAMERA_MIN, CAMERA_MAX = 0.3, 6.0  # plage de hauteurs de caméra admises (m)


def _fr(value, digits=1):
    return "{:.{}f}".format(value, digits).replace(".", ",")


class CalibrationError(ValueError):
    """Clics inexploitables (message destiné à l'utilisateur)."""


def vector(yaw, elev):
    """Vecteur unitaire (est, nord, haut) d'une direction."""
    y, e = math.radians(yaw), math.radians(elev)
    return (math.sin(y) * math.cos(e), math.cos(y) * math.cos(e), math.sin(e))


def angles(v):
    """Direction (cap, élévation) d'un vecteur."""
    x, y, z = v
    return math.degrees(math.atan2(x, y)) % 360, math.degrees(math.atan2(z, math.hypot(x, y)))


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _unit(v):
    n = math.sqrt(_dot(v, v))
    return (v[0] / n, v[1] / n, v[2] / n)


def tilt_from_verticals(verticals, prior=None):
    """Verticale vraie dans le repère de la photo, d'après des objets verticaux.

    verticals : liste de ((cap, élév.) du pied, (cap, élév.) du sommet).
    prior : (up, sigma en degrés), inclinaison déjà connue (calcul automatique) qui complète
    les objets cliqués : chaque objet ne corrige que la composante qu'il voit, l'autre reste
    celle de l'a priori.
    Retourne un dict : up (vecteur unitaire), tilt (degrés), toward (cap vers
    lequel le haut de la caméra penche), residual (écart moyen des objets à la
    verticale retenue, degrés, None avec un seul objet), partial (vrai si un
    seul objet ou des objets alignés, sans a priori : seule une composante est corrigée),
    sigma (incertitude, degrés, dans la direction la moins contrainte).
    """
    normals = []
    for bottom, top in verticals:
        if top[1] - bottom[1] < MIN_SPAN:
            raise CalibrationError("Cliquez d'abord le pied, puis le sommet de l'objet vertical, "
                                   "assez haut (au moins {:.0f}° plus haut).".format(MIN_SPAN))
        if abs((top[0] - bottom[0] + 540) % 360 - 180) > MAX_LEAN:
            raise CalibrationError("Le pied et le sommet ne semblent pas appartenir au même objet vertical.")
        normals.append(_unit(_cross(vector(*bottom), vector(*top))))
    # Verticale vraie (a, b, 1) : n·(a, b, 1) = 0 pour chaque objet, moindres carrés.
    # Un léger amortissement donne la plus petite correction compatible quand les objets
    # ne suffisent pas (un seul objet, ou objets dans la même direction).
    # Chaque objet pèse 1 / OBJECT_ERROR² ; l'a priori, 1 / sigma² sur chaque composante.
    w = 1.0 / math.radians(OBJECT_ERROR) ** 2
    a11 = a12 = a22 = r1 = r2 = 0.0
    for nx, ny, nz in normals:
        a11 += w * nx * nx
        a12 += w * nx * ny
        a22 += w * ny * ny
        r1 -= w * nx * nz
        r2 -= w * ny * nz
    seen = (a11, a12, a22)  # information des seuls objets
    if prior is not None:
        pup, psigma = prior
        wp = 1.0 / math.radians(max(psigma, 0.01)) ** 2
        a11, a22 = a11 + wp, a22 + wp
        r1, r2 = r1 + wp * pup[0] / pup[2], r2 + wp * pup[1] / pup[2]
        damp = 0.0
    else:
        damp = 1e-6 * max(a11 + a22, 1e-12)
        a11, a22 = a11 + damp, a22 + damp
    det = a11 * a22 - a12 * a12
    a, b = (a22 * r1 - a12 * r2) / det, (a11 * r2 - a12 * r1) / det
    up = _unit((a, b, 1.0))
    tilt = math.degrees(math.acos(min(1.0, up[2])))
    if tilt > MAX_TILT:
        raise CalibrationError("Inclinaison trouvée de {:.0f}° : objet pas vraiment vertical ou clics "
                               "imprécis. Recommencez sur un autre objet.".format(tilt))
    # Partiel si les plans des objets se coupent mal (objets dans la même direction)
    s11, s12, s22 = seen
    partial = prior is None and (len(normals) < 2 or s11 * s22 - s12 * s12 < 0.1 * ((s11 + s22) / 2) ** 2)
    lam = (a11 + a22) / 2 - math.sqrt(((a11 - a22) / 2) ** 2 + a12 * a12)  # moins contrainte
    residual = None
    if len(normals) > 1:
        residual = math.degrees(math.sqrt(sum(math.asin(min(1.0, abs(_dot(n, up)))) ** 2
                                              for n in normals) / len(normals)))
    sigma = UNCALIBRATED if partial else math.degrees(1.0 / math.sqrt(max(lam, 1e-12)))
    if residual is not None and not partial:
        sigma = max(sigma, residual / math.sqrt(len(normals)))
    return {"up": up, "tilt": tilt, "toward": math.degrees(math.atan2(up[0], up[1])) % 360,
            "residual": residual, "partial": partial, "count": len(normals), "sigma": sigma}


def tilt_components(up, heading):
    """(avant, droite) en degrés : de combien la verticale vraie, vue de la caméra, penche
    vers l'avant (cap `heading`) et vers la droite. Une caméra qui monte une pente de p° voit
    la verticale pencher de p° vers l'avant ; inclinée vers la gauche, vers la droite."""
    h = math.radians(heading)
    return (math.degrees(math.atan2(up[0] * math.sin(h) + up[1] * math.cos(h), up[2])),
            math.degrees(math.atan2(up[0] * math.cos(h) - up[1] * math.sin(h), up[2])))


def up_from_components(forward, right, heading):
    """Verticale vraie (repère de la photo) penchée de forward° vers l'avant et right° vers la
    droite (inverse de tilt_components)."""
    h = math.radians(heading)
    f, r = math.tan(math.radians(forward)), math.tan(math.radians(right))
    return _unit((f * math.sin(h) + r * math.cos(h), f * math.cos(h) - r * math.sin(h), 1.0))


def lidar_points(lon, lat, heading):
    """Points du sol où lire le LiDAR HD : grille LIDAR_GRID (en avant, à droite) autour de
    la caméra."""
    out = []
    for along, right in LIDAR_GRID:
        p = offset(lon, lat, heading, along) if along else (lon, lat)
        out.append(offset(p[0], p[1], heading + 90, right) if right else p)
    return out


def lidar_slopes(z):
    """(pente en long, dévers) en degrés du plan ajusté sur les altitudes des lidar_points
    (grille symétrique : les pentes sont des moyennes pondérées indépendantes). La verticale
    penche vers l'avant en montée, vers la droite quand la droite est plus haute."""
    sa = sum(a * h for (a, _), h in zip(LIDAR_GRID, z)) / sum(a * a for a, _ in LIDAR_GRID)
    sc = sum(c * h for (_, c), h in zip(LIDAR_GRID, z)) / sum(c * c for _, c in LIDAR_GRID)
    return math.degrees(math.atan(sa)), math.degrees(math.atan(sc))


def lidar_model(entries):
    """Modèle de la séquence : la caméra penche-t-elle avec le véhicule ? entries : inclinaisons
    automatiques des photos de la séquence (voir Calibration.auto). Retourne le décalage moyen
    (avant, droite) entre verticales et LiDAR (fixation de la caméra), l'écart-type sigma et le
    nombre de photos, ou None si trop peu de photos le vérifient ou si l'écart est trop grand."""
    diffs = []
    for e in entries:
        v, lidar = e.get("verticals"), e.get("lidar")
        if v and lidar:
            f, r = tilt_components(v["up"], e["heading"])
            diffs.append((f - lidar[0], r - lidar[1]))
    if len(diffs) < LIDAR_MIN_PHOTOS:
        return None
    n = len(diffs)
    mf, mr = sum(d[0] for d in diffs) / n, sum(d[1] for d in diffs) / n
    sigma = math.sqrt(sum((d[0] - mf) ** 2 + (d[1] - mr) ** 2 for d in diffs) / (2 * max(n - 1, 1)))
    if sigma > LIDAR_MAX_SIGMA:
        return None
    return {"offset": (mf, mr), "sigma": max(sigma, LIDAR_MIN_SIGMA), "count": n}


def untilt(up, yaw, elev, inverse=False):
    """Direction corrigée : rotation qui ramène la verticale `up` au zénith. inverse : la
    rotation contraire, d'une direction vraie à la direction vue par la caméra penchée."""
    v = vector(yaw, elev)
    k = (up[1], -up[0], 0.0)  # up × zénith
    s = math.hypot(k[0], k[1])
    if s < 1e-12:
        return yaw % 360, elev
    k = (k[0] / s, k[1] / s, 0.0)
    c = up[2]
    if inverse:
        s = -s
    kxv, kv = _cross(k, v), _dot(k, v)
    r = tuple(v[i] * c + kxv[i] * s + k[i] * kv * (1 - c) for i in range(3))  # Rodrigues
    return angles(r)


def _reference_length(kind, camera_height, clicks):
    """Longueur mesurée (m) avec une hauteur de caméra donnée : distance entre deux
    points au sol ("ground") ou hauteur d'un objet, pied puis sommet ("height")."""
    if kind == "height":
        return ground.measure_height(camera_height, *clicks)["value"]
    ground_z = (clicks[0].get("profile") or ground.flat_profile())[0][1]  # même caméra pour les deux clics
    (_, p, z1), (_, q, z2) = (ground._ground_point(camera_height, c, ground_z) for c in clicks)
    x, y = _local(q[0], q[1], p[0], p[1])
    return math.sqrt(x * x + y * y + (z2 - z1) ** 2)


def _solve_height(kind, clicks, known):
    """Hauteur de caméra qui redonne la longueur connue (dichotomie : la longueur
    mesurée croît avec la hauteur de caméra)."""
    def excess(h):
        try:
            return _reference_length(kind, h, clicks) - known
        except ground.GroundError:
            return float("inf")  # point trop loin : caméra trop haute

    lo, hi = CAMERA_MIN, CAMERA_MAX
    if excess(lo) > 0:
        raise CalibrationError("Longueur mesurée trop grande même avec une caméra à {} m : vérifiez la "
                               "longueur connue et les clics.".format(str(CAMERA_MIN).replace(".", ",")))
    if excess(hi) < 0:
        raise CalibrationError("Longueur mesurée trop petite même avec une caméra à {} m : vérifiez la "
                               "longueur connue et les clics.".format(str(CAMERA_MAX).replace(".", ",")))
    for _ in range(40):
        mid = (lo + hi) / 2.0
        if excess(mid) > 0:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2.0


def camera_height(kind, clicks, known, tilt_sigma=None):
    """Hauteur de caméra (m) et son incertitude, d'après une longueur connue.

    kind : "ground" (deux clics au sol) ou "height" (pied au sol, puis sommet) ;
    clicks : dicts avec elev (corrigée de l'inclinaison), yaw, lon, lat, profile.
    L'incertitude combine (somme quadratique) l'inclinaison de la photo, commune aux deux
    clics (tilt_sigma degrés, UNCALIBRATED par défaut), et la visée de chaque clic (AIM_ERROR).
    """
    if known <= 0:
        raise CalibrationError("Indiquez la longueur connue.")
    if kind == "height" and clicks[0].get("pic") != clicks[1].get("pic"):
        raise CalibrationError("Le pied et le sommet doivent être cliqués sur la même photo.")
    try:
        h = _solve_height(kind, clicks, known)
    except ground.GroundError as exc:
        raise CalibrationError(str(exc))
    tilt_sigma = UNCALIBRATED if tilt_sigma is None else tilt_sigma
    groups = []
    for north, east in ((tilt_sigma, 0.0), (0.0, tilt_sigma)):
        variants = []
        for sign in (-1, 1):
            up = up_from_components(sign * north, sign * east, 0.0)
            variants.append([dict(c, **dict(zip(("yaw", "elev"), untilt(up, c["yaw"], c["elev"])))) for c in clicks])
        groups.append(variants)
    for i in range(len(clicks)):
        variants = []
        for sign in (-1, 1):
            moved = [dict(c) for c in clicks]
            moved[i]["elev"] += sign * AIM_ERROR
            variants.append(moved)
        groups.append(variants)
    total = 0.0
    for variants in groups:
        worst = 0.0
        for moved in variants:
            try:
                worst = max(worst, abs(_solve_height(kind, moved, known) - h))
            except (CalibrationError, ground.GroundError):
                worst = max(worst, h)  # référence trop loin pour être fiable
        total += worst * worst
    return h, max(math.sqrt(total), 0.005)


class Calibration:
    """Calages : inclinaison par photo ; cap, position et hauteur de caméra par séquence.

    Seules les observations sont conservées (clics et repères) : les corrections en sont
    recalculées. to_dict / load permettent de les enregistrer (dans le profil QGIS), merge
    d'y ajouter un calage enregistré ailleurs (projet d'une version précédente) ;
    `changed`, s'il est défini, est appelé après chaque modification.
    """

    FORMAT = 1  # version du format enregistré

    def __init__(self):
        self.verticals = {}  # pic -> [((cap, élév.) pied, (cap, élév.) sommet)]
        self.landmarks = {}  # séquence -> [dicts : pic, lon, lat, yaw, elev, mlon, mlat, accuracy, prior]
        # séquence -> calages de la hauteur de caméra : dicts kind, known, clicks (clics bruts,
        # recalculés avec le calage actuel, voir camera), ou (hauteur, incertitude) enregistrés
        # par une version précédente (valeur figée)
        self.heights = {}
        self._cameras = {}  # séquence -> hauteur recalculée (cache, vidé à chaque modification)
        # pic -> inclinaison automatique : sequence, heading (cap du centre de l'image),
        # verticals (up, count, sigma : d'après les verticales de la photo, ou None), lidar
        # ((pente, dévers) en degrés sous le véhicule, ou None)
        self.auto = {}
        self._tilts = {}
        self._models = {}  # séquence -> lidar_model (cache)
        self.changed = None

    def _notify(self):
        self._cameras = {}  # l'inclinaison ou le cap ont pu changer : hauteurs à recalculer
        if self.changed is not None:
            self.changed()

    # Enregistrement --------------------------------------------------------
    def to_dict(self):
        return {
            "format": self.FORMAT,
            "verticals": {pic: [[list(b), list(t)] for b, t in v] for pic, v in self.verticals.items()},
            "landmarks": self.landmarks,
            "heights": {seq: [o if isinstance(o, dict) else list(o) for o in obs] for seq, obs in self.heights.items()},
            "auto": self.auto,
        }

    def load(self, data):
        """Remplace le calage par celui enregistré (dict de to_dict, ou None : calage vide).
        Les entrées illisibles sont ignorées. Ne déclenche pas `changed`."""
        self.verticals, self.landmarks, self.heights, self._tilts = {}, {}, {}, {}
        self.auto, self._models, self._cameras = {}, {}, {}
        if not isinstance(data, dict) or data.get("format") != self.FORMAT:
            return
        keys = ("pic", "lon", "lat", "yaw", "elev", "mlon", "mlat", "accuracy", "prior")
        try:
            for pic, verticals in (data.get("verticals") or {}).items():
                verticals = [((float(b[0]), float(b[1])), (float(t[0]), float(t[1]))) for b, t in verticals]
                try:
                    self._tilts[pic] = tilt_from_verticals(verticals)
                    self.verticals[pic] = verticals
                except CalibrationError:
                    pass
            for seq, obs in (data.get("landmarks") or {}).items():
                obs = [{k: (o[k] if k == "pic" else float(o[k])) for k in keys} for o in obs]
                if obs:
                    self.landmarks[seq] = obs
            for seq, obs in (data.get("heights") or {}).items():
                obs = [o for o in (_height_obs(o) for o in obs) if o is not None]
                if obs:
                    self.heights[seq] = obs
            for pic, entry in (data.get("auto") or {}).items():
                entry = _auto_entry(entry)
                if entry:
                    self.auto[pic] = entry
        except (TypeError, ValueError, KeyError, IndexError, AttributeError):
            pass  # format abîmé : on garde ce qui a pu être lu

    def merge(self, data):
        """Ajoute le calage enregistré `data` (dict de to_dict) pour les photos et séquences
        qui n'en ont pas encore : le calage en cours, plus récent, l'emporte. Ne déclenche pas
        `changed`. Retourne le nombre de photos et séquences ajoutées."""
        other = Calibration()
        other.load(data)
        added = 0
        for mine, theirs in ((self.verticals, other.verticals), (self.landmarks, other.landmarks),
                             (self.heights, other.heights), (self.auto, other.auto)):
            for key, obs in theirs.items():
                if key not in mine:
                    mine[key] = obs
                    added += 1
        for pic, tilt in other._tilts.items():
            self._tilts.setdefault(pic, tilt)
        self._models, self._cameras = {}, {}
        return added

    # Inclinaison ---------------------------------------------------------
    def add_vertical(self, pic, bottom, top):
        verticals = self.verticals.get(pic, []) + [(bottom, top)]
        result = tilt_from_verticals(verticals)  # lève CalibrationError sans rien garder
        self.verticals[pic], self._tilts[pic] = verticals, result
        self._notify()
        return result

    def tilt(self, pic):
        """Inclinaison de la photo (dict de tilt_from_verticals, avec sigma en degrés), ou None.
        Dans l'ordre : calée à la main sur deux objets ou plus (source "clics") ; calée à la
        main sur un objet et complétée par l'inclinaison automatique ("clics + verticales",
        "clics + lidar") ; calculée d'après les verticales de la photo ("verticales") ;
        estimée par le LiDAR HD ("lidar")."""
        manual, auto = self._tilts.get(pic), self.auto_tilt(pic)
        if manual and (not manual["partial"] or auto is None):
            return dict(manual, source="clics")
        if manual:
            combined = tilt_from_verticals(self.verticals[pic], (auto["up"], auto["sigma"]))
            return dict(combined, source="clics + " + auto["source"])
        return auto

    def manual_complete(self, pic):
        """Inclinaison calée à la main sur assez d'objets pour se passer du calcul automatique."""
        manual = self._tilts.get(pic)
        return bool(manual) and not manual["partial"]

    def auto_tilt(self, pic):
        """Inclinaison automatique de la photo (verticales, sinon LiDAR HD), ou None."""
        entry = self.auto.get(pic)
        if not entry:
            return None
        v = entry.get("verticals")
        if v:
            up = tuple(v["up"])
            return {"up": up, "tilt": math.degrees(math.acos(min(1.0, up[2]))),
                    "toward": math.degrees(math.atan2(up[0], up[1])) % 360, "residual": None, "partial": False,
                    "count": v["count"], "sigma": v["sigma"], "source": "verticales"}
        model = self.lidar_model(entry.get("sequence"))
        if model and entry.get("lidar"):
            f = entry["lidar"][0] + model["offset"][0]
            r = entry["lidar"][1] + model["offset"][1]
            up = up_from_components(f, r, entry["heading"])
            return {"up": up, "tilt": math.hypot(f, r), "toward": math.degrees(math.atan2(up[0], up[1])) % 360,
                    "residual": None, "partial": False, "count": model["count"], "sigma": model["sigma"],
                    "source": "lidar"}
        return None

    def set_auto(self, pic, entry):
        """Inclinaison automatique de la photo (voir self.auto), enregistrée."""
        self.auto[pic] = entry
        self._models.pop(entry.get("sequence"), None)
        self._notify()

    def lidar_model(self, sequence):
        """Modèle LiDAR de la séquence (voir lidar_model), ou None."""
        if sequence is None:
            return None
        if sequence not in self._models:
            self._models[sequence] = lidar_model([e for e in self.auto.values() if e.get("sequence") == sequence])
        return self._models[sequence]

    def clear_tilt(self, pic):
        self.verticals.pop(pic, None)
        self._tilts.pop(pic, None)
        self._notify()

    def untilted(self, pic, yaw, elev):
        t = self.tilt(pic)
        return untilt(t["up"], yaw, elev) if t else (yaw % 360, elev)

    # Hauteur de caméra --------------------------------------------------------
    def add_camera_height(self, sequence, kind, clicks, known):
        """Calage de la hauteur de caméra sur une longueur connue (clics corrigés du calage
        actuel). Les clics bruts sont conservés : la hauteur est recalculée si l'inclinaison ou
        le cap de la photo changent ensuite (voir camera)."""
        h, sigma = camera_height(kind, clicks, known, self._tilt_sigma(clicks[0].get("pic")))
        keep = ("pic", "sequence", "lon", "lat", "gps", "raw", "yaw", "elev", "profile")
        obs = {"kind": kind, "known": float(known),
               "clicks": [{k: c[k] for k in keep if c.get(k) is not None} for c in clicks]}
        self.heights.setdefault(sequence, []).append(obs)
        self._notify()
        return {"height": h, "sigma": sigma}

    def _height_of(self, sequence, obs):
        """(hauteur, incertitude) d'un calage de la hauteur de caméra, avec le calage actuel."""
        if not isinstance(obs, dict):
            return tuple(obs)
        clicks = []
        for c in obs["clicks"]:
            if c.get("raw"):  # direction brute : corrigée du calage actuel
                lon, lat = c.get("gps") or (c["lon"], c["lat"])
                yaw, elev = self.correct(c.get("pic"), sequence, c["raw"][0], c["raw"][1], lon, lat)
            else:  # direction déjà corrigée, figée
                yaw, elev = c["yaw"], c["elev"]
            clicks.append(dict(c, yaw=yaw, elev=elev))
        try:
            return camera_height(obs["kind"], clicks, obs["known"], self._tilt_sigma(clicks[0].get("pic")))
        except (CalibrationError, ground.GroundError):
            return None

    def _tilt_sigma(self, pic):
        """Incertitude (degrés) de l'inclinaison de la photo, UNCALIBRATED sans calage."""
        t = self.tilt(pic) if pic else None
        return t["sigma"] if t and t.get("sigma") is not None else UNCALIBRATED

    def camera(self, sequence):
        """Hauteur de caméra calée de la séquence : dict height, sigma, count, legacy (calages
        d'une version précédente, figés), ou None."""
        obs_list = self.heights.get(sequence) or []
        key = (id(obs_list), len(obs_list))  # calages ajoutés ou remplacés directement
        if self._cameras.get(sequence, (None,))[0] == key:
            return self._cameras[sequence][1]
        values, legacy = [], 0
        for o in obs_list:
            v = self._height_of(sequence, o)
            if v is not None:
                values.append(v)
                legacy += not isinstance(o, dict)
        res = None
        if values:
            sw = sum(1.0 / s ** 2 for _, s in values)
            res = {"height": sum(h / s ** 2 for h, s in values) / sw, "sigma": 1.0 / math.sqrt(sw),
                   "count": len(values), "legacy": legacy}
        self._cameras[sequence] = (key, res)
        return res

    def clear_camera(self, sequence):
        self.heights.pop(sequence, None)
        self._notify()

    # Cap -------------------------------------------------------------------
    def add_landmark(self, sequence, pic, lon, lat, yaw, elev, mlon, mlat, accuracy, survey):
        """Repère : clic (cap, élév. bruts) depuis la photo en (lon, lat), repère en (mlon, mlat)."""
        obs = {"pic": pic, "lon": lon, "lat": lat, "yaw": yaw, "elev": elev, "mlon": mlon, "mlat": mlat,
               "accuracy": DEFAULT_ACCURACY if accuracy is None else float(accuracy),
               "prior": SURVEY_PRIOR if survey else PRIOR}
        offset, sigma, dist = self._landmark_offset(obs)
        if dist < MIN_LANDMARK:
            raise CalibrationError("Repère à {} m de la photo : choisissez un repère plus éloigné.".format(_fr(dist)))
        # L'écart toléré tient compte du décalage de position GPS possible (repère proche)
        if abs(offset) > MAX_OFFSET + math.degrees(math.atan2(2 * obs["accuracy"], dist)):
            raise CalibrationError("Écart de cap de {:.0f}° : ce n'est sûrement pas le même repère sur la "
                                   "carte et dans la photo.".format(offset))
        others = self.landmarks.get(sequence, [])
        if len(others) >= 3:
            # Contrôle : direction du repère prédite par les autres repères seuls
            pose = self._solve_pose(others)
            yaw, _ = self.untilted(obs["pic"], obs["yaw"], obs["elev"])
            x, y = _local(obs["mlon"], obs["mlat"], obs["lon"], obs["lat"])
            x, y = x - pose["shift"][0], y - pose["shift"][1]
            r = abs((math.degrees(math.atan2(x, y)) - yaw - pose["offset"] + 540) % 360 - 180)
            spread = math.hypot(AIM_ERROR, math.degrees(math.atan2(MAP_ERROR, dist)), pose["sigma"],
                                math.degrees(math.atan2(pose["shift_sigma"], dist)))
            if r > max(1.0, OUTLIER * spread):
                raise CalibrationError("Ce repère s'écarte de {}° de ce que prévoient les précédents : repère "
                                       "mal pointé sur la carte ou dans la photo ?".format(_fr(r)))
        self.landmarks.setdefault(sequence, []).append(obs)
        self._notify()
        return {"offset": offset, "sigma": sigma, "distance": dist}

    def _landmark_offset(self, obs):
        """(décalage de cap, précision, distance) d'un repère seul, avec l'inclinaison actuelle
        et sans recalage de position."""
        yaw, _ = self.untilted(obs["pic"], obs["yaw"], obs["elev"])
        x, y = _local(obs["mlon"], obs["mlat"], obs["lon"], obs["lat"])
        dist = math.hypot(x, y)
        bearing = math.degrees(math.atan2(x, y))
        offset = (bearing - yaw + 540) % 360 - 180
        position = math.degrees(math.atan2(math.hypot(obs["accuracy"], MAP_ERROR), max(dist, 1e-6)))
        return offset, math.hypot(position, AIM_ERROR), dist

    def _solve_pose(self, obs):
        """Relèvement : décalage de cap et de position communs aux photos des repères.

        Moindres carrés (Gauss-Newton) sur (cap, est, nord), chaque inconnue gardant
        son a priori : décalage de cap nul à ±prior, position GPS juste à ±accuracy.
        Un repère corrige surtout le cap ; trois repères ou plus bien répartis autour
        des photos recalent aussi la position.
        """
        lon0 = sum(o["lon"] for o in obs) / len(obs)
        lat0 = sum(o["lat"] for o in obs) / len(obs)
        rows = []
        for o in obs:
            yaw, _ = self.untilted(o["pic"], o["yaw"], o["elev"])
            px, py = _local(o["lon"], o["lat"], lon0, lat0)
            mx, my = _local(o["mlon"], o["mlat"], lon0, lat0)
            dist = max(math.hypot(mx - px, my - py), 1e-6)
            sigma = math.radians(math.hypot(AIM_ERROR, math.degrees(math.atan2(MAP_ERROR, dist))))
            rows.append((px, py, mx, my, math.radians(yaw), 1.0 / sigma ** 2))
        prior = (1.0 / math.radians(obs[0]["prior"]) ** 2,
                 1.0 / obs[0]["accuracy"] ** 2, 1.0 / obs[0]["accuracy"] ** 2)
        p = [0.0, 0.0, 0.0]  # décalage de cap (radians), décalage de position est, nord (m)

        def residuals(p):
            out = []
            for px, py, mx, my, yaw, w in rows:
                dx, dy = mx - px - p[1], my - py - p[2]
                r = (math.atan2(dx, dy) - yaw - p[0] + 3 * math.pi) % (2 * math.pi) - math.pi
                d2 = dx * dx + dy * dy
                out.append((r, (-1.0, -dy / d2, dx / d2), w))
            return out

        for _ in range(10):
            n = [[prior[i] if i == j else 0.0 for j in range(3)] for i in range(3)]
            g = [-prior[i] * p[i] for i in range(3)]
            for r, jac, w in residuals(p):
                for i in range(3):
                    g[i] -= w * jac[i] * r
                    for j in range(3):
                        n[i][j] += w * jac[i] * jac[j]
            cov = _inverse3(n)
            step = [sum(cov[i][j] * g[j] for j in range(3)) for i in range(3)]
            p = [p[i] + step[i] for i in range(3)]
            if max(abs(step[0]) * 1e3, abs(step[1]), abs(step[2])) < 1e-6:
                break
        res = residuals(p)
        # Grand axe de l'ellipse d'incertitude de la position
        a, b, c = cov[1][1], cov[1][2], cov[2][2]
        major = math.sqrt(max((a + c) / 2 + math.sqrt(((a - c) / 2) ** 2 + b * b), 0.0))
        return {
            "offset": math.degrees(p[0]), "sigma": math.degrees(math.sqrt(cov[0][0])),
            "shift": (p[1], p[2]), "shift_sigma": major,
            # Position recalée : trois repères au moins, et nettement mieux connue qu'au GPS
            "positioned": len(obs) >= 3 and major < 0.7 * obs[0]["accuracy"],
            "residual": math.degrees(math.sqrt(sum(r * r for r, _, _ in res) / len(res))),
            "residuals": [math.degrees(r) for r, _, _ in res],
            "count": len(obs), "prior": obs[0]["prior"], "accuracy": obs[0]["accuracy"],
            "photos": [(o["lon"], o["lat"]) for o in obs],
        }

    def pose(self, sequence, lon=None, lat=None):
        """Recalage de la séquence (voir _solve_pose), ou None. Avec (lon, lat), None aussi
        si la photo est à plus de REACH m des photos qui ont servi au recalage : la dérive
        du GPS au fil de la séquence le rendrait faux."""
        obs = self.landmarks.get(sequence) if sequence else None
        if not obs:
            return None
        result = self._solve_pose(obs)
        if lon is not None and lat is not None:
            if min(math.hypot(*_local(lon, lat, plon, plat)) for plon, plat in result["photos"]) > REACH:
                return None
        return result

    def clear_heading(self, sequence):
        self.landmarks.pop(sequence, None)
        self._notify()

    # Application -----------------------------------------------------------
    def correct(self, pic, sequence, yaw, elev, lon=None, lat=None):
        """Direction corrigée (inclinaison de la photo, puis cap de la séquence)."""
        yaw, elev = self.untilted(pic, yaw, elev)
        pose = self.pose(sequence, lon, lat)
        if pose:
            yaw = (yaw + pose["offset"]) % 360
        return yaw, elev

    def uncorrect(self, pic, sequence, yaw, elev, lon=None, lat=None):
        """Inverse de correct : direction vue dans la photo (brute) d'une direction vraie."""
        pose = self.pose(sequence, lon, lat)
        if pose:
            yaw = (yaw - pose["offset"]) % 360
        t = self.tilt(pic)
        return untilt(t["up"], yaw, elev, inverse=True) if t else (yaw % 360, elev)

    def position(self, sequence, lon, lat):
        """Position recalée de la photo : (lon, lat, précision en m), ou None si la
        position n'a pas été recalée (moins de trois repères bien répartis)."""
        pose = self.pose(sequence, lon, lat)
        if not pose or not pose["positioned"]:
            return None
        sx, sy = pose["shift"]
        lon, lat = offset(lon, lat, math.degrees(math.atan2(sx, sy)), math.hypot(sx, sy))
        return lon, lat, pose["shift_sigma"]

    def heading_error(self, sequence, lon=None, lat=None):
        """Erreur de visée (degrés) d'une séquence recalée, ou None si elle ne l'est pas."""
        pose = self.pose(sequence, lon, lat)
        return math.hypot(pose["sigma"], AIM_ERROR) if pose else None


def _height_obs(o):
    """Calage de la hauteur de caméra relu (voir Calibration.heights), ou None si illisible."""
    try:
        if isinstance(o, dict):
            clicks = []
            for c in o["clicks"]:
                clicks.append({
                    "pic": c.get("pic"), "sequence": c.get("sequence"),
                    "lon": float(c["lon"]), "lat": float(c["lat"]),
                    "gps": [float(v) for v in c["gps"]] if c.get("gps") else None,
                    "raw": [float(c["raw"][0]), float(c["raw"][1])] if c.get("raw") else None,
                    "yaw": float(c["yaw"]) if c.get("yaw") is not None else None,
                    "elev": float(c["elev"]) if c.get("elev") is not None else None,
                    "profile": [(float(d), float(z)) for d, z in c["profile"]] if c.get("profile") else None})
            if o.get("kind") not in ("ground", "height") or len(clicks) != 2 or float(o["known"]) <= 0:
                return None
            if any(c["raw"] is None and (c["yaw"] is None or c["elev"] is None) for c in clicks):
                return None
            return {"kind": o["kind"], "known": float(o["known"]), "clicks": clicks}
        h, s = float(o[0]), float(o[1])
        return (h, s) if s > 0 else None
    except (TypeError, ValueError, KeyError, IndexError, AttributeError):
        return None


def _auto_entry(entry):
    """Inclinaison automatique relue (voir Calibration.auto), ou None si illisible."""
    try:
        out = {"sequence": entry.get("sequence"), "heading": float(entry["heading"]), "verticals": None,
               "lidar": None}
        v = entry.get("verticals")
        if v:
            up = _unit(tuple(float(c) for c in v["up"]))
            out["verticals"] = {"up": list(up), "count": int(v["count"]), "sigma": float(v["sigma"])}
        if entry.get("lidar"):
            out["lidar"] = [float(entry["lidar"][0]), float(entry["lidar"][1])]
        return out
    except (TypeError, ValueError, KeyError, IndexError, AttributeError, ZeroDivisionError):
        return None


def _inverse3(m):
    """Inverse d'une matrice 3×3 (symétrique définie positive ici)."""
    a, b, c = m[0]
    d, e, f = m[1]
    g, h, i = m[2]
    co = [[e * i - f * h, -(d * i - f * g), d * h - e * g],
          [-(b * i - c * h), a * i - c * g, -(a * h - b * g)],
          [b * f - c * e, -(a * f - c * d), a * e - b * d]]
    det = a * co[0][0] + b * co[0][1] + c * co[0][2]
    return [[co[j][i] / det for j in range(3)] for i in range(3)]
