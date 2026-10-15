# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Cédric COCHART
# SPDX-License-Identifier: GPL-2.0-or-later
"""Outil de mesure du plugin : la logique de calcul du cœur (core/measures.py), dessinée sur
la carte QGIS (measure.MeasureItem) et enregistrée dans la couche « Panoramax – mesures »."""

from datetime import datetime

from qgis.core import (
    Qgis,
    QgsCoordinateTransform,
    QgsFeature,
    QgsGeometry,
    QgsLineSymbol,
    QgsMarkerLineSymbolLayer,
    QgsMarkerSymbol,
    QgsPalLayerSettings,
    QgsPointXY,
    QgsProject,
    QgsTextBufferSettings,
    QgsTextFormat,
    QgsVectorLayerSimpleLabeling,
)
from qgis.PyQt.QtGui import QColor

from . import output
from .core import ground, measures
from .core.measures import CAMERA_SOURCES, KIND_NAMES, SURFACE_NAMES, _ground_z
from .measure import COLOR, WGS84, MeasureItem

LAYER_NAME = "Panoramax – mesures"
LAYER_FIELDS = (
    "field=type:string&field=surface:string&field=valeur_m:double&field=incert_m:double"
    "&field=horiz_m:double&field=vert_m:double&field=long_route_m:double&field=travers_m:double"
    "&field=dist_photo_m:double&field=camera_m:double&field=camera_calee:integer&field=terrain:string"
    "&field=recales:integer&field=photos:string&field=date_mesure:string&field=commentaire:string")


def _style_measures(layer):
    """Trait rouge, points aux extrémités (une hauteur, verticale, n'en montre qu'un) et
    valeur en étiquette."""
    symbol = QgsLineSymbol.createSimple({"color": COLOR.name(), "width": "0.6"})
    ends = QgsMarkerLineSymbolLayer()
    ends.setPlacements(Qgis.MarkerLinePlacement.Vertex)
    ends.setSubSymbol(QgsMarkerSymbol.createSimple(
        {"name": "circle", "color": COLOR.name(), "outline_color": "white", "outline_width": "0.3", "size": "2"}))
    symbol.appendSymbolLayer(ends)
    layer.renderer().setSymbol(symbol)
    text = QgsTextFormat()
    text.setSize(9)
    buffer = QgsTextBufferSettings()
    buffer.setEnabled(True)
    buffer.setColor(QColor("white"))
    buffer.setSize(1)
    text.setBuffer(buffer)
    settings = QgsPalLayerSettings()
    settings.fieldName = "replace(format_number(\"valeur_m\", 2), '.', ',') || ' m'"
    settings.isExpression = True
    settings.placement = Qgis.LabelPlacement.Horizontal
    settings.setFormat(text)
    layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
    layer.setLabelsEnabled(True)


def feature(m, layer):
    """Entité de la mesure m pour la couche des mesures : segment mesuré (pour une largeur
    perpendiculaire, perpendiculaire à la route, à mi-chemin des deux bords ; pour une hauteur,
    du pied au sommet, à la verticale l'un de l'autre) et attributs du résultat."""
    r, d = m.result, m.result["decomposition"]
    ends = r["points"] if len(r["points"]) >= 2 else r["picks"]
    geom = QgsGeometry.fromPolylineXY([QgsPointXY(lon, lat) for lon, lat in ends])
    geom.transform(QgsCoordinateTransform(WGS84, layer.crs(), QgsProject.instance()))
    height, source = m.tool.camera_info(m.clicks[0], _ground_z(m.clicks)[m.clicks[0].get("pic")])

    def num(value, digits=2):
        return None if value is None else round(value, digits)

    values = {
        "type": KIND_NAMES[m.kind],
        "surface": SURFACE_NAMES.get(m.surface, m.surface),
        "valeur_m": num(r["value"], 3),
        "incert_m": num(r["uncertainty"], 3),
        "horiz_m": num(d["horizontal"], 3),
        "vert_m": num(d["vertical"], 3),
        "long_route_m": num(d["along"], 3),
        "travers_m": num(d["across"], 3),
        "dist_photo_m": num(max(r["ranges"]), 1),
        "camera_m": num(height),
        "camera_calee": CAMERA_SOURCES.get(source, 0),
        "terrain": " + ".join(sorted({c.get("terrain") for c in m.clicks if c.get("terrain")})) or ground.FLAT,
        "recales": len(m.overrides),
        "photos": ",".join(dict.fromkeys(c.get("pic") or "" for c in m.clicks)),
        "date_mesure": datetime.now().isoformat(timespec="seconds"),
    }
    feat = QgsFeature(layer.fields())
    feat.setGeometry(geom)
    for name, value in values.items():
        if layer.fields().indexOf(name) >= 0:
            feat.setAttribute(name, value)
    return feat



class MeasureTool(measures.MeasureTool):
    """Outil de mesure du cœur, dessiné sur le canevas de la carte QGIS."""

    def new_item(self, wall=False):
        return MeasureItem(self.canvas, wall=wall)

    def save(self, parent=None):
        """Enregistre dans la couche « Panoramax – mesures » les mesures affichées terminées
        qui ne le sont pas encore ; une mesure déjà enregistrée puis modifiée (recalage,
        nouvelle visée) remplace son entité. Retourne (succès, message) ; message None si
        l'utilisateur a renoncé à créer la couche."""
        todo = [m for m in self.measures() if m.unsaved()]
        if not todo:
            if any(m.result for m in self.measures()):
                return False, "Les mesures affichées sont déjà enregistrées."
            return False, "Aucune mesure terminée à enregistrer."
        layer, error = output.ensure_layer("measures", LAYER_NAME, "mesures", "LineString", LAYER_FIELDS,
                                           _style_measures, parent)
        if layer is None:
            return False, error
        added = updated = 0
        managed = output.field_names(LAYER_FIELDS)
        for m in todo:
            # Couche changée depuis l'enregistrement (retirée du projet, autre GeoPackage) : son
            # ancien fid désignerait une autre entité, la mesure est ajoutée
            old_fid = m.fid if m.layer_id == layer.id() else None
            fid = output.write_feature(layer, feature(m, layer), old_fid, managed)
            if fid is None:
                return False, "Impossible d'enregistrer la mesure dans la couche « {} ».".format(layer.name())
            if old_fid is not None and fid == old_fid:
                updated += 1
            else:
                added += 1
            m.fid, m.layer_id, m.saved = fid, layer.id(), m._snapshot()
        parts = []
        if added:
            parts.append("{} mesure{} ajoutée{}".format(added, *(("s", "s") if added > 1 else ("", ""))))
        if updated:
            parts.append("{} mise{} à jour".format(updated, "s" if updated > 1 else ""))
        return True, "{} dans « {} ».".format(" et ".join(parts).capitalize(), layer.name())
