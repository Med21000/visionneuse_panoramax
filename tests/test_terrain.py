# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Cédric COCHART
# SPDX-License-Identifier: GPL-2.0-or-later
"""Tests de terrain.py : droite de pente robuste et enchaînement des sources d'altitude."""

import unittest
from unittest import mock
from urllib.parse import parse_qs, urlsplit

from support import LAT0, LON0, install_qgis_stub, module

install_qgis_stub()
terrain = module("terrain")
ground = module("ground")
triangulation = module("triangulation")


def line(slope, z0, dists):
    return [z0 + slope * s for s in dists]


class RobustProfile(unittest.TestCase):
    def test_straight_slope(self):
        dists = [i * 0.5 for i in range(-60, 181)]
        prof = terrain.robust_profile(dists, line(0.04, 250.0, dists))
        self.assertEqual(prof[0][0], 0.0)
        self.assertAlmostEqual(prof[0][1], 250.0, places=6)
        self.assertAlmostEqual(prof[1][0], ground.MAX_DISTANCE)
        self.assertAlmostEqual(prof[1][1], 250.0 + 0.04 * ground.MAX_DISTANCE, places=6)

    def test_ditch_and_bridge_ignored(self):
        dists = [i * 0.5 for i in range(-60, 181)]
        values = line(-0.03, 100.0, dists)
        for i, s in enumerate(dists):
            if 10 <= s < 20:
                values[i] -= 1.5  # fossé
            if 40 <= s < 65:
                values[i] -= 8.0  # rivière sous un pont
        prof = terrain.robust_profile(dists, values)
        self.assertAlmostEqual(prof[0][1], 100.0, delta=0.05)
        slope = (prof[1][1] - prof[0][1]) / prof[1][0]
        self.assertAlmostEqual(slope, -0.03, delta=0.003)

    def test_missing_values(self):
        dists = list(range(10))
        values = [None, "x", float("nan"), -99999, 5, 5, 5, 5, 5, 5]
        prof = terrain.robust_profile(dists, values)
        self.assertAlmostEqual(prof[0][1], 5.0)
        self.assertIsNone(terrain.robust_profile(dists, [None] * 8 + [1, 2]))
        self.assertIsNone(terrain.robust_profile(dists, None))


class FakeServices:
    """Remplace api.fetch_json : réponses synthétiques des services, terrain de pente `slope`."""

    def __init__(self, slope=0.05, z0=200.0, down=(), coarse_slope=None):
        self.slope, self.z0, self.down = slope, z0, set(down)
        self.coarse_slope = slope if coarse_slope is None else coarse_slope
        self.calls = []

    def z(self, lon, lat, slope):
        """Altitude d'un point : pente le long de l'axe nord (visées vers le nord dans les tests)."""
        return self.z0 + slope * triangulation._local(lon, lat, LON0, LAT0)[1]

    def __call__(self, url, callback, timeout_ms=None, prefer_cache=False):
        parts = urlsplit(url)
        query = {k: v[0] for k, v in parse_qs(parts.query).items()}
        host = parts.netloc
        self.calls.append(host)
        if host in self.down:
            callback(None, "délai dépassé")
        elif host == "data.geopf.fr":
            lons = [float(v) for v in query["lon"].split("|")]
            lats = [float(v) for v in query["lat"].split("|")]
            n = int(query["sampling"])
            pts = []
            for i in range(n):
                k = i / (n - 1)
                lon, lat = lons[0] + k * (lons[1] - lons[0]), lats[0] + k * (lats[1] - lats[0])
                pts.append({"lon": lon, "lat": lat, "z": self.z(lon, lat, self.slope)})
            callback({"elevations": pts}, None)
        elif host == "api.opentopodata.org":
            locs = [tuple(map(float, p.split(","))) for p in query["locations"].split("|")]
            callback({"status": "OK", "results": [{"elevation": self.z(lon, lat, self.coarse_slope)}
                                                  for lat, lon in locs]}, None)
        elif host == "api.open-meteo.com":
            lats = [float(v) for v in query["latitude"].split(",")]
            lons = [float(v) for v in query["longitude"].split(",")]
            callback({"elevation": [self.z(lon, lat, self.coarse_slope) for lon, lat in zip(lons, lats)]}, None)
        else:
            raise AssertionError("service inattendu : " + host)


class Provider(unittest.TestCase):
    def run_profile(self, services, use_ign=True):
        provider = terrain.TerrainProvider()
        provider.use_ign = use_ign
        results = []
        with mock.patch.object(terrain.api, "fetch_json", services):
            provider.profile(LON0, LAT0, 0.0, lambda *r: results.append(r))
        self.assertEqual(len(results), 1)
        return results[0]

    def assertSlope(self, prof, slope, z0=200.0):
        self.assertAlmostEqual(prof[0][1], z0, delta=0.01)
        self.assertAlmostEqual((prof[1][1] - prof[0][1]) / prof[1][0], slope, delta=0.001)

    def test_ign(self):
        services = FakeServices()
        prof, label, warning = self.run_profile(services)
        self.assertEqual(label, terrain.IGN_LABEL)
        self.assertIsNone(warning)
        self.assertSlope(prof, 0.05)
        self.assertEqual(services.calls, ["data.geopf.fr"])

    def test_fallbacks(self):
        prof, label, warning = self.run_profile(FakeServices(down={"data.geopf.fr"}))
        self.assertEqual(label, terrain.OPENTOPODATA_LABEL)
        self.assertIn("IGN", warning)
        self.assertSlope(prof, 0.05)
        prof, label, _ = self.run_profile(FakeServices(down={"data.geopf.fr", "api.opentopodata.org"}))
        self.assertEqual(label, terrain.OPENMETEO_LABEL)
        prof, label, _ = self.run_profile(FakeServices(), use_ign=False)
        self.assertEqual(label, terrain.OPENTOPODATA_LABEL)

    def test_flat_when_everything_fails(self):
        services = FakeServices(down={"data.geopf.fr"}, coarse_slope=0.3)  # pente invraisemblable
        prof, label, warning = self.run_profile(services)
        self.assertEqual(label, ground.FLAT)
        self.assertEqual(prof, ground.flat_profile())
        self.assertIn("invraisemblable", warning)
        self.assertEqual(services.calls, ["data.geopf.fr", "api.opentopodata.org", "api.open-meteo.com"])


if __name__ == "__main__":
    unittest.main()
