# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Cédric COCHART
# SPDX-License-Identifier: GPL-2.0-or-later
"""Outils de mesure communs aux hôtes (plugin QGIS, application web), hors MeasureTool
(voir measures.py) :

- photo_click : clic dans la photo affichée, converti en direction corrigée du calage,
  avec la position (recalée), sa précision et l'axe de la route ;
- Triangulator : visées d'un objet depuis plusieurs photos ;
- CalibrationTool : clics de calage (inclinaison, cap et position, hauteur de caméra) ;
- calibration_text, tilt_state : textes du calage et de l'inclinaison de la photo.

Le dessin sur la carte vient de l'hôte (new_item), comme pour MeasureTool.
"""

import math
from datetime import datetime

from . import host, metadata, triangulation
from .calibration import REACH, CalibrationError
from .measures import NullItem, _num

LONE_RAY = 60.0  # longueur (m) d'une visée tant qu'elle n'en croise aucune autre


def photo_click(item, yaw, elev, cal, quality=None, pos=None, axis=None):
    """Clic dans la photo `item` (fiche STAC) : direction brute yaw/elev (degrés, cap absolu),
    corrigée du calage `cal`. quality : vérification de la précision de la photo (voir
    precision.PrecisionChecker), ou None ; pos : position du clic dans le repère de la photo
    (radians, visionneuse web) ; axis : axe de la séquence (voir metadata.sequence_axis), à
    défaut l'orientation de la photo. Retourne le clic (dict) ou None sans position."""
    try:
        lon, lat = (float(v) for v in item["geometry"]["coordinates"][:2])
    except (KeyError, TypeError, ValueError):
        return None
    axis_source = "séquence"
    if axis is None:
        axis, axis_source = metadata.item_heading(item), "orientation de la photo"
    pic, sequence = item.get("id", ""), item.get("collection")
    cyaw, celev = cal.correct(pic, sequence, float(yaw), float(elev), lon, lat)
    precise = bool(quality and quality["precise"])
    accuracy = _accuracy(item, quality)
    fixed = cal.position(sequence, lon, lat)  # position recalée par les repères de la séquence
    clon, clat, caccuracy = fixed if fixed else (lon, lat, accuracy)
    # Position vérifiée précise : altitude NGF de la caméra (hauteur de caméra sans calage,
    # voir measures.MeasureTool.camera_info)
    camera_alt = host.ngf_altitude(lon, lat, metadata.item_altitude(item)) if precise else None
    return {"pic": pic, "lon": clon, "lat": clat, "camera_alt": camera_alt,
            "gps": [lon, lat], "gps_accuracy": accuracy,
            "yaw": cyaw, "elev": celev, "raw": [float(yaw) % 360, float(elev)],
            "sequence": sequence, "accuracy": caccuracy,
            "precise": metadata.precise_heading(item) is not None,
            "axis": axis, "axis_source": axis_source, "pos": pos}


def photo_sighting(item, heading, pitch, cal, quality=None):
    """Visée de triangulation depuis la photo `item` : cap brut du centre de la vue (degrés,
    cap précis de la photo compris) et son inclinaison, corrigés du calage. Retourne la visée
    (dict, voir triangulation.solve) ou None sans position."""
    try:
        lon, lat = (float(v) for v in item["geometry"]["coordinates"][:2])
    except (KeyError, TypeError, ValueError):
        return None
    pic, sequence = item.get("id", ""), item.get("collection")
    heading, _ = cal.correct(pic, sequence, float(heading), float(pitch), lon, lat)
    fixed = cal.position(sequence, lon, lat)
    clon, clat, accuracy = fixed if fixed else (lon, lat, _accuracy(item, quality))
    return {"pic": pic, "sequence": sequence, "lon": clon, "lat": clat, "heading": heading,
            "accuracy": accuracy, "precise": metadata.precise_heading(item) is not None,
            "heading_error": cal.heading_error(sequence, lon, lat)}


def _accuracy(item, quality):
    """Précision de la position de la photo : vérifiée (quality), sinon celle des métadonnées."""
    if quality and quality["precise"] and quality["accuracy"] is not None:
        return quality["accuracy"]
    return metadata.item_accuracy(item)


def tilt_state(cal, pic, pending=False):
    """(texte, manquante) : inclinaison de la photo pic, pour la ligne d'état (voir
    precision.state_text). pending : inclinaison automatique en cours de calcul."""
    tilt = cal.tilt(pic) if pic else None
    if tilt:
        if tilt["source"] == "clics":
            return "inclinaison calée à la main", False
        if tilt["source"].startswith("clics + "):
            return "inclinaison calée à la main et automatiquement (±{:.1f}°)".format(
                max(tilt["sigma"], 0.1)).replace(".", ","), False
        how = "verticales de la photo" if tilt["source"] == "verticales" else "pente et dévers LiDAR HD"
        return "inclinaison calée ({}, ±{:.1f}°)".format(how, max(tilt["sigma"], 0.1)).replace(".", ","), False
    if pending:
        return "inclinaison en cours de calcul…", False
    return "inclinaison non calée", True


def calibration_text(cal, pic, sequence, lon, lat):
    """Calage de la photo et de la séquence affichées (texte, vide sans calage)."""
    parts = []
    tilt = cal.tilt(pic) if pic else None
    if tilt and tilt.get("source", "clics") == "clics":
        parts.append("inclinaison {:.1f}° corrigée{} ({} objet{})".format(
            tilt["tilt"], " en partie" if tilt["partial"] else "", tilt["count"],
            "s" if tilt["count"] > 1 else "").replace(".", ","))
    elif tilt and tilt["source"].startswith("clics + "):
        parts.append("inclinaison {:.1f}° corrigée ({} objet{} cliqué{}, complété{} par {}, ±{:.1f}°)".format(
            tilt["tilt"], tilt["count"], *(("s", "s", "s") if tilt["count"] > 1 else ("", "", "")),
            "les verticales de la photo" if tilt["source"].endswith("verticales") else "le LiDAR HD",
            max(tilt["sigma"], 0.1)).replace(".", ","))
    elif tilt and tilt["source"] == "verticales":
        parts.append("inclinaison {:.1f}° corrigée automatiquement ({} verticales de la photo, ±{:.1f}°)".format(
            tilt["tilt"], tilt["count"], max(tilt["sigma"], 0.1)).replace(".", ","))
    elif tilt:
        parts.append("inclinaison {:.1f}° estimée par la pente et le dévers LiDAR HD (±{:.1f}°, modèle vérifié "
                     "sur {} photos de la séquence)".format(tilt["tilt"], tilt["sigma"], tilt["count"]).replace(".", ","))
    camera = cal.camera(sequence) if sequence else None
    if camera:
        text = "caméra à {:.2f} m (±{:.2f} m, {} référence{})".format(
            camera["height"], camera["sigma"], camera["count"], "s" if camera["count"] > 1 else "").replace(".", ",")
        if camera.get("legacy"):
            text += (", dont {} d'une version précédente, sans l'inclinaison automatique : refaites le calage"
                     .format(camera["legacy"]))
        parts.append(text)
    pose = cal.pose(sequence, lon, lat) if sequence else None
    if pose:
        text = "cap {:+.2f}° (±{:.2f}°)".format(pose["offset"], pose["sigma"])
        if pose["positioned"]:
            sx, sy = pose["shift"]
            text += ", position décalée de {:.2f} m vers {:.0f}° (±{:.2f} m)".format(
                math.hypot(sx, sy), math.degrees(math.atan2(sx, sy)) % 360, pose["shift_sigma"])
        text += " · {} repère{}".format(pose["count"], "s" if pose["count"] > 1 else "")
        if pose["count"] > 3:
            text += ", écart {:.2f}°".format(pose["residual"])
        parts.append(text.replace(".", ","))
    elif sequence and cal.landmarks.get(sequence):
        parts.append("recalage de la séquence hors de portée (photo à plus de {:.0f} m des repères)".format(
            REACH))
    return "Calage : " + " · ".join(parts) if parts else ""


class Triangulator:
    """Visées en cours et point d'intersection. Le dessin des visées sur la carte vient de
    l'hôte (new_item), l'enregistrement aussi (attributes donne les valeurs)."""

    # Consignes de visée : réticule au centre de la vue et bouton dans le plugin, clic sur
    # l'objet dans l'application web
    AIM = "Placez le réticule sur l'objet puis « Viser »"
    AIM_AGAIN = "visez le même objet puis « Viser » à nouveau"

    def __init__(self):
        self.sightings = []  # dicts : pic, lon, lat, heading, accuracy, precise
        self.result = None
        self.error = None
        self.item = None

    def new_item(self):
        """Dessin des visées et du point sur la carte : set_data(visées [(lon, lat, lon, lat)],
        point (lon, lat) ou None), remove(). Remplacé par l'hôte."""
        return NullItem()

    # ------------------------------------------------------------------
    def add(self, sighting):
        """Ajoute une visée (remplace celle déjà faite depuis la même photo)."""
        self.sightings = [s for s in self.sightings if s["pic"] != sighting["pic"]] + [sighting]
        self._compute()
        return self.status()

    def clear(self):
        self.sightings, self.result, self.error = [], None, None
        self._draw()

    def remove(self):
        if self.item is not None:
            self.item.remove()
            self.item = None

    def _compute(self):
        self.result, self.error = None, None
        if len(self.sightings) >= 2:
            try:
                self.result = triangulation.solve(self.sightings)
            except triangulation.TriangulationError as exc:
                self.error = str(exc)
        self._draw()

    def _draw(self):
        if self.item is None:
            if not self.sightings:
                return
            self.item = self.new_item()
        rays = []
        for i, s in enumerate(self.sightings):
            length = self.result["distances"][i] * 1.15 + 3 if self.result else LONE_RAY
            end = triangulation.offset(s["lon"], s["lat"], s["heading"], length)
            rays.append((s["lon"], s["lat"]) + end)
        self.item.set_data(rays, (self.result["lon"], self.result["lat"]) if self.result else None)

    # ------------------------------------------------------------------
    def status(self):
        n = len(self.sightings)
        if n == 0:
            return "{}. Recommencez depuis une autre photo, sur le côté de l'objet.".format(self.AIM)
        if n == 1:
            return "1 visée. Changez de photo, {}.".format(self.AIM_AGAIN)
        if self.error:
            return "{} visées : {}".format(n, self.error)
        r = self.result
        text = "{} visées : point à {} m · croisement {}° · incertitude ±{} m".format(
            n, " / ".join(_num(d) for d in r["distances"]), _num(r["angle"], 0), _num(r["uncertainty"]))
        if n > 2:
            text += " · écart des visées {} m".format(_num(r["rms"]))
        text += " · GPS ±{} m".format(_num(r["gps"]))
        if r["gps_assumed"]:
            text += " (précision inconnue pour certaines photos : {} m supposés)".format(
                _num(triangulation.GPS_ACCURACY, 0))
        if r["angle"] < 15:
            text += ". Croisement faible : une visée plus latérale améliorerait le point."
        if r["gps"] > triangulation.GPS_WARNING:
            text += (". Position GPS des photos imprécise (±{} m) : visez depuis des photos mieux "
                     "positionnées, ou d'autres séquences.".format(_num(r["gps"])))
        return text

    def attributes(self):
        """Attributs du point triangulé (couche « points triangulés » du plugin, export de
        l'application web), ou None sans résultat."""
        r = self.result
        if r is None:
            return None
        return {
            "nb_visees": len(self.sightings),
            "angle": round(r["angle"], 1),
            "incert_m": round(r["uncertainty"], 2),
            "ecart_m": round(r["rms"], 2),
            "dist_max_m": round(max(r["distances"]), 1),
            "gps_m": round(r["gps"], 1),
            "photos": ",".join(s["pic"] for s in self.sightings),
            "date_mesure": datetime.now().isoformat(timespec="seconds"),
        }


class CalibrationTool:
    """Clics de calage (voir core/calibration.py) : pied et sommet d'objets verticaux
    (inclinaison de la photo), ou repère cliqué dans la photo puis sur la carte
    (cap de la séquence)."""

    MAP_NAME = "la carte"  # dans les consignes : « la carte QGIS » dans le plugin

    def __init__(self, calibration, current, reference=None):
        self.calibration = calibration
        self.current = current  # () -> (photo, séquence) affichées
        self.reference = reference  # () -> ("ground" | "height", longueur connue en m)
        self.mode = "tilt"
        self.clicks = []  # clics en attente (dicts de PanoramaxDock.photoClicked)
        self.error = None
        self.last = None  # résultat du dernier calage (texte)
        self.new_camera = None  # hauteur de caméra de la séquence, juste calée (à reporter dans le panneau)
        self.item = None

    def new_item(self):
        """Dessin des visées vers les repères (mode cap), comme Triangulator.new_item."""
        return NullItem()

    def set_mode(self, mode):
        self.mode = mode
        self.reset()

    def _reference(self):
        return self.reference() if self.reference else ("ground", 0.0)

    def waiting_map(self):
        """Le repère cliqué dans la photo attend son clic sur la carte."""
        return self.mode == "heading" and len(self.clicks) == 1

    def add_click(self, click, notice=None):
        self.error, self.last = None, None
        if self.mode == "camera":
            self._add_camera_click(click)
        elif self.mode == "heading":
            if not click.get("sequence"):
                self.error = "Séquence de la photo inconnue : recalage du cap impossible."
            else:
                self.clicks = [click]
        else:
            if self.clicks and self.clicks[0]["pic"] != click["pic"]:
                self.clicks = []  # le sommet doit être sur la même photo que le pied
            self.clicks.append(click)
            if len(self.clicks) == 2:
                bottom, top = (tuple(c["raw"]) for c in self.clicks)
                self.clicks = []
                try:
                    r = self.calibration.add_vertical(click["pic"], bottom, top)
                except CalibrationError as exc:
                    self.error = str(exc)
                else:
                    self.last = "Inclinaison de la photo : {}° vers {}°{}.".format(
                        _num(r["tilt"], 2), _num(r["toward"], 0),
                        "" if r["residual"] is None else ", écart des objets {}°".format(_num(r["residual"], 2)))
                    if r["partial"]:
                        self.last += (" Correction partielle : cliquez un autre objet vertical, à environ 90° "
                                      "du premier, pour corriger toute l'inclinaison.")
        self._draw()
        return self.status()

    def _add_camera_click(self, click):
        if not click.get("sequence"):
            self.error = "Séquence de la photo inconnue : calage de la hauteur impossible."
            return
        if self.clicks and self.clicks[0]["pic"] != click["pic"]:
            self.clicks = []  # les deux clics se font sur la même photo
        self.clicks.append(click)
        if len(self.clicks) < 2:
            return
        clicks, self.clicks = self.clicks, []
        kind, known = self._reference()
        try:
            r = self.calibration.add_camera_height(click["sequence"], kind, clicks, known)
        except CalibrationError as exc:
            self.error = str(exc)
        else:
            self.new_camera = self.calibration.camera(click["sequence"])["height"]
            self.last = "Caméra à {} m (±{} m) d'après cette référence de {} m.".format(
                _num(r["height"], 2), _num(r["sigma"], 2), _num(known, 2))
            if self.calibration.camera(click["sequence"])["count"] > 1:
                self.last += " Avec les références précédentes : {} m.".format(_num(self.new_camera, 2))
            self.last += " Hauteur reportée dans le réglage « Caméra à »."
            if r["sigma"] > 0.25:
                self.last += " Référence trop loin ou trop courte pour être précise : prenez-en une plus proche."

    def add_map_point(self, mlon, mlat):
        """Repère cliqué sur la carte (WGS84), après son clic dans la photo."""
        if not self.waiting_map():
            return self.status()
        c, self.clicks = self.clicks[0], []
        # Position et précision GPS d'origine : le recalage ne doit pas partir d'une position déjà recalée
        lon, lat = c.get("gps") or (c["lon"], c["lat"])
        accuracy = c.get("gps_accuracy", c.get("accuracy"))
        survey = bool(c.get("precise")) and accuracy is not None and accuracy <= triangulation.SURVEY_ACCURACY
        try:
            r = self.calibration.add_landmark(c["sequence"], c["pic"], lon, lat, c["raw"][0],
                                              c["raw"][1], mlon, mlat, accuracy, survey)
        except CalibrationError as exc:
            self.error = str(exc)
        else:
            pose = self.calibration.pose(c["sequence"])
            n = pose["count"]
            self.last = "Repère {} à {} m (écart de cap brut {}°).".format(
                n, _num(r["distance"], 0), _num(r["offset"], 2))
            if pose["positioned"]:
                self.last += " Position recalée à ±{} m, cap à ±{}°.".format(
                    _num(pose["shift_sigma"], 2), _num(pose["sigma"], 2))
            elif n >= 3:
                self.last += (" Repères mal répartis pour recaler la position : ajoutez-en dans d'autres "
                              "directions (devant, derrière, sur les côtés).")
            else:
                self.last += (" Cap corrigé à ±{}°. À partir de 3 repères bien répartis autour de la photo, "
                              "la position est recalée aussi.".format(_num(pose["sigma"], 2)))
        self._draw()
        return self.status()

    def reset(self):
        """Abandonne les clics en attente (le calage déjà fait est conservé)."""
        self.clicks, self.error, self.last = [], None, None
        self._draw()

    def clear(self):
        """Efface le calage du mode : inclinaison de la photo ou cap de la séquence affichée."""
        pic, sequence = self.current()
        if self.mode == "tilt" and pic:
            self.calibration.clear_tilt(pic)
        elif self.mode == "heading" and sequence:
            self.calibration.clear_heading(sequence)
        elif self.mode == "camera" and sequence:
            self.calibration.clear_camera(sequence)
        self.reset()

    def remove(self):
        if self.item is not None:
            self.item.remove()
            self.item = None

    def refresh(self):
        """Photo affichée changée : la carte montre les visées de ses repères (voir _draw)."""
        self._draw()

    def _draw(self):
        """Carte : visées vers les repères pris depuis la photo affichée (mode cap). Ceux des
        autres photos de la séquence calent aussi celle-ci, mais leurs tracés encombreraient la
        carte et feraient croire qu'ils partent d'elle."""
        pic, sequence = self.current()
        obs = [o for o in self.calibration.landmarks.get(sequence, []) if o["pic"] == pic] \
            if self.mode == "heading" else []
        if self.item is None:
            if not obs:
                return
            self.item = self.new_item()
        rays = [(o["lon"], o["lat"], o["mlon"], o["mlat"]) for o in obs]
        self.item.set_data(rays, (obs[-1]["mlon"], obs[-1]["mlat"]) if obs else None)

    def viewer_marks(self):
        points = []
        for c in self.clicks:
            pos = c.get("pos") or [None, None]
            points.append({"pic": c.get("pic"), "yaw": pos[0], "pitch": pos[1],
                           "abs_yaw": c["raw"][0], "elev": c["raw"][1]})
        return {"points": points, "label": "", "beside": self.mode in ("tilt", "camera")}

    def status(self):
        n = len(self.clicks)
        text = ""
        if self.error:
            text = self.error + " "
        elif self.last:
            text = self.last + " "
        if self.mode == "camera":
            kind, known = self._reference()
            if kind == "height":
                if n == 1:
                    return text + "Cliquez le sommet de l'objet, sur la même photo."
                return text + ("Cliquez au pied d'un objet de hauteur connue ({} m), puis à son sommet. "
                               "Choisissez-le proche (moins de 10–15 m).".format(_num(known, 2)))
            if n == 1:
                return text + "Cliquez la seconde extrémité, au sol, sur la même photo."
            return text + ("Cliquez au sol les deux extrémités d'une longueur connue ({} m) : trait de marquage, "
                           "place de stationnement, ou longueur mesurée sur la carte. Choisissez-la proche "
                           "(moins de 10–15 m) et plutôt en travers de la vue : dans l'axe, elle est moins précise.".format(_num(known, 2)))
        if self.mode == "tilt":
            if n == 1:
                return text + "Cliquez le sommet du même objet."
            return text + ("Cliquez le pied puis le sommet d'un objet bien vertical (poteau, angle de façade). "
                           "Deux objets à environ 90° l'un de l'autre corrigent toute l'inclinaison de la photo.")
        if n == 1:
            return text + ("Cliquez maintenant ce même repère sur {} (glisser pour déplacer la carte, "
                           "molette pour zoomer, Échap pour annuler).".format(self.MAP_NAME))
        return text + ("Cliquez dans la photo un repère net, visible aussi sur la carte (poteau, angle de bâtiment). "
                       "Un repère seul corrige le cap (choisissez-le lointain) ; trois repères ou plus, bien "
                       "répartis autour de la photo, recalent aussi sa position. Le recalage vaut pour les photos "
                       "de la séquence à moins de {} m.".format(_num(REACH, 0)))
