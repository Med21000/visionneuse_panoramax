# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Cédric COCHART
# SPDX-License-Identifier: GPL-2.0-or-later
"""Tests de ground.py : direction d'un pixel, lancer de rayon sur le terrain, hauteur."""

import math
import unittest

from support import LAT0, LON0, module

ground = module("ground")
GroundError = ground.GroundError


def elev_to(drop, dist):
    """Élévation (degrés) d'un point situé `drop` m plus haut que la caméra, à `dist` m."""
    return math.degrees(math.atan2(drop, dist))


class ViewDirection(unittest.TestCase):
    def test_center(self):
        yaw, elev = ground.view_direction(37, -12, 60, 0, 0, 800)
        self.assertAlmostEqual(yaw, 37)
        self.assertAlmostEqual(elev, -12)

    def test_edges(self):
        yaw, elev = ground.view_direction(90, 0, 60, 0, -400, 800)  # bord haut
        self.assertAlmostEqual(yaw, 90)
        self.assertAlmostEqual(elev, 30)
        f = 400 / math.tan(math.radians(30))
        yaw, elev = ground.view_direction(350, 0, 60, 200, 0, 800)  # à droite : cap croissant
        self.assertAlmostEqual(yaw, (350 + math.degrees(math.atan(200 / f))) % 360)
        self.assertAlmostEqual(elev, 0)


class Intersect(unittest.TestCase):
    def test_flat(self):
        for dist in (2.0, 10.0, 35.0):
            s, z = ground.intersect(ground.flat_profile(), 2.0, elev_to(-2.0, dist))
            self.assertAlmostEqual(s, dist, places=6)
            self.assertAlmostEqual(z, 0.0, places=6)

    def test_slope(self):
        profile = [(0.0, 100.0), (60.0, 106.0)]  # pente de 10 % qui monte
        s, z = ground.intersect(profile, 2.0, 0.0)  # visée horizontale à 102 m
        self.assertAlmostEqual(s, 20.0, places=6)
        self.assertAlmostEqual(z, 102.0, places=6)

    def test_broken_profile(self):
        profile = [(0.0, 0.0), (10.0, 0.0), (20.0, 3.0), (60.0, 3.0)]  # talus de 3 m
        s, z = ground.intersect(profile, 2.0, 0.0)
        self.assertAlmostEqual(s, 10.0 + 10.0 * 2 / 3, places=6)
        self.assertAlmostEqual(z, 2.0, places=6)

    def test_no_ground(self):
        with self.assertRaises(GroundError):
            ground.intersect(ground.flat_profile(), 2.0, 5.0)
        with self.assertRaises(GroundError):  # au-delà de MAX_DISTANCE
            ground.intersect(ground.flat_profile(), 2.0, elev_to(-2.0, ground.MAX_DISTANCE + 10))


class MeasureHeight(unittest.TestCase):
    def click(self, elev, yaw=45.0, pic="p1"):
        return {"lon": LON0, "lat": LAT0, "yaw": yaw, "elev": elev, "pic": pic}

    def test_pole(self):
        base, top = self.click(elev_to(-2.0, 10.0)), self.click(elev_to(3.0, 10.0))
        result = ground.measure_height(2.0, base, top)
        self.assertAlmostEqual(result["value"], 5.0, places=6)
        self.assertAlmostEqual(result["ranges"][0], 10.0, places=6)
        self.assertGreater(result["uncertainty"], 0.0)

    def test_wall_clicked_aside(self):
        # Sommet à 20° de côté, sur le plan vertical face à la caméra passant par le pied
        reach = 10.0 / math.cos(math.radians(20))
        base, top = self.click(elev_to(-2.0, 10.0)), self.click(elev_to(3.0, reach), yaw=65.0)
        self.assertAlmostEqual(ground.measure_height(2.0, base, top)["value"], 5.0, places=6)

    def test_errors(self):
        base = self.click(elev_to(-2.0, 10.0))
        with self.assertRaises(GroundError):
            ground.measure_height(2.0, base, self.click(10.0, pic="p2"))
        with self.assertRaises(GroundError):
            ground.measure_height(2.0, base, self.click(-20.0))
        with self.assertRaises(GroundError):
            ground.measure_height(2.0, base, self.click(10.0, yaw=150.0))


if __name__ == "__main__":
    unittest.main()
