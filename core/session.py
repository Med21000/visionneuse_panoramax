# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Cédric COCHART
# SPDX-License-Identifier: GPL-2.0-or-later
"""Session de mesure, indépendante de l'affichage : ce que l'application hôte (plugin QGIS,
application web) relie à sa visionneuse, à sa carte et à son panneau.

- points de référence et mesure libre : visées cliquées dans les photos, triangulées
  (tools.Triangulator) ;
- hauteur : sommet (et pied) cliqués dans la photo → mesure (measures.MeasureTool) calée sur
  le point de référence (Measure.anchor) ; largeur : bord opposé sur la droite perpendiculaire
  à la route (widths.PerpendicularWidth) ;
- recalage d'un point (clic sur sa croix, nouvelle visée, flèches du clavier), consignes,
  repères de la visionneuse (photo affichée, projetés sur les autres photos), points à cadrer
  sur la carte ;
- calages : inclinaison, cap et position (tools.CalibrationTool) ;
- photo affichée : précision GPS vérifiée (precision.PrecisionChecker), inclinaison
  automatique (autotilt.TiltChecker), axe de la séquence entre les photos voisines.

L'hôte appelle les méthodes de Session (clics en radians dans le repère de la photo, comme
les donnent les visionneuses) ; elles renvoient le texte à afficher, ou des données Python
simples (listes, dictionnaires). Quand une lecture réseau ou un calcul aboutit après coup,
Session appelle notify() pour que l'hôte se redessine. Le dessin sur la carte passe par les
new_item des outils, que l'hôte remplace (ou par ses propres sous-classes des outils, passées
au constructeur). Module sans dépendance à QGIS.
"""

import math
import traceback

from . import autotilt, calibration, geometry, host, measures, metadata, precision, roads, tools, triangulation, widths

GROUND_MODES = ("road", "height")  # mesures appuyées sur un point de référence
CALIBRATION_MODES = ("tilt", "heading")
MODES = GROUND_MODES + ("tri",) + CALIBRATION_MODES
REF_MODES = ("height", "road")  # mesures appuyées sur un point de référence triangulé
NUDGE = 0.01  # pas (m) d'un point de largeur déplacé aux flèches du clavier
VERTICAL_LENGTH = 12.0  # m : verticale tracée au-dessus du point de référence d'une hauteur
NUDGE_WORDS = {"left": "vers la gauche", "right": "vers la droite", "up": "en s'éloignant",
               "down": "en se rapprochant"}
# Triangulation et mesures qui s'y appuient : acceptées hors RTK, mais signalées
RTK_WARNING = (" ⚠ Photo sans position GPS RTK vérifiée : mesure probablement incohérente "
               "(la triangulation demande des positions précises).")
# Fenêtre d'avertissement, une fois par séquence sans RTK vérifié (voir Session.take_rtk_alert)
RTK_ALERT = ("La localisation des images de cette séquence est trop imprécise pour fonctionner. "
             "Un GPS avec corrections RTK est requis.")


class Session:
    def __init__(self, notify=None, tool=None, triangulator=None, calibrator=None, calibration_store=None,
                 precision_checker=None, tilt_checker=None):
        """notify : fonction de l'hôte, appelée quand une lecture réseau ou un calcul aboutit
        après coup. tool, triangulator, calibrator : outils de l'hôte (sous-classes de
        measures.MeasureTool, tools.Triangulator, tools.CalibrationTool qui dessinent sur sa
        carte), sinon ceux du cœur ; calibration_store : calage de l'hôte (calibration.Calibration) ;
        precision_checker, tilt_checker : vérificateurs de l'hôte, partagés pour ne pas refaire ses
        requêtes (precision.PrecisionChecker, autotilt.TiltChecker)."""
        self.notify = notify or (lambda: None)
        self.calibration = calibration_store if calibration_store is not None else calibration.Calibration()
        self.roads = roads.RoadAxisProvider()
        self.precision = precision_checker if precision_checker is not None else precision.PrecisionChecker()
        self.tilts = tilt_checker if tilt_checker is not None else autotilt.TiltChecker()
        self.roads.ready = self._roads_ready
        self.tool = tool if tool is not None else measures.MeasureTool(None)
        self.tool.calibration = self.calibration
        self.tool.road_lookup = self.roads.lookup
        self.triangulator = triangulator if triangulator is not None else tools.Triangulator()
        self.triangulator.AIM = "Cliquez sur l'objet dans la photo"
        self.triangulator.AIM_AGAIN = "cliquez sur le même objet"
        self.calibrator = (calibrator if calibrator is not None
                           else tools.CalibrationTool(self.calibration, self.current_ids))
        self.mode = "road"
        self.tool.set_mode("road")
        self.item = None  # fiche STAC de la photo affichée
        self.quality = None  # vérification de sa précision (voir precision.PrecisionChecker)
        self.neighbours = {}  # fiches des photos voisines (axe de la séquence) : id -> fiche
        self.message = ""
        self.points = []  # points triangulés gardés : (lon, lat, attributs)
        self.links = []  # distances entre deux points gardés : (n° du premier, n° du second, attributs)
        self.named = 0  # points gardés depuis le début : nom du suivant (A, B… voir _point_name)
        self.proposal = None  # (n°, n°) : distance à proposer après l'effacement du point qui les reliait
        # Hauteur, largeur : point de référence triangulé (lon, lat, uncertainty, name, sequences ;
        # largeur : road, axe de la route en ce point, None en cours de lecture), validé : les
        # mesures suivantes s'y appuient, depuis n'importe quelle photo, jusqu'au prochain
        # (new_reference) ; None : à trianguler d'abord
        self.ref_point = None
        # largeur : (mesure, 0 pour le point de référence ou 1 pour le bord) à recaler ; (None, 0) :
        # point de référence sur lequel aucune largeur ne s'appuie encore
        self.width_sel = None
        # Hauteur : point de référence sélectionné (aucune hauteur ne s'y appuie encore), à recaler
        # sur sa verticale
        self.ref_sel = False
        # Largeur ou hauteur faite depuis le point de référence : seuls ses points s'ajustent (sur la
        # droite de mesure ou la verticale) jusqu'à « Nouvelle mesure depuis A » (new_measure)
        self.measure_done = False
        self.last_free = None  # mesure libre : n° du dernier point gardé, relié au suivant
        self.point_views = {}  # nom d'un point gardé -> {photo : [cap, élévation] de sa visée (radians)}
        self._handles = []  # clés des poignées de la carte, dans l'ordre de handles()
        self.calibration_version = 0  # augmente à chaque changement du calage (à enregistrer)
        self.active = False  # bouton « Mesure » : clics dans la photo pour mesurer
        self.rtk_alerted = set()  # séquences sans RTK déjà signalées par une fenêtre (take_rtk_alert)
        self.epoch = 0  # change quand les clics en cours sont abandonnés

    def current_ids(self):
        item = self.item or {}
        return item.get("id"), item.get("collection")

    def _active(self):
        """Outil du mode : MeasureTool, Triangulator ou CalibrationTool."""
        if self._aiming():
            return self.triangulator
        if self.mode in CALIBRATION_MODES:
            if self.calibrator.mode != self.mode:
                self.calibrator.set_mode(self.mode)
            return self.calibrator
        self.tool.set_mode(self.mode)
        return self.tool

    def _aiming(self):
        """Les clics dans la photo sont-ils des visées de triangulation ? En triangulation, et
        en hauteur ou largeur tant que le point de référence n'est pas validé."""
        return self.mode == "tri" or (self.mode in REF_MODES and self.ref_point is None)

    def _calibration_changed(self):
        self.calibration_version += 1

    # Réglages ------------------------------------------------------------------------
    def set_active(self, on):
        """Bouton « Mesure » : activé, les clics dans la photo mesurent et l'inclinaison de la
        photo est calculée ; désactivé, les mesures en cours sont terminées (les mesures réussies
        restent affichées), comme dans le plugin. Les visées de la triangulation restent : on
        peut désactiver la mesure pour aller viser depuis une autre photo."""
        self.active = bool(on)
        self.message = ""
        if self.active:
            self._check_tilt()
        else:
            self._clear_measures(keep_sightings=True)
        return self.status()

    def _check_tilt(self):
        """Inclinaison automatique de la photo affichée, en mesure ou en calage seulement : la
        triangulation ne vise qu'en cap, elle n'en a pas besoin (calcul long évité)."""
        if self.active and self.item and self.mode != "tri":
            pic = self.item.get("id")
            before = self.calibration.auto.get(pic)
            self.tilts.check(self.item, self.calibration, lambda: self._on_tilt(pic, before))

    def set_mode(self, mode):
        if mode not in MODES:
            return self.status()
        self._clear_measures()
        self.mode = mode
        self.ref_point = None
        self.width_sel = None
        self.measure_done = False
        self.ref_sel = False
        self.message = ""
        self._active()
        self._check_tilt()  # en quittant la triangulation
        return self.status()

    def _clear_measures(self, keep_sightings=False):
        """Termine les mesures en cours (réussies, elles restent affichées) ; le calage déjà
        fait est conservé (comme le plugin à un changement de mode)."""
        self.epoch += 1
        if not keep_sightings:
            self.triangulator.clear()
        self.tool.finish()
        self.calibrator.reset()

    def set_camera_height(self, value):
        self.tool.set_camera_height(float(value))
        return self.status()

    def set_api_url(self, url):
        """Instance Panoramax choisie : recherches du cœur (précision GPS, trajectoire)."""
        host.api_url = url
        self.neighbours = {}

    # Photo affichée ------------------------------------------------------------------
    def set_picture(self, item):
        """Photo affichée dans la visionneuse : sa fiche STAC."""
        self.item = item
        pic = item.get("id")
        self.quality = self.precision.known(item)
        self.precision.check(item, lambda res: self._on_precision(pic, res))
        self.calibrator.refresh()  # carte : visées des repères de calage pris depuis cette photo
        for rel in ("prev", "next"):  # voisines : axe de la séquence
            nid = metadata.id_from_href(metadata.item_link(item, rel))
            if nid and nid not in self.neighbours:
                host.fetch_json("{}/search?ids={}".format(host.api_url, nid),
                                    lambda data, error, nid=nid: self._on_neighbour(nid, data))
        self._check_tilt()
        return self.status()

    def _on_neighbour(self, nid, data):
        features = (data or {}).get("features") or []
        if features:
            self.neighbours[nid] = features[0]
            if len(self.neighbours) > 200:
                self.neighbours.pop(next(iter(self.neighbours)))

    def _axis(self, item):
        def neighbour(rel):
            return self.neighbours.get(metadata.id_from_href(metadata.item_link(item, rel)))
        return metadata.sequence_axis(item, neighbour("prev"), neighbour("next"))

    def _on_precision(self, pic, res):
        if pic != (self.item or {}).get("id"):
            return  # photo déjà quittée
        self.quality = res
        self.notify()

    def _on_tilt(self, pic, before=None):
        """Inclinaison automatique arrivée : clics déjà faits sur la photo corrigés. Rien si elle
        n'a pas changé (check rappelle aussi sans calcul : photo déjà calée, non 360°, sans cap) :
        ni calage réenregistré, ni affichage refait."""
        if self.calibration.auto.get(pic) == before:
            return
        self._calibration_changed()
        cal = self.calibration

        def correct(c):
            gps = c.get("gps") or [None, None]
            return cal.correct(pic, c.get("sequence"), c["raw"][0], c["raw"][1], gps[0], gps[1])

        try:
            self.tool.recorrect(pic, correct)
        except Exception:
            traceback.print_exc()
        self.notify()

    def _heading(self):
        """Cap du centre de l'image (degrés) : EXIF précis, sinon view:azimuth."""
        precise = metadata.precise_heading(self.item)
        return precise if precise is not None else (metadata.item_heading(self.item) or 0.0)

    # Clics -------------------------------------------------------------------------------
    def click(self, yaw, pitch):
        """Clic dans la photo affichée : direction dans le repère de la photo (radians, cap
        relatif au centre de l'image, comme le donne la visionneuse)."""
        if not self.active:
            return self.status()
        if not self.item:
            self.message = "Photo en cours de chargement : cliquez à nouveau."
            return self.status()
        if self._aiming():
            return self.aim(yaw, pitch)
        raw_yaw = (math.degrees(yaw) + self._heading()) % 360
        click = tools.photo_click(self.item, raw_yaw, math.degrees(pitch), self.calibration, self.quality,
                                  [yaw, pitch], self._axis(self.item))
        if click is None:
            self.message = "Position de la photo inconnue."
            return self.status()
        self.message = ""
        tool = self._active()
        if self.mode == "road":
            return self._width_click(click)
        if self.mode == "height" and self.ref_sel:  # point de référence recalé sur sa verticale
            self.ref_sel = False
            dz = self._recale_ref_foot(pitch)
            self.message = ("Pied au point de référence {} recalé de {} cm sur sa verticale, sur cette photo.").format(
                self.ref_point["name"], measures._num(abs(dz) * 100, 0))
            return self.status()
        if self.mode == "height" and tool.selected is not None and \
                click.get("pic") != tool.selected[0].clicks[0].get("pic"):
            self.message = ("Ce point se recale par un clic sur la photo de sa mesure (le pied et le sommet "
                            "y sont visés ensemble) ; ici, flèches ↑ ↓ : 1 cm ; Entrée pour terminer.")
            return self.status()
        if self.mode == "height" and tool.selected is None and (
                tool.current is None or len(tool.current.clicks) >= tool.current.needed()):
            near = self._height_near(click)
            if near is not None:  # clic sur la croix d'un pied ou d'un sommet : sélectionné
                tool.selected = near
                return self.status()
            if self._ref_near(click):  # croix du point de référence seule : sélectionnée
                self.ref_sel = True
                return self.status()
            if self._locked():  # pas d'autre hauteur sans « Nouvelle mesure depuis A »
                self.message = self._locked_text()
                return self.status()
        if self.mode == "height":
            return self._height_click(click)
        tool.add_click(click)  # calage de l'inclinaison ou du cap
        self._after_calibration(tool)
        return self.status()

    def _height_click(self, click):
        """Hauteur, étape 2 : le sommet (le pied est la visée du point de référence faite
        depuis cette photo), sinon le pied puis le sommet, ou la nouvelle visée du point
        sélectionné. Sans profil du terrain : calée sur le point de référence, la hauteur n'en
        dépend pas."""
        tool = self.tool
        reaimed = tool.selected is not None
        if not reaimed and (tool.current is None or len(tool.current.clicks) >= tool.current.needed()):
            foot = self._reference_foot()
            if foot is not None:
                tool.add_click(foot)
        text = tool.add_click(click)
        self.message = text if reaimed else ""  # point recalé : son résultat ; sinon la suite (status)
        self._anchor_height()
        return self.status()

    def select_point(self, yaw, pitch):
        """Clic droit dans la photo : sélectionne le point de hauteur ou de largeur dont la croix
        est sous le clic, depuis n'importe quelle photo, pour le recaler (clic gauche, flèches du
        clavier, Entrée pour terminer)."""
        if not self.active or not self.item or self.mode not in REF_MODES:
            return self.status()
        click = tools.photo_click(self.item, (math.degrees(yaw) + self._heading()) % 360, math.degrees(pitch),
                                  self.calibration, self.quality, [yaw, pitch], self._axis(self.item))
        if click is None:
            return self.status()
        self.message = ""
        if self.mode == "road":
            self.width_sel = self._width_near(click)
            found = self.width_sel is not None
        else:
            self.tool.selected = self._height_near(click)
            self.ref_sel = self.tool.selected is None and self._ref_near(click)
            found = self.tool.selected is not None or self.ref_sel
        if not found:
            self.message = "Aucune croix de mesure sous le clic droit."
        return self.status()

    def _width_click(self, click):
        """Largeur : le bord opposé, sur la droite perpendiculaire passant par le point de
        référence (voir core/widths.py). Chaque clic donne une largeur ; un clic (gauche ou droit,
        voir select_point) sur la croix d'un point le sélectionne, le suivant le recale (voir
        _recale_width)."""
        if self.width_sel is not None:
            return self._recale_width(click)
        near = self._width_near(click)
        if near is not None:  # clic sur la croix d'un point : sélectionné
            self.width_sel = near
            return self.status()
        if self._locked():  # pas d'autre largeur sans « Nouvelle mesure depuis A »
            self.message = self._locked_text()
            return self.status()
        ref = self.ref_point
        if ref.get("road") is None:
            self._road_at_reference()
            if ref.get("road") is None:
                self.message = "Axe de la route en cours de lecture (BD TOPO) : cliquez à nouveau dans un instant."
                return self.status()
        m = widths.PerpendicularWidth(self.tool, ref, ref["road"], click)
        if m.error:
            m.remove()
            self.message = m.error
            return self.status()
        self.tool.done.append(m)
        self.measure_done = True
        self.message = (m.status() + self._rtk_warning(ref["sequences"] + [click.get("sequence")])
                        + " Pour l'ajuster, cliquez une de ses croix. Autre largeur depuis ce point : « Nouvelle mesure "
                          "depuis {} ».".format(ref["name"]))
        return self.status()

    def _locked_text(self):
        name = self.ref_point["name"]
        if self.mode == "height":
            return ("Hauteur mesurée depuis le point {0} : cliquez une de ses croix pour l'ajuster (nouveau clic sur "
                    "la photo de la mesure, ou flèches ↑ ↓ : 1 cm sur la verticale ; Entrée pour terminer). Autre "
                    "hauteur depuis ce point : « Nouvelle mesure depuis {0} » ; autre point : « Nouveau point de "
                    "référence ».").format(name)
        return ("Largeur mesurée depuis le point {0} : cliquez une de ses croix pour l'ajuster (nouveau clic ou "
                "flèches du clavier, 1 cm le long de la droite de mesure ; Entrée pour terminer). Autre largeur "
                "depuis ce point : « Nouvelle mesure depuis {0} » ; autre point : « Nouveau point de "
                "référence ».").format(name)

    def _width_points(self, m):
        """Repères du point de référence et du bord d'une largeur dans la photo affichée :
        [{yaw, pitch (radians, repère de la photo), abs_yaw, elev (degrés)}]. Le point de
        référence, projeté au sol comme la droite de mesure (il est dessus) ; le bord, là où il
        a été cliqué sur sa photo, projeté au sol sur les autres."""
        pic = (self.item or {}).get("id")
        points = [self._project(plon, plat) for plon, plat in m.result["points"]]
        if pic is not None and pic == m.clicks[1].get("pic"):
            p = m.viewer_marks()["points"][1]
            points[1] = {"yaw": p["yaw"], "pitch": p["pitch"], "abs_yaw": p["abs_yaw"], "elev": p["elev"]}
        return points

    def _project(self, plon, plat):
        """Repère dans la photo affichée d'un point de la carte, au sol supposé plat (hauteur de
        caméra du réglage) : {yaw, pitch (radians, repère de la photo), abs_yaw, elev (degrés)}."""
        lon, lat = (float(v) for v in self.item["geometry"]["coordinates"][:2])
        x, y = triangulation._local(plon, plat, lon, lat)
        bearing = math.degrees(math.atan2(x, y))
        elev = -math.degrees(math.atan2(self.tool.camera_height, max(math.hypot(x, y), 0.5)))
        return {"yaw": math.radians((bearing - self._heading() + 540) % 360 - 180), "pitch": math.radians(elev),
                "abs_yaw": bearing % 360, "elev": elev}

    def _free_mark_point(self, k):
        """Repère du point gardé n° k dans la photo affichée : là où il a été visé depuis elle,
        sinon projeté au sol."""
        lon, lat, values = self.points[k]
        view = self.point_views.get(values["nom"], {}).get(self.item.get("id"))
        point = {"yaw": view[0], "pitch": view[1]} if view else self._project(lon, lat)
        return dict(point, pic=self.item.get("id"))

    def _free_marks(self):
        """Mesure libre : distances (trait et étiquette) et points gardés seuls, dans la photo
        affichée. Les points de référence des hauteurs et largeurs sont montrés par elles."""
        refs = {(m.reference or {}).get("name") for m in self._heights()} | {m.edge.get("name") for m in self._widths()}
        if self.ref_point:
            refs.add(self.ref_point["name"])
        marks, linked = [], set()
        for i, j, values in self.links:
            linked |= {i, j}
            marks.append({"points": [self._free_mark_point(i), self._free_mark_point(j)],
                          "label": "↔ {} m ± {} m".format(measures._num(values["valeur_m"], 2),
                                                          measures._num(values["incert_m"], 2))})
        for k, (_, _, values) in enumerate(self.points):
            if k not in linked and values["nom"] not in refs:
                marks.append({"points": [self._free_mark_point(k)], "label": ""})
        return marks

    def _reference_mark(self):
        """Croix du point de référence validé dans la photo affichée. Hauteur : là où il a été
        visé depuis cette photo, sinon projeté au sol. Largeur : sur la droite de mesure
        (projeté au sol comme elle). None dès qu'une mesure s'y appuie (sa croix le montre)."""
        ref, pic = self.ref_point, self.item.get("id")
        if self.mode == "road" and any(m.edge is ref for m in self._widths()):
            return None
        if self.mode == "height" and any((m.reference or {}).get("name") == ref["name"] for m in self._heights()):
            return None  # le pied des hauteurs qui s'y appuient le montre
        view = self._ref_view(pic) if self.mode != "road" else None
        if view is not None:
            point = {"yaw": view[0], "pitch": view[1]}
        else:
            point = self._project(ref["lon"], ref["lat"])
        # croix sélectionnée (voir width_sel, ref_sel)
        sel = self.width_sel == (None, 0) if self.mode == "road" else self.ref_sel
        return {"points": [dict(point, pic=pic, sel=sel)], "label": "", "beside": False}

    def _heights(self):
        return [m for m in self.tool.done if m.kind == "height" and m.result]

    def _height_points(self, m):
        """Repères du pied et du sommet d'une hauteur dans la photo affichée : [{yaw, pitch
        (radians, repère de la photo), abs_yaw, elev (degrés)}]. Sur la photo de la mesure, là
        où ils ont été visés ; sur les autres, projetés depuis leur position 3D, la caméra
        supposée à l'altitude de celle de la mesure (repères seulement)."""
        pic = (self.item or {}).get("id")
        if pic == m.clicks[0].get("pic"):
            return [{"yaw": p["yaw"], "pitch": p["pitch"], "abs_yaw": p["abs_yaw"], "elev": p["elev"]}
                    for p in m.viewer_marks()["points"]]
        pts, _, origin, _ = m.points()
        c0 = m.clicks[0]
        z0 = measures._ground_z(m.clicks)[c0.get("pic")]
        cam_z = geometry.camera(c0, self.tool.camera_for(c0, z0), origin, z0)[0][2]
        lon, lat = (float(v) for v in self.item["geometry"]["coordinates"][:2])
        cx, cy = triangulation._local(lon, lat, *origin)
        heading, out = self._heading(), []
        for x, y, z in pts:
            bearing = math.degrees(math.atan2(x - cx, y - cy))
            elev = math.degrees(math.atan2(z - cam_z, max(math.hypot(x - cx, y - cy), 0.5)))
            out.append({"yaw": math.radians((bearing - heading + 540) % 360 - 180), "pitch": math.radians(elev),
                        "abs_yaw": bearing % 360, "elev": elev})
        return out

    def _ref_near(self, click):
        """Hauteur : clic sur la croix du point de référence, quand aucune hauteur ne s'y appuie
        (sinon c'est le pied de celles-ci) ?"""
        mark = self._reference_mark() if self.mode == "height" and self.ref_point and self.item else None
        if not mark:
            return False
        p = mark["points"][0]  # comparé au clic brut (voir _nearest)
        point = {"abs_yaw": (math.degrees(p["yaw"]) + self._heading()) % 360, "elev": math.degrees(p["pitch"])}
        return self._nearest(click, [(None, [point])]) is not None

    def _height_near(self, click):
        """(hauteur, n° de point) dont le repère est le plus près du clic sur la photo
        affichée (à moins de measures.SELECT_ANGLE degrés), ou None."""
        return self._nearest(click, [(m, self._height_points(m)) for m in self._heights()])

    def _nearest(self, click, candidates):
        raw = click.get("raw") or [click["yaw"], click["elev"]]
        best, best_d = None, measures.SELECT_ANGLE
        for m, points in candidates:
            for index, p in enumerate(points):
                dyaw = (raw[0] - p["abs_yaw"] + 540) % 360 - 180
                d = math.hypot(dyaw * math.cos(math.radians(raw[1])), raw[1] - p["elev"])
                if d < best_d:
                    best, best_d = (m, index), d
        return best

    def _widths(self):
        return [m for m in self.tool.measures() if isinstance(m, widths.PerpendicularWidth) and m.result]

    def _width_near(self, click):
        """(largeur, n° de point) dont le repère est le plus près du clic (à moins de
        measures.SELECT_ANGLE degrés), ou None."""
        candidates = [(m, self._width_points(m)) for m in self._widths()]
        ref = self.ref_point
        if self.mode == "road" and ref is not None and not any(m.edge is ref for m in self._widths()):
            candidates.append((None, [self._project(ref["lon"], ref["lat"])]))  # sa croix seule
        return self._nearest(click, candidates)

    def _sel_edge(self, sel=None):
        """Point de référence de la sélection d'une largeur (sel, par défaut width_sel), et axe de
        la route en ce point (None en cours de lecture)."""
        m = (sel or self.width_sel)[0]
        edge = m.edge if m is not None else self.ref_point
        road = m.road if m is not None else edge.get("road")
        return edge, (road or {}).get("axis")

    def _recale_width(self, click):
        """Point sélectionné d'une largeur, recalé par ce clic, sans quitter la droite de mesure :
        le bord, là où la direction du clic la coupe ; le point de référence de même, et toutes
        les largeurs qui s'y appuient recalculées (la droite ne bouge pas : il glisse dessus)."""
        m, index = self.width_sel
        self.width_sel = None
        if index == 1:
            m.reaim(1, click)
            self.message = (m.error + " " if m.error else "") + m.status() + " Bord recalé."
            return self.status()
        edge, axis = self._sel_edge((m, index))
        if axis is None:
            self.message = "Axe de la route en cours de lecture (BD TOPO) : cliquez à nouveau dans un instant."
            return self.status()
        cam = triangulation._local(click["lon"], click["lat"], edge["lon"], edge["lat"])
        try:
            _, t, _, _ = widths.cross((0.0, 0.0), axis, cam, click["yaw"])
        except geometry.GeometryError as e:
            self.message = "Point de référence {} non recalé : {}".format(edge["name"], e)
            return self.status()
        refused = self._move_edge(edge, axis, t)
        if refused:
            self.message = refused
            return self.status()
        self.message = "Point de référence {} recalé de {} cm le long de la droite de mesure.".format(
            edge["name"], measures._num(abs(t) * 100, 0))
        if m is not None:
            self.message += " " + m.status()
        return self.status()

    def _move_edge(self, edge, axis, d):
        """Point de référence déplacé de d m le long de la droite de mesure, ses largeurs suivent.
        Retourne le texte du refus (au-delà d'un bord : remis en place), sinon None."""
        mates = [w for w in self._widths() if w.edge is edge]
        widths.move_edge(edge, axis, d)
        self._edge_moved(edge, mates)
        if any(w.error or w.result is None or w.result["value"] < widths.MIN_WIDTH for w in mates):
            widths.move_edge(edge, axis, -d)
            self._edge_moved(edge, mates)
            return "Le point de référence ne passe pas au-delà du bord."
        return None

    def nudge_point(self, key):
        """Flèches du clavier sur le point sélectionné (key : "left", "right", "up", "down"),
        qui reste sélectionné : hauteur, pied ou sommet monté (↑) ou descendu (↓) de 1 cm ;
        largeur, voir _nudge_width."""
        if key not in NUDGE_WORDS or not self.item:
            return self.status()
        if self.mode == "height" and self.ref_sel:
            if key in ("up", "down"):
                self._nudge_ref_foot(NUDGE if key == "up" else -NUDGE)
                self.message = "Pied au point de référence {} {} de 1 cm sur cette photo.".format(
                    self.ref_point["name"], "monté" if key == "up" else "descendu")
            else:
                self.message = "← → sans effet : le point de référence reste sur sa verticale."
            self.message += " Flèches ↑ ↓ : encore 1 cm ; clic : recaler ; Entrée : terminer."
            return self.status()
        if self.mode == "height" and self.tool.selected is not None:
            m, index = self.tool.selected
            if key in ("up", "down"):
                m.lift(index, NUDGE if key == "up" else -NUDGE)
                self.message = (m.error + " " if m.error else "") + (m.status() if m.result else "")
            else:
                self.message = "← → sans effet sur une hauteur : ↑ ↓ montent ou descendent le point."
            self.message += " Flèches ↑ ↓ : encore 1 cm ; clic : recaler ; Entrée : terminer."
            return self.status()
        return self._nudge_width(key)

    def finish_point(self):
        """Touche Entrée : fin du calage du point sélectionné (hauteur ou largeur), qui
        redevient un point ordinaire ; le résultat de sa mesure s'affiche."""
        if self.ref_sel:
            self.ref_sel = False
            self.message = ""
            return self.status()
        if self.mode == "height" and self.tool.selected is not None:
            m = self.tool.selected[0]
            self.tool.cancel_selection()
            self.message = m.status() if m.result else ""
            return self.status()
        return self.finish_width()

    def _nudge_width(self, key):
        """Flèches du clavier : point sélectionné d'une largeur déplacé de 1 cm le long de la
        droite de mesure, dans le sens vu sur la photo affichée (key : "left", "right" ; "up" :
        en s'éloignant de la caméra, "down" : en s'en rapprochant). Il reste sélectionné. Le
        point de référence entraîne toutes les largeurs qui en partent."""
        if self.width_sel is None:
            return self.status()
        m, index = self.width_sel
        if index == 1:
            d = self._nudge_sign(m.result["points"][1], m.road["axis"], key) * NUDGE
            m.nudge(d * m.side())  # vers l'extérieur du point de référence : plus large
            self.message = m.status()
        else:
            edge, axis = self._sel_edge()
            if axis is None:
                self.message = "Axe de la route en cours de lecture (BD TOPO) : réessayez dans un instant."
                return self.status()
            d = self._nudge_sign((edge["lon"], edge["lat"]), axis, key) * NUDGE  # le long de perpendicular(axe)
            refused = self._move_edge(edge, axis, d)
            if refused:
                self.message = refused
            else:
                self.message = "Point de référence {} déplacé de 1 cm {}.{}".format(
                    edge["name"], NUDGE_WORDS[key], " " + m.status() if m is not None else "")
        self.message += " Flèches : encore 1 cm ; clic : recaler ; Entrée : terminer."
        return self.status()

    def finish_width(self):
        """Touche Entrée : fin du calage du point sélectionné d'une largeur, qui redevient un
        point ordinaire ; le résultat de la largeur s'affiche."""
        if self.width_sel is not None:
            m = self.width_sel[0]
            self.width_sel = None
            self.message = m.status() if m is not None and m.result else ""
        return self.status()

    def _nudge_sign(self, point, axis, key):
        """Sens (+1 ou -1, le long de widths.perpendicular(axis)) qui déplace point (lon, lat)
        vers la droite (ou s'éloigne de la caméra) sur la photo affichée."""
        lon, lat = (float(v) for v in self.item["geometry"]["coordinates"][:2])
        x, y = triangulation._local(*point, lon, lat)
        ux, uy = widths.perpendicular(axis)
        if key in ("up", "down"):
            sign = 1 if x * ux + y * uy > 0 else -1  # +u éloigne de la caméra
        else:
            sign = 1 if x * uy - y * ux < 0 else -1  # +u tourne le cap vers la droite (sens horaire)
        return sign if key in ("right", "up") else -sign

    def _edge_moved(self, edge, mates):
        """Point de référence déplacé : son point gardé et ses largeurs suivent."""
        for k, (_, _, v) in enumerate(self.points):
            if v["nom"] == edge["name"]:
                self.points[k] = (edge["lon"], edge["lat"], v)
        for w in mates:
            w.clicks[0] = w._edge_mark(w.clicks[1])
            w.compute()

    def _road_at_reference(self):
        """Axe de la route au point de référence de la largeur : tronçon de la BD TOPO qui
        longe la trajectoire de la photo, sinon trajectoire de la séquence (voir roads.py).
        Laisse road à None pendant la lecture (reprise par _roads_ready)."""
        ref = self.ref_point
        track = self._axis(self.item) if self.item else None
        heading = track if track is not None else metadata.item_heading(self.item or {})
        res = self.roads.lookup(ref["lon"], ref["lat"], None if heading is None else (heading + 90) % 360,
                                (self.item or {}).get("collection"))
        if isinstance(res, dict):
            ref["road"] = {"axis": res["axis"], "source": res["source"]}
        elif res != "pending":
            ref["road"] = {"axis": heading or 0.0,
                           "source": "séquence" if track is not None else "orientation de la photo"}

    def new_reference(self):
        """Hauteur : « Nouveau point de référence » ; les hauteurs déjà faites restent."""
        self.epoch += 1  # pied cliqué en attente de son profil : abandonné
        self.tool.clear()
        self.triangulator.clear()
        self.ref_point = None
        self.width_sel = None
        self.measure_done = False
        self.ref_sel = False
        self.message = ""
        return self.status()

    def new_measure(self):
        """« Nouvelle mesure depuis A » : la largeur ou la hauteur faite reste, le clic suivant (bord
        opposé, sommet) en donne une autre depuis le même point de référence."""
        self.width_sel = None
        self.tool.cancel_selection()
        self.measure_done = False
        self.ref_sel = False
        self.message = ""
        return self.status()

    def _locked(self):
        """Largeur ou hauteur faite depuis le point de référence (et toujours là) : les clics ne
        servent plus qu'à ajuster ses points, jusqu'à new_measure."""
        ref = self.ref_point
        if not self.measure_done or ref is None:
            return False
        if self.mode == "road":
            return any(w.edge is ref for w in self._widths())
        return self.mode == "height" and any((m.reference or {}).get("name") == ref["name"] for m in self._heights())

    def _ref_view(self, pic):
        """Hauteur : [cap, élévation] (radians, repère de la photo pic) du pied au point de
        référence : recalé sur cette photo (ref["feet"]), sinon sa visée depuis elle, ou None."""
        ref = self.ref_point or {}
        if pic in ref.get("feet", {}):
            return ref["feet"][pic]
        seen = next((s for s in ref.get("sightings", []) if s["pic"] == pic and s.get("view")), None)
        return list(seen["view"]) if seen else None

    def _ref_distance(self):
        """Distance horizontale (m) de la photo affichée au point de référence."""
        lon, lat = (float(v) for v in self.item["geometry"]["coordinates"][:2])
        return max(math.hypot(*triangulation._local(self.ref_point["lon"], self.ref_point["lat"], lon, lat)), 0.5)

    def _recale_ref_foot(self, pitch):
        """Hauteur : pied au point de référence recalé sur la photo affichée, à l'élévation pitch
        (radians), sur sa verticale (le cap ne change pas) ; les hauteurs suivantes depuis cette
        photo partent de là."""
        ref, pic = self.ref_point, self.item.get("id")
        mark = self._reference_mark()
        yaw = mark["points"][0]["yaw"] if mark else self._project(ref["lon"], ref["lat"])["yaw"]
        old = self._ref_view(pic)
        ref.setdefault("feet", {})[pic] = [yaw, pitch]
        dz = (math.tan(pitch) - math.tan(old[1] if old else self._project(ref["lon"], ref["lat"])["pitch"])) \
            * self._ref_distance()
        return dz

    def _nudge_ref_foot(self, dz):
        """Hauteur : pied au point de référence monté (dz > 0) ou descendu de dz m sur la photo
        affichée."""
        ref, pic = self.ref_point, self.item.get("id")
        view = self._ref_view(pic)
        if view is None:
            p = self._project(ref["lon"], ref["lat"])
            view = [p["yaw"], p["pitch"]]
        d = self._ref_distance()
        ref.setdefault("feet", {})[pic] = [view[0], math.atan((d * math.tan(view[1]) + dz) / d)]

    def _reference_foot(self):
        """Hauteur : clic du pied pris dans la visée du point de référence faite depuis la photo
        affichée (le pied est le point triangulé), ou recalé sur elle ; None sinon."""
        view = self._ref_view((self.item or {}).get("id")) if self.ref_point else None
        if view is None:
            return None
        yaw, pitch = view
        return tools.photo_click(self.item, (math.degrees(yaw) + self._heading()) % 360, math.degrees(pitch),
                                 self.calibration, self.quality, [yaw, pitch], self._axis(self.item))

    def _anchor_height(self):
        """Hauteur terminée : calée sur le point de référence, qui reste pour les suivantes."""
        m = self.tool.current
        if self.mode != "height" or self.ref_point is None or m is None or not m.result or m.reference:
            return
        ref = self.ref_point
        m.anchor(ref["lon"], ref["lat"], ref["uncertainty"], ref["name"])
        if m.error:
            self.message = m.error
            return
        self.message = (m.status() + self._rtk_warning(ref["sequences"] + [c.get("sequence") for c in m.clicks])
                        + " Pour l'ajuster, cliquez une de ses croix. Autre hauteur depuis ce point : « Nouvelle "
                          "mesure depuis {} ».".format(ref["name"]))
        self.measure_done = True
        self.tool.finish()  # mesure terminée : affichée avec les autres pendant les visées suivantes

    def _after_calibration(self, tool):
        if tool is not self.calibrator:
            return
        self._calibration_changed()

    def waiting_map(self):
        """Calage du cap : le repère cliqué dans la photo attend son clic sur la carte."""
        return self.mode == "heading" and self.calibrator.waiting_map()

    def map_click(self, lon, lat):
        """Clic sur la carte pendant le calage du cap : le repère. Retourne True s'il a servi."""
        if not self.waiting_map():
            return False
        self.calibrator.add_map_point(float(lon), float(lat))
        self._calibration_changed()
        return True

    def cancel_landmark(self):
        self.calibrator.reset()
        return self.status()

    def aim(self, yaw, pitch):
        """Triangulation : visée de la direction cliquée (radians, dans le repère de la photo)."""
        if not self.active:
            return self.status()
        if not self.item:
            self.message = "Aucune photo affichée : impossible de viser."
            return self.status()
        sighting = tools.photo_sighting(self.item, (math.degrees(yaw) + self._heading()) % 360,
                                        math.degrees(pitch), self.calibration, self.quality)
        if sighting is None:
            self.message = "Position de la photo inconnue : impossible de viser."
            return self.status()
        self.message = ""
        sighting["view"] = [yaw, pitch]  # repère de la visée dans la photo (viewer_marks)
        replaced = any(s["pic"] == sighting["pic"] for s in self.triangulator.sightings)
        self.triangulator.add(sighting)
        if replaced:  # une visée par photo : un second clic (le sommet ?) remplace la première
            self.message = "Visée de cette photo remplacée : une seule visée par photo. "
            if self.mode == "height":
                self.message += ("Étape 1 : visez le pied de l'objet depuis une autre photo (flèches pour changer "
                                 "de photo), puis « Valider le point triangulé » ; le pied et le sommet se cliquent ensuite.")
            elif self.mode == "road":
                self.message += ("Étape 1 : visez le premier bord depuis une autre photo (flèches pour changer "
                                 "de photo), puis « Valider le point triangulé » ; le bord opposé se clique ensuite.")
            else:
                self.message += "Changez de photo (flèches) pour viser le même objet d'un autre point de vue."
            self.message += self._rtk_warning([s.get("sequence") for s in self.triangulator.sightings])
        return self.status()

    def keep_point(self):
        """Garde le point triangulé (exporté avec les mesures), puis repart de zéro."""
        values = self.triangulator.attributes()
        if values is None:
            self.message = self.triangulator.error or "Il faut au moins deux visées qui se croisent."
            return self.status()
        r = self.triangulator.result
        sightings = self.triangulator.sightings
        sequences = [s.get("sequence") for s in sightings]
        warning = self._rtk_warning(sequences)
        name = _point_name(self.named)
        self.named += 1
        values = dict({"nom": name}, **values, commentaire="")
        self.points.append((r["lon"], r["lat"], values))
        self.point_views[name] = {s["pic"]: s["view"] for s in sightings if s.get("view")}
        self.triangulator.clear()
        if self.mode in REF_MODES:  # point de référence, sans distance au précédent
            self.ref_point = {"lon": r["lon"], "lat": r["lat"], "uncertainty": r["uncertainty"], "name": name,
                              "sequences": sequences, "road": None, "sightings": list(sightings)}
            if self.mode == "height":
                self.message = ("Point de référence {} validé (±{} m) : c'est le pied. Cliquez maintenant le sommet "
                                "de l'objet, depuis cette photo ou une autre de la triangulation (à choisir sur la "
                                "carte).")
            else:
                self._road_at_reference()
                self.message = ("Point de référence {} validé (±{} m). Cliquez maintenant le bord opposé, au sol, "
                                "depuis une photo prise quelques mètres avant ou après (à choisir sur la carte) : seule "
                                "la direction du clic compte, la largeur se prend sur la droite tracée.")
            self.message = self.message.format(name, measures._num(r["uncertainty"])) + warning
            return self.status()
        self.message = "Point {} gardé (±{} m).".format(name, measures._num(r["uncertainty"]))
        if self.last_free is not None:  # mesure libre : distance au point gardé précédent, aussitôt
            self.message += " " + self._link(self.last_free, len(self.points) - 1)
        self.last_free = len(self.points) - 1
        self.message += (" Visez maintenant le point suivant depuis au moins deux photos : sa distance à {} sera "
                         "calculée.").format(name) + warning
        return self.status()

    def _link(self, i, j):
        """Distance horizontale entre les points gardés n° i et j (la triangulation donne une
        position sur la carte), incertitude des deux points combinée, gardée comme une mesure.
        Retourne le texte à afficher."""
        (lon1, lat1, v1), (lon2, lat2, v2) = self.points[i], self.points[j]
        x, y = triangulation._local(lon2, lat2, lon1, lat1)
        value, unc = math.hypot(x, y), math.hypot(v1["incert_m"], v2["incert_m"])
        self.links.append((i, j, {"valeur_m": round(value, 3), "incert_m": round(unc, 2), "commentaire": ""}))
        return "Distance {}–{} : {} m (±{} m).".format(
            v1["nom"], v2["nom"], measures._num(value, 2), measures._num(unc, 2))

    def move_handle(self, index, lon, lat):
        """Poignée n° index (voir handles) glissée sur la carte en (lon, lat)."""
        if not 0 <= index < len(self._handles):
            return self.status()
        try:
            self.message = self.tool.move_handle(self._handles[index], float(lon), float(lat))
        except Exception:
            traceback.print_exc()
            self.message = "Recalage impossible."
        return self.status()

    def clear(self):
        """« Effacer » : sélection d'un point, mesure en cours, visées, ou calage du mode."""
        self.message = ""
        tool = self._active()
        if tool is self.tool and tool.cancel_selection():
            return self.status()
        if self.width_sel is not None or self.ref_sel:
            self.width_sel, self.ref_sel = None, False
            return self.status()
        self.epoch += 1
        tool.clear()
        if tool is self.calibrator:
            self._calibration_changed()
        return self.status()

    def clear_all(self):
        self.epoch += 1
        self.triangulator.clear()
        self.tool.clear_all()
        self.calibrator.reset()
        self.points, self.links, self.named = [], [], 0
        self.last_free, self.point_views = None, {}
        self.ref_point = None
        self.width_sel = None
        self.measure_done = False
        self.ref_sel = False
        self.message = ""
        return self.status()

    def _roads_ready(self):
        changed = self.tool.recompute_roads()
        ref = self.ref_point
        if ref is not None and self.mode == "road" and ref.get("road") is None:  # axe au point de référence
            self._road_at_reference()
            changed = changed or ref.get("road") is not None
        if changed:
            self.notify()

    # Affichage -----------------------------------------------------------------------
    def status(self):
        """Texte de la mesure (consigne ou résultat)."""
        if not self.active:
            return self.message or "Cliquez « 📐 Mesure » pour mesurer dans la photo ou la caler."
        if self.message:
            return self.message
        if self.width_sel is not None:
            if self.width_sel[1] == 0:
                return ("Point de référence {} sélectionné : cliquez sa position exacte, depuis cette photo ou une "
                        "autre (seule la direction compte, il reste sur la droite de mesure) ; flèches du clavier : "
                        "1 cm le long de la droite (← → dans la photo, ↑ ↓ en s'éloignant ou en se rapprochant) ; "
                        "Entrée pour terminer.").format(self._sel_edge()[0].get("name", ""))
            return ("Bord de la largeur sélectionné : cliquez sa position exacte, depuis cette photo ou une autre "
                    "(seule la direction compte, il reste sur la droite de mesure) ; flèches du clavier : 1 cm le "
                    "long de la droite (← → dans la photo, ↑ ↓ en s'éloignant ou en se rapprochant) ; Entrée "
                    "pour terminer.")
        if self.mode == "height" and self.ref_sel:
            return ("Point de référence {} sélectionné : cliquez la position exacte du pied sur cette photo (il "
                    "reste sur sa verticale), ou flèches du clavier ↑ ↓ : 1 cm ; Entrée pour terminer.").format(
                self.ref_point["name"])
        if self.mode == "height" and self.tool.selected is not None:
            return ("{} de la hauteur sélectionné : cliquez sa position exacte sur la photo de la mesure, ou "
                    "flèches du clavier ↑ ↓ : 1 cm ; Entrée pour terminer.").format(
                "Pied" if self.tool.selected[1] == 0 else "Sommet")
        if self.mode in ("tilt", "heading"):
            return self._calibration_text()
        text = self._active().status()
        sequences = []
        if self._aiming():
            sequences = [s.get("sequence") for s in self.triangulator.sightings]
        if self.mode == "tri":
            text = "Mesure libre (distance au point gardé précédent) : " + text
        if self.mode == "height" and self.ref_point is None:
            text = ("Hauteur, étape 1/2 : visez le pied de l'objet depuis au moins deux photos (une visée par "
                    "photo, flèches pour changer de photo). " + text)
            if self.triangulator.result is not None:
                text += " Puis « Valider le point triangulé »."
        elif self._locked():
            text = self._locked_text()
            sequences = self.ref_point["sequences"]
        elif self.mode == "height":
            current = self.tool.current
            if (current is None or len(current.clicks) >= current.needed()) and self._reference_foot() is not None:
                text = ("Cliquez le sommet de l'objet : le pied est le point {} visé depuis cette photo.").format(
                    self.ref_point["name"])
            elif current is None or len(current.clicks) >= current.needed():
                text = ("Cette photo n'a pas servi à viser le point : cliquez le pied puis le sommet, ou passez sur "
                        "une photo de la triangulation (seul le sommet y est à cliquer).")
            text = "Hauteur, étape 2/2 (point {}) : {}".format(self.ref_point["name"], text)
            current = self.tool.current
            sequences = self.ref_point["sequences"] + [c.get("sequence") for c in (current.clicks if current else [])]
        elif self.mode == "road" and self.ref_point is None:
            text = ("Largeur, étape 1/2 : visez le premier bord (pied de bordure, marquage…) depuis au moins deux "
                    "photos (une visée par photo, flèches pour changer de photo). " + text)
            if self.triangulator.result is not None:
                text += " Puis « Valider le point triangulé »."
        elif self._locked():
            text = self._locked_text()
            sequences = self.ref_point["sequences"]
        elif self.mode == "road":
            road = self.ref_point.get("road")
            text = ("Largeur, étape 2/2 (point {}) : cliquez le bord opposé, au sol, depuis une photo prise quelques "
                    "mètres avant ou après le point (pas juste en face) ; la largeur se prend sur la droite tracée{}.").format(
                self.ref_point["name"], " (axe de la route en cours de lecture)" if road is None
                else ", perpendiculaire à l'axe {}° ({})".format(measures._num(road["axis"] % 180, 0), road["source"]))
            sequences = self.ref_point["sequences"]
        return text + self._rtk_warning(sequences)

    def _name_marks(self, marks):
        """Étiquettes des noms des points triangulés dans la photo affichée, sans croix (« name ») :
        point de référence des largeurs et des hauteurs, à sa croix (repère du point de référence
        en cours, point 0 de ses mesures) ; points gardés de la mesure libre. side : côté de la
        croix opposé à la mesure (« left », « right », « above », « below »), pour ne pas la
        masquer ; dessous par défaut (une hauteur monte, une largeur s'étend sur les côtés)."""
        pic = self.item.get("id")
        at = {}  # nom -> (repère, repère de l'autre bout de la mesure ou None)
        ref = self.ref_point
        if ref is not None and self.active and self.mode in REF_MODES:
            mark = self._reference_mark()
            if mark:
                at[ref["name"]] = (mark["points"][0], None)
        for w in self._widths():
            points = self._width_points(w)
            at.setdefault(w.edge.get("name"), (points[0], points[1]))
        for m in self._heights():
            if m.reference and m.reference.get("name"):
                points = self._height_points(m)
                at.setdefault(m.reference["name"], (points[0], points[1] if len(points) > 1 else None))
        partner = {}
        for i, j, _ in self.links:  # mesure libre : à l'opposé du point relié
            partner.setdefault(i, j)
            partner.setdefault(j, i)
        for k, (_, _, values) in enumerate(self.points):
            if values["nom"] not in at:
                other = self._free_mark_point(partner[k]) if k in partner else None
                at[values["nom"]] = (self._free_mark_point(k), other)
        return [{"points": [dict(p, pic=pic, sel=False)], "label": name, "name": True, "side": _away(p, other)}
                for name, (p, other) in at.items() if name and p is not None]

    def _calibration_text(self):
        """Consigne des modes de calage, étape par étape, avec ce qui est déjà fait."""
        tool, pic = self.calibrator, (self.item or {}).get("id")
        prefix = (tool.error + " ") if tool.error else ""
        waiting = len(tool.clicks)
        if self.mode == "tilt":
            done = len(self.calibration.verticals.get(pic, []))
            if waiting:
                return prefix + ("Calage de l'inclinaison, objet {} : cliquez maintenant son sommet, le plus haut "
                                 "possible sur la même verticale (zoomez pour être précis).").format(done + 1)
            if done == 0:
                return prefix + ("Calage de l'inclinaison, objet 1 sur 2 : cliquez le pied d'un objet bien vertical "
                                 "(poteau, angle de façade, montant de panneau), au ras du sol, puis son sommet.")
            if done == 1:
                return prefix + ("1 objet calé : l'inclinaison n'est corrigée qu'en partie. Objet 2 sur 2 : cliquez le "
                                 "pied puis le sommet d'un autre objet vertical, à environ 90° du premier dans la "
                                 "photo (sur le côté si le premier est devant).")
            tilt = self.calibration.tilt(pic) or {}
            sigma = " (±{}°)".format(measures._num(tilt["sigma"], 2)) if tilt.get("sigma") is not None else ""
            return prefix + ("Inclinaison calée sur {} objets{} : c'est fait pour cette photo. Un objet de plus "
                             "l'affine ; sinon, passez à un mode de mesure.").format(done, sigma)
        sequence = (self.item or {}).get("collection")
        done = len(self.calibration.landmarks.get(sequence, [])) if sequence else 0
        if waiting:
            return prefix + ("Repère {} : cliquez maintenant ce même repère sur la carte (glisser pour la déplacer, "
                             "molette pour zoomer, Échap pour annuler).").format(done + 1)
        if done == 0:
            return prefix + ("Calage du cap, repère 1 : cliquez dans la photo un repère net, visible aussi sur la "
                             "carte (angle de bâtiment, poteau, borne), plutôt lointain ; puis cliquez-le sur la carte.")
        if done < 3:
            return prefix + ("{} repère{} : le cap est corrigé. Pour recaler aussi la position de la photo, il en faut "
                             "au moins 3, répartis autour d'elle (devant, derrière, sur les côtés) : cliquez le repère "
                             "suivant dans la photo, puis sur la carte.").format(done, "s" if done > 1 else "")
        return prefix + ("{} repères : cap et position recalés pour les photos de la séquence à moins de {} m. Un "
                         "repère de plus affine le calage ; sinon, passez à un mode de mesure.").format(
            done, measures._num(calibration.REACH, 0))

    def _rtk_warning(self, sequences):
        """RTK_WARNING si l'une des séquences n'a pas de position RTK vérifiée (vérification
        terminée), sinon ""."""
        for sequence in dict.fromkeys(s for s in sequences if s):
            res = self.precision.known({"collection": sequence})
            if res is not None and not res.get("precise"):
                return RTK_WARNING
        return ""

    def state(self):
        """État de la photo affichée (dictionnaire) : ligne de qualité, calage, hauteur de caméra
        proposée, raisons du mode dégradé…"""
        item, pic = self.item, (self.item or {}).get("id")
        lon = lat = None
        try:
            lon, lat = (float(v) for v in item["geometry"]["coordinates"][:2])
        except (KeyError, TypeError, ValueError):
            pass
        suggestion = precision.camera_suggestion(self.quality) if item else None
        tilt = tools.tilt_state(self.calibration, pic, bool(pic) and self.tilts.pending(pic))
        sequence = (item or {}).get("collection")
        return ({
            "quality": precision.state_text(self.quality, metadata.item_accuracy(item), tilt) if item else "",
            "calibration": tools.calibration_text(self.calibration, pic, sequence, lon, lat) if item else "",
            "camera": [round(suggestion[0], 2), suggestion[1]] if suggestion else None,
            "waiting_map": self.waiting_map(),
            "point": self.triangulator.result is not None,
            "aiming": self._aiming(),
            "point_sel": self.width_sel is not None or self.ref_sel or (
                self.mode == "height" and self.tool.selected is not None),
            # bouton « Nouvelle mesure depuis A » : nom du point de référence, ou None
            "new_measure": self.ref_point["name"] if self._locked() else None,
            "active": self.active,
            "lights": self._lights(pic) if item else [],
        })

    def _lights(self, pic):
        """Voyants de la visionneuse : [(nom, état)], état « ok », « non » ou « attente »."""
        res = self.quality

        def light(value):
            return "attente" if value is None else ("ok" if value else "non")

        if self.calibration.tilt(pic):
            tilt = "ok"
        else:
            tilt = "attente" if self.tilts.pending(pic) else "non"
        return [("RTK", light(res["precise"] if res else None)),
                ("LiDAR HD", light(res["lidar"] if res else None)),
                ("Inclinaison", tilt)]

    def viewer_marks(self):
        """Repères de la photo affichée (liste) : pour chaque mesure, points (cap et élévation
        dans le repère de la photo, radians), libellé, point sélectionné."""
        pic = (self.item or {}).get("id")
        # Hauteurs et largeurs : visibles depuis toutes les photos (voir _height_points, _width_points)
        marks = [self.tool._marks(m) for m in self.tool.done
                 if not isinstance(m, widths.PerpendicularWidth) and not (m.kind == "height" and m.result)]
        if self.item:
            for m in self._heights():
                marks.append({"points": [dict(p, pic=pic, sel=self.tool.selected == (m, k))
                                         for k, p in enumerate(self._height_points(m))],
                              "label": m.label(), "beside": True})
            for m in self._widths():
                marks.append({"points": [dict(p, pic=pic, sel=self.width_sel == (m, k))
                                         for k, p in enumerate(self._width_points(m))],
                              "label": m.label(), "beside": False})
        if self.item:  # mesure libre : points gardés et distances, depuis toutes les photos
            marks += self._free_marks()
        if self.active and self._aiming():  # visées faites depuis cette photo
            marks = marks + [{"points": [{"pic": s["pic"], "yaw": s["view"][0], "pitch": s["view"][1]}
                                         for s in self.triangulator.sightings if "view" in s], "sighting": True}]
        elif self.active and self.mode in GROUND_MODES:  # mesure ou calage en cours
            marks = marks + [self.tool.viewer_marks()]
        elif self.active and self.mode in CALIBRATION_MODES:
            marks = marks + [self.calibrator.viewer_marks()]
        if self.active and self.mode in REF_MODES and self.ref_point and self.item:
            marks = marks + [m for m in [self._reference_mark()] if m]
        if self.item:  # noms des points triangulés (A, B…) à côté de leur croix
            marks = marks + self._name_marks(marks)
        road = (self.ref_point or {}).get("road") if self.active and self.mode == "road" else None
        if road and self.item:  # en premier : dessinée sous les traits rouges des largeurs
            marks = [self._guide_mark(road)] + marks
        if self.active and self.mode == "height" and self.ref_point and self.item:  # de même
            marks = [self._vertical_mark()] + marks
        out = []
        for m in marks:
            points = [p for p in m["points"] if p.get("pic") == pic and p.get("yaw") is not None]
            if points:
                out.append({"points": [[p["yaw"], p["pitch"], bool(p.get("sel"))] for p in points],
                            "label": m.get("label", ""), "beside": m.get("beside", False),
                            "sighting": m.get("sighting", False), "guide": m.get("guide", False),
                            "vertical": m.get("vertical", False), "name": m.get("name", False),
                            "side": m.get("side", "")})
        return out

    def focus(self):
        """Points à montrer ensemble sur la carte ([[lon, lat]]) pendant une mesure :
        photo affichée, photos des visées en cours et leur point, point de référence de la
        hauteur, mesure en cours ou dernière terminée (points et photos)."""
        if not self.active or self.mode in CALIBRATION_MODES:
            return []
        if self.mode == "tri" and not self.triangulator.sightings and self.links:
            i, j, _ = self.links[-1]  # mesure libre : distance qui vient d'être calculée, seule
            return [list(self.points[i][:2]), list(self.points[j][:2]), "segment"]
        pts = []
        try:
            pts.append([float(v) for v in self.item["geometry"]["coordinates"][:2]])
        except (KeyError, TypeError, ValueError):
            pass
        pts += [[s["lon"], s["lat"]] for s in self.triangulator.sightings]
        if self.triangulator.result:
            pts.append([self.triangulator.result["lon"], self.triangulator.result["lat"]])
        if self.mode in REF_MODES and self.ref_point:
            pts.append([self.ref_point["lon"], self.ref_point["lat"]])
        if self.mode in GROUND_MODES:
            m = self.tool.current or (self.tool.done[-1] if self.tool.done else None)
            if m is not None and m.result:
                pts += [list(p) for p in m.result["picks"]] + [[c["lon"], c["lat"]] for c in m.clicks]
        return pts

    def _vertical_mark(self):
        """Hauteur : verticale vraie passant par le point de référence, dans la photo affichée
        (repère de la visionneuse, tracé vert) : depuis la croix du pied (là où il a été visé
        depuis cette photo, sinon projeté au sol) jusqu'à VERTICAL_LENGTH m au-dessus. Tracée
        dans le monde puis ramenée dans la photo par le calage (inclinaison, cap) : sur une photo
        penchée et calée, elle suit les objets verticaux et non le bord de l'image."""
        item, cal = self.item, self.calibration
        pic, sequence = item.get("id"), item.get("collection")
        lon, lat = (float(v) for v in item["geometry"]["coordinates"][:2])
        heading = self._heading()
        view = self._ref_view(pic)
        if view is None:
            p = self._project(self.ref_point["lon"], self.ref_point["lat"])
            view = [p["yaw"], p["pitch"]]
        # Pied dans le monde : cap et élévation vrais ; la verticale garde ce cap
        bearing, elev = cal.correct(pic, sequence, (math.degrees(view[0]) + heading) % 360,
                                    math.degrees(view[1]), lon, lat)
        d = self._ref_distance()
        z0 = d * math.tan(math.radians(max(-89.0, min(89.0, elev))))
        points = []
        for k in range(25):
            e = math.degrees(math.atan2(z0 + k * VERTICAL_LENGTH / 24, d))
            yaw, pitch = cal.uncorrect(pic, sequence, bearing, e, lon, lat)
            points.append({"pic": pic, "yaw": math.radians((yaw - heading + 540) % 360 - 180),
                           "pitch": math.radians(pitch)})
        return {"points": points, "guide": True, "vertical": True}

    def _guide_mark(self, road):
        """Droite de mesure de la largeur dans la photo affichée (repère de la visionneuse) :
        points au sol supposé plat, à la hauteur de caméra du réglage ; la mesure n'en dépend pas."""
        lon, lat = (float(v) for v in self.item["geometry"]["coordinates"][:2])
        heading, height = self._heading(), self.tool.camera_height
        ux, uy = widths.perpendicular(road["axis"])
        ax, ay = triangulation._local(self.ref_point["lon"], self.ref_point["lat"], lon, lat)
        points = []
        for k in range(-40, 41):
            t = k * widths.GUIDE_LENGTH / 40
            x, y = ax + t * ux, ay + t * uy
            d = math.hypot(x, y)
            if d < 1.0:
                continue
            yaw = (math.degrees(math.atan2(x, y)) - heading + 540) % 360 - 180
            points.append({"pic": self.item.get("id"), "yaw": math.radians(yaw), "pitch": -math.atan2(height, d)})
        return {"points": points, "guide": True}

    def handles(self):
        """Poignées déplaçables sur la carte : [[lon, lat]], repérées par leur rang pour
        move_handle. Seulement en mesure, comme dans le plugin."""
        handles = self.tool.handles() if self.active and self.mode in GROUND_MODES else []
        self._handles = [key for key, _, _ in handles]
        return [[lon, lat] for _, lon, lat in handles]

    def results(self):
        """Mesures terminées et points gardés, pour la liste des résultats de l'hôte."""
        out = [{"id": id(m), "kind": "mesure", "text": m.label(), "comment": getattr(m, "comment", "")}
               for m in self.tool.measures() if m.result]
        out += [{"id": i, "kind": "point", "text": "Point {} ±{} m".format(
                    v["nom"], measures._num(v["incert_m"], 2)), "comment": v.get("commentaire", "")}
                for i, (_, _, v) in enumerate(self.points)]
        out += [{"id": k, "kind": "distance", "text": "↔ {}–{} : {} m ± {} m".format(
                    self.points[i][2]["nom"], self.points[j][2]["nom"], measures._num(v["valeur_m"], 2),
                    measures._num(v["incert_m"], 2)), "comment": v.get("commentaire", "")}
                for k, (i, j, v) in enumerate(self.links)]
        return out

    def delete_result(self, kind, ident):
        """Efface un résultat de la liste : mesure (et son dessin), point triangulé (et les
        distances qui le relient ; les autres gardent leur nom) ou distance."""
        if kind == "mesure":
            tool = self.tool
            for m in tool.measures():
                if id(m) == ident:
                    if tool.selected is not None and tool.selected[0] is m:
                        tool.selected = None
                    m.remove()
                    if m is tool.current:
                        tool.current = None
                    else:
                        tool.done.remove(m)
                    self.message = "Mesure effacée."
                    self.width_sel = None  # point d'une mesure effacée : plus de sélection
                    break
        elif kind == "point" and 0 <= ident < len(self.points):
            name = self.points[ident][2]["nom"]
            self.last_free = None if self.last_free == ident else (
                None if self.last_free is None else self.last_free - (self.last_free > ident))
            before = [i for i, j, _ in self.links if j == ident]
            after = [j for i, j, _ in self.links if i == ident]
            del self.points[ident]
            shift = lambda k: k - (k > ident)  # noqa: E731
            self.links = [(shift(i), shift(j), v) for i, j, v in self.links if ident not in (i, j)]
            self.message = "Point {} effacé.".format(name)
            # Point au milieu d'une chaîne (A–B–C, B effacé) : la distance A–C est proposée
            if before and after and not any({i, j} == {shift(before[0]), shift(after[0])} for i, j, _ in self.links):
                self.proposal = (shift(before[0]), shift(after[0]))
        elif kind == "distance" and 0 <= ident < len(self.links):
            del self.links[ident]
            self.message = "Distance effacée."
        return self.status()

    def take_rtk_alert(self):
        """RTK_ALERT à afficher en fenêtre d'avertissement, une seule fois par séquence : mesure
        activée sur une photo dont la vérification a conclu à l'absence de position RTK ; None
        sinon (vérification en cours ou sans conclusion, RTK vérifié, séquence déjà signalée,
        mesure désactivée)."""
        res, item = self.quality, self.item or {}
        if not self.active or res is None or res.get("precise") or res.get("transient"):
            return None  # transient : service en panne pendant la vérification, pas de conclusion
        sequence = item.get("collection") or item.get("id")
        if sequence in self.rtk_alerted:
            return None
        self.rtk_alerted.add(sequence)
        return RTK_ALERT

    def take_proposal(self):
        """Distance proposée après un effacement ([n°, n°, « A », « C »] ou None), une
        seule fois."""
        p, self.proposal = self.proposal, None
        if p is None or max(p) >= len(self.points):
            return None
        return [p[0], p[1], self.points[p[0]][2]["nom"], self.points[p[1]][2]["nom"]]

    def link_points(self, i, j):
        """Distance entre deux points gardés, à la demande (voir take_proposal)."""
        if 0 <= i < len(self.points) and 0 <= j < len(self.points) and i != j:
            self.message = self._link(int(i), int(j))
        return self.status()

    def set_comment(self, kind, ident, text):
        """Commentaire d'une mesure ou d'un point gardé (champ « commentaire » de l'export)."""
        if kind == "point":
            if 0 <= ident < len(self.points):
                self.points[ident][2]["commentaire"] = text
            return
        if kind == "distance":
            if 0 <= ident < len(self.links):
                self.links[ident][2]["commentaire"] = text
            return
        for m in self.tool.measures():  # rangé sur la mesure : effacée, son commentaire disparaît avec elle
            if id(m) == ident:
                m.comment = text

    def has_results(self):
        return bool(self.points) or bool(self.links) or any(m.result for m in self.tool.measures())


def _point_name(n):
    """Nom du n-ième point gardé (0 : A) : A … Z, puis AA, AB…, comme les colonnes d'un tableur."""
    name = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        name = chr(65 + r) + name
    return name


def _away(p, other):
    """Côté de la croix p opposé au point other (repères {yaw, pitch} de la photo, radians) :
    « left », « right », « above » ou « below » ; « below » sans other."""
    if other is None or other.get("yaw") is None or p.get("yaw") is None:
        return "below"
    dyaw = ((p["yaw"] - other["yaw"] + math.pi) % (2 * math.pi) - math.pi) * math.cos(p["pitch"])
    dpitch = p["pitch"] - other["pitch"]
    if abs(dyaw) >= abs(dpitch):
        return "right" if dyaw > 0 else "left"  # le cap croît vers la droite de l'écran
    return "above" if dpitch > 0 else "below"
