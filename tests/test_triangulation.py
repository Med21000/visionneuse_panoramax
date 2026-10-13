# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Cédric COCHART
# SPDX-License-Identifier: GPL-2.0-or-later
"""Tests de triangulation.py : intersection de visées en plan."""

import unittest

from support import bearing, distance, module, point

triangulation = module("triangulation")
TriangulationError = triangulation.TriangulationError


def sighting(photo, target, **extra):
    s = {"lon": photo[0], "lat": photo[1], "heading": bearing(photo, target)}
    s.update(extra)
    return s


class Projection(unittest.TestCase):
    def test_local_geographic_round_trip(self):
        lon, lat = point(123.4, -56.7)
        x, y = triangulation._local(lon, lat, *point(0, 0))
        self.assertAlmostEqual(x, 123.4, places=6)
        self.assertAlmostEqual(y, -56.7, places=6)

    def test_offset(self):
        origin = point(0, 0)
        for heading, east, north in ((0, 0, 100), (90, 100, 0), (180, 0, -100), (225, -70.7107, -70.7107)):
            x, y = triangulation._local(*triangulation.offset(origin[0], origin[1], heading, 100), *origin)
            self.assertAlmostEqual(x, east, places=3)
            self.assertAlmostEqual(y, north, places=3)

    def test_crossing_angle(self):
        self.assertAlmostEqual(triangulation.crossing_angle(10, 100), 90)
        self.assertAlmostEqual(triangulation.crossing_angle(0, 179), 1)
        self.assertAlmostEqual(triangulation.crossing_angle(350, 10), 20)
        self.assertAlmostEqual(triangulation.crossing_angle(30, 210), 0)


class Solve(unittest.TestCase):
    def setUp(self):
        self.target = point(15, 30)
        self.photos = [point(0, 0), point(20, 0), point(40, 5)]

    def test_two_exact_sightings(self):
        result = triangulation.solve([sighting(p, self.target, accuracy=2) for p in self.photos[:2]])
        self.assertLess(distance((result["lon"], result["lat"]), self.target), 0.001)
        for d, photo in zip(result["distances"], self.photos):
            self.assertAlmostEqual(d, distance(photo, self.target), places=2)
        self.assertLess(result["rms"], 0.001)
        self.assertFalse(result["gps_assumed"])

    def test_three_sightings_and_gps(self):
        sightings = [sighting(p, self.target, accuracy=a) for p, a in zip(self.photos, (2, None, 4))]
        result = triangulation.solve(sightings)
        self.assertLess(distance((result["lon"], result["lat"]), self.target), 0.001)
        self.assertTrue(result["gps_assumed"])
        self.assertEqual(result["gps"], max(4, triangulation.GPS_ACCURACY))

    def test_uncertainty_grows_with_gps_error(self):
        good = triangulation.solve([sighting(p, self.target, accuracy=1) for p in self.photos[:2]])
        bad = triangulation.solve([sighting(p, self.target, accuracy=5) for p in self.photos[:2]])
        self.assertGreater(bad["uncertainty"], good["uncertainty"])

    def test_noisy_sightings_stay_close(self):
        sightings = [sighting(p, self.target, accuracy=2) for p in self.photos]
        for s, noise in zip(sightings, (0.5, -0.5, 0.3)):
            s["heading"] += noise
        result = triangulation.solve(sightings)
        self.assertLess(distance((result["lon"], result["lat"]), self.target), 0.5)
        self.assertGreater(result["rms"], 0.0)

    def test_errors(self):
        target = self.target
        with self.assertRaises(TriangulationError):  # une seule visée
            triangulation.solve([sighting(self.photos[0], target)])
        with self.assertRaises(TriangulationError):  # visées parallèles
            triangulation.solve([{"lon": p[0], "lat": p[1], "heading": 0.0} for p in self.photos[:2]])
        behind = [sighting(p, target) for p in self.photos[:2]]
        for s in behind:
            s["heading"] = (s["heading"] + 180) % 360
        with self.assertRaises(TriangulationError):  # point derrière les photos
            triangulation.solve(behind)
        far = point(10, 800)
        with self.assertRaises(TriangulationError):  # plus de MAX_DISTANCE
            triangulation.solve([sighting(p, far) for p in (point(0, 0), point(100, 0))])


class HeadingError(unittest.TestCase):
    def test_values(self):
        self.assertEqual(triangulation.heading_error({}), triangulation.HEADING_ERROR)
        self.assertEqual(triangulation.heading_error({"precise": True, "accuracy": 1.5}),
                         triangulation.SURVEY_HEADING_ERROR)
        self.assertEqual(triangulation.heading_error({"precise": True, "accuracy": 5}),
                         triangulation.HEADING_ERROR)
        self.assertEqual(triangulation.heading_error({"precise": True, "accuracy": 1, "heading_error": 0.2}), 0.2)



class CommonErrors(unittest.TestCase):
    def test_same_sequence(self):
        """Visées d'une même séquence : erreurs de position et de cap communes, qui ne se
        compensent pas ; plus incertain que les mêmes visées depuis des séquences différentes."""
        target = point(0, 30)
        photos = [point(x, 0) for x in range(-20, 21, 5)]  # neuf photos le long d'une rue
        def sightings(seqs):
            return [{"lon": p[0], "lat": p[1], "heading": bearing(p, target), "accuracy": 2.0, "sequence": s}
                    for p, s in zip(photos, seqs)]
        independent = triangulation.solve(sightings("abcdefghi"))["uncertainty"]
        same = triangulation.solve(sightings("a" * 9))["uncertainty"]
        self.assertGreater(same, independent)
        self.assertGreaterEqual(same, 2.0)  # au moins le décalage GPS commun


if __name__ == "__main__":
    unittest.main()
