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
- Cap (par séquence) : on clique dans la photo un repère visible sur la carte,
  puis ce repère sur la carte. Le décalage mesuré est combiné au cap d'origine
  selon leurs précisions (moyenne pondérée) : un repère proche, où l'erreur de
  position GPS de la photo pèse lourd, ne dégrade jamais le cap.

Directions : (cap absolu en degrés, 0 = nord, sens horaire ; élévation en
degrés). Les corrections s'appliquent dans cet ordre : inclinaison, puis cap.
Calage conservé pour la session QGIS. Module sans dépendance à QGIS.
"""

import math

from . import ground
from .triangulation import _local

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


CAMERA_MIN, CAMERA_MAX = 0.3, 6.0  # plage de hauteurs de caméra admises (m)


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
    """Calages de la session : inclinaison par photo, décalage de cap par séquence."""

    def __init__(self):
        self.verticals = {}  # pic -> [((cap, élév.) pied, (cap, élév.) sommet)]
        self.landmarks = {}  # séquence -> [dicts : pic, lon, lat, yaw, elev, mlon, mlat, accuracy, prior]
        self.heights = {}  # séquence -> [(hauteur de caméra, incertitude)]
        self._tilts = {}

    # Inclinaison ---------------------------------------------------------
    def add_vertical(self, pic, bottom, top):
        verticals = self.verticals.get(pic, []) + [(bottom, top)]
        result = tilt_from_verticals(verticals)  # lève CalibrationError sans rien garder
        self.verticals[pic], self._tilts[pic] = verticals, result
        return result

    def tilt(self, pic):
        return self._tilts.get(pic)

    def clear_tilt(self, pic):
        self.verticals.pop(pic, None)
        self._tilts.pop(pic, None)

    def untilted(self, pic, yaw, elev):
        t = self._tilts.get(pic)
        return untilt(t["up"], yaw, elev) if t else (yaw % 360, elev)

    # Hauteur de caméra --------------------------------------------------------
    def add_camera_height(self, sequence, kind, clicks, known):
        h, sigma = camera_height(kind, clicks, known)
        self.heights.setdefault(sequence, []).append((h, sigma))
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

    # Cap -------------------------------------------------------------------
    def add_landmark(self, sequence, pic, lon, lat, yaw, elev, mlon, mlat, accuracy, survey):
        """Repère : clic (cap, élév. bruts) depuis la photo en (lon, lat), repère en (mlon, mlat)."""
        obs = {"pic": pic, "lon": lon, "lat": lat, "yaw": yaw, "elev": elev, "mlon": mlon, "mlat": mlat,
               "accuracy": DEFAULT_ACCURACY if accuracy is None else float(accuracy),
               "prior": SURVEY_PRIOR if survey else PRIOR}
        offset, sigma, dist = self._landmark_offset(obs)
        if dist < MIN_LANDMARK:
            raise CalibrationError("Repère à {:.1f} m de la photo : choisissez un repère plus éloigné.".format(dist))
        if abs(offset) > MAX_OFFSET:
            raise CalibrationError("Écart de cap de {:.0f}° : ce n'est sûrement pas le même repère sur la "
                                   "carte et dans la photo.".format(offset))
        self.landmarks.setdefault(sequence, []).append(obs)
        return {"offset": offset, "sigma": sigma, "distance": dist}

    def _landmark_offset(self, obs):
        """(décalage de cap, précision, distance) d'un repère, avec l'inclinaison actuelle."""
        yaw, _ = self.untilted(obs["pic"], obs["yaw"], obs["elev"])
        x, y = _local(obs["mlon"], obs["mlat"], obs["lon"], obs["lat"])
        dist = math.hypot(x, y)
        bearing = math.degrees(math.atan2(x, y))
        offset = (bearing - yaw + 540) % 360 - 180
        position = math.degrees(math.atan2(math.hypot(obs["accuracy"], MAP_ERROR), max(dist, 1e-6)))
        return offset, math.hypot(position, AIM_ERROR), dist

    def heading(self, sequence):
        """Correction de cap de la séquence : dict offset, sigma (degrés), count, raw (moyenne
        des repères seuls), ou None. Combinée au cap d'origine (décalage nul, précision prior)."""
        obs = self.landmarks.get(sequence)
        if not obs:
            return None
        w0 = 1.0 / obs[0]["prior"] ** 2
        sw = swo = 0.0
        for o in obs:
            offset, sigma, _ = self._landmark_offset(o)
            sw += 1.0 / sigma ** 2
            swo += offset / sigma ** 2
        return {"offset": swo / (w0 + sw), "sigma": 1.0 / math.sqrt(w0 + sw), "count": len(obs),
                "raw": swo / sw, "raw_sigma": 1.0 / math.sqrt(sw), "prior": obs[0]["prior"]}

    def clear_heading(self, sequence):
        self.landmarks.pop(sequence, None)

    # Application -----------------------------------------------------------
    def correct(self, pic, sequence, yaw, elev):
        """Direction corrigée (inclinaison de la photo, puis cap de la séquence)."""
        yaw, elev = self.untilted(pic, yaw, elev)
        h = self.heading(sequence) if sequence else None
        if h:
            yaw = (yaw + h["offset"]) % 360
        return yaw, elev

    def heading_error(self, sequence):
        """Erreur de visée (degrés) d'une séquence recalée, ou None si elle ne l'est pas."""
        h = self.heading(sequence) if sequence else None
        return math.hypot(h["sigma"], AIM_ERROR) if h else None
