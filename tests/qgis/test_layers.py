# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Cédric COCHART
# SPDX-License-Identifier: GPL-2.0-or-later
"""Tests de layers.py avec le vrai QGIS : opacité de la couche filaire (60 % à la création,
puis la dernière choisie par l'utilisateur), conservée par le filtre."""

import os
import tempfile
import unittest

from qgis_support import TMP, module

from qgis.core import QgsProject, QgsSettings

layers = module("layers")
filters = module("filters")


class Opacity(unittest.TestCase):
    def setUp(self):
        QgsSettings().remove(layers.OPACITY_SETTING)
        QgsProject.instance().clear()
        QgsProject.instance().layersAdded.connect(layers.watch_opacity)  # comme le plugin
        self.addCleanup(QgsProject.instance().layersAdded.disconnect, layers.watch_opacity)
        self.addCleanup(QgsProject.instance().clear)

    def test_default_then_remembered(self):
        layer = layers.create_tile_layer()
        self.assertAlmostEqual(layer.opacity(), 0.6)
        layer.setOpacity(0.35)  # réglage de l'utilisateur
        QgsProject.instance().clear()  # nouveau projet
        self.assertAlmostEqual(layers.create_tile_layer().opacity(), 0.35)

    def test_kept_by_filter(self):
        layer = layers.create_tile_layer()
        layer.setOpacity(0.8)
        layers.apply_filter(filters.PhotoFilter(kind="360"))
        self.assertAlmostEqual(layers.find_tile_layer().opacity(), 0.8)

    def test_layer_of_a_project(self):
        layer = layers.create_tile_layer()
        layer.setOpacity(0.9)
        path = os.path.join(tempfile.mkdtemp(dir=TMP), "filaire.qgz")
        self.assertTrue(QgsProject.instance().write(path))
        QgsSettings().setValue(layers.OPACITY_SETTING, 0.5)
        QgsProject.instance().clear()
        self.assertTrue(QgsProject.instance().read(path))
        loaded = layers.find_tile_layer()
        self.assertAlmostEqual(loaded.opacity(), 0.9)  # l'opacité du projet est gardée
        loaded.setOpacity(0.7)  # et un changement est mémorisé
        self.assertAlmostEqual(layers.saved_opacity(), 0.7)

    def test_damaged_setting(self):
        QgsSettings().setValue(layers.OPACITY_SETTING, "abc")
        self.assertEqual(layers.saved_opacity(), layers.DEFAULT_OPACITY)
        QgsSettings().setValue(layers.OPACITY_SETTING, 3)
        self.assertEqual(layers.saved_opacity(), 1.0)


if __name__ == "__main__":
    unittest.main()
