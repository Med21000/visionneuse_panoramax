# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Cédric COCHART
# SPDX-License-Identifier: GPL-2.0-or-later
"""Couche filaire Panoramax (tuiles vectorielles).

Créée semi-transparente (DEFAULT_OPACITY) pour laisser voir le fond de carte ; l'opacité
que l'utilisateur lui donne ensuite est mémorisée dans son profil et reprise pour les
couches filaires créées plus tard (voir watch_opacity). Une couche enregistrée dans un
projet garde l'opacité du projet."""

from qgis.core import (
    Qgis,
    QgsDataSourceUri,
    QgsLineSymbol,
    QgsMarkerSymbol,
    QgsProject,
    QgsSettings,
    QgsVectorTileBasicRenderer,
    QgsVectorTileBasicRendererStyle,
    QgsVectorTileLayer,
)

from . import api

TILE_LAYER_NAME = "Panoramax – filaire"
ORANGE = "#FF6F00"
OPACITY_SETTING = "visionneuse_panoramax/tiles_opacity"  # dernière opacité donnée au filaire
DEFAULT_OPACITY = 0.6
WATCHED = "panoramaxOpacityWatched"  # propriété Qt (non enregistrée) des couches dont l'opacité est suivie


def is_tile_layer(layer):
    return isinstance(layer, QgsVectorTileLayer) and bool(layer.customProperty("panoramax/tiles"))


def find_tile_layer():
    for layer in QgsProject.instance().mapLayers().values():
        if is_tile_layer(layer):
            return layer
    return None


def saved_opacity():
    """Opacité des nouvelles couches filaires : la dernière choisie, sinon DEFAULT_OPACITY."""
    try:
        value = float(QgsSettings().value(OPACITY_SETTING, DEFAULT_OPACITY))
    except (TypeError, ValueError):
        return DEFAULT_OPACITY
    return min(1.0, max(0.0, value))


def watch_opacity(layers):
    """Mémorise l'opacité que l'utilisateur donne aux couches filaires parmi `layers` (couches
    créées par le plugin, ou chargées avec un projet : à relier à QgsProject.layersAdded)."""
    for layer in layers:
        if is_tile_layer(layer) and not layer.property(WATCHED):
            layer.setProperty(WATCHED, True)
            layer.opacityChanged.connect(lambda value: QgsSettings().setValue(OPACITY_SETTING, float(value)))


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


def create_tile_layer(photo_filter=None):
    """Crée (ou retrouve) la couche filaire Panoramax en tuiles vectorielles."""
    existing = find_tile_layer()
    if existing is not None:
        return existing

    layer = QgsVectorTileLayer(_tile_uri(), _tile_layer_name())
    if not layer.isValid():
        return None
    layer.setCustomProperty("panoramax/tiles", True)
    layer.setOpacity(saved_opacity())
    layer.setRenderer(_build_renderer())
    if photo_filter is not None:
        apply_filter(photo_filter, layer)
    QgsProject.instance().addMapLayer(layer)
    watch_opacity([layer])
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


def apply_filter(photo_filter, layer=None):
    """Applique le filtre des photos (voir filters.PhotoFilter) aux styles de la couche
    filaire, selon la couche des tuiles qu'ils dessinent ; les symboles sont conservés.
    Retourne la couche, ou None si elle n'existe pas."""
    layer = layer or find_tile_layer()
    if layer is None or layer.renderer() is None:
        return None
    renderer = layer.renderer().clone()
    if not hasattr(renderer, "styles"):  # moteur de rendu remplacé (style MapLibre…) : pas de filtre
        return layer
    styles = []
    for style in renderer.styles():
        if style.layerName() in ("sequences", "pictures", "grid"):
            style.setFilterExpression(photo_filter.tile_expression(style.layerName()))
        styles.append(style)
    renderer.setStyles(styles)
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
