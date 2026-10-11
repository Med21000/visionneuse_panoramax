# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Cédric COCHART
# SPDX-License-Identifier: GPL-2.0-or-later
"""Tests de altitude.py avec le vrai QGIS : altitude GPS ellipsoïdale → altitude NGF-IGN69.

La grille du géoïde (RAF20) est téléchargée à la demande par PROJ : sans accès au réseau
ni grille installée, la conversion est impossible et le test est sauté."""

import unittest

from qgis_support import module

altitude = module("altitude")


class NgfAltitude(unittest.TestCase):
    def test_pouilly_en_auxois(self):
        # Photo imajbox du CD21 : altitude GPS 440,90 m, sol LiDAR HD 391,43 m sous la caméra
        z = altitude.ngf_altitude(4.554558, 47.262279, 440.90)
        if z is None:
            self.skipTest("grille RAF20 indisponible (réseau ?)")
        self.assertAlmostEqual(440.90 - z, 47.59, delta=0.05)  # géoïde à cet endroit
        self.assertAlmostEqual(z - 391.43, 1.88, delta=0.05)  # hauteur de caméra

    def test_outside_france_or_missing(self):
        self.assertIsNone(altitude.ngf_altitude(10.0, 50.0, 400.0))  # Allemagne : pas de grille IGN
        self.assertIsNone(altitude.ngf_altitude(4.55, 47.26, None))


if __name__ == "__main__":
    unittest.main()
