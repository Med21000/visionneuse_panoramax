# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Cédric COCHART
# SPDX-License-Identifier: GPL-2.0-or-later
"""Mesures entre deux points 3D : préréglages (largeurs, hauteur) et mesure libre.

Toutes reposent sur le même moteur (geometry.py) : chaque clic devient un point 3D
sur une surface, puis la mesure est une grandeur calculée sur ces points.

- "road" : largeur perpendiculaire à la chaussée (deux points au sol, composante
  en travers de l'axe de la route) ;
- "width" : largeur parallèle à la route (distance 3D entre deux points au sol) ;
- "height" : hauteur d'un objet (pied au sol, sommet dans le plan vertical face à
  la caméra, composante verticale) ;
- "free" : mesure libre, sur la surface choisie (sol, façade, plan vertical face à
  la caméra, plan horizontal, triangulation 3D), distance 3D.

Un point peut être recalé après coup sur la carte : sa position horizontale vient
alors de la carte, et son altitude du rayon de visée de la photo à cette distance.
La mesure est recalculée ; pour une hauteur, elle ne dépend plus ni du terrain ni
de la hauteur de caméra.
"""

import math

from . import geometry, ground, triangulation
from .calibration import MAP_ERROR
from .measure import DEFAULT_CAMERA_HEIGHT, MeasureItem, _num

FREE_SURFACES = (
    ("Sol", "ground"),
    ("Façade (plan vertical)", "facade"),
    ("Plan vertical face à la caméra", "vertical"),
    ("Plan horizontal", "horizontal"),
    ("Triangulation 3D (deux photos)", "tri3d"),
)
SURFACE_NAMES = {key: label.lower() for label, key in FREE_SURFACES}
PRESET_SURFACES = {"road": "ground", "width": "ground", "height": "vertical"}  # surface des préréglages
PLANE_HEIGHT_ERROR = 0.05  # incertitude de la hauteur saisie du plan horizontal (m)
SELECT_ANGLE = 1.0  # écart maximal (degrés) entre un clic et le repère d'un point pour le sélectionner
ERRORS = (geometry.GeometryError, ground.GroundError)


def _ground_z(clicks):
    """Altitude du sol sous chaque photo : celle du premier clic fait sur la photo, la même
    ensuite pour tous ses clics (le profil d'un clic dépend de la direction visée)."""
    out = {}
    for c in clicks:
        out.setdefault(c.get("pic"), (c.get("profile") or ground.flat_profile())[0][1])
    return out


class Facade:
    """Façade : plan vertical passant par deux points (lon, lat, altitude) au pied du mur.

    Partagée par les mesures prises dessus : la recaler sur la carte les recalcule
    toutes. source : les deux clics qui l'ont définie dans la photo, None une fois
    qu'une extrémité a été recalée sur la carte (son incertitude est alors celle de
    la carte).
    """

    def __init__(self, points, source=None):
        self.points = list(points)
        self.source = source


class Measure:
    """Une mesure entre deux points 3D (voir le module)."""

    def __init__(self, tool, kind, surface):
        self.tool = tool
        self.kind, self.surface = kind, surface
        self.facade = tool.facade  # Facade partagée, ou None
        self.plane_height = tool.plane_height
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

    def needed(self):
        return 4 if self.surface == "tri3d" else 2

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

    def _click_index(self, index, pic=None):
        """Clic d'où vient le point n° index (en triangulation 3D, celui de la photo pic)."""
        if self.surface != "tri3d":
            return index
        pair = [2 * index, 2 * index + 1]
        if pic is None:
            return pair[0]
        for i in pair:
            if self.clicks[i].get("pic") == pic:
                return i
        return None

    def relocate(self, index, lon, lat):
        """Recale le point n° index à la position (lon, lat) cliquée sur la carte. Son plan
        vertical est orienté face à la photo qui l'a visé."""
        c = self.clicks[self._click_index(index)]
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
        i = self._click_index(index, click.get("pic"))
        if i is None:
            self.error = "Visez ce point depuis l'une des photos qui l'ont visé."
            return
        state = list(self.clicks), set(self.aimed)
        self.clicks[i] = click
        if self.overrides:
            self.aimed.add(index)
        self.compute()
        if self.error:
            error = self.error
            self.clicks, self.aimed = state
            self.compute()
            self.error = error

    # Calcul -------------------------------------------------------------------
    def points(self, clicks=None, facade=None, plane_height=None, overrides=None):
        """Points 3D (repère local centré sur le premier clic) des clics complets,
        angle d'incidence le plus rasant sur un plan, origine du repère, et pour
        chaque point le clic dont il vient."""
        clicks = self.clicks if clicks is None else clicks
        if facade is None and self.facade is not None:
            facade = self.facade.points
        overrides = self.overrides if overrides is None else overrides
        plane_height = self.plane_height if plane_height is None else plane_height
        if not clicks:
            return [], None, None, []
        origin = (clicks[0]["lon"], clicks[0]["lat"])
        h = self.tool.camera_for
        ground_z = _ground_z(clicks)

        def zof(click):
            return ground_z[click.get("pic")]

        # Plan calé de la mesure, défini par les points glissés sur la carte : vertical, passant
        # par les deux premiers (le mur tracé sur la carte), ou par le seul glissé, face à la
        # photo qui l'avait visé
        anchors = {k: triangulation._local(v[0], v[1], origin[0], origin[1]) + (v[2],)
                   for k, v in overrides.items()}
        plane = None
        if len(anchors) >= 2:
            (k1, a1), (k2, a2) = sorted(anchors.items())[:2]
            if math.hypot(a2[0] - a1[0], a2[1] - a1[1]) >= 0.2:
                plane = geometry.vertical_plane((a1[0], a1[1], 0.0), (a2[0], a2[1], 0.0))
        if plane is None and anchors:
            x, y, azimuth = sorted(anchors.items())[0][1]
            a = math.radians(azimuth)
            plane = ((x, y, 0.0), (math.sin(a), math.cos(a), 0.0))

        def fixed(k, p, click):
            """Point k visé à nouveau après un recalage : intersection du rayon et du plan calé.
            Recalé sur la carte : à la position de la carte, à l'altitude du rayon de visée."""
            if k in self.aimed and plane is not None:
                return geometry.on_plane(click, h(click), origin, *plane, ground_z=zof(click))[0]
            if k not in anchors:
                return p
            x, y, _ = anchors[k]
            cam, u = geometry.camera(click, h(click), origin, zof(click))
            reach = math.hypot(x - cam[0], y - cam[1])
            return (x, y, cam[2] + reach * u[2] / max(math.hypot(u[0], u[1]), 1e-9))

        pts, sources, incidence = [], [], None
        if self.surface == "ground":
            for c in clicks:
                pts.append(fixed(len(pts), geometry.on_ground(c, h(c), origin)[0], c))
                sources.append(c)
        elif self.surface == "tri3d":
            for k in range(0, len(clicks) - 1, 2):
                pair = clicks[k:k + 2]
                if pair[0].get("pic") == pair[1].get("pic"):
                    raise geometry.GeometryError("Changez de photo pour viser le même point sous un autre angle.")
                p = geometry.triangulate([geometry.camera(c, h(c), origin, zof(c)) for c in pair])[0]
                pts.append(fixed(len(pts), p, pair[0]))
                sources.append(pair[0])
        else:
            planar = clicks
            if self.surface == "facade":
                if not facade:
                    raise geometry.GeometryError("Façade non définie : définissez-la dans la photo ou sur la carte.")
                plane = geometry.vertical_plane(*(geometry.to_local(*p, origin) for p in facade))
            elif self.surface == "vertical":
                first = fixed(0, geometry.on_ground(clicks[0], h(clicks[0]), origin)[0], clicks[0])
                cam = geometry.camera(clicks[0], h(clicks[0]), origin, zof(clicks[0]))[0]
                plane = geometry.facing_plane(cam, first)
                planar, pts, sources = clicks[1:], [first], [clicks[0]]
            else:  # horizontal
                plane = ((0.0, 0.0, zof(clicks[0]) + plane_height), (0.0, 0.0, 1.0))
            for c in planar:
                p, angle = geometry.on_plane(c, h(c), origin, *plane, ground_z=zof(c))
                pts.append(fixed(len(pts), p, c))
                sources.append(c)
                incidence = angle if incidence is None else min(incidence, angle)
        return pts, incidence, origin, sources

    def quantity(self, d):
        """Grandeur mesurée d'après la décomposition d (voir geometry.decompose)."""
        if self.kind == "road":
            if d["across"] is None:
                raise geometry.GeometryError("Axe de la route inconnu pour cette photo : largeur impossible "
                                             "à calculer.")
            return d["across"]
        if self.kind == "height" or (self.kind == "free" and self.surface == "vertical"):
            if self.kind == "height" and d["vertical"] <= 0:
                raise geometry.GeometryError("Le sommet est sous le pied : cliquez d'abord au pied, puis au "
                                             "sommet.")
            return abs(d["vertical"])
        return d["d3"]

    def _value(self, **variant):
        pts, _, _, _ = self.points(**variant)
        return self.quantity(geometry.decompose(pts[0], pts[1], self.clicks[0].get("axis")))

    def _spread(self):
        """Écart maximal de la mesure quand varient : l'élévation de chaque clic (±PITCH_ERROR),
        le cap en triangulation 3D (erreur de visée), la position d'un point recalé sur la
        carte (±MAP_ERROR), les clics de définition de la façade, la hauteur du plan."""
        ref = self._value()
        variants = []
        for i, c in enumerate(self.clicks):
            moves = [("elev", ground.PITCH_ERROR)]
            if self.surface == "tri3d":
                moves.append(("yaw", triangulation.heading_error(c)))
            for key, step in moves:
                for sign in (-1, 1):
                    moved = [dict(x) for x in self.clicks]
                    moved[i][key] += sign * step
                    variants.append({"clicks": moved})
        for k, (lon, lat, azimuth) in self.overrides.items():
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                moved = dict(self.overrides)
                moved[k] = triangulation._geographic(dx * MAP_ERROR, dy * MAP_ERROR, lon, lat) + (azimuth,)
                variants.append({"overrides": moved})
        if self.surface == "horizontal":
            variants += [{"plane_height": self.plane_height + s * PLANE_HEIGHT_ERROR} for s in (-1, 1)]
        source = self.facade.source if self.facade is not None else None
        if self.surface == "facade" and self.facade is not None and not source:
            for i in range(2):  # façade recalée sur la carte : chaque extrémité à ±MAP_ERROR
                lon, lat, z = self.facade.points[i]
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    moved = list(self.facade.points)
                    moved[i] = triangulation._geographic(dx * MAP_ERROR, dy * MAP_ERROR, lon, lat) + (z,)
                    variants.append({"facade": moved})
        if self.surface == "facade" and source:
            for i in range(2):
                for sign in (-1, 1):
                    moved = [dict(x) for x in source]
                    moved[i]["elev"] += sign * ground.PITCH_ERROR
                    try:
                        variants.append({"facade": self.tool.facade_from(moved)})
                    except ERRORS:
                        return float("inf")
        worst = 0.0
        for variant in variants:
            try:
                worst = max(worst, abs(self._value(**variant) - ref))
            except ERRORS:
                return float("inf")
        return worst

    def compute(self):
        self.result, self.error, self.incidence = None, None, None
        try:
            if self.kind == "height" and len(self.clicks) == 2 and self.clicks[0].get("pic") != self.clicks[1].get("pic"):
                raise geometry.GeometryError("Le pied et le sommet doivent être cliqués sur la même photo.")
            pts, self.incidence, origin, sources = self.points()
            if len(pts) == 2 and len(self.clicks) == self.needed():
                d = geometry.decompose(pts[0], pts[1], self.clicks[0].get("axis"))
                value = self.quantity(d)
                lonlat = [geometry.to_geographic(p, origin)[:2] for p in pts]
                ranges = []
                for p, c in zip(pts, sources):
                    cx, cy = triangulation._local(c["lon"], c["lat"], origin[0], origin[1])
                    ranges.append(math.hypot(p[0] - cx, p[1] - cy))
                self.result = {"value": value, "decomposition": d, "uncertainty": self._spread(),
                               "ranges": ranges, "points": lonlat, "extra": None, "picks": lonlat,
                               "shifts": self._shifts(pts, origin)}
                if self.kind == "road":  # pied de la perpendiculaire, en face du premier bord
                    a = math.radians(self.clicks[0]["axis"])
                    vx, vy = pts[1][0] - pts[0][0], pts[1][1] - pts[0][1]
                    along = vx * math.sin(a) + vy * math.cos(a)
                    foot = (pts[1][0] - along * math.sin(a), pts[1][1] - along * math.cos(a), pts[0][2])
                    self.result["points"] = [lonlat[0], geometry.to_geographic(foot, origin)[:2]]
                    self.result["extra"] = lonlat[1]
                elif self.kind == "height":
                    self.result["points"] = [lonlat[0]]  # sommet au-dessus du pied
        except ERRORS as exc:
            self.error = str(exc)
        self._draw()

    def _shifts(self, pts, origin):
        """Pour chaque clic, écart (cap, élévation en degrés) entre la direction cliquée et
        celle où la caméra voit le point 3D qui en résulte : nul pour un point resté sur sa
        visée, non nul pour un point recalé de côté sur la carte (ou triangulé)."""
        ground_z = _ground_z(self.clicks)
        out = []
        for i, c in enumerate(self.clicks):
            p = pts[i // 2] if self.surface == "tri3d" else pts[i]
            cam, _ = geometry.camera(c, self.tool.camera_for(c), origin, ground_z[c.get("pic")])
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

    # Affichage ----------------------------------------------------------------
    def label(self):
        if not self.result:
            return ""
        symbol = "↕ " if self._vertical() else "↔ "
        return "{}{} m ± {} m".format(symbol, _num(self.result["value"], 2), _num(self.result["uncertainty"], 2))

    def _vertical(self):
        if self.kind in ("road", "width"):
            return False
        if self.kind == "height" or self.surface == "vertical":
            return True
        d = self.result["decomposition"] if self.result else None
        return bool(d) and abs(d["vertical"]) > d["horizontal"]

    def _draw(self):
        if self.result:
            points, extra = self.result["points"], self.result["extra"]
            label = "{} m".format(_num(self.result["value"], 2))
        else:
            points, extra, label = [p[1:] for p in self.picks()], None, ""
        if self.item is None and points:
            self.item = MeasureItem(self.tool.canvas)
        if self.item is not None:
            self.item.set_data(points, label, extra)

    def remove(self):
        if self.item is not None:
            self.item.remove()
            self.item = None

    def point_of_click(self, i):
        """N° du point auquel contribue le clic n° i."""
        return i // 2 if self.surface == "tri3d" else i

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
                value, unc, _num(d["horizontal"], 2), _num(self.clicks[0]["axis"], 0),
                self.clicks[0].get("axis_source") or "?")
        elif self.kind == "width":
            text = "Largeur : {} m (±{} m) entre les deux points · à {} et {} m de la photo".format(
                value, unc, _num(r["ranges"][0]), _num(r["ranges"][1]))
            if abs(d["vertical"]) >= 0.05:
                text += " · dénivelé {} m".format(_num(d["vertical"], 2))
        elif self.kind == "height":
            text = "Hauteur : {} m (±{} m) · objet à {} m".format(value, unc, _num(r["ranges"][0]))
        else:
            if self.surface == "vertical":
                text = "Hauteur : {} m (±{} m) · distance 3D {} · horizontale {}".format(
                    value, unc, _num(d["d3"], 2), _num(d["horizontal"], 2))
            else:
                text = "Distance 3D : {} m (±{} m) · horizontale {} · verticale {}".format(
                    value, unc, _num(d["horizontal"], 2), _num(d["vertical"], 2))
            if d["along"] is not None:
                text += " · le long de la route {} · en travers {}".format(_num(d["along"], 2), _num(d["across"], 2))
            text += " · surface : {}".format(SURFACE_NAMES[self.surface])
        if self.overrides:
            text += " · {} point{} recalé{} sur la carte".format(
                len(self.overrides), *(("s", "s") if len(self.overrides) > 1 else ("", "")))
            if self.aimed:
                text += ", dont {} visé{} à nouveau dans son plan".format(
                    len(self.aimed), "s" if len(self.aimed) > 1 else "")
        if self.surface == "horizontal":
            text += " · plan à {} m sous la caméra, incidence {}°".format(
                _num(self.tool.camera_for(self.clicks[0]) - self.plane_height, 2), _num(self.incidence or 0, 0))
        if self.incidence is not None and self.incidence < geometry.GRAZING:
            text += ". Visée rasante sur le plan ({}°) : mesure imprécise, prenez une photo plus en face".format(
                _num(self.incidence, 0))
        labels = {c.get("terrain") for c in self.clicks if c.get("terrain")}
        height, calibrated = self.tool.camera_info(self.clicks[0])
        text += ". Terrain : {}, caméra à {} m{}.".format(" + ".join(sorted(labels)) or ground.FLAT,
                                                        _num(height, 2), " (calée)" if calibrated else "")
        if len(labels) > 1:
            text += " Altitudes de sources différentes : la composante verticale peut être faussée."
        if self.notice:
            text += " ({}.)".format(self.notice)
        return text + " Point mal placé : glissez-le sur la carte."


class MeasureTool:
    """Mesure en cours, mesures terminées gardées à l'écran, façade et réglages."""

    def __init__(self, canvas):
        self.canvas = canvas
        self.mode = "road"  # préréglage, ou "free"
        self.surface = "ground"  # surface de la mesure libre
        self.camera_height = DEFAULT_CAMERA_HEIGHT  # hauteur saisie dans le panneau
        self.calibration = None  # calibration.Calibration : hauteur calée par séquence
        self.plane_height = 1.0  # hauteur du plan horizontal au-dessus du sol (m)
        self.facade = None  # Facade en cours (voir Facade)
        self.defining = False  # les prochains clics dans la photo définissent la façade
        self.facade_clicks = []
        self.facade_item = None
        self.error = None
        self.current = None
        self.done = []  # mesures terminées, gardées à l'écran jusqu'à clear_all
        self.selected = None  # (mesure, n° de point) à viser à nouveau dans la visionneuse

    # Réglages ---------------------------------------------------------------
    def set_mode(self, mode):
        if mode != self.mode:
            self.finish()
            self.mode = mode
            self.defining = self._surface() == "facade" and self.facade is None
            self._draw_facade()

    def set_surface(self, surface):
        if surface != self.surface:
            self.finish()
            self.surface = surface
            self.defining = self._surface() == "facade" and self.facade is None
            self._draw_facade()

    def _surface(self):
        return PRESET_SURFACES.get(self.mode, self.surface)

    def set_camera_height(self, value):
        self.camera_height = float(value)
        if self.current is not None:
            self.current.compute()

    def set_plane_height(self, value):
        self.plane_height = float(value)
        if self.current is not None and self.current.surface == "horizontal":
            self.current.plane_height = self.plane_height
            self.current.compute()

    def camera_info(self, click):
        """(hauteur de caméra du clic, calée ?) : calée pour sa séquence, sinon celle du panneau."""
        sequence = click.get("sequence")
        cam = self.calibration.camera(sequence) if self.calibration and sequence else None
        return (cam["height"], True) if cam else (self.camera_height, False)

    def camera_for(self, click):
        return self.camera_info(click)[0]

    def needs_profile(self):
        return True  # altitude du sol sous la caméra, et du sol visé

    # Façade -------------------------------------------------------------------
    def start_facade(self):
        """Les deux prochains clics dans la photo, au pied du mur, définissent la façade."""
        self.finish()
        self.defining, self.facade_clicks, self.error = True, [], None

    def facade_from(self, clicks):
        """Façade (deux points lon, lat, altitude) à partir de deux clics au pied du mur."""
        origin = (clicks[0]["lon"], clicks[0]["lat"])
        a, b = (geometry.on_ground(c, self.camera_for(c), origin)[0] for c in clicks)
        geometry.vertical_plane(a, b)  # contrôle
        return [geometry.to_geographic(a, origin), geometry.to_geographic(b, origin)]

    def move_facade(self, index, lon, lat):
        """Recale l'extrémité n° index de la façade sur la carte, et recalcule les mesures
        prises dessus. Retourne un message d'erreur, ou None."""
        facade = self.facade
        old = list(facade.points), facade.source
        facade.points[index] = (lon, lat, facade.points[index][2])
        facade.source = None
        try:
            origin = facade.points[0][:2]
            geometry.vertical_plane(*(geometry.to_local(*p, origin) for p in facade.points))
        except geometry.GeometryError as exc:
            facade.points, facade.source = old
            return str(exc)
        for m in self.measures():
            if m.facade is facade:
                m.compute()
        self._draw_facade()
        return None

    def _draw_facade(self):
        if self.facade and self._surface() == "facade":
            if self.facade_item is None:
                self.facade_item = MeasureItem(self.canvas)
            self.facade_item.set_data([p[:2] for p in self.facade.points], "façade")
        elif self.facade_item is not None:
            self.facade_item.set_data([], "")

    # Clics --------------------------------------------------------------------
    def select_near(self, click):
        """Clic sur le repère d'un point existant (à moins de SELECT_ANGLE degrés), entre deux
        mesures : le sélectionne pour le viser à nouveau. Retourne True si le clic a servi à
        cela. Un point déjà sélectionné : le clic est sa nouvelle visée, même tout près de son
        repère (une correction fine l'est toujours) ; on annule avec cancel_selection."""
        if self.selected is not None or self.defining:
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
        if self.defining:
            self.facade_clicks.append(click)
            if len(self.facade_clicks) == 2:
                clicks, self.facade_clicks = self.facade_clicks, []
                try:
                    self.facade = Facade(self.facade_from(clicks), clicks)
                    self.defining = False
                except ERRORS as exc:
                    self.error = str(exc)
                self._draw_facade()
            return self.status()
        if self.current is None or len(self.current.clicks) >= self.current.needed():
            self.finish()  # nouvelle mesure, la précédente reste affichée
            self.current = Measure(self, self.mode, self._surface())
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
        self.current, self.facade_clicks, self.selected = None, [], None

    def clear(self):
        """Efface la mesure en cours (les mesures terminées restent affichées)."""
        if self.current is not None:
            self.current.remove()
        self.current, self.facade_clicks, self.error, self.selected = None, [], None, None

    def clear_all(self):
        """Efface aussi les mesures terminées et la façade."""
        self.clear()
        for m in self.done:
            m.remove()
        self.done = []
        self.facade = None
        self.defining = self._surface() == "facade"
        self._draw_facade()

    def remove(self):
        self.clear()
        for m in self.done:
            m.remove()
        self.done = []
        if self.facade_item is not None:
            self.facade_item.remove()
            self.facade_item = None

    def measures(self):
        """Toutes les mesures affichées (terminées, puis en cours)."""
        return self.done + ([self.current] if self.current is not None else [])

    # Recalage sur la carte ----------------------------------------------------
    def handles(self):
        """Poignées déplaçables sur la carte : [(clé, lon, lat)], clé ("point", mesure,
        n° de point) ou ("facade", n° d'extrémité)."""
        out = [(("point", m, k), lon, lat) for m in self.measures() for k, lon, lat in m.picks()]
        if self.facade is not None and self._surface() == "facade":
            out += [(("facade", i), p[0], p[1]) for i, p in enumerate(self.facade.points)]
        return out

    def move_handle(self, key, lon, lat):
        """Recale une poignée à (lon, lat). Retourne le texte à afficher dans le panneau."""
        if key[0] == "facade":
            error = self.move_facade(key[1], lon, lat)
            if error:
                return error + " Recalage annulé."
            measure = self.current or (self.done[-1] if self.done else None)
            text = "Façade recalée sur la carte."
            return text + (" " + measure.status() if measure is not None and measure.result else "")
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
        if self.defining:
            points = []
            for c in self.facade_clicks:
                pos = c.get("pos") or [None, None]
                raw = c.get("raw") or [c["yaw"], c["elev"]]
                points.append({"pic": c.get("pic"), "yaw": pos[0], "pitch": pos[1], "abs_yaw": raw[0],
                               "elev": raw[1]})
            return {"points": points, "label": "", "beside": False}
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
        if self.defining:
            if not self.facade_clicks:
                return prefix + ("Façade : cliquez au pied du mur dans la photo, à une extrémité. Ses extrémités "
                                 "se recalent ensuite en les glissant sur la carte, au bord du bâtiment.")
            return "Façade : cliquez au pied du mur, à l'autre extrémité."
        m = self.current
        if m is not None and m.result:
            return m.status()
        n = len(m.clicks) if m is not None else 0
        if m is not None and m.error:
            prefix = m.error + " "
        surface = m.surface if m is not None else self._surface()
        if surface == "tri3d":
            steps = ("Point 1 : cliquez-le sur cette photo.",
                     "Point 1 : changez de photo et cliquez le même point, sous un autre angle.",
                     "Point 2 : cliquez-le (sur cette photo ou une autre).",
                     "Point 2 : changez de photo et cliquez le même point, sous un autre angle.")
            return prefix + steps[min(n, 3)]
        hints = {
            "road": ("Cliquez au pied du premier bord (bordure, marquage, limite de chaussée…).",
                     "Cliquez au pied du bord opposé, pas forcément juste en face : la largeur est prise "
                     "perpendiculairement à la route."),
            "width": ("Cliquez au sol à une extrémité de l'objet.",
                      "Cliquez au sol à l'autre extrémité : la distance directe entre les deux points est mesurée."),
            "height": ("Cliquez dans la photo au pied de l'objet, exactement au contact du sol.",
                       "Cliquez au sommet de l'objet, sur la même photo."),
        }.get(self.mode) or {
            "ground": ("Cliquez le premier point, au sol.", "Cliquez le second point, au sol."),
            "facade": ("Cliquez le premier point sur la façade.", "Cliquez le second point sur la façade."),
            "vertical": ("Cliquez le premier point, au sol (pied de l'objet).",
                         "Cliquez le second point, dans le plan vertical face à la caméra qui passe par le premier "
                         "(sommet, angle…)."),
            "horizontal": ("Cliquez le premier point sur le plan horizontal à {} m du sol.".format(
                _num(self.plane_height, 2)), "Cliquez le second point sur le même plan."),
        }[surface]
        return prefix + hints[min(n, 1)]
