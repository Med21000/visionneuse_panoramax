# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Cédric COCHART
# SPDX-License-Identifier: GPL-2.0-or-later
"""Tests de terrain.py : droite de pente robuste et enchaînement des sources d'altitude."""

import unittest
from unittest import mock
from urllib.parse import parse_qs, urlsplit

from support import LAT0, LON0, install_qgis_stub, module

install_qgis_stub()
terrain = module("core.terrain")
ground = module("core.ground")
triangulation = module("core.triangulation")


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


def at(profile, d):
    """Altitude du profil (linéaire par morceaux) à la distance d."""
    for (s1, z1), (s2, z2) in zip(profile, profile[1:]):
        if s1 <= d <= s2:
            return z1 + (z2 - z1) * (d - s1) / (s2 - s1)
    raise ValueError(d)


class RealProfile(unittest.TestCase):
    dists = [-10.0 + 0.5 * i for i in range(71)]

    def test_curb_followed(self):
        # Chaussée plate, trottoir 15 cm plus haut à partir de 4 m : suivi par le profil réel
        values = [200.0 + (0.15 if s >= 4.0 else 0.0) for s in self.dists]
        prof = terrain.real_profile(self.dists, values)
        self.assertEqual(prof[0], (0.0, 200.0))
        self.assertAlmostEqual(at(prof, 2.0), 200.0)
        self.assertAlmostEqual(at(prof, 6.0), 200.15)
        line = terrain.robust_profile(self.dists, values)
        self.assertGreater(abs(at(line, 2.0) - 200.0), 0.1)  # la droite de pente place la chaussée trop haut

    def test_isolated_spikes_removed(self):
        # Points isolés aberrants (objet mal classé dans le MNT) : écartés par la médiane glissante
        spikes = {3.0: 0.6, 6.0: -0.5, 10.0: 0.8}
        values = [100.0 + 0.02 * s + spikes.get(s, 0.0) for s in self.dists]
        prof = terrain.real_profile(self.dists, values)
        for d in spikes:
            self.assertAlmostEqual(at(prof, d), 100.0 + 0.02 * d, delta=0.011)

    def test_extended_beyond_the_data(self):
        values = [50.0 + 0.04 * s for s in self.dists]
        prof = terrain.real_profile(self.dists, values)
        self.assertEqual(prof[-1][0], ground.MAX_DISTANCE)
        self.assertAlmostEqual(at(prof, 40.0), 50.0 + 0.04 * 40.0, delta=0.02)

    def test_unusable(self):
        gaps = [None if i % 3 == 0 else 100.0 for i in range(71)]  # un tiers des altitudes manque
        self.assertIsNone(terrain.real_profile(self.dists, gaps))
        behind = [100.0 if s > 0.5 else None for s in self.dists]  # rien sous la photo
        self.assertIsNone(terrain.real_profile(self.dists, behind))


class FakeServices:
    """Remplace api.fetch_json : réponses synthétiques des services, terrain de pente `slope`.
    Le LiDAR HD n'est publié que si `lidar` est vrai (sinon le service renvoie -99999)."""

    def __init__(self, slope=0.05, z0=200.0, down=(), coarse_slope=None, relief=None, lidar=False):
        self.slope, self.z0, self.down, self.relief, self.lidar = slope, z0, set(down), relief, lidar
        self.coarse_slope = slope if coarse_slope is None else coarse_slope
        self.calls = []

    def z(self, lon, lat, slope):
        """Altitude d'un point : pente le long de l'axe nord (visées vers le nord dans les tests),
        ou relief(distance au nord) s'il est donné."""
        north = triangulation._local(lon, lat, LON0, LAT0)[1]
        return self.relief(north) if self.relief else self.z0 + slope * north

    def __call__(self, url, callback, timeout_ms=None, prefer_cache=False):
        parts = urlsplit(url)
        query = {k: v[0] for k, v in parse_qs(parts.query).items()}
        host = parts.netloc
        lidar = query.get("resource") == terrain.LIDAR_RESOURCE
        self.calls.append("lidar" if lidar else host)
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
                z = self.z(lon, lat, self.slope) if self.lidar or not lidar else -99999.0
                pts.append({"lon": lon, "lat": lat, "z": z})
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
    def run_profile(self, services, bridge=False):
        provider = terrain.TerrainProvider()
        provider.bridge = bridge
        results = []
        with mock.patch.object(terrain.host, "fetch_json", services):
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
        self.assertIsNone(warning)  # LiDAR HD pas encore publié : repli silencieux sur le RGE ALTI
        self.assertSlope(prof, 0.05)
        self.assertEqual(services.calls, ["lidar", "data.geopf.fr"])

    def test_lidar(self):
        services = FakeServices(lidar=True)
        prof, label, warning = self.run_profile(services)
        self.assertEqual((label, warning), (terrain.LIDAR_LABEL, None))
        self.assertSlope(prof, 0.05)
        self.assertEqual(services.calls, ["lidar"])

    def test_fallbacks(self):
        prof, label, warning = self.run_profile(FakeServices(down={"data.geopf.fr"}))
        self.assertEqual(label, terrain.OPENTOPODATA_LABEL)
        self.assertIn("IGN", warning)
        self.assertSlope(prof, 0.05)
        prof, label, _ = self.run_profile(FakeServices(down={"data.geopf.fr", "api.opentopodata.org"}))
        self.assertEqual(label, terrain.OPENMETEO_LABEL)

    def test_window_close_to_the_photo(self):
        # Rue plate sur 15 m devant la photo, puis relief qui monte de 8 % (colline derrière les
        # maisons) : la droite de pente doit suivre la rue près des points mesurés.
        services = FakeServices(relief=lambda s: 200.0 + 0.08 * max(0.0, s - 15.0))
        prof, label, _ = self.run_profile(services)
        self.assertEqual(label, terrain.IGN_LABEL)
        at6 = prof[0][1] + (prof[1][1] - prof[0][1]) / prof[1][0] * 6.0
        self.assertAlmostEqual(at6, 200.0, delta=0.06)  # sol à 6 m : 0,3 m trop haut sur 120 m

    def test_lidar_real_profile(self):
        services = FakeServices(lidar=True, relief=lambda s: 200.0 + (0.15 if s >= 4.0 else 0.0))
        prof, label, _ = self.run_profile(services)
        self.assertEqual(label, terrain.LIDAR_LABEL)
        self.assertAlmostEqual(at(prof, 6.0), 200.15, delta=0.01)

    def test_lidar_incomplete(self):
        # Profil LiDAR à trous : droite de pente
        services = FakeServices(lidar=True, relief=lambda s: 200.0 + 0.05 * s if int(s * 2) % 3 else -99999.0)
        prof, label, _ = self.run_profile(services)
        self.assertEqual(label, terrain.LIDAR_LINE_LABEL)
        self.assertEqual(len(prof), 2)

    def test_bridge(self):
        services = FakeServices()
        prof, label, warning = self.run_profile(services, bridge=True)
        self.assertEqual((prof, label, warning), (ground.flat_profile(), terrain.BRIDGE_LABEL, None))
        self.assertEqual(services.calls, [])  # aucun service interrogé

    def test_flat_when_everything_fails(self):
        services = FakeServices(down={"data.geopf.fr"}, coarse_slope=0.3)  # pente invraisemblable
        prof, label, warning = self.run_profile(services)
        self.assertEqual(label, ground.FLAT)
        self.assertEqual(prof, ground.flat_profile())
        self.assertIn("invraisemblable", warning)
        self.assertEqual(services.calls, ["lidar", "data.geopf.fr", "api.opentopodata.org", "api.open-meteo.com"])



class Cache(unittest.TestCase):
    """Profils gardés pour la session et sources sans données plus interrogées dans la zone."""

    def profile(self, provider, services, yaw=0.0, lon=LON0, lat=LAT0):
        results = []
        with mock.patch.object(terrain.host, "fetch_json", services):
            provider.profile(lon, lat, yaw, lambda *r: results.append(r))
        return results

    def test_same_view_once(self):
        provider, services = terrain.TerrainProvider(), FakeServices(lidar=True)
        first = self.profile(provider, services)
        again = self.profile(provider, services, yaw=0.03)  # même direction au dixième de degré près
        self.assertEqual(first, again)
        self.assertEqual(services.calls, ["lidar"])
        self.profile(provider, services, yaw=0.5)  # 0,5° : 5 cm à 6 m, autre profil près d'une bordure
        self.assertEqual(services.calls, ["lidar", "lidar"])

    def test_concurrent_requests_shared(self):
        provider, pending, results = terrain.TerrainProvider(), [], []
        services = FakeServices(lidar=True)
        with mock.patch.object(terrain.host, "fetch_json", lambda url, cb, **kw: pending.append((url, cb))):
            provider.profile(LON0, LAT0, 0.0, lambda *r: results.append(r))
            provider.profile(LON0, LAT0, 0.0, lambda *r: results.append(r))
        self.assertEqual(len(pending), 1)
        url, callback = pending[0]
        services(url, callback)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0], results[1])

    def test_lidar_not_published_skipped_nearby(self):
        provider, services = terrain.TerrainProvider(), FakeServices()
        self.profile(provider, services)
        self.profile(provider, services, yaw=90.0, lon=LON0 + 0.00005)  # autre clic, même zone
        self.assertEqual(services.calls, ["lidar", "data.geopf.fr", "data.geopf.fr"])
        far = LON0 + 0.01  # à 750 m : le LiDAR HD y est à nouveau demandé
        self.profile(provider, services, lon=far)
        self.assertEqual(services.calls[-2:], ["lidar", "data.geopf.fr"])

    def test_services_down_retried(self):
        provider = terrain.TerrainProvider()
        down = FakeServices(down={"data.geopf.fr", "api.opentopodata.org", "api.open-meteo.com"})
        (prof, label, warning), = self.profile(provider, down)
        self.assertEqual(label, ground.FLAT)
        services = FakeServices(lidar=True)
        (prof, label, warning), = self.profile(provider, services)  # services revenus : pas de sol plat gardé
        self.assertEqual(label, terrain.LIDAR_LABEL)
        self.assertEqual(services.calls, ["lidar"])  # un service injoignable n'est pas « sans données »



class PreciseGround(unittest.TestCase):
    def test_lidar_only(self):
        # Hauteur de caméra par l'altitude GPS : sol LiDAR HD seulement (le RGE ALTI peut être décalé)
        self.assertIn(terrain.LIDAR_LABEL, terrain.PRECISE_LABELS)
        self.assertIn(terrain.LIDAR_LINE_LABEL, terrain.PRECISE_LABELS)
        self.assertNotIn(terrain.IGN_LABEL, terrain.PRECISE_LABELS)


if __name__ == "__main__":
    unittest.main()
