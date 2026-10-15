# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Cédric COCHART
# SPDX-License-Identifier: GPL-2.0-or-later
"""Tests de core/tools.py : clic et visée depuis une photo, état de l'inclinaison,
triangulateur sans carte, axe de la séquence."""

import unittest
from unittest import mock

from support import LAT0, LON0, bearing, module, point

tools = module("core.tools")
calibration = module("core.calibration")
metadata = module("core.metadata")


def item(pic="p1", sequence="s1", lon=LON0, lat=LAT0, azimuth=30.0, accuracy=4.0, altitude=None):
    props = {"view:azimuth": azimuth, "quality:horizontal_accuracy": accuracy}
    if altitude is not None:
        props["exif"] = {"Exif.GPSInfo.GPSAltitude": str(altitude)}
    return {"id": pic, "collection": sequence, "geometry": {"type": "Point", "coordinates": [lon, lat, 0]},
            "properties": props}


class PhotoClick(unittest.TestCase):
    def test_uncalibrated(self):
        cal = calibration.Calibration()
        c = tools.photo_click(item(), 370.0, -5.0, cal, pos=[0.1, -0.08])
        self.assertEqual(c["pic"], "p1")
        self.assertEqual(c["sequence"], "s1")
        self.assertAlmostEqual(c["raw"][0], 10.0)
        self.assertAlmostEqual(c["yaw"], 10.0)  # sans calage : direction brute
        self.assertAlmostEqual(c["elev"], -5.0)
        self.assertEqual((c["lon"], c["lat"]), (LON0, LAT0))
        self.assertEqual(c["accuracy"], 4.0)
        self.assertEqual(c["axis"], 30.0)
        self.assertEqual(c["axis_source"], "orientation de la photo")
        self.assertEqual(c["pos"], [0.1, -0.08])
        self.assertIsNone(c["camera_alt"])

    def test_sequence_axis_and_verified_accuracy(self):
        quality = {"precise": True, "accuracy": 0.1}
        with mock.patch.object(tools.host, "ngf_altitude", return_value=None):
            c = tools.photo_click(item(), 10.0, -5.0, calibration.Calibration(), quality, axis=95.0)
        self.assertEqual((c["axis"], c["axis_source"]), (95.0, "séquence"))
        self.assertEqual(c["accuracy"], 0.1)
        self.assertEqual(c["gps_accuracy"], 0.1)

    def test_camera_altitude_only_when_precise(self):
        ngf = mock.Mock(return_value=250.0)
        with mock.patch.object(tools.host, "ngf_altitude", ngf):
            self.assertIsNone(tools.photo_click(item(altitude=300.0), 0.0, -5.0, calibration.Calibration(),
                                                {"precise": False, "accuracy": None})["camera_alt"])
            c = tools.photo_click(item(altitude=300.0), 0.0, -5.0, calibration.Calibration(),
                                  {"precise": True, "accuracy": 0.1})
        self.assertEqual(c["camera_alt"], 250.0)

    def test_no_position(self):
        self.assertIsNone(tools.photo_click({"id": "p"}, 0.0, 0.0, calibration.Calibration()))

    def test_sighting(self):
        s = tools.photo_sighting(item(), 45.0, 0.0, calibration.Calibration())
        self.assertEqual((s["pic"], s["lon"], s["lat"], s["accuracy"]), ("p1", LON0, LAT0, 4.0))
        self.assertAlmostEqual(s["heading"], 45.0)


class TiltState(unittest.TestCase):
    def test_states(self):
        cal = calibration.Calibration()
        self.assertEqual(tools.tilt_state(cal, "p1"), ("inclinaison non calée", True))
        self.assertEqual(tools.tilt_state(cal, "p1", pending=True), ("inclinaison en cours de calcul…", False))
        self.assertEqual(tools.tilt_state(cal, None), ("inclinaison non calée", True))


class TriangulatorWithoutMap(unittest.TestCase):
    def test_two_sightings(self):
        tri = tools.Triangulator()
        target = point(0.0, 20.0)
        for east in (-10.0, 10.0):
            lon, lat = point(east, 0.0)
            tri.add({"pic": "p{}".format(east), "lon": lon, "lat": lat,
                     "heading": bearing((lon, lat), target), "accuracy": 2.0, "precise": False})
        self.assertAlmostEqual(tri.result["lon"], target[0], places=6)
        self.assertAlmostEqual(tri.result["lat"], target[1], places=6)
        values = tri.attributes()
        self.assertEqual(values["nb_visees"], 2)
        self.assertEqual(values["photos"], "p-10.0,p10.0")
        self.assertIn("2 visées", tri.status())

    def test_no_result(self):
        tri = tools.Triangulator()
        self.assertIsNone(tri.attributes())
        self.assertIn("Viser", tri.status())


class SequenceAxis(unittest.TestCase):
    def test_between_neighbours(self):
        a, b = item(lon=point(0, -5)[0], lat=point(0, -5)[1]), item(lon=point(5, 0)[0], lat=point(5, 0)[1])
        self.assertAlmostEqual(metadata.sequence_axis(item(), a, b), 45.0, delta=0.1)
        self.assertAlmostEqual(metadata.sequence_axis(item(), None, b), 90.0, delta=0.1)
        self.assertIsNone(metadata.sequence_axis(item(), None, None))


if __name__ == "__main__":
    unittest.main()
