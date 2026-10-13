# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Cédric COCHART
# SPDX-License-Identifier: GPL-2.0-or-later
"""Tests de measure_panel.py : texte du calage rappelé sous les mesures."""

import unittest

from support import LAT0, LON0, bearing, install_qgis_stub, module, point

install_qgis_stub()
measure_panel = module("measure_panel")
calibration = module("core.calibration")


class CalibrationText(unittest.TestCase):
    def setUp(self):
        self.cal = calibration.Calibration()

    def text(self, pic="p1", sequence="s1", at=(LON0, LAT0)):
        return measure_panel.calibration_text(self.cal, pic, sequence, *at)

    def test_empty(self):
        self.assertEqual(self.text(), "")
        self.assertEqual(self.text(pic=None, sequence=None), "")

    def test_tilt_and_camera(self):
        self.cal._tilts["p1"] = {"up": (0, 0, 1), "tilt": 1.84, "toward": 0, "residual": None, "partial": True,
                                 "count": 1}
        self.cal.heights["s1"] = [(2.1, 0.05), (2.1, 0.05)]
        self.assertEqual(self.text(), "Calage : inclinaison 1,8° corrigée en partie (1 objet) · "
                                      "caméra à 2,10 m (±0,04 m, 2 références), dont 2 d'une version précédente, "
                                      "sans l'inclinaison automatique : refaites le calage")

    def test_automatic_tilt(self):
        up = calibration.up_from_components(1.5, -0.8, 30.0)
        self.cal.set_auto("p1", {"sequence": "s1", "heading": 30.0, "lidar": [1.0, -0.6],
                                 "verticals": {"up": list(up), "count": 140, "sigma": 0.08}})
        self.assertEqual(self.text(), "Calage : inclinaison 1,7° corrigée automatiquement (140 verticales de la "
                                      "photo, ±0,1°)")
        for i in range(3):
            self.cal.set_auto("v{}".format(i), self.cal.auto["p1"])
        self.cal.set_auto("p2", {"sequence": "s1", "heading": 30.0, "lidar": [1.0, 0.0], "verticals": None})
        self.assertEqual(self.text(pic="p2"), "Calage : inclinaison 1,5° estimée par la pente et le dévers LiDAR HD "
                                              "(±0,3°, modèle vérifié sur 4 photos de la séquence)")

    def test_one_object_and_automatic(self):
        up = calibration.up_from_components(1.5, -0.8, 30.0)
        self.cal.set_auto("p1", {"sequence": "s1", "heading": 30.0, "lidar": None,
                                 "verticals": {"up": list(up), "count": 140, "sigma": 0.08}})
        self.cal.add_vertical("p1", (40.0, -10.0), (40.0, 20.0))
        self.assertRegex(self.text(), r"^Calage : inclinaison \d,\d° corrigée \(1 objet cliqué, complété par les "
                                      r"verticales de la photo, ±0,\d°\)$")

    def test_legacy_camera_height(self):
        self.cal.heights["s1"] = [(2.1, 0.05)]
        self.assertIn("dont 1 d'une version précédente, sans l'inclinaison automatique : refaites le calage",
                      self.text())

    def test_heading(self):
        mark = point(0, 80)
        self.cal.add_landmark("s1", "p1", LON0, LAT0, bearing((LON0, LAT0), mark) - 2.0, -2.0, *mark, 3.0, False)
        text = self.text()
        self.assertTrue(text.startswith("Calage : cap +"), text)
        self.assertIn("1 repère", text)
        self.assertNotIn("position", text)  # un seul repère : cap seulement

    def test_out_of_reach(self):
        mark = point(0, 80)
        self.cal.add_landmark("s1", "p1", LON0, LAT0, bearing((LON0, LAT0), mark), -2.0, *mark, 3.0, False)
        far = point(0, calibration.REACH + 100)
        self.assertIn("hors de portée", self.text(at=far))


if __name__ == "__main__":
    unittest.main()
