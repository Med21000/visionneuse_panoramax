# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Cédric COCHART
# SPDX-License-Identifier: GPL-2.0-or-later
"""Mesures entre deux points 3D : largeurs, longueur et hauteur.

Toutes reposent sur le même moteur (geometry.py) : chaque clic devient un point 3D
sur une surface, puis la mesure est une grandeur calculée sur ces points.

- "road" : largeur perpendiculaire à la chaussée (deux points au sol, composante
  en travers de l'axe de la route) ;
- "width" : longueur parallèle à la route (distance 3D entre deux points au sol) ;
- "height" : hauteur d'un objet (pied au sol, sommet dans le plan vertical face à
  la caméra, ou sur la façade du cadastre, composante verticale).

La mesure libre 3D (façade, plans, triangulation 3D entre deux photos) a été retirée le
15/10/2026 : elle donnait des résultats trop incohérents.

Un point peut être recalé après coup sur la carte : sa position horizontale vient
alors de la carte, et son altitude du rayon de visée de la photo à cette distance.
La mesure est recalculée ; pour une hauteur, elle ne dépend plus ni du terrain ni
de la hauteur de caméra.
"""

import math

from . import calibration, geometry, ground, triangulation
from .calibration import AIM_ERROR, MAP_ERROR, UNCALIBRATED

DEFAULT_CAMERA_HEIGHT = 1.9  # hauteur de caméra par défaut (m)


def _num(value, digits=1):
    """Nombre à la française (virgule décimale)."""
    return "{:.{}f}".format(value, digits).replace(".", ",")


class NullItem:
    """Dessin d'une mesure sur la carte, quand l'hôte n'en fournit pas (voir MeasureTool.new_item)."""

    def set_data(self, *args, **kwargs):
        pass

    def remove(self):
        pass


SURFACE_NAMES = {"ground": "sol", "vertical": "plan vertical face à la caméra"}
PRESET_SURFACES = {"road": "ground", "width": "ground", "height": "vertical"}  # surface de chaque mesure
CAMERA_RANGE = (0.5, 4.0)  # hauteur de caméra plausible (m) déduite de l'altitude GPS
CAMERA_SOURCES = {None: 0, "calée": 1, "altitude GPS": 2}  # champ camera_calee de la couche des mesures
# Incertitude de la hauteur de caméra (m) selon sa source : altitude GPS d'une position vérifiée
# (écart-type sur la séquence de Pouilly : 2 cm, plus le LiDAR), sinon valeur saisie (1,7 à 2,2 m
# selon le véhicule) ; une hauteur calée a sa propre incertitude
CAMERA_ERRORS = {"altitude GPS": 0.05, None: 0.15}
# Façade du cadastre : écart du mur (m), confirmé par le LiDAR HD ou non (voir buildings.py)
WALL_ERROR, WALL_ERROR_UNCHECKED = 0.5, 1.0
SELECT_ANGLE = 1.0  # écart maximal (degrés) entre un clic et le repère d'un point pour le sélectionner
ERRORS = (geometry.GeometryError, ground.GroundError)
KIND_NAMES = {"road": "largeur perpendiculaire à la chaussée", "width": "longueur parallèle à la route",
              "height": "hauteur"}




def _ground_z(clicks):
    """Altitude du sol sous chaque photo : celle du premier clic fait sur la photo, la même
    ensuite pour tous ses clics (le profil d'un clic dépend de la direction visée)."""
    out = {}
    for c in clicks:
        out.setdefault(c.get("pic"), (c.get("profile") or ground.flat_profile())[0][1])
    return out


def _profile_z(profile, s):
    """Altitude du terrain à la distance horizontale s le long d'un profil [(distance, z)]."""
    for (s0, z0), (s1, z1) in zip(profile, profile[1:]):
        if s <= s1 or (s1, z1) == profile[-1]:
            k = (s - s0) / (s1 - s0) if s1 > s0 else 0.0
            return z0 + k * (z1 - z0)
    return profile[-1][1]


def _wall_foot(click, camera_height, origin, ground_z, plane):
    """Pied du mur visé : là où la visée horizontale du clic coupe le plan vertical du mur,
    à l'altitude du sol (profil de terrain du clic)."""
    cam, u = geometry.camera(click, camera_height, origin, ground_z)
    (px, py, _), (nx, ny, _) = plane
    hx, hy = u[0], u[1]
    den = nx * hx + ny * hy
    if abs(den) < 1e-6:
        raise geometry.GeometryError("Visée parallèle à la façade du cadastre : visez-la plus de face.")
    t = (nx * (px - cam[0]) + ny * (py - cam[1])) / den
    norm = math.hypot(hx, hy)
    if t <= 0:
        raise geometry.GeometryError("La façade du cadastre est derrière la visée.")
    s = t * norm  # distance horizontale
    profile = ground.anchored(click.get("profile") or ground.flat_profile(), ground_z)
    return (cam[0] + t * hx, cam[1] + t * hy, _profile_z(profile, s))


class Measure:
    """Une mesure entre deux points 3D (voir le module)."""

    def __init__(self, tool, kind, surface):
        self.tool = tool
        self.kind, self.surface = kind, surface
        self.clicks = []  # dicts de PanoramaxDock.photoClicked, avec profil de terrain
        # n° de point -> (lon, lat, orientation en degrés) : point recalé sur la carte, qui fixe
        # son plan vertical (passant par lui, de normale horizontale orientée vers la caméra
        # qui l'a visé au moment du recalage)
        self.overrides = {}
        self.aimed = set()  # points visés à nouveau après un recalage : sur le plan calé de la mesure
        self.result = None
        self.error = None
        self.notice = None  # avertissement sur la source d'altitude
        self.incidence = None  # angle d'incidence le plus rasant sur un plan (degrés)
        self.item = None
        self.fid = None  # entité de la couche des mesures, une fois enregistrée
        self.layer_id = None  # couche de cette entité : un fid ne vaut que dans sa couche
        self.saved = None  # résultat au moment de l'enregistrement (voir _snapshot)
        self.road = None  # axe de la route au point mesuré (largeur perpendiculaire, voir roads.py)
        self.wall = None  # façade du cadastre visée (hauteur, voir buildings.py)
        self.wall_pending = False
        self.wall_item = None
        self.road_pending = False  # lecture de cet axe en cours

    def needed(self):
        return 2

    def add_click(self, click, notice=None):
        if notice:
            self.notice = notice
        self.clicks.append(click)
        self.compute()
        if self.error:
            error = self.error
            self.clicks.pop()  # clic refusé : on le refait
            self.compute()
            self.error = error

    def relocate(self, index, lon, lat):
        """Recale le point n° index à la position (lon, lat) cliquée sur la carte. Son plan
        vertical est orienté face à la photo qui l'a visé."""
        c = self.clicks[index]
        x, y = triangulation._local(lon, lat, c["lon"], c["lat"])
        state = dict(self.overrides), set(self.aimed)
        self.overrides[index] = (lon, lat, math.degrees(math.atan2(x, y)))
        self.aimed.discard(index)  # le point est là où on l'a lâché
        self.compute()
        if self.error:
            error = self.error
            self.overrides, self.aimed = state
            self.compute()
            self.error = error

    def reaim(self, index, click):
        """Vise à nouveau le point n° index : le nouveau clic remplace le sien. Si la mesure a
        été calée sur la carte, le point est pris sur son plan calé (profondeur de la carte,
        position fine de la photo), qu'il ait été lui-même recalé ou non ; sinon, sur la
        surface de la mesure."""
        state = list(self.clicks), set(self.aimed)
        self.clicks[index] = click
        if self.overrides:
            self.aimed.add(index)
        self.compute()
        if self.error:
            error = self.error
            self.clicks, self.aimed = state
            self.compute()
            self.error = error

    # Calcul -------------------------------------------------------------------
    def points(self, clicks=None, overrides=None, camera_delta=None, wall_shift=0.0):
        """Points 3D (repère local centré sur le premier clic) des clics complets,
        angle d'incidence le plus rasant sur un plan, origine du repère, et pour
        chaque point le clic dont il vient. camera_delta (photo -> écart de hauteur de caméra,
        m) et wall_shift (décalage de la façade du cadastre vers l'extérieur, m) servent au
        calcul de l'incertitude."""
        clicks = self.clicks if clicks is None else clicks
        overrides = self.overrides if overrides is None else overrides
        if not clicks:
            return [], None, None, []
        origin = (clicks[0]["lon"], clicks[0]["lat"])
        ground_z = _ground_z(clicks)

        def zof(click):
            return ground_z[click.get("pic")]

        def h(click):
            return self.tool.camera_for(click, zof(click)) + (camera_delta or {}).get(click.get("pic"), 0.0)

        # Plan calé de la mesure, défini par les points glissés sur la carte : vertical, passant
        # par les deux premiers (le mur tracé sur la carte), ou par le seul glissé, face à la
        # photo qui l'avait visé
        anchors = {k: triangulation._local(v[0], v[1], origin[0], origin[1]) + (v[2],)
                   for k, v in overrides.items()}
        anchored = None
        if len(anchors) >= 2:
            (k1, a1), (k2, a2) = sorted(anchors.items())[:2]
            if math.hypot(a2[0] - a1[0], a2[1] - a1[1]) >= 0.2:
                anchored = geometry.vertical_plane((a1[0], a1[1], 0.0), (a2[0], a2[1], 0.0))
        if anchored is None and anchors:
            x, y, azimuth = sorted(anchors.items())[0][1]
            a = math.radians(azimuth)
            anchored = ((x, y, 0.0), (math.sin(a), math.cos(a), 0.0))

        def fixed(k, p, click):
            """Point k visé à nouveau après un recalage : intersection du rayon et du plan calé.
            Recalé sur la carte : à la position de la carte, à l'altitude du rayon de visée."""
            if k in self.aimed and anchored is not None:  # plan calé, pas la surface de la mesure
                return geometry.on_plane(click, h(click), origin, *anchored, ground_z=zof(click))[0]
            if k not in anchors:
                return p
            x, y, _ = anchors[k]
            cam, u = geometry.camera(click, h(click), origin, zof(click))
            reach = math.hypot(x - cam[0], y - cam[1])
            return (x, y, cam[2] + reach * u[2] / max(math.hypot(u[0], u[1]), 1e-9))

        def recaled(k):
            """Point k visé à nouveau dans le plan calé : la surface de la mesure ne sert pas."""
            return k in self.aimed and anchored is not None

        pts, sources, incidence, plane = [], [], None, None
        if self.surface == "ground":
            for c in clicks:
                k = len(pts)
                pts.append(fixed(k, None if recaled(k) else geometry.on_ground(c, h(c), origin, zof(c))[0], c))
                sources.append(c)
        else:  # plan vertical : celui du mur du cadastre, sinon face à la caméra
            if self.wall and 0 not in anchors:
                # Façade du cadastre : pied au bas du mur, à l'altitude du sol (même si le clic
                # tombe sur une voiture devant), sommet sur le mur
                plane = geometry.vertical_plane(*(geometry.to_local(lon, lat, 0.0, origin)
                                                  for lon, lat in self.wall["points"]))
                if wall_shift:
                    (px, py, pz), (nx, ny, nz) = plane
                    k = wall_shift / math.hypot(nx, ny)
                    plane = ((px + k * nx, py + k * ny, pz), (nx, ny, nz))
                first = _wall_foot(clicks[0], h(clicks[0]), origin, zof(clicks[0]), plane)
                planar, pts, sources = clicks[1:], [first], [clicks[0]]
            else:
                first = fixed(0, geometry.on_ground(clicks[0], h(clicks[0]), origin, zof(clicks[0]))[0], clicks[0])
                cam = geometry.camera(clicks[0], h(clicks[0]), origin, zof(clicks[0]))[0]
                plane = geometry.facing_plane(cam, first)
                planar, pts, sources = clicks[1:], [first], [clicks[0]]
            for c in planar:
                if recaled(len(pts)):
                    pts.append(fixed(len(pts), None, c))
                    sources.append(c)
                    continue
                p, angle = geometry.on_plane(c, h(c), origin, *plane, ground_z=zof(c))
                pts.append(fixed(len(pts), p, c))
                sources.append(c)
                incidence = angle if incidence is None else min(incidence, angle)
        return pts, incidence, origin, sources

    @property
    def axis(self):
        """Axe de la route retenu (cap en degrés) : au point mesuré si connu (voir roads.py),
        sinon direction du véhicule à la caméra, portée par le premier clic."""
        return self.road["axis"] if self.road else self.clicks[0].get("axis")

    @property
    def axis_source(self):
        if self.road:
            return self.road["source"]
        source = self.clicks[0].get("axis_source")
        return source + ", lecture de la BD TOPO…" if self.road_pending and source else source

    def _wall_changed(self):
        """Façade du cadastre visée par le pied (hauteur, voir buildings.py), si l'outil la
        demande. Retourne True si elle a changé (les points sont à recalculer)."""
        lookup = self.tool.wall_lookup if self.tool.use_wall else None
        old = self.wall
        self.wall, self.wall_pending = None, False
        if lookup is not None:
            c = self.clicks[0]
            res = lookup(c["lon"], c["lat"], c["yaw"])
            if isinstance(res, dict):
                self.wall = res
            else:
                self.wall_pending = res == "pending"
        return self.wall != old

    def _road_axis(self, pts, origin):
        """Axe de la route au milieu des deux bords (voir roads.py), si l'outil sait le lire."""
        lookup = self.tool.road_lookup
        self.road, self.road_pending = None, False
        if lookup is None:
            return
        mid = ((pts[0][0] + pts[1][0]) / 2, (pts[0][1] + pts[1][1]) / 2, pts[0][2])
        lon, lat = geometry.to_geographic(mid, origin)[:2]
        crossing = math.degrees(math.atan2(pts[1][0] - pts[0][0], pts[1][1] - pts[0][1])) % 360
        res = lookup(lon, lat, crossing, self.clicks[0].get("sequence"))
        if isinstance(res, dict):
            self.road = res
        else:
            self.road_pending = res == "pending"

    def quantity(self, d):
        """Grandeur mesurée d'après la décomposition d (voir geometry.decompose)."""
        if self.kind == "road":
            if d["across"] is None:
                raise geometry.GeometryError("Axe de la route inconnu pour cette photo : largeur impossible "
                                             "à calculer.")
            return d["across"]
        if self.kind == "height":
            if d["vertical"] <= 0:
                raise geometry.GeometryError("Le sommet est sous le pied : cliquez d'abord au pied, puis au "
                                             "sommet.")
            return abs(d["vertical"])
        return d["d3"]

    def _value(self, **variant):
        pts, _, _, _ = self.points(**variant)
        return self.quantity(geometry.decompose(pts[0], pts[1], self.axis))

    def _spread(self):
        """Incertitude de la mesure (m) : somme quadratique des écarts dus à chaque source
        d'erreur indépendante, chacune prise à ±1 écart-type :
        - inclinaison de chaque photo, commune à tous ses clics (tangage et roulis) : celle du
          calage appliqué, ou UNCALIBRATED sans calage ;
        - hauteur de caméra de chaque photo (CAMERA_ERRORS, ou celle de la hauteur calée) ;
        - visée de chaque clic (AIM_ERROR) ;
        - position de chaque point recalé sur la carte, façade du cadastre."""
        ref = self._value()
        groups = []  # par source d'erreur : variantes à ±1 écart-type
        for pic in dict.fromkeys(c.get("pic") for c in self.clicks):
            sigma = self._tilt_sigma(pic)
            for comp in ((sigma, 0.0), (0.0, sigma)):
                groups.append([{"clicks": self._tilted_clicks(self.clicks, pic, sign * comp[0], sign * comp[1])}
                               for sign in (-1, 1)])
            dh = self._camera_sigma(pic)
            groups.append([{"camera_delta": {pic: sign * dh}} for sign in (-1, 1)])
        for i in range(len(self.clicks)):
            for key, step in (("elev", AIM_ERROR), ("yaw", AIM_ERROR)):
                variants = []
                for sign in (-1, 1):
                    moved = [dict(x) for x in self.clicks]
                    moved[i][key] += sign * step
                    variants.append({"clicks": moved})
                groups.append(variants)
        for k, (lon, lat, azimuth) in self.overrides.items():
            for dx, dy in ((1, 0), (0, 1)):
                variants = []
                for sign in (-1, 1):
                    moved = dict(self.overrides)
                    moved[k] = triangulation._geographic(sign * dx * MAP_ERROR, sign * dy * MAP_ERROR, lon, lat) + (azimuth,)
                    variants.append({"overrides": moved})
                groups.append(variants)
        if self.wall and not self.overrides:
            err = WALL_ERROR_UNCHECKED if self.wall.get("warning") else WALL_ERROR
            groups.append([{"wall_shift": s * err} for s in (-1, 1)])
        total = 0.0
        for variants in groups:
            worst = 0.0
            for variant in variants:
                try:
                    worst = max(worst, abs(self._value(**variant) - ref))
                except ERRORS:
                    return float("inf")
            total += worst * worst
        return math.sqrt(total)

    def _tilt_sigma(self, pic):
        """Incertitude (degrés) de l'inclinaison appliquée aux clics de la photo."""
        cal = self.tool.calibration
        tilt = cal.tilt(pic) if cal is not None and pic else None
        return tilt["sigma"] if tilt and tilt.get("sigma") is not None else UNCALIBRATED

    def _camera_sigma(self, pic):
        """Incertitude (m) de la hauteur de caméra de la photo, selon sa source."""
        c = next(x for x in self.clicks if x.get("pic") == pic)
        _, source = self.tool.camera_info(c, _ground_z(self.clicks)[pic])
        if source == "calée":
            return self.tool.calibration.camera(c.get("sequence"))["sigma"]
        return CAMERA_ERRORS.get(source, CAMERA_ERRORS[None])

    @staticmethod
    def _tilted_clicks(clicks, pic, north, east):
        """Clics de la photo pic vus par une caméra penchée en plus de north° vers le nord et
        east° vers l'est (variation d'inclinaison commune à tous ses clics)."""
        up = calibration.up_from_components(north, east, 0.0)
        out = []
        for c in clicks:
            c = dict(c)
            if c.get("pic") == pic:
                c["yaw"], c["elev"] = calibration.untilt(up, c["yaw"], c["elev"])
            out.append(c)
        return out

    def compute(self):
        self.result, self.error, self.incidence = None, None, None
        try:
            if self.kind == "height" and len(self.clicks) == 2 and self.clicks[0].get("pic") != self.clicks[1].get("pic"):
                raise geometry.GeometryError("Le pied et le sommet doivent être cliqués sur la même photo.")
            pts, self.incidence, origin, sources = self.points()
            if len(pts) == 2 and len(self.clicks) == self.needed():
                if self.kind == "height" and self._wall_changed():
                    pts, self.incidence, origin, sources = self.points()
                if self.kind == "road":
                    self._road_axis(pts, origin)
                d = geometry.decompose(pts[0], pts[1], self.axis)
                value = self.quantity(d)
                lonlat = [geometry.to_geographic(p, origin)[:2] for p in pts]
                ranges = []
                for p, c in zip(pts, sources):
                    cx, cy = triangulation._local(c["lon"], c["lat"], origin[0], origin[1])
                    ranges.append(math.hypot(p[0] - cx, p[1] - cy))
                self.result = {"value": value, "decomposition": d, "uncertainty": self._spread(),
                               "ranges": ranges, "points": lonlat, "extra": None, "picks": lonlat,
                               "shifts": self._shifts(pts, origin)}
                if self.kind == "road":
                    # Trait perpendiculaire à l'axe, à mi-chemin des deux bords cliqués le long de la
                    # route ; chaque bout est relié en pointillés à son bord cliqué
                    a = math.radians(self.axis)
                    ux, uy = math.sin(a), math.cos(a)
                    half = ((pts[1][0] - pts[0][0]) * ux + (pts[1][1] - pts[0][1]) * uy) / 2.0
                    start = (pts[0][0] + half * ux, pts[0][1] + half * uy, pts[0][2])
                    end = (pts[1][0] - half * ux, pts[1][1] - half * uy, pts[0][2])
                    self.result["points"] = [geometry.to_geographic(p, origin)[:2] for p in (start, end)]
                    self.result["extra"] = list(lonlat)
                elif self.kind == "height":
                    self.result["points"] = [lonlat[0]]  # sommet au-dessus du pied
        except ERRORS as exc:
            self.error = str(exc)
        self._draw()

    def _shifts(self, pts, origin):
        """Pour chaque clic, écart (cap, élévation en degrés) entre la direction cliquée et
        celle où la caméra voit le point 3D qui en résulte : nul pour un point resté sur sa
        visée, non nul pour un point recalé de côté sur la carte."""
        ground_z = _ground_z(self.clicks)
        out = []
        for i, c in enumerate(self.clicks):
            p = pts[i]
            z = ground_z[c.get("pic")]
            cam, _ = geometry.camera(c, self.tool.camera_for(c, z), origin, z)
            vx, vy, vz = p[0] - cam[0], p[1] - cam[1], p[2] - cam[2]
            yaw = math.degrees(math.atan2(vx, vy))
            elev = math.degrees(math.atan2(vz, math.hypot(vx, vy)))
            out.append(((yaw - c["yaw"] + 540) % 360 - 180, elev - c["elev"]))
        return out

    def picks(self):
        """Points recalables sur la carte : [(n° de point, lon, lat)]. Sur le plan vertical
        face à la caméra (hauteur), seul le pied l'est : le sommet en découle."""
        if self.result:
            pts = [(k, p[0], p[1]) for k, p in enumerate(self.result["picks"])]
        else:
            try:
                raw, _, origin, _ = self.points()
            except ERRORS:
                return []
            pts = [(k,) + geometry.to_geographic(p, origin)[:2] for k, p in enumerate(raw)]
        return pts[:1] if self.surface == "vertical" else pts

    # Enregistrement -----------------------------------------------------------
    def _snapshot(self):
        """Ce qui doit être réécrit dans la couche s'il change : valeur, incertitude, points."""
        r = self.result
        return round(r["value"], 4), round(r["uncertainty"], 4), tuple(map(tuple, r["picks"]))

    def unsaved(self):
        """Mesure terminée, pas encore enregistrée, ou modifiée depuis (recalage, visée)."""
        return bool(self.result) and self.saved != self._snapshot()

    # Affichage ----------------------------------------------------------------
    def label(self):
        if not self.result:
            return ""
        symbol = "↕ " if self._vertical() else "↔ "
        return "{}{} m ± {} m".format(symbol, _num(self.result["value"], 2), _num(self.result["uncertainty"], 2))

    def _vertical(self):
        if self.kind in ("road", "width"):
            return False
        return True  # hauteur

    def _draw(self):
        if self.result:
            points, extra = self.result["points"], self.result["extra"]
            label = "{} m".format(_num(self.result["value"], 2))
        else:
            points, extra, label = [p[1:] for p in self.picks()], None, ""
        if self.item is None and points:
            self.item = self.tool.new_item()
        if self.item is not None:
            self.item.set_data(points, label, extra)
        wall = self.wall["points"] if self.wall else []
        if self.wall_item is None and wall:
            self.wall_item = self.tool.new_item(wall=True)
        if self.wall_item is not None:
            self.wall_item.set_data(wall, "façade du cadastre" if wall else "")

    def remove(self):
        if self.item is not None:
            self.item.remove()
            self.item = None
        if self.wall_item is not None:
            self.wall_item.remove()
            self.wall_item = None

    def point_of_click(self, i):
        """N° du point auquel contribue le clic n° i."""
        return i

    def viewer_marks(self, selected=None):
        """Repères dans la visionneuse (voir PanoramaxDock.set_measure_marks) : là où la
        photo voit chaque point 3D, c'est-à-dire à l'endroit cliqué, décalé d'autant que le
        point a été recalé de côté sur la carte. selected : n° du point sélectionné."""
        shifts = (self.result or {}).get("shifts") or [(0.0, 0.0)] * len(self.clicks)
        points = []
        for i, (c, (dyaw, delev)) in enumerate(zip(self.clicks, shifts)):
            pos = c.get("pos") or [None, None]
            raw = c.get("raw") or [c["yaw"], c["elev"]]
            if pos[0] is not None:  # position dans la photo (radians), visionneuse web
                pos = [pos[0] + math.radians(dyaw), pos[1] + math.radians(delev)]
            points.append({"pic": c.get("pic"), "yaw": pos[0], "pitch": pos[1],
                           "abs_yaw": raw[0] + dyaw, "elev": raw[1] + delev,
                           "sel": selected is not None and self.point_of_click(i) == selected})
        return {"points": points, "label": self.label(), "beside": bool(self.result) and self._vertical()}

    def status(self):
        """Résultat (mesure complète) ; les consignes de clic sont dans MeasureTool.status."""
        r = self.result
        d = r["decomposition"]
        value, unc = _num(r["value"], 2), _num(r["uncertainty"], 2)
        if self.kind == "road":
            text = ("Largeur perpendiculaire à la chaussée : {} m (±{} m) · en biais {} m · axe {}° ({})").format(
                value, unc, _num(d["horizontal"], 2), _num(self.axis % 180, 0), self.axis_source or "?")
        elif self.kind == "width":
            text = "Longueur : {} m (±{} m) entre les deux points · à {} et {} m de la photo".format(
                value, unc, _num(r["ranges"][0]), _num(r["ranges"][1]))
            if abs(d["vertical"]) >= 0.05:
                text += " · dénivelé {} m".format(_num(d["vertical"], 2))
        elif self.kind == "height":
            text = "Hauteur : {} m (±{} m) · objet à {} m".format(value, unc, _num(r["ranges"][0]))
            if self.wall:
                text += " · façade du cadastre à {} m ({})".format(_num(self.wall["distance"]), self.wall["check"])
            elif self.tool.use_wall:
                text += (" · lecture du cadastre…" if self.wall_pending else
                         " · aucune façade du cadastre dans la visée : pied cliqué")
        if self.overrides:
            text += " · {} point{} recalé{} sur la carte".format(
                len(self.overrides), *(("s", "s") if len(self.overrides) > 1 else ("", "")))
            if self.aimed:
                text += ", dont {} visé{} à nouveau dans son plan".format(
                    len(self.aimed), "s" if len(self.aimed) > 1 else "")
        if self.incidence is not None and self.incidence < geometry.GRAZING:
            text += ". Visée rasante sur le plan ({}°) : mesure imprécise, prenez une photo plus en face".format(
                _num(self.incidence, 0))
        labels = {c.get("terrain") for c in self.clicks if c.get("terrain")}
        height, source = self.tool.camera_info(self.clicks[0], _ground_z(self.clicks)[self.clicks[0].get("pic")])
        text += ". Terrain : {}, caméra à {} m{}.".format(" + ".join(sorted(labels)) or ground.FLAT,
                                                        _num(height, 2), " ({})".format(source) if source else "")
        if len(labels) > 1:
            text += " Altitudes de sources différentes : la composante verticale peut être faussée."
        if self.notice:
            text += " ({}.)".format(self.notice)
        return text + " Point mal placé : glissez-le sur la carte."


class MeasureTool:
    """Mesure en cours, mesures terminées gardées à l'écran et réglages."""

    def __init__(self, canvas):
        self.canvas = canvas
        self.mode = "road"  # "road", "width" ou "height"
        self.camera_height = DEFAULT_CAMERA_HEIGHT  # hauteur saisie dans le panneau
        self.road_lookup = None  # roads.RoadAxisProvider.lookup : axe de la route au point mesuré
        self.wall_lookup = None  # buildings.BuildingProvider.lookup : façade du cadastre visée
        self.use_wall = False  # hauteur : pied et sommet sur la façade du cadastre
        self.calibration = None  # calibration.Calibration : hauteur calée par séquence
        self.error = None
        self.current = None
        self.done = []  # mesures terminées, gardées à l'écran jusqu'à clear_all
        self.selected = None  # (mesure, n° de point) à viser à nouveau dans la visionneuse

    def new_item(self, wall=False):
        """Dessin d'une mesure (ou de la façade du cadastre, wall) sur la carte : set_data(points, libellé,
        extras) et remove(). Rien ici ; l'hôte le fournit (plugin QGIS : measure.MeasureItem)."""
        return NullItem()

    # Réglages ---------------------------------------------------------------
    def set_mode(self, mode):
        if mode != self.mode:
            self.finish()
            self.mode = mode

    def set_camera_height(self, value):
        self.camera_height = float(value)
        if self.current is not None:
            self.current.compute()

    def camera_info(self, click, ground_z=None):
        """(hauteur de caméra du clic, source) ; source "altitude GPS", "calée" ou None.

        Dans l'ordre : altitude NGF de la caméra (position vérifiée précise, voir precision.py)
        moins l'altitude du sol sous la photo (ground_z, si elle vient du LiDAR HD), la plus
        précise et propre à chaque photo ; hauteur calée pour la séquence ; réglage du panneau."""
        camera_alt = click.get("camera_alt")
        if camera_alt is not None and ground_z is not None and click.get("precise_ground"):
            height = camera_alt - ground_z
            if CAMERA_RANGE[0] <= height <= CAMERA_RANGE[1]:
                return height, "altitude GPS"
        sequence = click.get("sequence")
        cam = self.calibration.camera(sequence) if self.calibration and sequence else None
        return (cam["height"], "calée") if cam else (self.camera_height, None)

    def camera_for(self, click, ground_z=None):
        return self.camera_info(click, ground_z)[0]

    def needs_profile(self):
        return True  # altitude du sol sous la caméra, et du sol visé

    # Clics --------------------------------------------------------------------
    def select_near(self, click):
        """Clic sur le repère d'un point existant (à moins de SELECT_ANGLE degrés), entre deux
        mesures : le sélectionne pour le viser à nouveau. Retourne True si le clic a servi à
        cela. Un point déjà sélectionné : le clic est sa nouvelle visée, même tout près de son
        repère (une correction fine l'est toujours) ; on annule avec cancel_selection."""
        if self.selected is not None:
            return False
        if self.current is not None and 0 < len(self.current.clicks) < self.current.needed():
            return False
        raw = click.get("raw") or [click["yaw"], click["elev"]]
        best, best_d = None, SELECT_ANGLE
        for m in self.measures():
            if not m.result:
                continue
            for i, p in enumerate(m.viewer_marks()["points"]):
                if p["pic"] != click.get("pic"):
                    continue
                dyaw = (raw[0] - p["abs_yaw"] + 540) % 360 - 180
                d = math.hypot(dyaw * math.cos(math.radians(raw[1])), raw[1] - p["elev"])
                if d < best_d:
                    best, best_d = (m, m.point_of_click(i)), d
        if best is None:
            return False
        self.error = None
        self.selected = best
        return True

    def cancel_selection(self):
        """Annule la sélection d'un point. Retourne True s'il y en avait une."""
        if self.selected is None:
            return False
        self.selected = None
        return True

    def add_click(self, click, notice=None):
        self.error = None
        if self.selected is not None:  # visée à nouveau du point sélectionné
            measure, index = self.selected
            self.selected = None
            if notice:
                measure.notice = notice
            measure.reaim(index, click)
            if measure.error:
                return measure.error + " " + (measure.status() if measure.result else "")
            return measure.status()
        if self.current is None or len(self.current.clicks) >= self.current.needed():
            self.finish()  # nouvelle mesure, la précédente reste affichée
            self.current = Measure(self, self.mode, PRESET_SURFACES[self.mode])
        self.current.add_click(click, notice)
        return self.status()

    def finish(self):
        """Termine la mesure en cours : réussie, elle reste affichée (visionneuse et carte)
        jusqu'à clear_all ; sinon elle est abandonnée."""
        if self.current is not None:
            if self.current.result:
                self.done.append(self.current)
            else:
                self.current.remove()
        self.current, self.selected = None, None

    def clear(self):
        """Efface la mesure en cours (les mesures terminées restent affichées)."""
        if self.current is not None:
            self.current.remove()
        self.current, self.error, self.selected = None, None, None

    def clear_all(self):
        """Efface aussi les mesures terminées."""
        self.clear()
        for m in self.done:
            m.remove()
        self.done = []

    def remove(self):
        self.clear_all()

    def measures(self):
        """Toutes les mesures affichées (terminées, puis en cours)."""
        return self.done + ([self.current] if self.current is not None else [])

    def set_use_wall(self, value):
        """Hauteur sur la façade du cadastre (case du panneau) : mesures de hauteur recalculées."""
        self.use_wall = bool(value)
        return self.recompute_walls()

    def recompute_walls(self):
        """Façades du cadastre arrivées ou demandées : hauteurs recalculées. Retourne le
        nombre de mesures concernées."""
        heights = [m for m in self.measures() if m.kind == "height" and len(m.clicks) == m.needed()]
        for m in heights:
            m.compute()
        return len(heights)

    def recompute_roads(self):
        """Axe de la route arrivé (voir roads.py) : largeurs perpendiculaires recalculées.
        Retourne le nombre de mesures concernées."""
        roads = [m for m in self.measures() if m.kind == "road" and len(m.clicks) == m.needed()]
        for m in roads:
            m.compute()
        return len(roads)

    def recorrect(self, pic, correct):
        """Calage de la photo `pic` changé (inclinaison automatique arrivée après les clics) :
        directions de ses clics recalculées depuis leur direction brute par correct(clic) →
        (cap, élévation), puis mesures recalculées. Retourne le nombre de mesures concernées."""
        count = 0
        for m in self.measures():
            clicks = [c for c in m.clicks if c.get("pic") == pic and c.get("raw")]
            for c in clicks:
                c["yaw"], c["elev"] = correct(c)
            if clicks:
                m.compute()
                count += 1
        return count

    # Enregistrement -----------------------------------------------------------
    # Recalage sur la carte ----------------------------------------------------
    def handles(self):
        """Poignées déplaçables sur la carte : [(clé, lon, lat)], clé ("point", mesure,
        n° de point)."""
        return [(("point", m, k), lon, lat) for m in self.measures() for k, lon, lat in m.picks()]

    def move_handle(self, key, lon, lat):
        """Recale une poignée à (lon, lat). Retourne le texte à afficher dans le panneau."""
        measure, index = key[1], key[2]
        measure.relocate(index, lon, lat)
        if measure.error:
            return measure.error + " Recalage annulé."
        return measure.status() if measure.result else self.status()

    # Visionneuse ----------------------------------------------------------------
    def _marks(self, m):
        return m.viewer_marks(self.selected[1] if self.selected and self.selected[0] is m else None)

    def done_marks(self):
        return [self._marks(m) for m in self.done]

    def viewer_marks(self):
        if self.current is None:
            return {"points": [], "label": "", "beside": False}
        return self._marks(self.current)

    def status(self):
        prefix = (self.error + " ") if self.error else ""
        if self.selected is not None:
            measure, index = self.selected
            where = (" : il sera placé dans le plan calé sur la carte" if measure.overrides else
                     " : il sera replacé sur la {}".format(SURFACE_NAMES[measure.surface]))
            return ("Point {} sélectionné{}. Cliquez sa position exacte dans la photo (zoomez pour être précis), "
                    "ou « Effacer » pour annuler.").format(index + 1, where)
        m = self.current
        if m is not None and m.result:
            return m.status()
        n = len(m.clicks) if m is not None else 0
        if m is not None and m.error:
            prefix = m.error + " "
        hints = {
            "road": ("Cliquez au pied du premier bord (bordure, marquage, limite de chaussée…).",
                     "Cliquez au pied du bord opposé, pas forcément juste en face : la largeur est prise "
                     "perpendiculairement à la route."),
            "width": ("Cliquez au sol à une extrémité de l'objet.",
                      "Cliquez au sol à l'autre extrémité : la distance directe entre les deux points est mesurée."),
            "height": ("Cliquez dans la photo au pied de l'objet, exactement au contact du sol.",
                       "Cliquez au sommet de l'objet, sur la même photo."),
        }[self.mode]
        return prefix + hints[min(n, 1)]
