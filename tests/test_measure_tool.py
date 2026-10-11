# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Cédric COCHART
# SPDX-License-Identifier: GPL-2.0-or-later
"""Tests de measure_tool.py : préréglages (largeurs, hauteur), mesure libre, recalage sur la carte.

Scène : sol plat à l'altitude 0, route nord-sud (axe 0°), caméra à CAMERA m. Les clics
sont calculés depuis des points 3D connus, que les mesures doivent retrouver.
"""

import math
import unittest
from unittest import mock

from support import Stub, distance, install_qgis_stub, module, point

install_qgis_stub()
measure_tool = module("measure_tool")
calibration = module("calibration")

CAMERA = 1.9


class FakeItem:
    """Dessin sur la carte remplacé : garde seulement les dernières données."""

    def __init__(self, canvas):
        self.data = None

    def set_data(self, *data):
        self.data = data

    def remove(self):
        pass


def click(target, photo=(0.0, 0.0), pic="p1", camera=CAMERA, **extra):
    """Clic depuis la photo en photo (est, nord) vers target (est, nord, altitude)."""
    dx, dy, dz = target[0] - photo[0], target[1] - photo[1], target[2] - camera
    lon, lat = point(*photo)
    c = {"lon": lon, "lat": lat, "pic": pic, "yaw": math.degrees(math.atan2(dx, dy)) % 360,
         "elev": math.degrees(math.atan2(dz, math.hypot(dx, dy))), "axis": 0.0, "sequence": "s1"}
    c.update(extra)
    return c


class Base(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(measure_tool, "MeasureItem", FakeItem)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.tool = measure_tool.MeasureTool(Stub())
        self.tool.camera_height = CAMERA

    def measure(self, mode, clicks, surface=None):
        self.tool.set_mode(mode)
        if surface:
            self.tool.set_surface(surface)
        for c in clicks:
            self.tool.add_click(c)
        return self.tool.current

    def assertValue(self, m, expected, delta=1e-6):
        self.assertIsNone(m.error)
        self.assertIsNotNone(m.result)
        self.assertAlmostEqual(m.result["value"], expected, delta=delta)


class Presets(Base):
    def test_road_width(self):
        m = self.measure("road", [click((-3.0, 8.0, 0.0)), click((4.0, 12.0, 0.0))])
        self.assertValue(m, 7.0)
        self.assertGreater(m.result["uncertainty"], 0.0)
        foot = m.result["points"][1]  # pied de la perpendiculaire, en face du premier bord
        self.assertLess(distance(foot, point(4.0, 8.0)), 1e-6)
        self.assertIn("Largeur perpendiculaire", m.status())

    def test_oblique_road(self):
        a = math.radians(30.0)  # axe de la route orienté à 30° : bords séparés de 6 m en travers
        along, across = (math.sin(a), math.cos(a)), (math.cos(a), -math.sin(a))
        p1 = (-2.0, 9.0)
        p2 = (p1[0] + 6.0 * across[0] + 3.0 * along[0], p1[1] + 6.0 * across[1] + 3.0 * along[1])
        m = self.measure("road", [click(p1 + (0.0,), axis=30.0), click(p2 + (0.0,), axis=30.0)])
        self.assertValue(m, 6.0)
        foot = (p1[0] + 6.0 * across[0], p1[1] + 6.0 * across[1])
        self.assertLess(distance(m.result["points"][1], point(*foot)), 1e-6)

    def test_road_without_axis(self):
        m = self.measure("road", [click((-3.0, 8.0, 0.0), axis=None), click((4.0, 12.0, 0.0), axis=None)])
        self.assertIsNotNone(m.error)
        self.assertIsNone(m.result)

    def test_width(self):
        m = self.measure("width", [click((-3.0, 8.0, 0.0)), click((4.0, 12.0, 0.0))])
        self.assertValue(m, math.hypot(7.0, 4.0))
        for r, target in zip(m.result["ranges"], ((-3.0, 8.0), (4.0, 12.0))):
            self.assertAlmostEqual(r, math.hypot(*target), places=6)

    def test_height(self):
        m = self.measure("height", [click((5.0, 5.0, 0.0)), click((5.0, 5.0, 4.0))])
        self.assertValue(m, 4.0)

    def test_height_errors(self):
        m = self.measure("height", [click((5.0, 5.0, 0.0)), click((5.0, 5.0, 4.0), pic="p2")])
        self.assertIsNotNone(m.error)
        self.assertEqual(len(m.clicks), 1)  # clic refusé, à refaire
        m = self.measure("height", [click((5.0, 5.0, 0.0)), click((5.0, 5.0, 0.0)), click((5.0, 5.0, -1.0))])
        self.assertIsNotNone(m.error)

    def test_calibrated_camera_height(self):
        cal = calibration.Calibration()
        cal.heights["s1"] = [(2.4, 0.01)]
        self.tool.calibration = cal
        clicks = [click((-3.0, 8.0, 0.0), camera=2.4), click((4.0, 12.0, 0.0), camera=2.4)]
        m = self.measure("road", clicks)
        self.assertValue(m, 7.0)
        self.assertIn("(calée)", m.status())

    def test_finished_measures_kept(self):
        self.measure("width", [click((-3.0, 8.0, 0.0)), click((4.0, 12.0, 0.0))])
        self.tool.add_click(click((0.0, 6.0, 0.0)))  # nouvelle mesure : la précédente est gardée
        self.assertEqual(len(self.tool.done), 1)
        self.assertEqual(len(self.tool.measures()), 2)


class Free(Base):
    def test_ground(self):
        m = self.measure("free", [click((1.0, 6.0, 0.0)), click((-2.0, 10.0, 0.0))], surface="ground")
        self.assertValue(m, 5.0)

    def test_facade(self):
        self.tool.set_mode("free")
        self.tool.set_surface("facade")
        self.assertTrue(self.tool.defining)
        for target in ((8.0, 2.0, 0.0), (8.0, 14.0, 0.0)):  # pied du mur, à l'est
            self.tool.add_click(click(target))
        self.assertFalse(self.tool.defining)
        for target in ((8.0, 5.0, 1.0), (8.0, 9.0, 4.0)):
            self.tool.add_click(click(target))
        self.assertValue(self.tool.current, 5.0, delta=1e-5)

    def test_vertical(self):
        m = self.measure("free", [click((-4.0, 9.0, 0.0)), click((-4.0, 9.0, 6.5))], surface="vertical")
        self.assertValue(m, 6.5)

    def test_horizontal(self):
        self.tool.set_plane_height(1.0)
        m = self.measure("free", [click((2.0, 6.0, 1.0)), click((-1.0, 10.0, 1.0))], surface="horizontal")
        self.assertValue(m, 5.0)

    def test_tri3d(self):
        a, b = (6.0, 20.0, 5.0), (9.0, 24.0, 5.0)
        photos = ((0.0, 0.0), (12.0, 2.0))
        clicks = [click(a, photos[0], "p1"), click(a, photos[1], "p2"),
                  click(b, photos[0], "p1"), click(b, photos[1], "p2")]
        m = self.measure("free", clicks, surface="tri3d")
        self.assertValue(m, 5.0)

    def test_tri3d_same_photo(self):
        m = self.measure("free", [click((6.0, 20.0, 5.0)), click((6.0, 20.0, 5.0))], surface="tri3d")
        self.assertIsNotNone(m.error)


class Relocation(Base):
    def test_height_relocated_on_map(self):
        # Hauteur de caméra fausse : la hauteur mesurée l'est aussi...
        clicks = [click((5.0, 5.0, 0.0)), click((5.0, 5.0, 4.0))]
        self.tool.camera_height = 1.5
        m = self.measure("height", clicks)
        self.assertNotAlmostEqual(m.result["value"], 4.0, places=1)
        # ... mais plus une fois le pied recalé sur la carte à sa vraie place
        m.relocate(0, *point(5.0, 5.0))
        self.assertValue(m, 4.0)
        self.assertEqual(len(m.picks()), 1)  # seul le pied est recalable

    def test_width_relocated(self):
        m = self.measure("width", [click((-3.0, 8.0, 0.0)), click((4.0, 12.0, 0.0))])
        m.relocate(1, *point(4.0, 20.0))
        # Position de la carte, altitude du rayon de visée d'origine prolongé jusque-là
        z = CAMERA - CAMERA * math.hypot(4.0, 20.0) / math.hypot(4.0, 12.0)
        self.assertValue(m, math.sqrt(7.0 ** 2 + 12.0 ** 2 + z ** 2))
        self.assertIn("recalé", m.status())
        dyaw, delev = m.result["shifts"][1]  # le repère se déplace de côté dans la visionneuse
        self.assertAlmostEqual(dyaw, math.degrees(math.atan2(4, 20) - math.atan2(4, 12)), places=6)
        self.assertAlmostEqual(delev, 0.0, places=6)

    def test_reaim(self):
        m = self.measure("width", [click((-3.0, 8.0, 0.0)), click((4.0, 12.0, 0.0))])
        m.reaim(1, click((4.0, 16.0, 0.0)))
        self.assertValue(m, math.hypot(7.0, 8.0))


if __name__ == "__main__":
    unittest.main()
