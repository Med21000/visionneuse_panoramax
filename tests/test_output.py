# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Cédric COCHART
# SPDX-License-Identifier: GPL-2.0-or-later
"""Tests de output.write_feature et de l'enregistrement des mesures, sans QGIS (couches simulées).

Mise à jour d'une entité : le commentaire de l'utilisateur et les champs qu'il a ajoutés sont
conservés. Une mesure enregistrée dans une couche n'écrase pas, sous le même fid, l'entité
d'une autre couche. Les mêmes cas sont vérifiés avec le vrai QGIS dans tests/qgis/test_output.py.
"""

import unittest
from unittest import mock

from support import Stub, install_qgis_stub, module

install_qgis_stub()
output = module("output")
measure_tool = module("measure_tool")


class Field:
    def __init__(self, name):
        self._name = name

    def name(self):
        return self._name


class Fields:
    def __init__(self, names):
        self.names = list(names)

    def count(self):
        return len(self.names)

    def at(self, i):
        return Field(self.names[i])


class Feature:
    def __init__(self, values, valid=True):
        self.values, self.valid = list(values), valid

    def attribute(self, i):
        return self.values[i]

    def geometry(self):
        return "géométrie"

    def isValid(self):  # noqa: N802
        return self.valid


class Provider:
    def __init__(self, layer):
        self.layer = layer

    def changeGeometryValues(self, geometries):  # noqa: N802
        return True

    def changeAttributeValues(self, changes):  # noqa: N802
        for fid, attrs in changes.items():
            for i, value in attrs.items():
                self.layer.rows[fid][i] = value
        return True

    def addFeatures(self, features):  # noqa: N802
        written = []
        for f in features:
            fid = max(self.layer.rows, default=0) + 1
            self.layer.rows[fid] = list(f.values)
            written.append(Stub())
            written[-1].id = lambda fid=fid: fid
        return True, written


class Layer:
    """Couche simulée : lignes {fid: [valeurs]}, champs dans l'ordre de `names`."""

    def __init__(self, names, layer_id="couche"):
        self._fields, self.rows, self._id = Fields(names), {}, layer_id

    def id(self):
        return self._id

    def name(self):
        return "Panoramax – mesures"

    def fields(self):
        return self._fields

    def getFeature(self, fid):  # noqa: N802
        return Feature(self.rows.get(fid, []), valid=fid in self.rows)

    def isEditable(self):  # noqa: N802
        return False

    def dataProvider(self):  # noqa: N802
        return Provider(self)

    def updateExtents(self):  # noqa: N802
        pass

    def triggerRepaint(self):  # noqa: N802
        pass

    def value(self, fid, name):
        return self.rows[fid][self._fields.names.index(name)]


NAMES = ["fid", "valeur_m", "photos", "commentaire", "ajout_utilisateur"]


class WriteFeature(unittest.TestCase):
    def test_comment_and_user_fields_kept(self):
        layer = Layer(NAMES)
        fid = output.write_feature(layer, Feature([None, 1.0, "p1", None, None]))
        layer.rows[fid][3], layer.rows[fid][4] = "bordure abîmée", "à revoir"  # saisis dans QGIS
        managed = output.field_names("field=valeur_m:double&field=photos:string&field=commentaire:string")
        self.assertEqual(output.write_feature(layer, Feature([None, 2.0, "p2", None, None]), fid, managed), fid)
        self.assertEqual((layer.value(fid, "valeur_m"), layer.value(fid, "photos")), (2.0, "p2"))
        self.assertEqual(layer.value(fid, "commentaire"), "bordure abîmée")
        self.assertEqual(layer.value(fid, "ajout_utilisateur"), "à revoir")

    def test_comment_kept_without_managed(self):
        layer = Layer(NAMES)
        fid = output.write_feature(layer, Feature([None, 1.0, "p1", None, None]))
        layer.rows[fid][3] = "bordure abîmée"
        output.write_feature(layer, Feature([None, 2.0, "p1", None, "x"]), fid)
        self.assertEqual(layer.value(fid, "commentaire"), "bordure abîmée")
        self.assertEqual(layer.value(fid, "ajout_utilisateur"), "x")  # tous les autres champs réécrits

    def test_field_names(self):
        self.assertEqual(output.field_names("field=a:string&field=b_m:double&crs=EPSG:4326"), ["a", "b_m"])
        self.assertIn("commentaire", output.field_names(measure_tool.LAYER_FIELDS))


class SaveMeasures(unittest.TestCase):
    """MeasureTool.save : le fid d'une mesure ne vaut que dans la couche où elle a été enregistrée."""

    def setUp(self):
        self.tool = measure_tool.MeasureTool(Stub())
        self.measure = mock.Mock(result={"value": 1.0}, fid=None, layer_id=None)
        self.measure.unsaved.return_value = True
        for patcher in (mock.patch.object(measure_tool.MeasureTool, "measures", lambda tool: [self.measure]),
                        mock.patch.object(measure_tool, "feature", lambda m, layer: Feature([None, 1.0, "p1", None, None]))):
            patcher.start()
            self.addCleanup(patcher.stop)

    def save(self, layer):
        with mock.patch.object(output, "ensure_layer", return_value=(layer, None)):
            return self.tool.save()

    def test_update_in_same_layer(self):
        layer = Layer(NAMES, "A")
        self.assertTrue(self.save(layer)[0])
        self.assertEqual((self.measure.fid, self.measure.layer_id), (1, "A"))
        ok, message = self.save(layer)
        self.assertTrue(ok)
        self.assertIn("mise à jour", message)
        self.assertEqual(len(layer.rows), 1)

    def test_other_layer_not_overwritten(self):
        self.assertTrue(self.save(Layer(NAMES, "A"))[0])
        other = Layer(NAMES, "B")
        other.rows[1] = [1, 9.99, "autre", "ne pas écraser", None]  # même fid, autre mesure
        ok, message = self.save(other)
        self.assertTrue(ok)
        self.assertIn("ajoutée", message)
        self.assertEqual(other.rows[1], [1, 9.99, "autre", "ne pas écraser", None])
        self.assertEqual(len(other.rows), 2)
        self.assertEqual((self.measure.fid, self.measure.layer_id), (2, "B"))


if __name__ == "__main__":
    unittest.main()
