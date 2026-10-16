# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Cédric COCHART
# SPDX-License-Identifier: GPL-2.0-or-later
"""Tests de core/widths.py : largeur perpendiculaire à la route depuis un point triangulé.

Scène : route nord-sud (axe 0°), point triangulé A à 5 m à l'est et 10 m au nord de l'origine ;
la droite de mesure est donc la ligne est-ouest y = 10. Bord opposé B à 3 m à l'ouest (x = -3),
soit 8 m de largeur. Les clics sont calculés depuis des positions connues de la caméra.
"""

import math
import unittest
from unittest import mock

from support import Stub, bearing, distance, install_qgis_stub, module, point

install_qgis_stub()
measure_tool = module("measure_tool")
widths = module("core.widths")
geometry = module("core.geometry")

BDTOPO = {"axis": 0.0, "source": "BD TOPO, RUE"}
TRACK = {"axis": 0.0, "source": "séquence"}


class FakeItem:
    def __init__(self, canvas, wall=False):
        self.data = None

    def set_data(self, *data):
        self.data = data

    def remove(self):
        pass


def edge(uncertainty=0.1):
    lon, lat = point(5.0, 10.0)
    return {"lon": lon, "lat": lat, "uncertainty": uncertainty, "name": "A"}


def click(camera=(0.0, 0.0), target=(-3.0, 10.0), accuracy=0.1, precise=True):
    """Clic sur le sol en target (est, nord) depuis la caméra en camera."""
    lon, lat = point(*camera)
    yaw = bearing((lon, lat), point(*target))
    return {"pic": "p1", "lon": lon, "lat": lat, "yaw": yaw, "elev": -10.0, "raw": [yaw, -10.0],
            "pos": [0.1, -0.2], "accuracy": accuracy, "precise": precise, "sequence": "s1"}


class Base(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(measure_tool, "MeasureItem", FakeItem)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.tool = measure_tool.MeasureTool(Stub())
        self.tool.camera_height = 1.9


class Cross(unittest.TestCase):
    def test_intersection(self):
        (x, y), t, s, angle = widths.cross((5.0, 10.0), 0.0, (0.0, 0.0), math.degrees(math.atan2(-3.0, 10.0)))
        self.assertAlmostEqual(x, -3.0, places=6)
        self.assertAlmostEqual(y, 10.0, places=6)
        self.assertAlmostEqual(t, -8.0, places=4)
        self.assertAlmostEqual(s, math.hypot(3.0, 10.0), places=6)
        self.assertAlmostEqual(angle, math.degrees(math.atan2(10.0, 3.0)), places=6)

    def test_parallel_sighting_refused(self):
        # Photo juste en face de A : la visée du bord opposé longe la droite de mesure
        with self.assertRaises(geometry.GeometryError):
            widths.cross((5.0, 10.0), 0.0, (0.0, 10.0), 270.0)

    def test_line_behind(self):
        with self.assertRaises(geometry.GeometryError):
            widths.cross((5.0, 10.0), 0.0, (0.0, 20.0), 30.0)


class Width(Base):
    def test_value_and_marks(self):
        m = widths.PerpendicularWidth(self.tool, edge(), BDTOPO, click())
        self.assertIsNone(m.error)
        self.assertAlmostEqual(m.result["value"], 8.0, places=4)
        self.assertLess(m.result["uncertainty"], 0.3)
        self.assertIn("8,00 m", m.status())
        self.assertIn("point triangulé A", m.status())
        self.assertEqual(m.label()[:1], "↔")
        self.assertEqual(m.picks(), [])
        b = m.result["points"][1]
        self.assertLess(distance(b, point(-3.0, 10.0)), 1e-3)
        # Repères de la visionneuse : A et B sur la photo du clic
        marks = m.viewer_marks()["points"]
        self.assertEqual([p["pic"] for p in marks], ["p1", "p1"])
        self.assertAlmostEqual(marks[0]["abs_yaw"], bearing(point(0.0, 0.0), point(5.0, 10.0)), places=6)

    def test_terrain_and_camera_height_without_effect(self):
        m = widths.PerpendicularWidth(self.tool, edge(), BDTOPO, click())
        self.tool.camera_height = 2.5
        m.compute()
        self.assertAlmostEqual(m.result["value"], 8.0, places=4)

    def test_uncertainty_sources(self):
        base = widths.PerpendicularWidth(self.tool, edge(), BDTOPO, click()).result["uncertainty"]
        # Point de référence moins précis, axe de la séquence, GPS grand public : plus incertain
        for m in (widths.PerpendicularWidth(self.tool, edge(1.0), BDTOPO, click()),
                  widths.PerpendicularWidth(self.tool, edge(), TRACK, click()),
                  widths.PerpendicularWidth(self.tool, edge(), BDTOPO, click(accuracy=3.0, precise=False))):
            self.assertGreater(m.result["uncertainty"], base)
        # Visée plus en biais (photo plus proche de la droite) : plus incertain
        oblique = widths.PerpendicularWidth(self.tool, edge(), BDTOPO, click(camera=(0.0, 9.0)))
        self.assertAlmostEqual(oblique.result["value"], 8.0, places=4)
        self.assertGreater(oblique.result["uncertainty"], base)
        self.assertIn("rasante", oblique.status())

    def test_refused_click(self):
        m = widths.PerpendicularWidth(self.tool, edge(), BDTOPO, click(camera=(0.0, 10.0)))
        self.assertIsNone(m.result)
        self.assertIn("parallèle", m.error)

    def test_reaim(self):
        m = widths.PerpendicularWidth(self.tool, edge(), BDTOPO, click())
        m.reaim(1, click(target=(-4.0, 10.0)))
        self.assertAlmostEqual(m.result["value"], 9.0, places=4)
        m.reaim(0, click())
        self.assertIn("point de référence", m.error)

    def test_guide(self):
        a, b = widths.guide(edge(), 0.0)
        self.assertLess(distance(a, point(-10.0, 10.0)), 1e-3)
        self.assertLess(distance(b, point(20.0, 10.0)), 1e-3)


if __name__ == "__main__":
    unittest.main()
