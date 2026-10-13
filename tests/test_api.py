# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Cédric COCHART
# SPDX-License-Identifier: GPL-2.0-or-later
"""Tests de api.py : précision, correction différentielle et altitude lues dans les métadonnées ;
réponses JSON, cache des fiches (réponses dans le désordre, changement d'instance)."""

import unittest
from unittest import mock

from support import install_qgis_stub, module

install_qgis_stub()
api = module("api")


def item(accuracy=2.0, **exif):
    tags = {"Exif.GPSInfo." + k: v for k, v in exif.items()}
    return {"properties": {"quality:horizontal_accuracy": accuracy, "exif": tags}}


class Metadata(unittest.TestCase):
    def test_accuracy(self):
        self.assertEqual(api.item_accuracy(item(GPSHPositioningError="3/100")), 0.03)  # déclarée dans l'EXIF
        self.assertEqual(api.item_accuracy(item(4.0)), 4.0)  # annoncée par Panoramax
        self.assertIsNone(api.item_accuracy(item(None)))
        self.assertIsNone(api.item_accuracy({}))
        self.assertIsNone(api.exif_accuracy(item(GPSHPositioningError="0/1")))

    def test_differential(self):
        self.assertTrue(api.gps_differential(item(GPSDifferential="1")))
        self.assertFalse(api.gps_differential(item(GPSDifferential="0")))
        self.assertFalse(api.gps_differential(item()))

    def test_altitude(self):
        self.assertAlmostEqual(api.item_altitude(item(GPSAltitude="44090/100", GPSAltitudeRef="0")), 440.90)
        self.assertAlmostEqual(api.item_altitude(item(GPSAltitude="1250/100", GPSAltitudeRef="1")), -12.5)
        self.assertIsNone(api.item_altitude(item()))
        self.assertIsNone(api.item_altitude(item(GPSAltitude="abc")))

    def test_search_in_sequence(self):
        url = api.search_url(5.0, 47.0, 60, 20, "seq-1")
        self.assertIn("collections=seq-1", url)
        self.assertNotIn("collections", api.search_url(5.0, 47.0))


class FetchJson(unittest.TestCase):
    def run_fetch(self, payload, callback):
        with mock.patch.object(api, "fetch", lambda url, done, *a: done(payload, None)):
            api.fetch_json("https://exemple/api", callback)

    def test_callback_error_not_reported_as_json(self):
        calls = []

        def callback(data, error):
            calls.append((data, error))
            raise ValueError("erreur du callback")

        with self.assertRaises(ValueError):
            self.run_fetch(b'{"a": 1}', callback)
        self.assertEqual(calls, [({"a": 1}, None)])  # appelé une seule fois

    def test_invalid_json(self):
        calls = []
        self.run_fetch(b"pas du json", lambda data, error: calls.append((data, error)))
        self.assertEqual(len(calls), 1)
        self.assertIsNone(calls[0][0])
        self.assertIn("JSON invalide", calls[0][1])


def fiche(pid, prev=None, nxt=None):
    links = [{"rel": rel, "href": "https://exemple/api/collections/s/items/" + target}
             for rel, target in (("prev", prev), ("next", nxt)) if target]
    return {"id": pid, "geometry": {"coordinates": [5.0, 47.0]}, "links": links}


class ItemCache(unittest.TestCase):
    """Fiches des photos : les réponses du service sont délivrées à la main (self.answer)."""

    def setUp(self):
        api.clear_cache()
        self.requests = {}  # id -> callbacks de fetch_json en attente
        patcher = mock.patch.object(api, "fetch_json", self.fake_fetch)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(api.clear_cache)

    def fake_fetch(self, url, callback, *args, **kwargs):
        self.requests.setdefault(api.id_from_href(url), []).append(callback)

    def answer(self, pid, item=None):
        for callback in self.requests.pop(pid):
            callback(item or fiche(pid), None)

    def test_out_of_order(self):
        got = []
        api.get_item("a", lambda item, error: got.append(item["id"]))
        api.get_item("b", lambda item, error: got.append(item["id"]))
        self.assertEqual(api.requested_id(), "b")
        self.answer("b", fiche("b", prev="b0", nxt="b1"))
        self.answer("a", fiche("a", prev="a0", nxt="a1"))  # arrivée après celle de b
        self.assertEqual(got, ["b", "a"])  # délivrée (le panneau l'écarte, voir requested_id)…
        self.assertNotIn("a0", self.requests)  # … sans précharger les voisines de a
        self.assertIsNotNone(api.cached_item("b"))
        self.assertIn("b0", self.requests)

    def test_instance_changed(self):
        got = []
        api.get_item("a", lambda item, error: got.append(item))
        api.set_instance("autre", "https://autre.exemple")
        self.addCleanup(api.set_instance, None)
        self.answer("a")  # réponse de l'ancienne instance
        self.assertEqual(got, [])
        self.assertIsNone(api.cached_item("a"))
        self.assertIsNone(api.requested_id())

    def test_shared_request(self):
        got = []
        api.get_item("a", lambda item, error: got.append(1))
        api.get_item("a", lambda item, error: got.append(2))
        self.assertEqual(len(self.requests["a"]), 1)  # une seule requête
        self.answer("a")
        self.assertEqual(got, [1, 2])
        api.get_item("a", lambda item, error: got.append(3))  # en cache
        self.assertEqual(got, [1, 2, 3])
        self.assertEqual(self.requests, {})


if __name__ == "__main__":
    unittest.main()
