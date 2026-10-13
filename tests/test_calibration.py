# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Cédric COCHART
# SPDX-License-Identifier: GPL-2.0-or-later
"""Tests de calibration.py : inclinaison, hauteur de caméra, recalage du cap et de la position."""

import math
import unittest

from support import LAT0, LON0, bearing, distance, module, point

calibration = module("core.calibration")
CalibrationError = calibration.CalibrationError


def tilted_up(tilt, toward):
    """Verticale vraie dans le repère d'une caméra dont le haut penche de `tilt`° vers `toward`."""
    return calibration.vector(toward, 90.0 - tilt)


def seen(up, yaw, elev):
    """Direction vue par la caméra inclinée d'une direction vraie (inverse de untilt)."""
    v = calibration.vector(yaw, elev)
    k = (-up[1], up[0], 0.0)  # zénith × up
    s = math.hypot(k[0], k[1])
    if s < 1e-12:
        return yaw % 360, elev
    k, c = (k[0] / s, k[1] / s, 0.0), up[2]
    kxv, kv = calibration._cross(k, v), calibration._dot(k, v)
    return calibration.angles(tuple(v[i] * c + kxv[i] * s + k[i] * kv * (1 - c) for i in range(3)))


def vertical_object(up, yaw, dist, height=6.0, camera=2.0):
    """Clics (pied, sommet) vus par la caméra inclinée sur un objet vertical."""
    return (seen(up, yaw, math.degrees(math.atan2(-camera, dist))),
            seen(up, yaw, math.degrees(math.atan2(height - camera, dist))))


def angle_between(a, b):
    return math.degrees(math.acos(min(1.0, calibration._dot(a, b))))


class Vectors(unittest.TestCase):
    def test_vector_angles(self):
        for yaw, elev in ((0, 0), (90, 10), (271.5, -35), (359, 80)):
            y, e = calibration.angles(calibration.vector(yaw, elev))
            self.assertAlmostEqual(y, yaw % 360, places=9)
            self.assertAlmostEqual(e, elev, places=9)

    def test_untilt_identity(self):
        y, e = calibration.untilt((0.0, 0.0, 1.0), 123.0, -4.0)
        self.assertAlmostEqual(y, 123.0)
        self.assertAlmostEqual(e, -4.0)

    def test_untilt_inverts_tilt(self):
        up = tilted_up(3.0, 40.0)
        for yaw, elev in ((0, 0), (130, -20), (250, 15)):
            y, e = calibration.untilt(up, *seen(up, yaw, elev))
            self.assertAlmostEqual(y, yaw, places=9)
            self.assertAlmostEqual(e, elev, places=9)

    def test_inverse3(self):
        m = [[4.0, 1.0, 2.0], [1.0, 3.0, 0.0], [2.0, 0.0, 5.0]]
        inv = calibration._inverse3(m)
        for i in range(3):
            for j in range(3):
                self.assertAlmostEqual(sum(m[i][k] * inv[k][j] for k in range(3)), float(i == j))


class Tilt(unittest.TestCase):
    # tilt_from_verticals amortit légèrement le calcul (1e-6) : quelques millionièmes de degré d'écart
    def test_two_objects(self):
        up = tilted_up(2.5, 70.0)
        result = calibration.tilt_from_verticals([vertical_object(up, 10, 8), vertical_object(up, 100, 12)])
        self.assertLess(angle_between(result["up"], up), 1e-4)
        self.assertAlmostEqual(result["tilt"], 2.5, places=4)
        self.assertAlmostEqual(result["toward"], 70.0, places=4)
        self.assertFalse(result["partial"])
        self.assertLess(result["residual"], 1e-4)

    def test_one_object_is_partial(self):
        up = tilted_up(2.0, 0.0)
        result = calibration.tilt_from_verticals([vertical_object(up, 90, 10)])
        self.assertTrue(result["partial"])
        self.assertIsNone(result["residual"])
        # Un objet à l'est corrige la pente vue de côté dans sa direction (nord-sud), ici toute l'inclinaison
        self.assertAlmostEqual(result["tilt"], 2.0, places=4)

    def test_errors(self):
        up = (0.0, 0.0, 1.0)
        bottom, top = vertical_object(up, 0, 10)
        with self.assertRaises(CalibrationError):  # sommet avant le pied
            calibration.tilt_from_verticals([(top, bottom)])
        with self.assertRaises(CalibrationError):  # pied et sommet sur deux objets différents
            calibration.tilt_from_verticals([(bottom, (top[0] + 60, top[1]))])
        far = tilted_up(15.0, 0.0)
        with self.assertRaises(CalibrationError):  # inclinaison invraisemblable
            calibration.tilt_from_verticals([vertical_object(far, 10, 8), vertical_object(far, 100, 8)])


class CameraHeight(unittest.TestCase):
    def click(self, elev, yaw=0.0, pic="p1"):
        return {"lon": LON0, "lat": LAT0, "yaw": yaw, "elev": elev, "pic": pic}

    def test_ground_reference(self):
        h = 2.1  # deux traits au sol à 8 et 11 m : longueur connue 3 m
        clicks = [self.click(math.degrees(math.atan2(-h, d))) for d in (8.0, 11.0)]
        found, sigma = calibration.camera_height("ground", clicks, 3.0)
        self.assertAlmostEqual(found, h, places=6)
        self.assertGreater(sigma, 0.0)

    def test_ground_reference_with_different_profiles(self):
        h = 2.1  # même scène, profil du second clic décalé de 0,5 m sous la caméra
        clicks = [self.click(math.degrees(math.atan2(-h, d))) for d in (8.0, 11.0)]
        clicks[0]["profile"] = [(0.0, 50.0), (60.0, 50.0)]
        clicks[1]["profile"] = [(0.0, 50.5), (60.0, 50.5)]
        found, _ = calibration.camera_height("ground", clicks, 3.0)
        self.assertAlmostEqual(found, h, places=6)

    def test_height_reference(self):
        h = 1.75  # objet de 4 m à 10 m
        clicks = [self.click(math.degrees(math.atan2(-h, 10.0))), self.click(math.degrees(math.atan2(4 - h, 10.0)))]
        found, _ = calibration.camera_height("height", clicks, 4.0)
        self.assertAlmostEqual(found, h, places=6)

    def test_errors(self):
        clicks = [self.click(-10.0), self.click(-8.0)]
        with self.assertRaises(CalibrationError):
            calibration.camera_height("ground", clicks, 0.0)
        with self.assertRaises(CalibrationError):  # longueur impossible avec 0,3 à 6 m
            calibration.camera_height("ground", clicks, 500.0)
        with self.assertRaises(CalibrationError):
            calibration.camera_height("height", [self.click(-10.0), self.click(10.0, pic="p2")], 3.0)

    def test_recomputed_with_current_tilt(self):
        """Hauteur calée sur une photo penchée, avant que son inclinaison soit connue : biaisée ;
        une fois l'inclinaison connue, la hauteur est recalculée à partir des clics bruts."""
        h, up = 2.1, tilted_up(1.5, 0.0)  # caméra penchée de 1,5° vers l'avant (nord)
        clicks = []
        for d in (8.0, 11.0):
            yaw, elev = seen(up, 0.0, math.degrees(math.atan2(-h, d)))
            clicks.append(dict(self.click(elev, yaw), raw=[yaw, elev], sequence="s"))
        cal = calibration.Calibration()
        cal.add_camera_height("s", "ground", clicks, 3.0)
        biased = cal.camera("s")["height"]
        self.assertGreater(abs(biased - h), 0.05)
        cal.add_vertical("p1", *vertical_object(up, 90, 8))  # inclinaison calée ensuite
        cal.add_vertical("p1", *vertical_object(up, 180, 8))
        self.assertAlmostEqual(cal.camera("s")["height"], h, delta=0.01)
        other = calibration.Calibration()  # enregistrement et relecture : clics bruts conservés
        other.load(cal.to_dict())
        self.assertAlmostEqual(other.camera("s")["height"], h, delta=0.01)
        self.assertEqual(other.camera("s")["legacy"], 0)

    def test_legacy_values(self):
        cal = calibration.Calibration()
        cal.load({"format": 1, "heights": {"s": [[2.0, 0.1], [2.2, -1]], "t": [{"kind": "ground"}]}})
        self.assertEqual(cal.heights["s"], [(2.0, 0.1)])  # incertitude invalide ignorée
        self.assertEqual(cal.camera("s")["legacy"], 1)
        self.assertNotIn("t", cal.heights)  # calage illisible ignoré

    def test_weighted_mean(self):
        cal = calibration.Calibration()
        cal.heights["s"] = [(2.0, 0.1), (2.3, 0.2)]
        result = cal.camera("s")
        self.assertAlmostEqual(result["height"], (2.0 / 0.01 + 2.3 / 0.04) / (1 / 0.01 + 1 / 0.04))
        self.assertLess(result["sigma"], 0.1)
        self.assertIsNone(cal.camera("autre"))


class Landmarks(unittest.TestCase):
    """Photo en (0, 0) ; repères autour, vus avec un cap faussé et/ou une position GPS décalée."""

    def setUp(self):
        self.cal = calibration.Calibration()
        self.notified = 0
        self.cal.changed = self._changed

    def _changed(self):
        self.notified += 1

    def add(self, mark, true_photo=(0.0, 0.0), gps=(0.0, 0.0), heading_bias=0.0, survey=False, accuracy=3.0):
        """Repère (est, nord) vu depuis la photo réellement en true_photo, enregistrée en gps,
        avec une caméra dont le cap est faux de heading_bias."""
        photo = point(*gps)
        yaw = (bearing(point(*true_photo), point(*mark)) - heading_bias) % 360
        return self.cal.add_landmark("seq", "p1", photo[0], photo[1], yaw, -2.0, *point(*mark), accuracy, survey)

    def test_single_landmark_heading(self):
        result = self.add((0, 80), heading_bias=2.0)
        self.assertAlmostEqual(result["offset"], 2.0, places=6)
        pose = self.cal.pose("seq")
        self.assertGreater(pose["offset"], 1.0)  # rapproché du décalage, freiné par l'a priori
        self.assertLess(pose["offset"], 2.0)
        self.assertFalse(pose["positioned"])
        self.assertIsNone(self.cal.position("seq", *point(0, 0)))
        self.assertEqual(self.notified, 1)

    def test_heading_and_position(self):
        marks = [(40, 30), (-35, 40), (-30, -45), (45, -35), (0, 60)]
        for mark in marks:
            self.add(mark, gps=(2.0, -1.5), heading_bias=1.5, accuracy=4.0)
        pose = self.cal.pose("seq")
        self.assertTrue(pose["positioned"])
        self.assertAlmostEqual(pose["offset"], 1.5, delta=0.05)
        lon, lat, sigma = self.cal.position("seq", *point(2.0, -1.5))
        self.assertLess(distance((lon, lat), point(0, 0)), 0.1)
        self.assertLess(sigma, 4.0)
        yaw, elev = self.cal.correct("p1", "seq", 100.0, -3.0, *point(2.0, -1.5))
        self.assertAlmostEqual(yaw, 100.0 + pose["offset"])
        self.assertEqual(elev, -3.0)
        self.assertIsNotNone(self.cal.heading_error("seq"))

    def test_reach(self):
        self.add((0, 80), heading_bias=2.0)
        far = point(0, calibration.REACH + 50)
        self.assertIsNone(self.cal.pose("seq", *far))
        self.assertEqual(self.cal.correct("p1", "seq", 100.0, 0.0, *far), (100.0, 0.0))

    def test_rejected_landmarks(self):
        with self.assertRaises(CalibrationError):  # trop proche
            self.add((0, 3))
        with self.assertRaises(CalibrationError):  # écart de cap invraisemblable
            self.add((0, 80), heading_bias=35.0)
        for mark in ((40, 30), (-35, 40), (-30, -45)):
            self.add(mark, heading_bias=1.0)
        with self.assertRaises(CalibrationError):  # contredit les trois premiers
            self.add((45, -35), heading_bias=6.0)
        self.assertEqual(len(self.cal.landmarks["seq"]), 3)


class Storage(unittest.TestCase):
    def test_round_trip(self):
        cal = calibration.Calibration()
        up = tilted_up(2.0, 30.0)
        cal.add_vertical("p1", *vertical_object(up, 10, 8))
        cal.add_vertical("p1", *vertical_object(up, 100, 8))
        cal.heights["s"] = [(2.1, 0.05)]
        mark = point(0, 80)
        cal.add_landmark("s", "p1", LON0, LAT0, 1.0, -2.0, mark[0], mark[1], None, True)
        other = calibration.Calibration()
        other.load(cal.to_dict())
        self.assertEqual(other.to_dict(), cal.to_dict())
        self.assertAlmostEqual(other.tilt("p1")["tilt"], 2.0, places=4)
        self.assertEqual(other.untilted("p1", 50.0, 1.0), cal.untilted("p1", 50.0, 1.0))
        self.assertAlmostEqual(other.pose("s")["offset"], cal.pose("s")["offset"])

    def test_merge_keeps_current(self):
        old = calibration.Calibration()
        old.add_vertical("p1", *vertical_object(tilted_up(4.0, 0.0), 90, 8))
        old.add_vertical("p2", *vertical_object(tilted_up(1.0, 0.0), 90, 8))
        old.heights["s1"], old.heights["s2"] = [(1.5, 0.05)], [(2.5, 0.05)]
        cal = calibration.Calibration()
        cal.add_vertical("p1", *vertical_object(tilted_up(2.0, 0.0), 90, 8))  # refait depuis
        cal.heights["s1"] = [(2.1, 0.05)]
        notified = []
        cal.changed = lambda: notified.append(True)
        self.assertEqual(cal.merge(old.to_dict()), 2)  # p2 et s2
        self.assertAlmostEqual(cal.tilt("p1")["tilt"], 2.0, places=4)  # le calage refait l'emporte
        self.assertAlmostEqual(cal.tilt("p2")["tilt"], 1.0, places=4)
        self.assertEqual(cal.camera("s1")["height"], 2.1)
        self.assertEqual(cal.camera("s2")["height"], 2.5)
        self.assertEqual(notified, [])
        self.assertEqual(cal.merge(None), 0)
        self.assertEqual(cal.merge(old.to_dict()), 0)  # déjà repris

    def test_bad_data(self):
        cal = calibration.Calibration()
        for data in (None, "x", {"format": 99}, {"format": 1, "verticals": {"p": [[1]]}},
                     {"format": 1, "landmarks": {"s": [{"pic": "p"}]}}):
            cal.load(data)  # ne lève pas d'exception
            self.assertEqual(cal.verticals, {})
            self.assertEqual(cal.landmarks, {})


if __name__ == "__main__":
    unittest.main()
