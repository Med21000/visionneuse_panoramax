# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Cédric COCHART
# SPDX-License-Identifier: GPL-2.0-or-later
"""Tests de geometry.py : points 3D sur une surface, triangulation 3D, décomposition."""

import math
import unittest

from support import LAT0, LON0, module, point

geometry = module("geometry")
GeometryError = geometry.GeometryError
ORIGIN = (LON0, LAT0)


def click(yaw, elev, at=(0.0, 0.0)):
    lon, lat = point(*at)
    return {"lon": lon, "lat": lat, "yaw": yaw, "elev": elev}


def aim(cam, target):
    """(cap, élévation) de cam vers target, points du repère local."""
    dx, dy, dz = (t - c for t, c in zip(target, cam))
    return math.degrees(math.atan2(dx, dy)) % 360, math.degrees(math.atan2(dz, math.hypot(dx, dy)))


class Basics(unittest.TestCase):
    def assertPoint(self, p, expected, places=6):
        for a, b in zip(p, expected):
            self.assertAlmostEqual(a, b, places=places)

    def test_direction(self):
        self.assertPoint(geometry.direction(0, 0), (0, 1, 0))
        self.assertPoint(geometry.direction(90, 0), (1, 0, 0))
        self.assertPoint(geometry.direction(0, 90), (0, 0, 1))
        self.assertAlmostEqual(sum(c * c for c in geometry.direction(123, -17)), 1.0)

    def test_round_trip(self):
        p = (12.5, -40.25, 231.0)
        self.assertPoint(geometry.to_local(*geometry.to_geographic(p, ORIGIN), ORIGIN), p)

    def test_decompose(self):
        d = geometry.decompose((0, 0, 0), (3, 4, 12))
        self.assertAlmostEqual(d["d3"], 13)
        self.assertAlmostEqual(d["horizontal"], 5)
        self.assertAlmostEqual(d["vertical"], 12)
        self.assertIsNone(d["along"])
        d = geometry.decompose((0, 0, 0), (3, 4, 12), axis=0)  # route nord-sud
        self.assertAlmostEqual(d["along"], 4)
        self.assertAlmostEqual(d["across"], 3)
        d = geometry.decompose((0, 0, 0), (3, 4, 0), axis=90)
        self.assertAlmostEqual(d["along"], 3)
        self.assertAlmostEqual(d["across"], 4)

    def test_solve3(self):
        a = [[4.0, 1.0, 2.0], [1.0, 3.0, 0.0], [2.0, 0.0, 5.0]]
        x = (1.0, -2.0, 3.0)
        b = [sum(a[i][j] * x[j] for j in range(3)) for i in range(3)]
        self.assertPoint(geometry._solve3(a, b), x)
        with self.assertRaises(GeometryError):
            geometry._solve3([[1, 2, 3], [2, 4, 6], [0, 0, 1]], [1, 2, 3])


class Surfaces(Basics):
    def test_on_ground(self):
        p, incidence = geometry.on_ground(click(90, math.degrees(math.atan2(-2, 10))), 2.0, ORIGIN)
        self.assertPoint(p, (10, 0, 0))
        self.assertEqual(incidence, 90.0)

    def test_camera(self):
        c, u = geometry.camera(click(90, 0, at=(5, -3)), 1.9, ORIGIN, ground_z=210.0)
        self.assertPoint(c, (5, -3, 211.9))
        self.assertPoint(u, (1, 0, 0))

    def test_facade(self):
        plane = geometry.vertical_plane((10, -5, 0), (10, 5, 0))  # mur nord-sud à 10 m à l'est
        target = (10.0, 3.0, 4.5)
        yaw, elev = aim((0, 0, 2.0), target)
        p, incidence = geometry.on_plane(click(yaw, elev), 2.0, ORIGIN, *plane, ground_z=0.0)
        self.assertPoint(p, target)
        self.assertAlmostEqual(incidence, math.degrees(math.asin(10 / math.hypot(10, 3, 2.5))), places=6)

    def test_facing_and_horizontal_planes(self):
        cam = (0.0, 0.0, 2.0)
        foot = (6.0, 8.0, 0.0)
        plane = geometry.facing_plane(cam, foot)
        top = (6.0, 8.0, 7.0)
        p, incidence = geometry.on_plane(click(*aim(cam, top)), 2.0, ORIGIN, *plane, ground_z=0.0)
        self.assertPoint(p, top)
        target = (-4.0, 12.0, 1.0)  # sur un plan horizontal à 1 m du sol
        p, _ = geometry.on_plane(click(*aim(cam, target)), 2.0, ORIGIN, (0, 0, 1.0), (0, 0, 1), ground_z=0.0)
        self.assertPoint(p, target)

    def test_plane_errors(self):
        plane = geometry.vertical_plane((10, -5, 0), (10, 5, 0))
        with self.assertRaises(GeometryError):  # visée parallèle au mur
            geometry.on_plane(click(0, 0), 2.0, ORIGIN, *plane, ground_z=0.0)
        with self.assertRaises(GeometryError):  # mur derrière la caméra
            geometry.on_plane(click(270, 0), 2.0, ORIGIN, *plane, ground_z=0.0)
        far = geometry.vertical_plane((geometry.MAX_RANGE + 10, -5, 0), (geometry.MAX_RANGE + 10, 5, 0))
        with self.assertRaises(GeometryError):
            geometry.on_plane(click(90, 0), 2.0, ORIGIN, *far, ground_z=0.0)
        with self.assertRaises(GeometryError):
            geometry.vertical_plane((1, 1, 0), (1.1, 1, 0))
        with self.assertRaises(GeometryError):
            geometry.facing_plane((0, 0, 2), (0.05, 0, 0))


class Triangulate3D(Basics):
    def test_exact(self):
        target = (12.0, 25.0, 6.0)
        cams = [(0.0, 0.0, 2.0), (15.0, 0.0, 2.2), (30.0, 10.0, 1.9)]
        rays = [(c, geometry.direction(*aim(c, target))) for c in cams]
        p, ranges, crossing = geometry.triangulate(rays)
        self.assertPoint(p, target)
        for r, c in zip(ranges, cams):
            self.assertAlmostEqual(r, math.dist(c, target), places=6)
        self.assertGreater(crossing, geometry.MIN_CROSSING)

    def test_errors(self):
        u = geometry.direction(0, 0)
        with self.assertRaises(GeometryError):  # visées parallèles
            geometry.triangulate([((0, 0, 2), u), ((1, 0, 2), u)])
        target = (5.0, 10.0, 2.0)
        cams = [(0.0, 0.0, 2.0), (10.0, 0.0, 2.0)]
        rays = [(c, tuple(-v for v in geometry.direction(*aim(c, target)))) for c in cams]
        with self.assertRaises(GeometryError):  # croisement derrière les photos
            geometry.triangulate(rays)


if __name__ == "__main__":
    unittest.main()
