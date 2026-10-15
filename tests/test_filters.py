# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Cédric COCHART
# SPDX-License-Identifier: GPL-2.0-or-later
"""Tests de filters.py : expressions du filaire, filtre des fiches STAC, mémorisation."""

import unittest
from unittest import mock

from support import install_qgis_stub, module

install_qgis_stub()
filters = module("filters")
PhotoFilter = filters.PhotoFilter

FULL = PhotoFilter(since="2024-01-15", kind="360", max_gps=2.0, producer=("acc-1", "Producteur A"))


class FakeSettings:
    """QgsSettings en mémoire, partagé par toutes les instances."""
    store = {}

    def value(self, key, default=None):
        return self.store.get(key, default)

    def setValue(self, key, value):  # noqa: N802 (nom de l'API Qt)
        self.store[key] = value


def item(datetime="2025-05-28T10:00:00Z", fov=360, gps=1.5, producer="acc-1", in_properties=False):
    props = {"datetime": datetime, "pers:interior_orientation": {"field_of_view": fov},
             "quality:horizontal_accuracy": gps}
    providers = [{"name": "Producteur A", "id": producer, "roles": ["producer"]}] if producer else []
    out = {"id": "p1", "properties": props}
    if in_properties:
        props["providers"] = providers
    else:
        out["providers"] = providers
    for key in [k for k, v in props.items() if v is None]:
        del props[key]
    return out


class TileExpression(unittest.TestCase):
    def test_inactive(self):
        flt = PhotoFilter()
        self.assertFalse(flt.active())
        for layer in ("sequences", "pictures", "grid"):
            self.assertEqual(flt.tile_expression(layer), "")

    def test_sequences_and_pictures(self):
        self.assertEqual(FULL.tile_expression("sequences"),
                         "\"date\" >= '2024-01-15' AND \"type\" = 'equirectangular' AND "
                         "to_real(\"gps_accuracy\") <= 2.0 AND \"account_id\" = 'acc-1'")
        self.assertTrue(FULL.tile_expression("pictures").startswith("\"ts\" >= '2024-01-15'"))
        self.assertIn("\"type\" = 'flat'", PhotoFilter(kind="flat").tile_expression("pictures"))

    def test_grid_filters_kind_only(self):
        self.assertEqual(FULL.tile_expression("grid"), '"nb_360_pictures" > 0')
        self.assertEqual(PhotoFilter(kind="flat").tile_expression("grid"), '"nb_flat_pictures" > 0')
        self.assertEqual(PhotoFilter(since="2024-01-01").tile_expression("grid"), "")

    def test_quotes_escaped(self):
        flt = PhotoFilter(producer=("l'équipe", "L'équipe"))
        self.assertEqual(flt.tile_expression("pictures"), "\"account_id\" = 'l''équipe'")


class Matches(unittest.TestCase):
    def test_all_criteria(self):
        self.assertTrue(FULL.matches(item()))
        self.assertTrue(FULL.matches(item(in_properties=True)))  # producteurs dans les propriétés
        self.assertTrue(PhotoFilter().matches({}))

    def test_each_criterion_rejects(self):
        self.assertFalse(FULL.matches(item(datetime="2023-12-31T23:00:00Z")))
        self.assertTrue(FULL.matches(item(datetime="2024-01-15T08:00:00Z")))  # même jour
        self.assertFalse(FULL.matches(item(fov=90)))
        self.assertFalse(FULL.matches(item(gps=3.0)))
        self.assertFalse(FULL.matches(item(producer="autre")))

    def test_missing_values_reject(self):
        self.assertFalse(FULL.matches(item(gps=None)))
        self.assertFalse(FULL.matches(item(producer=None)))
        self.assertFalse(FULL.matches(item(datetime=None)))
        # Angle de vue absent : photo classique (téléphone)
        self.assertTrue(PhotoFilter(kind="flat").matches(item(fov=None)))
        self.assertFalse(PhotoFilter(kind="360").matches(item(fov=None)))


class Describe(unittest.TestCase):
    def test_describe(self):
        self.assertEqual(FULL.describe(), "depuis le 15/01/2024, 360°, GPS ≤ 2 m, producteur « Producteur A »")
        self.assertEqual(PhotoFilter(kind="flat", max_gps=4.5).describe(), "classiques, GPS ≤ 4,5 m")
        self.assertEqual(PhotoFilter().describe(), "")

    def test_item_producer(self):
        self.assertEqual(filters.item_producer(item()), ("acc-1", "Producteur A"))
        self.assertEqual(filters.item_producer(item(in_properties=True)), ("acc-1", "Producteur A"))
        self.assertIsNone(filters.item_producer(item(producer=None)))
        self.assertIsNone(filters.item_producer(None))
        other = {"providers": [{"name": "Hébergeur", "id": "h", "roles": ["host"]}]}
        self.assertIsNone(filters.item_producer(other))


class Storage(unittest.TestCase):
    def setUp(self):
        FakeSettings.store = {}
        patcher = mock.patch.object(filters, "QgsSettings", FakeSettings)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_round_trip(self):
        FULL.save()
        self.assertEqual(PhotoFilter.load(), FULL)
        PhotoFilter().save()
        self.assertEqual(PhotoFilter.load(), PhotoFilter())

    def test_empty_and_damaged_settings(self):
        self.assertEqual(PhotoFilter.load(), PhotoFilter())
        FakeSettings.store = {filters.SETTINGS + "max_gps": "abc", filters.SETTINGS + "producer_id": "x"}
        self.assertEqual(PhotoFilter.load(), PhotoFilter(producer=("x", "x")))


if __name__ == "__main__":
    unittest.main()
