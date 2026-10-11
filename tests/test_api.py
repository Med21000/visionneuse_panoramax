# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Cédric COCHART
# SPDX-License-Identifier: GPL-2.0-or-later
"""Tests de api.py : précision, correction différentielle et altitude lues dans les métadonnées."""

import unittest

from support import install_qgis_stub, module

install_qgis_stub()
api = module("api")


def item(accuracy=2.0, **exif):
    tags = {"Exif.GPSInfo." + k: v for k, v in exif.items()}
    return {"properties": {"quality:horizontal_accuracy": accuracy, "exif": tags}}


class Metadata(unittest.TestCase):
    def test_accuracy(self):
        self.assertEqual(api.item_accuracy(item(GPSHPositioningError="3/100")), 0.03)  # déclarée dans l'EXIF
        self.assertEqual(api.item_accuracy(item(4.0)), 4.0)  # annoncée par Panoramax
        self.assertIsNone(api.item_accuracy(item(None)))
        self.assertIsNone(api.item_accuracy({}))
        self.assertIsNone(api.exif_accuracy(item(GPSHPositioningError="0/1")))

    def test_differential(self):
        self.assertTrue(api.gps_differential(item(GPSDifferential="1")))
        self.assertFalse(api.gps_differential(item(GPSDifferential="0")))
        self.assertFalse(api.gps_differential(item()))

    def test_altitude(self):
        self.assertAlmostEqual(api.item_altitude(item(GPSAltitude="44090/100", GPSAltitudeRef="0")), 440.90)
        self.assertAlmostEqual(api.item_altitude(item(GPSAltitude="1250/100", GPSAltitudeRef="1")), -12.5)
        self.assertIsNone(api.item_altitude(item()))
        self.assertIsNone(api.item_altitude(item(GPSAltitude="abc")))

    def test_search_in_sequence(self):
        url = api.search_url(5.0, 47.0, 60, 20, "seq-1")
        self.assertIn("collections=seq-1", url)
        self.assertNotIn("collections", api.search_url(5.0, 47.0))


if __name__ == "__main__":
    unittest.main()
