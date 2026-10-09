# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Cédric COCHART
# SPDX-License-Identifier: GPL-2.0-or-later
"""Calage des photos : correction d'inclinaison et recalage du cap.

- Inclinaison (par photo) : une caméra 360 est rarement parfaitement de niveau.
  On clique le pied puis le sommet d'objets verticaux (poteau, angle de façade) :
  chacun impose que la vraie verticale soit dans le plan qui contient la caméra
  et l'objet. Un objet corrige la pente vue de côté dans sa direction ; deux
  objets à peu près à angle droit corrigent l'inclinaison complète.
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
Calage enregistré dans le projet QGIS (voir plugin.py). Module sans dépendance à QGIS.
"""

import math

from . import ground
from .triangulation import _local, offset

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


def tilt_from_verticals(verticals):
    """Verticale vraie dans le repère de la photo, d'après des objets verticaux.

    verticals : liste de ((cap, élév.) du pied, (cap, élév.) du sommet).
    Retourne un dict : up (vecteur unitaire), tilt (degrés), toward (cap vers
    lequel le haut de la caméra penche), residual (écart moyen des objets à la
    verticale retenue, degrés, None avec un seul objet), partial (vrai si un
    seul objet ou des objets alignés : seule une composante est corrigée).
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
    a11 = a12 = a22 = r1 = r2 = 0.0
    for nx, ny, nz in normals:
        a11 += nx * nx
        a12 += nx * ny
        a22 += ny * ny
        r1 -= nx * nz
        r2 -= ny * nz
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
    partial = len(normals) < 2 or (a11 - damp) * (a22 - damp) - a12 * a12 < 0.1 * ((a11 + a22 - 2 * damp) / 2) ** 2
    residual = None
    if len(normals) > 1:
        residual = math.degrees(math.sqrt(sum(math.asin(min(1.0, abs(_dot(n, up)))) ** 2
                                              for n in normals) / len(normals)))
    return {"up": up, "tilt": tilt, "toward": math.degrees(math.atan2(up[0], up[1])) % 360,
            "residual": residual, "partial": partial, "count": len(normals)}


def untilt(up, yaw, elev):
    """Direction corrigée : rotation qui ramène la verticale `up` au zénith."""
    v = vector(yaw, elev)
    k = (up[1], -up[0], 0.0)  # up × zénith
    s = math.hypot(k[0], k[1])
    if s < 1e-12:
        return yaw % 360, elev
    k = (k[0] / s, k[1] / s, 0.0)
    c = up[2]
    kxv, kv = _cross(k, v), _dot(k, v)
    r = tuple(v[i] * c + kxv[i] * s + k[i] * kv * (1 - c) for i in range(3))  # Rodrigues
    return angles(r)


def _reference_length(kind, camera_height, clicks):
    """Longueur mesurée (m) avec une hauteur de caméra donnée : distance entre deux
    points au sol ("ground") ou hauteur d'un objet, pied puis sommet ("height")."""
    if kind == "height":
        return ground.measure_height(camera_height, *clicks)["value"]
    (_, p, z1), (_, q, z2) = (ground._ground_point(camera_height, c) for c in clicks)
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


def camera_height(kind, clicks, known):
    """Hauteur de caméra (m) et son incertitude, d'après une longueur connue.

    kind : "ground" (deux clics au sol) ou "height" (pied au sol, puis sommet) ;
    clicks : dicts avec elev (corrigée de l'inclinaison), yaw, lon, lat, profile.
    L'incertitude correspond à ±ground.PITCH_ERROR sur l'élévation de chaque clic.
    """
    if known <= 0:
        raise CalibrationError("Indiquez la longueur connue.")
    if kind == "height" and clicks[0].get("pic") != clicks[1].get("pic"):
        raise CalibrationError("Le pied et le sommet doivent être cliqués sur la même photo.")
    try:
        h = _solve_height(kind, clicks, known)
    except ground.GroundError as exc:
        raise CalibrationError(str(exc))
    spread = 0.0
    for i in range(len(clicks)):
        for sign in (-1, 1):
            moved = [dict(c) for c in clicks]
            moved[i]["elev"] += sign * ground.PITCH_ERROR
            try:
                spread = max(spread, abs(_solve_height(kind, moved, known) - h))
            except (CalibrationError, ground.GroundError):
                spread = max(spread, h)  # référence trop loin pour être fiable
    return h, max(spread, 0.005)


class Calibration:
    """Calages : inclinaison par photo ; cap, position et hauteur de caméra par séquence.

    Seules les observations sont conservées (clics et repères) : les corrections en sont
    recalculées. to_dict / load permettent de les enregistrer (dans le projet QGIS) ;
    `changed`, s'il est défini, est appelé après chaque modification.
    """

    FORMAT = 1  # version du format enregistré

    def __init__(self):
        self.verticals = {}  # pic -> [((cap, élév.) pied, (cap, élév.) sommet)]
        self.landmarks = {}  # séquence -> [dicts : pic, lon, lat, yaw, elev, mlon, mlat, accuracy, prior]
        self.heights = {}  # séquence -> [(hauteur de caméra, incertitude)]
        self._tilts = {}
        self.changed = None

    def _notify(self):
        if self.changed is not None:
            self.changed()

    # Enregistrement --------------------------------------------------------
    def to_dict(self):
        return {
            "format": self.FORMAT,
            "verticals": {pic: [[list(b), list(t)] for b, t in v] for pic, v in self.verticals.items()},
            "landmarks": self.landmarks,
            "heights": {seq: [list(o) for o in obs] for seq, obs in self.heights.items()},
        }

    def load(self, data):
        """Remplace le calage par celui enregistré (dict de to_dict, ou None : calage vide).
        Les entrées illisibles sont ignorées. Ne déclenche pas `changed`."""
        self.verticals, self.landmarks, self.heights, self._tilts = {}, {}, {}, {}
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
                obs = [(float(h), float(s)) for h, s in obs if float(s) > 0]
                if obs:
                    self.heights[seq] = obs
        except (TypeError, ValueError, KeyError, IndexError, AttributeError):
            pass  # format abîmé : on garde ce qui a pu être lu

    # Inclinaison ---------------------------------------------------------
    def add_vertical(self, pic, bottom, top):
        verticals = self.verticals.get(pic, []) + [(bottom, top)]
        result = tilt_from_verticals(verticals)  # lève CalibrationError sans rien garder
        self.verticals[pic], self._tilts[pic] = verticals, result
        self._notify()
        return result

    def tilt(self, pic):
        return self._tilts.get(pic)

    def clear_tilt(self, pic):
        self.verticals.pop(pic, None)
        self._tilts.pop(pic, None)
        self._notify()

    def untilted(self, pic, yaw, elev):
        t = self._tilts.get(pic)
        return untilt(t["up"], yaw, elev) if t else (yaw % 360, elev)

    # Hauteur de caméra --------------------------------------------------------
    def add_camera_height(self, sequence, kind, clicks, known):
        h, sigma = camera_height(kind, clicks, known)
        self.heights.setdefault(sequence, []).append((h, sigma))
        self._notify()
        return {"height": h, "sigma": sigma}

    def camera(self, sequence):
        """Hauteur de caméra calée de la séquence : dict height, sigma, count, ou None."""
        obs = self.heights.get(sequence)
        if not obs:
            return None
        sw = sum(1.0 / s ** 2 for _, s in obs)
        return {"height": sum(h / s ** 2 for h, s in obs) / sw, "sigma": 1.0 / math.sqrt(sw), "count": len(obs)}

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
