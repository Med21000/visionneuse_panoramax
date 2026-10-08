# -*- coding: utf-8 -*-
"""Mesures sur une photo par lancer de rayon sur le terrain.

La caméra est à une hauteur connue au-dessus du sol. Un clic donne une
direction (cap, élévation) ; on suit ce rayon jusqu'à ce qu'il passe sous le
terrain, décrit par un profil d'altitudes le long de la visée (MNT ou service
d'altimétrie). Sans profil, le sol est supposé plat et horizontal. On en déduit
la position des points au sol, les distances entre points et la hauteur d'un
objet (pied puis sommet).

La précision chute vite avec la distance (une erreur d'inclinaison de 0,5°
déplace un point situé à 15 m de plus d'un mètre) : réservé aux objets proches.
Module sans dépendance à QGIS, pour pouvoir être testé seul.
"""

import math

from .triangulation import _local, offset

PITCH_ERROR = 0.5  # erreur d'inclinaison supposée (degrés) pour l'incertitude affichée
MAX_DISTANCE = 60.0  # longueur du profil de terrain et portée maximale d'une mesure
FLAT = "sol plat"


class GroundError(ValueError):
    """Clic inexploitable (message destiné à l'utilisateur)."""


def view_direction(heading, pitch, vfov, dx, dy, height_px):
    """Direction (cap, élévation en degrés) d'un pixel de la vue.

    heading/pitch : orientation du centre de la vue ; vfov : champ vertical ;
    dx, dy : décalage du pixel par rapport au centre (dy vers le bas) ;
    height_px : hauteur de la vue en pixels. Même modèle que capture._view_rays.
    """
    f = (height_px / 2.0) / math.tan(math.radians(vfov) / 2.0)
    u, v = dx / f, -dy / f
    p = math.radians(pitch)
    y = v * math.cos(p) + math.sin(p)
    z = -v * math.sin(p) + math.cos(p)
    h = math.radians(heading)
    x2 = u * math.cos(h) + z * math.sin(h)
    z2 = -u * math.sin(h) + z * math.cos(h)
    return math.degrees(math.atan2(x2, z2)) % 360, math.degrees(math.atan2(y, math.hypot(x2, z2)))


def flat_profile():
    """Profil d'un sol plat et horizontal (altitude relative 0)."""
    return [(0.0, 0.0), (MAX_DISTANCE, 0.0)]


def intersect(profile, camera_height, elevation):
    """Premier point où le rayon passe sous le terrain.

    profile : [(distance horizontale en m, altitude du terrain)], trié, depuis 0
    (sous la caméra). Retourne (distance, altitude du sol). Le terrain est
    linéaire entre deux échantillons : l'intersection y est exacte.
    """
    z_cam = profile[0][1] + camera_height
    slope = math.tan(math.radians(elevation))
    prev_s, prev_f = profile[0][0], camera_height
    for s, z in profile[1:]:
        f = z_cam + s * slope - z  # hauteur du rayon au-dessus du terrain
        if f <= 0:
            s_hit = prev_s + (s - prev_s) * prev_f / (prev_f - f)
            k = (s_hit - prev_s) / (s - prev_s) if s > prev_s else 0.0
            z_prev = z_cam + prev_s * slope - prev_f
            return s_hit, z_prev + k * (z - z_prev)
        prev_s, prev_f = s, f
    raise GroundError("Le point visé ne touche pas le sol dans les {:.0f} m : cliquez sur le sol, "
                      "plus près de la photo.".format(MAX_DISTANCE))


def _ground_point(camera_height, click):
    s, z = intersect(click.get("profile") or flat_profile(), camera_height, click["elev"])
    return s, offset(click["lon"], click["lat"], click["yaw"], s), z


def _spread(fn, clicks):
    """Écart maximal du résultat quand chaque élévation varie de ±PITCH_ERROR."""
    ref = fn(clicks)
    worst = 0.0
    for i in range(len(clicks)):
        for sign in (-1, 1):
            moved = [dict(c) for c in clicks]
            moved[i]["elev"] += sign * PITCH_ERROR
            try:
                worst = max(worst, abs(fn(moved) - ref))
            except GroundError:
                return float("inf")
    return worst


def measure_distance(camera_height, a, b):
    """Distance horizontale au sol entre deux clics (dicts lon, lat, yaw, elev, profile).

    Les clics peuvent venir de deux photos différentes : chacun est placé depuis
    la position de sa photo. Le dénivelé n'est donné que si les deux profils sont
    de vraies altitudes (pas le sol plat).
    """
    def dist(clicks):
        (_, p, _), (_, q, _) = (_ground_point(camera_height, c) for c in clicks)
        x, y = _local(q[0], q[1], p[0], p[1])
        return math.hypot(x, y)

    (d1, p1, z1), (d2, p2, z2) = _ground_point(camera_height, a), _ground_point(camera_height, b)
    absolute = all(c.get("terrain", FLAT) != FLAT for c in (a, b))
    return {"points": [p1, p2], "ranges": [d1, d2], "value": dist([a, b]),
            "dz": (z2 - z1) if absolute else None, "uncertainty": _spread(dist, [a, b])}


def measure_height(camera_height, base, top):
    """Hauteur d'un objet : clic au pied (au sol) puis au sommet, sur la même photo."""
    if base.get("pic") != top.get("pic"):
        raise GroundError("Le pied et le sommet doivent être cliqués sur la même photo.")
    profile = base.get("profile") or flat_profile()

    def height(clicks):
        b, t = clicks
        s, z_ground = intersect(profile, camera_height, b["elev"])
        z_top = profile[0][1] + camera_height + s * math.tan(math.radians(t["elev"]))
        return z_top - z_ground

    d, p, _ = _ground_point(camera_height, base)
    h = height([base, top])
    if h <= 0:
        raise GroundError("Le sommet est sous le pied : cliquez d'abord au pied, puis au sommet.")
    return {"points": [p], "ranges": [d], "value": h, "dz": None, "uncertainty": _spread(height, [base, top])}
