# -*- coding: utf-8 -*-
"""Couche filaire Panoramax (tuiles vectorielles) et extraction en couches vecteur."""

import math
import struct

from qgis.core import (
    Qgis,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsDataSourceUri,
    QgsFeature,
    QgsField,
    QgsFields,
    QgsGeometry,
    QgsLineSymbol,
    QgsMarkerSymbol,
    QgsPointXY,
    QgsProject,
    QgsProperty,
    QgsRectangle,
    QgsVectorLayer,
    QgsVectorTileBasicRenderer,
    QgsVectorTileBasicRendererStyle,
    QgsVectorTileLayer,
)
from qgis.PyQt.QtCore import QMetaType

from . import api, mvt

TILE_LAYER_NAME = "Panoramax – filaire"
ORANGE = "#FF6F00"
WEB_MERCATOR_HALF = 20037508.342789244
MAX_TILES = 300

SEQUENCE_FIELDS = [
    ("id", QMetaType.Type.QString),
    ("account_id", QMetaType.Type.QString),
    ("model", QMetaType.Type.QString),
    ("type", QMetaType.Type.QString),
    ("date", QMetaType.Type.QString),
    ("gps_accuracy", QMetaType.Type.Double),
    ("h_pixel_density", QMetaType.Type.Double),
]
PICTURE_FIELDS = [
    ("id", QMetaType.Type.QString),
    ("account_id", QMetaType.Type.QString),
    ("ts", QMetaType.Type.QString),
    ("heading", QMetaType.Type.Double),
    ("sequences", QMetaType.Type.QString),
    ("type", QMetaType.Type.QString),
    ("model", QMetaType.Type.QString),
    ("gps_accuracy", QMetaType.Type.Double),
    ("h_pixel_density", QMetaType.Type.Double),
]


def _fields(spec):
    fields = QgsFields()
    for name, mtype in spec:
        fields.append(QgsField(name, mtype))
    return fields


# --------------------------------------------------------------------------
# Couche tuiles vectorielles
# --------------------------------------------------------------------------

def find_tile_layer():
    for layer in QgsProject.instance().mapLayers().values():
        if isinstance(layer, QgsVectorTileLayer) and layer.customProperty("panoramax/tiles"):
            return layer
    return None


def _tile_uri():
    uri = QgsDataSourceUri()
    uri.setParam("type", "xyz")
    uri.setParam("url", api.TILES_URL)
    uri.setParam("zmin", str(api.TILES_MIN_ZOOM))
    uri.setParam("zmax", str(api.TILES_MAX_ZOOM))
    return bytes(uri.encodedUri()).decode("utf-8")


def _tile_layer_name():
    if api.INSTANCE_NAME == "meta":
        return TILE_LAYER_NAME
    return "{} ({})".format(TILE_LAYER_NAME, api.INSTANCE_NAME)


def create_tile_layer():
    """Crée (ou retrouve) la couche filaire Panoramax en tuiles vectorielles."""
    existing = find_tile_layer()
    if existing is not None:
        return existing

    layer = QgsVectorTileLayer(_tile_uri(), _tile_layer_name())
    if not layer.isValid():
        return None
    layer.setCustomProperty("panoramax/tiles", True)
    layer.setRenderer(_build_renderer())
    QgsProject.instance().addMapLayer(layer)
    return layer


def retarget_tile_layer():
    """Fait pointer la couche filaire existante vers l'instance courante.

    La couche garde sa place dans l'arbre des couches et son style.
    """
    layer = find_tile_layer()
    if layer is None:
        return None
    renderer = layer.renderer().clone() if layer.renderer() else _build_renderer()
    layer.setDataSource(_tile_uri(), _tile_layer_name(), "xyzvectortiles")
    layer.setRenderer(renderer)
    layer.triggerRepaint()
    return layer


def _build_renderer():
    renderer = QgsVectorTileBasicRenderer()
    styles = []

    # Aperçu à petite échelle : grille de densité (zooms 0 à 6)
    grid = QgsVectorTileBasicRendererStyle("Densité (petite échelle)", "grid", Qgis.GeometryType.Point)
    grid.setSymbol(QgsMarkerSymbol.createSimple(
        {"name": "circle", "color": "230,81,0,160", "outline_style": "no", "size": "1.6"}))
    grid.setMaxZoomLevel(6)
    styles.append(grid)

    # Séquences (le filaire proprement dit)
    seq = QgsVectorTileBasicRendererStyle("Séquences", "sequences", Qgis.GeometryType.Line)
    seq.setSymbol(QgsLineSymbol.createSimple(
        {"line_color": ORANGE, "line_width": "0.6", "capstyle": "round", "joinstyle": "round"}))
    seq.setMinZoomLevel(6)
    styles.append(seq)

    # Photos, visibles en grande échelle, orientées selon leur cap
    pics = QgsVectorTileBasicRendererStyle("Photos", "pictures", Qgis.GeometryType.Point)
    symbol = QgsMarkerSymbol.createSimple(
        {"name": "circle", "color": ORANGE, "outline_color": "white", "outline_width": "0.2", "size": "1.8"})
    pics.setSymbol(symbol)
    pics.setMinZoomLevel(15)
    styles.append(pics)

    renderer.setStyles(styles)
    return renderer


# --------------------------------------------------------------------------
# Extraction en couche vecteur
# --------------------------------------------------------------------------

def _to_feature(fields, spec, gtype, parts, attrs, to_target):
    """Entité QGIS (dans le SCR cible) à partir d'une entité MVT décodée."""
    if gtype == mvt.GEOM_POINT:
        pts = [p[0] for p in parts if p]
        if not pts:
            return None
        geom = QgsGeometry.fromPointXY(QgsPointXY(*pts[0]))
    elif gtype == mvt.GEOM_LINE:
        lines = [[QgsPointXY(x, y) for x, y in p] for p in parts if len(p) >= 2]
        if not lines:
            return None
        geom = QgsGeometry.fromMultiPolylineXY(lines)
    else:
        return None
    geom.transform(to_target)
    feat = QgsFeature(fields)
    feat.setGeometry(geom)
    for name, mtype in spec:
        val = attrs.get(name)
        if val is not None and mtype == QMetaType.Type.QString:
            val = str(val)
        feat.setAttribute(name, val)
    return feat


# --------------------------------------------------------------------------
# Emprise et tuiles
# --------------------------------------------------------------------------

def _tile_range(extent_3857, zoom):
    n = 2 ** zoom
    size = 2 * WEB_MERCATOR_HALF / n

    def col(x):
        return min(n - 1, max(0, int(math.floor((x + WEB_MERCATOR_HALF) / size))))

    def row(y):
        return min(n - 1, max(0, int(math.floor((WEB_MERCATOR_HALF - y) / size))))

    return (col(extent_3857.xMinimum()), col(extent_3857.xMaximum()),
            row(extent_3857.yMaximum()), row(extent_3857.yMinimum()))


def _tile_count(rng):
    c0, c1, r0, r1 = rng
    return (c1 - c0 + 1) * (r1 - r0 + 1)


def choose_zoom(extent_3857, min_zoom, max_zoom):
    """Zoom le plus détaillé possible sans dépasser MAX_TILES tuiles."""
    for z in range(max_zoom, min_zoom - 1, -1):
        if _tile_count(_tile_range(extent_3857, z)) <= MAX_TILES:
            return z
    return None


def extract(kind, extent, extent_crs, progress=None):
    """Télécharge les tuiles couvrant l'emprise et retourne une couche mémoire.

    kind : "sequences" ou "pictures".
    progress : callable(fait, total) -> bool (False pour annuler).
    Retourne (couche | None, message).
    """
    project = QgsProject.instance()
    crs_3857 = QgsCoordinateReferenceSystem("EPSG:3857")
    target_crs = project.crs() if project.crs().isValid() else extent_crs

    to_3857 = QgsCoordinateTransform(extent_crs, crs_3857, project)
    extent_3857 = to_3857.transformBoundingBox(extent)
    clip_3857 = QgsRectangle(extent_3857)

    if kind == "sequences":
        # Zoom 13 : précision ~1,2 m par tuile de 4,9 km (4 fois moins de tuiles qu'au zoom 14)
        min_zoom, max_zoom, spec, geom_name = 6, 13, SEQUENCE_FIELDS, "MultiLineString"
    else:
        min_zoom, max_zoom, spec, geom_name = 15, 15, PICTURE_FIELDS, "Point"

    zoom = choose_zoom(extent_3857, min_zoom, max_zoom)
    if zoom is None:
        return None, ("L'emprise est trop grande ({} tuiles au minimum). "
                      "Zoomez davantage avant d'extraire.").format(
                          _tile_count(_tile_range(extent_3857, min_zoom)))

    fields = _fields(spec)
    to_target = QgsCoordinateTransform(crs_3857, target_crs, project)
    c0, c1, r0, r1 = _tile_range(extent_3857, zoom)
    total = _tile_count((c0, c1, r0, r1))

    collected = []
    errors = 0
    done = 0
    for x in range(c0, c1 + 1):
        for y in range(r0, r1 + 1):
            if progress is not None and not progress(done, total):
                return None, "Extraction annulée."
            done += 1
            try:
                data = api.fetch_blocking(api.TILES_URL.format(z=zoom, x=x, y=y))
            except IOError:
                errors += 1
                continue
            if not data:
                continue
            try:
                decoded = mvt.decode(data, zoom, x, y, {kind})
            except (ValueError, IndexError, struct.error):
                errors += 1
                continue
            for gtype, parts, attrs in decoded.get(kind, []):
                feat = _to_feature(fields, spec, gtype, parts, attrs, to_target)
                if feat is not None:
                    collected.append(feat)
    if progress is not None:
        progress(total, total)

    # Découpe à l'emprise demandée (dans le SCR cible)
    clip = QgsGeometry.fromRect(clip_3857)
    clip.transform(to_target)

    layer = QgsVectorLayer("{}?crs={}".format(geom_name, target_crs.authid() or target_crs.toWkt()),
                           "Panoramax – {}{}".format(
                               "séquences" if kind == "sequences" else "photos",
                               "" if api.INSTANCE_NAME == "meta" else " ({})".format(api.INSTANCE_NAME)),
                           "memory")
    out_fields = QgsFields(fields)
    if kind == "pictures":
        out_fields.append(QgsField("url", QMetaType.Type.QString))
    provider = layer.dataProvider()
    provider.addAttributes(out_fields.toList())
    layer.updateFields()

    out = []
    if kind == "sequences":
        # Une séquence est découpée aux limites de tuiles : on recolle par identifiant.
        by_id = {}
        for feat in collected:
            if not feat.hasGeometry():
                continue
            key = feat.attribute("id") or "sans_id_{}".format(len(by_id))
            by_id.setdefault(key, []).append(feat)
        for parts in by_id.values():
            geom = QgsGeometry.unaryUnion([f.geometry() for f in parts])
            if geom is None or geom.isEmpty():
                continue
            geom = geom.mergeLines()
            geom = geom.intersection(clip)
            if geom.isEmpty():
                continue
            geom.convertToMultiType()
            out_feat = QgsFeature(layer.fields())
            out_feat.setGeometry(geom)
            for name, _ in spec:
                out_feat.setAttribute(name, parts[0].attribute(name))
            out.append(out_feat)
    else:
        seen = set()
        for feat in collected:
            pid = feat.attribute("id")
            if not feat.hasGeometry() or pid in seen:
                continue
            if not feat.geometry().intersects(clip):
                continue
            seen.add(pid)
            out_feat = QgsFeature(layer.fields())
            out_feat.setGeometry(feat.geometry())
            for name, _ in spec:
                out_feat.setAttribute(name, feat.attribute(name))
            out_feat.setAttribute("url", api.explore_url(pic_id=pid))
            out.append(out_feat)

    provider.addFeatures(out)
    layer.updateExtents()
    _style_extracted(layer, kind)

    msg = "{} entité(s) extraite(s) depuis {} tuile(s) au zoom {}.".format(len(out), total, zoom)
    if errors:
        msg += " {} tuile(s) en erreur.".format(errors)
    return layer, msg


def _style_extracted(layer, kind):
    if kind == "sequences":
        symbol = QgsLineSymbol.createSimple({"line_color": ORANGE, "line_width": "0.8"})
    else:
        symbol = QgsMarkerSymbol.createSimple(
            {"name": "arrow", "color": ORANGE, "outline_color": "white", "outline_width": "0.2", "size": "3"})
        symbol.setDataDefinedAngle(_heading_property())
    layer.renderer().setSymbol(symbol)


def _heading_property():
    return QgsProperty.fromExpression('coalesce("heading", 0)')

