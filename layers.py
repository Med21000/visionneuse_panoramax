# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Cédric COCHART
# SPDX-License-Identifier: GPL-2.0-or-later
"""Couche filaire Panoramax (tuiles vectorielles)."""

from qgis.core import (
    Qgis,
    QgsDataSourceUri,
    QgsLineSymbol,
    QgsMarkerSymbol,
    QgsProject,
    QgsVectorTileBasicRenderer,
    QgsVectorTileBasicRendererStyle,
    QgsVectorTileLayer,
)

from . import api

TILE_LAYER_NAME = "Panoramax – filaire"
ORANGE = "#FF6F00"


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
