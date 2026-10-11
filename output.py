# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Cédric COCHART
# SPDX-License-Identifier: GPL-2.0-or-later
"""Couches de résultats (points triangulés, mesures) : retrouvées dans le projet, ou
créées au premier enregistrement, dans un GeoPackage ou en couche temporaire.

Une couche temporaire disparaît à la fermeture de QGIS si on oublie de la sauvegarder :
au premier enregistrement, l'utilisateur choisit un GeoPackage (mémorisé, et partagé par
les deux couches, une table chacune) ou, à défaut, une couche temporaire.
"""

import os

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsProject,
    QgsSettings,
    QgsVectorFileWriter,
    QgsVectorLayer,
)
from qgis.PyQt.QtWidgets import QFileDialog, QMessageBox

WGS84 = QgsCoordinateReferenceSystem("EPSG:4326")
LAYER_KEY = "visionneuse_panoramax/kind"  # propriété qui identifie les couches du plugin
GPKG_SETTING = "visionneuse_panoramax/gpkg"  # dernier GeoPackage choisi
DEFAULT_GPKG = "panoramax_mesures.gpkg"


def find_layer(kind):
    """Couche du plugin de ce type déjà dans le projet, ou None."""
    for layer in QgsProject.instance().mapLayers().values():
        if isinstance(layer, QgsVectorLayer) and layer.isValid() and layer.customProperty(LAYER_KEY) == kind:
            return layer
    return None


def _ask_storage(parent, name):
    """Chemin du GeoPackage choisi, "" pour une couche temporaire, None si annulé."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Question)
    box.setWindowTitle("Panoramax")
    box.setText("Où enregistrer la couche « {} » ?".format(name))
    box.setInformativeText("Un fichier GeoPackage est conservé à la fermeture de QGIS. Une couche temporaire "
                           "est perdue si elle n'est pas sauvegardée avant.")
    gpkg = box.addButton("Fichier GeoPackage…", QMessageBox.ButtonRole.AcceptRole)
    memory = box.addButton("Couche temporaire", QMessageBox.ButtonRole.DestructiveRole)
    box.addButton(QMessageBox.StandardButton.Cancel)
    box.setDefaultButton(gpkg)
    box.exec()
    if box.clickedButton() is memory:
        return ""
    if box.clickedButton() is not gpkg:
        return None
    last = QgsSettings().value(GPKG_SETTING, "") or ""
    home = QgsProject.instance().homePath() or os.path.expanduser("~")
    start = last if last and os.path.isdir(os.path.dirname(last)) else os.path.join(home, DEFAULT_GPKG)
    path, _ = QFileDialog.getSaveFileName(
        parent, "GeoPackage des mesures Panoramax", start, "GeoPackage (*.gpkg)",
        options=QFileDialog.Option.DontConfirmOverwrite)  # fichier existant : on y ajoute une table
    if not path:
        return None
    if not path.lower().endswith(".gpkg"):
        path += ".gpkg"
    QgsSettings().setValue(GPKG_SETTING, path)
    return path


def _open_table(path, table, name, template):
    """Table du GeoPackage, créée sur le modèle de la couche template si elle n'existe pas.
    Retourne (couche, erreur)."""
    layer = QgsVectorLayer("{}|layername={}".format(path, table), name, "ogr")
    if layer.isValid():
        missing = [f.name() for f in template.fields() if layer.fields().indexOf(f.name()) < 0]
        if layer.geometryType() != template.geometryType() or missing:
            return None, ("La table « {} » de {} existe déjà avec une autre structure : choisissez un autre "
                          "fichier.".format(table, os.path.basename(path)))
        return layer, None
    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = "GPKG"
    options.layerName = table
    options.fileEncoding = "UTF-8"
    options.actionOnExistingFile = (QgsVectorFileWriter.ActionOnExistingFile.CreateOrOverwriteLayer
                                    if os.path.exists(path) else
                                    QgsVectorFileWriter.ActionOnExistingFile.CreateOrOverwriteFile)
    error, message, _, _ = QgsVectorFileWriter.writeAsVectorFormatV3(
        template, path, QgsProject.instance().transformContext(), options)
    if error != QgsVectorFileWriter.WriterError.NoError:
        return None, "Impossible de créer {} : {}".format(os.path.basename(path), message)
    layer = QgsVectorLayer("{}|layername={}".format(path, table), name, "ogr")
    if not layer.isValid():
        return None, "Impossible d'ouvrir la table « {} » de {}.".format(table, os.path.basename(path))
    return layer, None


def ensure_layer(kind, name, table, geometry, fields, style, parent=None):
    """Couche du plugin de ce type : celle du projet, ou une nouvelle, dans un GeoPackage
    ou en couche temporaire selon le choix de l'utilisateur.

    geometry : "Point" ou "LineString" ; fields : champs au format des URI "memory"
    (field=nom:type&...) ; style : fonction appliquée à une nouvelle couche (symbole,
    étiquettes). Retourne (couche, message) : couche None si l'utilisateur a annulé
    (message None) ou en cas d'erreur (message)."""
    layer = find_layer(kind)
    if layer is not None:
        return layer, None
    path = _ask_storage(parent, name)
    if path is None:
        return None, None
    project = QgsProject.instance()
    crs = project.crs() if project.crs().isValid() else WGS84
    layer = QgsVectorLayer("{}?crs={}&{}".format(geometry, crs.authid() or "EPSG:4326", fields), name, "memory")
    if path:
        layer, error = _open_table(path, table, name, layer)
        if layer is None:
            return None, error
    layer.setCustomProperty(LAYER_KEY, kind)
    style(layer)
    project.addMapLayer(layer)
    return layer, None


def write_feature(layer, feature, fid=None):
    """Ajoute l'entité à la couche, ou remplace celle d'identifiant fid si elle y est
    encore. Retourne l'identifiant de l'entité écrite, ou None en cas d'échec."""
    if fid is not None and layer.getFeature(fid).isValid():
        attrs = {i: feature.attribute(i) for i in range(layer.fields().count())
                 if layer.fields().at(i).name() != "fid"}
        if layer.isEditable():
            ok = layer.changeGeometry(fid, feature.geometry())
            ok = all([layer.changeAttributeValue(fid, i, v) for i, v in attrs.items()]) and ok
        else:
            provider = layer.dataProvider()
            ok = provider.changeGeometryValues({fid: feature.geometry()})
            ok = provider.changeAttributeValues({fid: attrs}) and ok
    elif layer.isEditable():
        ok = layer.addFeature(feature)
        fid = feature.id()
    else:
        ok, written = layer.dataProvider().addFeatures([feature])
        fid = written[0].id() if ok and written else None
    if not ok:
        return None
    layer.updateExtents()
    layer.triggerRepaint()
    return fid
