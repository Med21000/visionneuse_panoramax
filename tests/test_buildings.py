# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Cédric COCHART
# SPDX-License-Identifier: GPL-2.0-or-later
"""Tests de buildings.py : façade du cadastre traversée par une visée, contrôle par le LiDAR.

Les bâtiments et les grilles LiDAR sont construits en mètres (est, nord) autour de
support.LON0/LAT0 ; les services sont simulés."""

import array
import unittest
from unittest import mock

from support import LAT0, LON0, install_qgis_stub, module, point

install_qgis_stub()
buildings = module("buildings")
CELL = buildings._cell(LON0, LAT0)


def building(*corners):
    ring = [list(point(*c)) for c in corners + corners[:1]]
    return {"type": "Feature", "properties": {}, "geometry": {"type": "Polygon", "coordinates": [ring]}}


def grid_bytes(height_at, ground=200.0):
    """Grille LiDAR de la maille (flottants 32 bits petit-boutistes, ligne 0 au nord) :
    ground + height_at(est, nord), ou None (pas de données)."""
    w, h = buildings.grid_size(CELL)
    lon0, lat0, lon1, lat1 = buildings._bounds(CELL)
    x0, y0 = buildings.triangulation._local(lon0, lat1, LON0, LAT0)
    values = array.array("f")
    for r in range(h):
        for c in range(w):
            z = height_at(x0 + (c + 0.5) * buildings.PIXEL, y0 - (r + 0.5) * buildings.PIXEL)
            values.append(-99999.0 if z is None else ground + z)
    if array.array("f", [1.0]).tobytes() != b"\x00\x00\x80\x3f":
        values.byteswap()
    return values.tobytes()


# Maison de 10 m × 8 m dont la façade est à 12 m à l'est de la photo
HOUSE = building((12, -5), (20, -5), (20, 5), (12, 5))


def mns_house(roof_overhang=0.6):
    return lambda x, y: 7.0 if 12 - roof_overhang <= x <= 20 + roof_overhang and -5 <= y <= 5 else 0.0


class Wall(unittest.TestCase):
    def edges(self, *features):
        return buildings.parse_edges({"features": list(features)})

    def test_first_wall_crossed(self):
        far = building((30, -5), (40, -5), (40, 5), (30, 5))
        hit = buildings.wall_hit(self.edges(far, HOUSE), LON0, LAT0, 90.0)
        self.assertAlmostEqual(hit["distance"], 12.0, delta=0.01)
        (lon1, lat1), (lon2, lat2) = hit["points"]
        self.assertAlmostEqual(lon1, lon2, places=6)  # mur nord-sud, x = 12 m
        self.assertIsNone(buildings.wall_hit(self.edges(HOUSE), LON0, LAT0, 270.0))  # derrière
        self.assertIsNone(buildings.wall_hit(self.edges(HOUSE), LON0, LAT0, 0.0))  # à côté

    def test_oblique(self):
        hit = buildings.wall_hit(self.edges(HOUSE), LON0, LAT0, 110.0)  # visée vers le sud-est
        self.assertAlmostEqual(hit["distance"], 12.0 / 0.9397, delta=0.02)

    def test_light_buildings_ignored(self):
        shed = building((6, -2), (9, -2), (9, 2), (6, 2))
        shed["properties"] = {"type": "Construction légère"}  # abri de jardin devant la maison
        hit = buildings.wall_hit(self.edges(shed, HOUSE), LON0, LAT0, 90.0)
        self.assertAlmostEqual(hit["distance"], 12.0, delta=0.01)

    def test_grid_west_of_edge(self):
        g = buildings.Grid(grid_bytes(lambda x, y: 3.0), CELL)
        self.assertIsNone(g.at(g.lon0 - 0.1 * (g.lon1 - g.lon0) / g.w, LAT0))  # juste à l'ouest de la grille

    def test_parse(self):
        multi = {"type": "Feature", "geometry": {"type": "MultiPolygon", "coordinates": [
            HOUSE["geometry"]["coordinates"]]}}
        self.assertEqual(len(self.edges(HOUSE)), 4)
        self.assertEqual(len(self.edges(multi)), 4)
        self.assertEqual(buildings.parse_edges({"features": [{"geometry": {"type": "Point"}}]}), [])
        self.assertEqual(buildings.parse_edges(None), [])


class Lidar(unittest.TestCase):
    def grids(self, mns, mnt=lambda x, y: 0.0):
        return (buildings.Grid(grid_bytes(mns), CELL), buildings.Grid(grid_bytes(mnt), CELL))

    def test_grid(self):
        g = buildings.Grid(grid_bytes(lambda x, y: 3.0 if x > 0 else None), CELL)
        self.assertAlmostEqual(g.at(*point(5, 0)), 203.0, places=3)
        self.assertIsNone(g.at(*point(-5, 0)))  # pas de données
        self.assertIsNone(g.at(LON0 + 1.0, LAT0))  # hors de la grille
        with self.assertRaises(ValueError):
            buildings.Grid(b"\x00" * 16, CELL)

    def test_built_near_the_wall(self):
        mns, mnt = self.grids(mns_house())
        t = buildings.lidar_built(mns, mnt, LON0, LAT0, 90.0, 9.0, 14.0)
        self.assertAlmostEqual(t, 11.4, delta=buildings.PIXEL)  # bord du toit, 0,6 m avant le mur
        self.assertEqual(buildings.check(12.0, t), ("façade confirmée par le LiDAR HD", False))
        # Arbre à 5 m de la photo : ignoré, il est avant la fenêtre de recherche
        tree = lambda x, y: 9.0 if 4 <= x <= 6 and -2 <= y <= 2 else mns_house()(x, y)  # noqa: E731
        mns, mnt = self.grids(tree)
        self.assertAlmostEqual(buildings.lidar_built(mns, mnt, LON0, LAT0, 90.0, 9.0, 14.0), 11.4,
                               delta=buildings.PIXEL)
        mns, mnt = self.grids(lambda x, y: None, lambda x, y: None)
        self.assertIs(buildings.lidar_built(mns, mnt, LON0, LAT0, 90.0, 9.0, 14.0), False)

    def test_check(self):
        self.assertTrue(buildings.check(12.0, 9.0)[1])  # bâti 3 m avant le cadastre
        self.assertIn("divergent de 3,0 m", buildings.check(12.0, 9.0)[0])
        self.assertTrue(buildings.check(12.0, 12.8)[1])  # bâti après le cadastre
        self.assertIn("pas de bâti", buildings.check(12.0, None)[0])
        self.assertIn("non publié", buildings.check(12.0, False)[0])


class FakeServices:
    def __init__(self, features, mns, mnt=lambda x, y: 0.0, down=()):
        self.features, self.mns, self.mnt, self.down = features, mns, mnt, set(down)
        self.pending, self.calls = [], []

    def fetch_json(self, url, callback, **kwargs):
        self.calls.append("cadastre")
        self.pending.append(lambda: callback(None, "délai dépassé") if "cadastre" in self.down
                            else callback({"features": self.features}, None))

    def fetch(self, url, callback, **kwargs):
        kind = "mns" if "MNS" in url else "mnt"
        self.calls.append(kind)
        self.pending.append(lambda: callback(grid_bytes(self.mns if kind == "mns" else self.mnt), None))

    def answer(self):
        while self.pending:
            self.pending.pop(0)()


class Provider(unittest.TestCase):
    def lookup(self, provider, services, yaw=90.0):
        with mock.patch.object(buildings.api, "fetch_json", services.fetch_json), \
                mock.patch.object(buildings.api, "fetch", services.fetch):
            return provider.lookup(LON0, LAT0, yaw)

    def test_read_then_cache(self):
        provider, ready = buildings.BuildingProvider(), []
        provider.ready = lambda: ready.append(1)
        services = FakeServices([HOUSE], mns_house())
        self.assertEqual(self.lookup(provider, services), buildings.PENDING)
        services.answer()
        self.assertEqual(len(ready), 3)  # cadastre, MNS, MNT
        hit = self.lookup(provider, services)
        self.assertAlmostEqual(hit["distance"], 12.0, delta=0.01)
        self.assertEqual((hit["check"], hit["warning"]), ("façade confirmée par le LiDAR HD", False))
        self.assertIsNone(self.lookup(provider, services, yaw=270.0))
        self.assertEqual(sorted(services.calls), ["cadastre", "mns", "mnt"])  # une lecture par maille

    def test_tree_in_front(self):
        # Arbre entre la photo et la façade : il dépasse du MNT, mais la façade reste confirmée
        tree = lambda x, y: 9.0 if 4 <= x <= 6 and -2 <= y <= 2 else mns_house()(x, y)  # noqa: E731
        provider = buildings.BuildingProvider()
        services = FakeServices([HOUSE], tree)
        self.lookup(provider, services)
        services.answer()
        self.assertEqual(self.lookup(provider, services)["check"], "façade confirmée par le LiDAR HD")

    def test_without_lidar(self):
        provider = buildings.BuildingProvider()
        services = FakeServices([HOUSE], lambda x, y: None, lambda x, y: None)
        self.lookup(provider, services)
        services.answer()
        hit = self.lookup(provider, services)
        self.assertTrue(hit["warning"])
        self.assertIn("non publié", hit["check"])

    def test_unreadable_grid_not_kept(self):
        # Exception du service WMS renvoyée comme une réponse normale : pas « LiDAR non publié »
        provider = buildings.BuildingProvider()
        services = FakeServices([HOUSE], mns_house())
        services.fetch = lambda url, cb, **kw: services.pending.append(lambda: cb(b"<ServiceException/>", None))
        self.lookup(provider, services)
        services.answer()
        self.assertNotIn(("mns", CELL), provider._cache)
        with mock.patch.object(buildings.time, "monotonic", lambda: 1e12):
            self.assertEqual(self.lookup(provider, FakeServices([HOUSE], mns_house())), buildings.PENDING)

    def test_failure_not_kept(self):
        provider = buildings.BuildingProvider()
        services = FakeServices([HOUSE], mns_house(), down={"cadastre"})
        self.lookup(provider, services)
        services.answer()
        self.assertIsNone(self.lookup(provider, services))  # panne : rien, sans nouvel essai immédiat
        with mock.patch.object(buildings.time, "monotonic", lambda: 1e12):
            self.assertEqual(self.lookup(provider, FakeServices([HOUSE], mns_house())), buildings.PENDING)


if __name__ == "__main__":
    unittest.main()
