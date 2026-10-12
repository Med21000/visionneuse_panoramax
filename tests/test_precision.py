# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Cédric COCHART
# SPDX-License-Identifier: GPL-2.0-or-later
"""Tests de precision.py : précision GPS vérifiée sur la séquence, mode dégradé.

Les services sont simulés : photos voisines de la séquence (Panoramax), sol LiDAR HD (IGN) et
géoïde (écart constant de GEOID m entre altitude GPS et altitude NGF)."""

import random
import unittest
from unittest import mock
from urllib.parse import parse_qs, urlsplit

from support import LAT0, install_qgis_stub, module, point

install_qgis_stub()
precision = module("precision")

GEOID, GROUND, CAMERA = 47.59, 391.4, 1.88


def photo(i, noise=0.0, differential=True, sequence="seq", altitude=True):
    lon, lat = point(0, 5.0 * i)
    exif = {"Exif.GPSInfo.GPSDifferential": "1" if differential else "0"}
    if altitude:
        exif["Exif.GPSInfo.GPSAltitude"] = "{:.0f}/100".format((GROUND + 0.01 * i + CAMERA + GEOID + noise) * 100)
    return {"id": "p{}".format(i), "collection": sequence, "geometry": {"coordinates": [lon, lat]},
            "properties": {"exif": exif, "quality:horizontal_accuracy": 2.0}}


class FakeServices:
    def __init__(self, photos, lidar=True, down=()):
        self.photos, self.lidar, self.down, self.calls = photos, lidar, set(down), []

    def __call__(self, url, callback, timeout_ms=None, prefer_cache=False):
        host = urlsplit(url).netloc
        self.calls.append(host)
        if host in self.down:
            callback(None, "délai dépassé")
        elif "search" in url:
            callback({"features": self.photos}, None)
        else:
            lats = [float(v) for v in parse_qs(urlsplit(url).query)["lat"][0].split("|")]
            # Sol qui monte de 1 cm par photo (5 m), comme l'altitude des photos
            callback({"elevations": [GROUND + 0.01 * round((lat - LAT0) * 111195 / 5) if self.lidar else -99999
                                     for lat in lats]}, None)


class Check(unittest.TestCase):
    def check(self, item, services, checker=None):
        checker = checker or precision.PrecisionChecker()
        out = []
        with mock.patch.object(precision.api, "fetch_json", services), \
                mock.patch.object(precision.altitude, "ngf_altitude", lambda lon, lat, h: None if h is None else h - GEOID):
            checker.check(item, out.append)
        self.assertEqual(len(out), 1)
        return out[0]

    def test_rtk_sequence_verified(self):
        rng = random.Random(1)
        photos = [photo(i, rng.gauss(0, 0.04)) for i in range(20)]
        res = self.check(photos[0], FakeServices(photos))
        self.assertTrue(res["precise"])
        self.assertEqual(res["accuracy"], precision.VERIFIED_ACCURACY)
        self.assertAlmostEqual(res["height"], CAMERA, delta=0.03)
        self.assertLess(res["spread"], precision.MAX_SPREAD)
        self.assertFalse(precision.degraded(res))

    def test_dgps_sequence_not_verified(self):
        rng = random.Random(2)
        photos = [photo(i, rng.gauss(0, 0.5)) for i in range(20)]  # altitude GPS à ±0,5 m
        res = self.check(photos[0], FakeServices(photos))
        self.assertFalse(res["precise"])
        self.assertTrue(res["lidar"])
        self.assertTrue(precision.degraded(res))  # LiDAR publié, mais position non vérifiée

    def test_degraded_without_lidar(self):
        photos = [photo(i) for i in range(20)]
        res = self.check(photos[0], FakeServices(photos, lidar=False))
        self.assertFalse(res["precise"])
        self.assertIs(res["lidar"], False)
        self.assertTrue(precision.degraded(res))
        res = self.check(photo(0, differential=False), FakeServices([], lidar=False))  # GPS simple
        self.assertTrue(precision.degraded(res))

    def test_declared_accuracy(self):
        item = photo(0, differential=False)
        item["properties"]["exif"]["Exif.GPSInfo.GPSHPositioningError"] = "5/100"
        res = self.check(item, FakeServices([], lidar=True))
        self.assertEqual((res["precise"], res["accuracy"], res["lidar"]), (True, 0.05, True))
        self.assertFalse(precision.degraded(res))
        services = FakeServices([], lidar=False)
        res = self.check(item, services)
        self.assertTrue(res["precise"])
        self.assertTrue(precision.degraded(res))  # précise, mais sans LiDAR HD
        self.assertEqual(services.calls, ["data.geopf.fr"])  # LiDAR sous la photo seulement

    def test_too_few_photos_or_altitudes(self):
        photos = [photo(i) for i in range(5)] + [photo(i, altitude=False) for i in range(5, 20)]
        res = self.check(photos[0], FakeServices(photos))
        self.assertFalse(res["precise"])
        self.assertIn("5 photos voisines", res["reason"])

    def test_once_per_sequence(self):
        photos = [photo(i) for i in range(20)]
        services, checker = FakeServices(photos), precision.PrecisionChecker()
        self.check(photos[0], services, checker)
        self.check(photos[7], services, checker)
        self.assertEqual(len(services.calls), 2)  # une recherche et une requête LiDAR
        self.assertTrue(checker.known(photos[3])["precise"])

    def test_differential_required(self):
        # Altitudes cohérentes, mais aucune correction différentielle déclarée : non vérifiée
        photos = [photo(i, differential=False) for i in range(20)]
        services = FakeServices(photos)
        res = self.check(photos[0], services)
        self.assertFalse(res["precise"])
        self.assertTrue(res["lidar"])
        self.assertEqual(services.calls, ["data.geopf.fr"])  # LiDAR sous la photo seulement

    def test_service_down_simple_gps(self):
        res = self.check(photo(0, differential=False), FakeServices([], down={"data.geopf.fr"}))
        self.assertIsNone(res["lidar"])
        self.assertTrue(precision.degraded(res))

    def test_service_down(self):
        photos = [photo(i) for i in range(20)]
        res = self.check(photos[0], FakeServices(photos, down={"data.geopf.fr"}))
        self.assertFalse(res["precise"])
        self.assertIsNone(res["lidar"])  # inconnu
        self.assertTrue(precision.degraded(res))  # position non vérifiée
        self.assertIn("non vérifiée (service d'altimétrie indisponible)", precision.degraded_reasons(res)[-1])


    def test_service_down_not_kept(self):
        """Résultat obtenu malgré un service injoignable : la vérification est refaite ensuite."""
        photos = [photo(i) for i in range(20)]
        checker = precision.PrecisionChecker()
        res = self.check(photos[0], FakeServices(photos, down={"data.geopf.fr"}), checker)
        self.assertTrue(res["transient"])
        self.assertIsNone(checker.known(photos[0]))
        res = self.check(photos[1], FakeServices(photos), checker)  # service revenu
        self.assertTrue(res["precise"])
        self.assertTrue(checker.known(photos[0])["precise"])

    def test_search_down_not_kept(self):
        photos = [photo(i) for i in range(20)]
        checker = precision.PrecisionChecker()
        services = FakeServices(photos, down={"api.panoramax.xyz"})
        res = self.check(photos[0], services, checker)
        self.assertFalse(res["precise"])
        self.assertTrue(res["transient"])
        self.assertIsNone(checker.known(photos[0]))

    def test_photos_without_sequence(self):
        """Deux photos sans séquence vérifiées en même temps : chacune son résultat."""
        declared = photo(0, differential=False, sequence=None)
        declared["properties"]["exif"]["Exif.GPSInfo.GPSHPositioningError"] = "5/100"
        simple = photo(1, differential=False, sequence=None)
        pending = []
        checker, out = precision.PrecisionChecker(), {}

        def fake(url, callback, **kwargs):
            pending.append(callback)

        with mock.patch.object(precision.api, "fetch_json", fake):
            checker.check(declared, lambda res: out.setdefault("declared", res))
            checker.check(simple, lambda res: out.setdefault("simple", res))
        self.assertEqual(len(pending), 2)  # deux vérifications, pas une seule partagée
        for callback in pending:
            callback({"elevations": [{"z": GROUND}]}, None)
        self.assertTrue(out["declared"]["precise"])
        self.assertFalse(out["simple"]["precise"])


class Texts(unittest.TestCase):
    def test_full_mode(self):
        res = precision.result(True, 0.1, True, "vérifiée")
        self.assertFalse(precision.degraded(res))
        self.assertEqual(precision.degraded_reasons(res), [])
        self.assertEqual(precision.state_text(res), "Position GPS vérifiée (±0,10 m) · LiDAR HD")

    def test_degraded(self):
        res = precision.result(False, lidar=False, reason="pas de correction différentielle déclarée")
        self.assertEqual(precision.degraded_reasons(res), [
            "position GPS non vérifiée au décimètre (pas de correction différentielle déclarée)",
            "pas de LiDAR HD publié à cet endroit"])
        self.assertEqual(precision.state_text(res, 4.0),
                         "⚠ Position GPS non vérifiée (±4 m annoncés) · pas de LiDAR HD : mode de mesure dégradé")
        self.assertFalse(precision.degraded(None))  # vérification en cours
        self.assertIn("en cours", precision.state_text(None))

if __name__ == "__main__":
    unittest.main()
