# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Cédric COCHART
# SPDX-License-Identifier: GPL-2.0-or-later
"""Tests de roads.py : axe de la route au point mesuré (BD TOPO, trajectoire de la séquence).

Les tronçons et les photos sont placés en mètres (est, nord) autour de support.LON0/LAT0 ;
les services sont simulés."""

import math
import unittest
from unittest import mock

from support import install_qgis_stub, module, point

install_qgis_stub()
roads = module("roads")


def section(*pts, name="D16"):
    return {"type": "Feature", "properties": {"cpx_numero": name},
            "geometry": {"type": "LineString", "coordinates": [list(point(*p)) for p in pts]}}


def arc(radius, start, stop, step=5):
    """Route en arc de cercle de centre (radius, 0), du cap start au cap stop (degrés)."""
    out = []
    for a in range(start, stop + 1, step):
        t = math.radians(a)
        out.append((radius - radius * math.cos(t), radius * math.sin(t)))
    return out


def axis_diff(a, b):
    d = abs((a - b) % 180)
    return min(d, 180 - d)


class RoadAxis(unittest.TestCase):
    def axis(self, features, at=(0.0, 0.0), crossing=90.0):
        return roads.road_axis(roads.parse_sections({"features": features}), *point(*at), crossing)

    def test_straight(self):
        res = self.axis([section((0, -50), (30, 50))])
        self.assertAlmostEqual(res["axis"] % 180, math.degrees(math.atan2(30, 100)), places=4)
        self.assertEqual(res["source"], "BD TOPO, D16")

    def test_curve_followed(self):
        # Virage de rayon 40 m : axe local au point mesuré (cap 30°), pas celui de l'entrée
        bend = arc(40.0, 0, 90, step=3)
        t = math.radians(30)
        at = (40 - 40 * math.cos(t), 40 * math.sin(t))
        res = self.axis([section(*bend)], at=at, crossing=120.0)
        self.assertLess(axis_diff(res["axis"], 30.0), 1.0)

    def test_crossroads(self):
        # Carrefour en croix : la largeur traverse la rue nord-sud (segment cliqué est-ouest)
        features = [section((0, -60), (0, 60), name="D16"), section((-60, 0), (60, 0), name="R DU 8 MAI")]
        res = self.axis(features, at=(1.0, 2.0), crossing=90.0)
        self.assertEqual(res["source"], "BD TOPO, D16")
        res = self.axis(features, at=(2.0, 1.0), crossing=0.0)
        self.assertEqual(res["source"], "BD TOPO, R DU 8 MAI")

    def test_nearest(self):
        # Deux rues parallèles traversées par la mesure : la plus proche du milieu des bords
        features = [section((2, -50), (2, 50), name="D16"), section((-9, -50), (-9, 50), name="R BASSE")]
        self.assertEqual(self.axis(features)["source"], "BD TOPO, D16")
        self.assertEqual(self.axis(features[::-1])["source"], "BD TOPO, D16")
        self.assertAlmostEqual(self.axis(features)["distance"], 2.0, places=3)

    def test_nothing(self):
        self.assertIsNone(self.axis([section((40, -50), (40, 50))]))  # trop loin
        self.assertIsNone(self.axis([section((0, -50), (0, 50))], crossing=10.0))  # parallèle au segment
        self.assertIsNone(self.axis([]))

    def test_parse(self):
        data = {"features": [
            {"properties": {"nom_collaboratif_gauche": "R RENE LAFORGE"},
             "geometry": {"type": "MultiLineString", "coordinates": [[[5, 47], [5.001, 47]], [[5, 47.001]]]}},
            {"properties": {}, "geometry": {"type": "Point", "coordinates": [5, 47]}},
            {"properties": {}, "geometry": None}]}
        sections = roads.parse_sections(data)
        self.assertEqual([s["name"] for s in sections], ["R RENE LAFORGE"])  # ligne d'un seul point ignorée
        self.assertEqual(roads.parse_sections(None), [])


class Trajectory(unittest.TestCase):
    def photos(self, pts, sequence="seq"):
        return {"features": [{"collection": sequence, "geometry": {"coordinates": list(point(*p))}} for p in pts]}

    def test_local_direction(self):
        # Photos tous les 5 m sur une route au cap 60° ; d'autres, loin, sur une autre direction
        a = math.radians(60)
        pts = [(d * math.sin(a), d * math.cos(a)) for d in range(-30, 31, 5)] + [(100, y) for y in range(0, 50, 5)]
        res = roads.trajectory_axis(self.photos(pts), "seq", *point(0, 0))
        self.assertLess(axis_diff(res["axis"], 60.0), 0.01)
        self.assertEqual(res["source"], "trajectoire au point mesuré")

    def test_refused(self):
        self.assertIsNone(roads.trajectory_axis(self.photos([(0, 0), (0, 5)]), "seq", *point(0, 0)))
        self.assertIsNone(roads.trajectory_axis(self.photos([(0, 0), (0.2, 0.1), (0.1, 0.3)]), "seq",
                                                *point(0, 0)))  # arrêt
        self.assertIsNone(roads.trajectory_axis(self.photos([(0, y) for y in range(-15, 16, 5)], "autre"), "seq",
                                                *point(0, 0)))


class FakeServices:
    def __init__(self, sections=(), photos=(), down=()):
        self.sections, self.photos, self.down = list(sections), list(photos), set(down)
        self.pending, self.calls = [], []

    def __call__(self, url, callback, **kwargs):
        kind = "wfs" if "WFS" in url else "search"
        self.calls.append(kind)
        if kind in self.down:
            self.pending.append(lambda: callback(None, "délai dépassé"))
        elif kind == "wfs":
            self.pending.append(lambda: callback({"features": self.sections}, None))
        else:
            self.pending.append(lambda: callback({"features": [
                {"collection": "seq", "geometry": {"coordinates": list(point(*p))}} for p in self.photos]}, None))

    def answer(self):
        while self.pending:
            self.pending.pop(0)()


class Provider(unittest.TestCase):
    def setUp(self):
        self.provider = roads.RoadAxisProvider()
        self.ready = []
        self.provider.ready = lambda: self.ready.append(1)

    def lookup(self, services, at=(0.0, 0.0), crossing=90.0):
        with mock.patch.object(roads.api, "fetch_json", services):
            return self.provider.lookup(*point(*at), crossing, "seq")

    def test_bdtopo_then_cache(self):
        services = FakeServices(sections=[section((0, -50), (0, 50))])
        self.assertEqual(self.lookup(services), roads.PENDING)
        self.assertEqual(self.lookup(services), roads.PENDING)  # une seule lecture en cours
        services.answer()
        self.assertEqual(self.ready, [1])
        self.assertEqual(self.lookup(services)["source"], "BD TOPO, D16")
        self.assertEqual(self.lookup(services, at=(3.0, 20.0))["source"], "BD TOPO, D16")  # même maille
        self.assertEqual(services.calls, ["wfs"])

    def test_trajectory_when_no_section(self):
        services = FakeServices(sections=[], photos=[(0, y) for y in range(-15, 16, 5)])
        self.lookup(services)
        services.answer()
        self.assertEqual(self.lookup(services), roads.PENDING)  # puis les photos de la séquence
        services.answer()
        self.assertEqual(self.lookup(services)["source"], "trajectoire au point mesuré")
        self.assertEqual(services.calls, ["wfs", "search"])

    def test_failure_retried_later(self):
        services = FakeServices(down={"wfs", "search"})
        self.lookup(services)
        services.answer()
        self.lookup(services)
        services.answer()
        self.assertIsNone(self.lookup(services))  # pannes : axe de la caméra
        self.assertEqual(services.calls, ["wfs", "search"])  # pas de nouvel essai tout de suite
        with mock.patch.object(roads.time, "monotonic", lambda: 1e12):
            self.assertEqual(self.lookup(FakeServices()), roads.PENDING)  # plus tard : nouvel essai


if __name__ == "__main__":
    unittest.main()
