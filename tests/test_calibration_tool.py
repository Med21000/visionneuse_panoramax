# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Cédric COCHART
# SPDX-License-Identifier: GPL-2.0-or-later
"""Tests de measure.CalibrationTool : clics de calage (inclinaison, hauteur de caméra, cap)."""

import math
import unittest
from unittest import mock

from support import LAT0, LON0, Stub, bearing, install_qgis_stub, module, point
from test_calibration import seen as tilted  # direction vue par une caméra inclinée

install_qgis_stub()
measure = module("measure")
calibration = module("core.calibration")


class FakeItem:
    def __init__(self, canvas):
        self.data = None

    def set_data(self, *data):
        self.data = data

    def remove(self):
        pass


def click(yaw, elev, pic="p1", sequence="s1", **extra):
    c = {"pic": pic, "sequence": sequence, "lon": LON0, "lat": LAT0, "yaw": yaw, "elev": elev,
         "raw": [yaw, elev]}
    c.update(extra)
    return c


def elev_to(drop, dist):
    return math.degrees(math.atan2(drop, dist))


class Base(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(measure, "SightingsItem", FakeItem)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.cal = calibration.Calibration()
        self.shown = ("p1", "s1")
        self.ref = ("ground", 3.0)
        self.tool = measure.CalibrationTool(Stub(), self.cal, lambda: self.shown, lambda: self.ref)


class Tilt(Base):
    def vertical(self, up, yaw, dist=10.0, pic="p1"):
        """Clics pied puis sommet d'un poteau de 6 m, caméra à 2 m."""
        return [click(*tilted(up, yaw, elev_to(-2.0, dist)), pic=pic),
                click(*tilted(up, yaw, elev_to(4.0, dist)), pic=pic)]

    def test_two_objects(self):
        up = calibration.vector(60.0, 87.5)  # 2,5° vers 60°
        self.tool.set_mode("tilt")
        for c in self.vertical(up, 10.0):
            text = self.tool.add_click(c)
        self.assertIn("Correction partielle", text)
        for c in self.vertical(up, 100.0):
            text = self.tool.add_click(c)
        self.assertTrue(text.startswith("Inclinaison de la photo : 2,50° vers 60°"), text)
        self.assertNotIn("partielle", text)
        self.assertAlmostEqual(self.cal.tilt("p1")["tilt"], 2.5, places=3)

    def test_top_on_other_photo_restarts(self):
        self.tool.set_mode("tilt")
        bottom, _ = self.vertical((0, 0, 1), 10.0)
        self.tool.add_click(bottom)
        _, top = self.vertical((0, 0, 1), 10.0, pic="p2")
        self.assertIn("sommet du même objet", self.tool.add_click(top))  # nouveau pied, sur p2
        self.assertEqual(len(self.tool.clicks), 1)
        self.assertIsNone(self.cal.tilt("p1"))

    def test_error(self):
        self.tool.set_mode("tilt")
        bottom, top = self.vertical((0, 0, 1), 10.0)
        self.tool.add_click(top)
        text = self.tool.add_click(bottom)  # sommet cliqué avant le pied
        self.assertTrue(text.startswith("Cliquez d'abord le pied"), text)
        self.assertEqual(self.tool.clicks, [])
        self.assertIsNone(self.cal.tilt("p1"))

    def test_clear(self):
        self.tool.set_mode("tilt")
        for c in self.vertical(calibration.vector(0.0, 88.0), 10.0):
            self.tool.add_click(c)
        self.assertIsNotNone(self.cal.tilt("p1"))
        self.tool.clear()
        self.assertIsNone(self.cal.tilt("p1"))


class CameraHeight(Base):
    def test_ground_reference(self):
        self.tool.set_mode("camera")
        self.assertTrue(self.tool.needs_profile())
        h = 2.2  # deux traits au sol à 8 et 11 m : 3 m
        for d in (8.0, 11.0):
            text = self.tool.add_click(click(0.0, elev_to(-h, d)))
        self.assertAlmostEqual(self.tool.new_camera, h, places=4)
        self.assertTrue(text.startswith("Caméra à 2,20 m"), text)
        self.assertAlmostEqual(self.cal.camera("s1")["height"], h, places=4)
        self.tool.clear()
        self.assertIsNone(self.cal.camera("s1"))

    def test_height_reference(self):
        self.ref = ("height", 4.0)
        self.tool.set_mode("camera")
        self.assertTrue(self.tool.needs_profile())  # pied au sol
        self.tool.add_click(click(0.0, elev_to(-1.8, 10.0)))
        self.assertFalse(self.tool.needs_profile())  # sommet
        self.tool.add_click(click(0.0, elev_to(2.2, 10.0)))
        self.assertAlmostEqual(self.tool.new_camera, 1.8, places=4)

    def test_errors(self):
        self.tool.set_mode("camera")
        text = self.tool.add_click(click(0.0, -10.0, sequence=None))
        self.assertTrue(text.startswith("Séquence de la photo inconnue"), text)
        self.ref = ("ground", 500.0)
        self.tool.add_click(click(0.0, -10.0))
        self.assertTrue(self.tool.add_click(click(0.0, -8.0)).startswith("Longueur mesurée trop"))
        self.assertIsNone(self.cal.camera("s1"))


class Heading(Base):
    def test_landmark(self):
        self.tool.set_mode("heading")
        self.assertFalse(self.tool.needs_profile())
        mark = point(0, 80)
        gps = (LON0, LAT0)
        # Clic sur une photo déjà recalée : le repère part de la position GPS d'origine
        c = click(bearing(gps, mark) - 1.5, -2.0, lon=LON0 + 0.0001, gps=list(gps), gps_accuracy=1.5,
                  accuracy=0.5, precise=True)
        self.assertIn("sur la carte QGIS", self.tool.add_click(c))
        self.assertTrue(self.tool.waiting_map())
        text = self.tool.add_map_point(*mark)
        self.assertTrue(text.startswith("Repère 1 à 80 m (écart de cap brut 1,50°)"), text)
        obs = self.cal.landmarks["s1"][0]
        self.assertEqual((obs["lon"], obs["lat"]), gps)
        self.assertEqual(obs["prior"], calibration.SURVEY_PRIOR)  # cap précis et GPS ≤ 2 m
        self.assertIsNotNone(self.tool.item.data)  # visée dessinée sur la carte
        self.assertEqual(len(self.tool.item.data[0]), 1)
        # Autre photo de la séquence : la visée de ce repère n'y est pas dessinée
        self.shown = ("p2", "s1")
        self.tool.refresh()
        self.assertEqual(self.tool.item.data, ([], None))
        self.shown = ("p1", "s1")
        self.tool.refresh()
        self.assertEqual(len(self.tool.item.data[0]), 1)
        self.tool.clear()
        self.assertNotIn("s1", self.cal.landmarks)

    def test_map_point_without_photo_click(self):
        self.tool.set_mode("heading")
        self.tool.add_map_point(*point(0, 80))
        self.assertEqual(self.cal.landmarks, {})

    def test_errors(self):
        self.tool.set_mode("heading")
        text = self.tool.add_click(click(0.0, -2.0, sequence=None))
        self.assertTrue(text.startswith("Séquence de la photo inconnue"), text)
        self.assertFalse(self.tool.waiting_map())
        self.tool.add_click(click(0.0, -2.0))
        text = self.tool.add_map_point(*point(0, 3))  # repère trop proche
        self.assertTrue(text.startswith("Repère à 3,0 m de la photo"), text)
        self.assertEqual(self.cal.landmarks, {})


class Marks(Base):
    def test_viewer_marks(self):
        self.tool.set_mode("tilt")
        self.tool.add_click(click(10.0, -5.0, pos=[0.1, -0.2]))
        marks = self.tool.viewer_marks()
        self.assertEqual(marks["points"], [{"pic": "p1", "yaw": 0.1, "pitch": -0.2, "abs_yaw": 10.0, "elev": -5.0}])
        self.assertTrue(marks["beside"])
        self.tool.reset()
        self.assertEqual(self.tool.viewer_marks()["points"], [])


if __name__ == "__main__":
    unittest.main()
