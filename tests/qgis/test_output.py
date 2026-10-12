# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Cédric COCHART
# SPDX-License-Identifier: GPL-2.0-or-later
"""Tests de output.py et de l'enregistrement des mesures, avec le vrai QGIS.

Couche temporaire ou GeoPackage, tables partagées, structure incompatible, ajout et
mise à jour d'entités, boîte de dialogue du choix ; enregistrement de bout en bout de
mesures (MeasureTool) et de points triangulés (Triangulator).
"""

import math
import os
import tempfile
import unittest
from unittest import mock

from qgis_support import TMP, module

from qgis.core import QgsFeature, QgsGeometry, QgsPointXY, QgsProject, QgsSettings, QgsVectorLayer
from qgis.gui import QgsMapCanvas
from qgis.PyQt.QtWidgets import QFileDialog, QMessageBox

output = module("output")
measure = module("measure")
measure_tool = module("measure_tool")
triangulation = module("triangulation")

FIELDS = "field=nom:string&field=valeur:double"
LON0, LAT0 = 5.0415, 47.3220


def point(east, north):
    return triangulation._geographic(east, north, LON0, LAT0)


def new_path(name="mesures.gpkg"):
    return os.path.join(tempfile.mkdtemp(dir=TMP), name)


def feature(layer, value, x=LON0):
    f = QgsFeature(layer.fields())
    f.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(x, LAT0)))
    f.setAttribute("nom", "a")
    f.setAttribute("valeur", value)
    return f


class Base(unittest.TestCase):
    def setUp(self):
        QgsProject.instance().clear()
        self.styled = []

    def ensure(self, storage, kind="essai", table="essai", fields=FIELDS, geometry="Point"):
        """ensure_layer avec le choix de l'utilisateur simulé (chemin, "" ou None)."""
        with mock.patch.object(output, "_ask_storage", return_value=storage):
            return output.ensure_layer(kind, "Couche " + kind, table, geometry, fields, self.styled.append)


class EnsureLayer(Base):
    def test_memory(self):
        layer, error = self.ensure("")
        self.assertIsNone(error)
        self.assertEqual(layer.providerType(), "memory")
        self.assertEqual(layer.crs().authid(), "EPSG:4326")  # projet sans SCR
        self.assertEqual(self.styled, [layer])
        self.assertIs(output.find_layer("essai"), layer)
        self.assertEqual(self.ensure(new_path())[0], layer)  # déjà dans le projet : pas de nouvelle question

    def test_cancel(self):
        self.assertEqual(self.ensure(None), (None, None))
        self.assertIsNone(output.find_layer("essai"))

    def test_geopackage_shared_by_two_tables(self):
        path = new_path()
        a, _ = self.ensure(path, kind="mesures", table="mesures", geometry="LineString")
        b, _ = self.ensure(path, kind="points", table="points_triangules")
        for layer, table in ((a, "mesures"), (b, "points_triangules")):
            self.assertEqual(layer.providerType(), "ogr")
            self.assertIn("layername=" + table, layer.source())
        gpkg = QgsVectorLayer(path, "", "ogr")
        tables = sorted(s.split("!!::!!")[1] for s in gpkg.dataProvider().subLayers())
        self.assertEqual(tables, ["mesures", "points_triangules"])

    def test_existing_table_reused(self):
        path = new_path()
        layer, _ = self.ensure(path)
        output.write_feature(layer, feature(layer, 1.5))
        QgsProject.instance().clear()  # projet rouvert : la table est retrouvée avec son contenu
        again, error = self.ensure(path)
        self.assertIsNone(error)
        self.assertEqual(again.featureCount(), 1)

    def test_incompatible_table(self):
        path = new_path()
        self.ensure(path)
        QgsProject.instance().clear()
        layer, error = self.ensure(path, fields=FIELDS + "&field=autre:integer")
        self.assertIsNone(layer)
        self.assertIn("existe déjà avec une autre structure", error)
        QgsProject.instance().clear()
        layer, error = self.ensure(path, geometry="LineString")
        self.assertIsNone(layer)


class WriteFeature(Base):
    def check(self, layer):
        fid = output.write_feature(layer, feature(layer, 1.0))
        self.assertIsNotNone(fid)
        self.assertEqual(output.write_feature(layer, feature(layer, 2.0, x=LON0 + 0.001), fid), fid)
        self.assertEqual(layer.featureCount(), 1)
        updated = layer.getFeature(fid)
        self.assertEqual(updated["valeur"], 2.0)
        self.assertAlmostEqual(updated.geometry().asPoint().x(), LON0 + 0.001)
        other = output.write_feature(layer, feature(layer, 3.0), fid + 100)  # entité disparue : ajout
        self.assertNotEqual(other, fid)
        self.assertEqual(layer.featureCount(), 2)

    def test_provider(self):
        self.check(self.ensure(new_path())[0])

    def test_memory(self):
        self.check(self.ensure("")[0])

    def test_editable(self):
        layer, _ = self.ensure(new_path())
        layer.startEditing()
        self.check(layer)
        self.assertTrue(layer.commitChanges())
        self.assertEqual(layer.featureCount(), 2)

    def check_kept(self, layer):
        """Mise à jour : le commentaire de l'utilisateur et les champs non calculés sont conservés."""
        fid = output.write_feature(layer, feature(layer, 1.0))
        index = layer.fields().indexOf("commentaire")
        if layer.isEditable():
            self.assertTrue(layer.changeAttributeValue(fid, index, "à vérifier"))
        else:
            self.assertTrue(layer.dataProvider().changeAttributeValues({fid: {index: "à vérifier"}}))
        new = feature(layer, 2.0)
        new.setAttribute("nom", "b")
        self.assertEqual(output.write_feature(layer, new, fid, managed=["valeur"]), fid)
        updated = layer.getFeature(fid)
        self.assertEqual(updated["valeur"], 2.0)
        self.assertEqual(updated["nom"], "a")  # champ hors de `managed` : inchangé
        self.assertEqual(updated["commentaire"], "à vérifier")
        self.assertEqual(output.write_feature(layer, new, fid), fid)  # tous les champs calculés…
        self.assertEqual(layer.getFeature(fid)["nom"], "b")
        self.assertEqual(layer.getFeature(fid)["commentaire"], "à vérifier")  # … sauf le commentaire

    def test_comment_kept(self):
        fields = FIELDS + "&field=commentaire:string"
        self.check_kept(self.ensure(new_path(), fields=fields)[0])
        QgsProject.instance().clear()
        self.check_kept(self.ensure("", fields=fields)[0])
        QgsProject.instance().clear()
        layer, _ = self.ensure(new_path(), fields=fields)
        layer.startEditing()
        self.check_kept(layer)
        self.assertTrue(layer.commitChanges())

    def test_field_names(self):
        self.assertEqual(output.field_names("field=a:string&field=b_m:double&crs=EPSG:4326"), ["a", "b_m"])


class AskStorage(unittest.TestCase):
    """Boîte de dialogue du choix : bouton cliqué et fenêtre de fichier simulés."""

    def ask(self, button, file=("", "")):
        def fake_exec(box):
            box.chosen = next((b for b in box.buttons() if b.text() == button), None)

        with mock.patch.object(QMessageBox, "exec", fake_exec), \
                mock.patch.object(QMessageBox, "clickedButton", lambda box: box.chosen), \
                mock.patch.object(QFileDialog, "getSaveFileName", return_value=file) as dialog:
            return output._ask_storage(None, "Couche"), dialog

    def test_choices(self):
        self.assertEqual(self.ask("Couche temporaire")[0], "")
        self.assertIsNone(self.ask("&Cancel")[0])
        self.assertIsNone(self.ask("Annuler")[0])
        self.assertIsNone(self.ask("Fichier GeoPackage…")[0])  # fenêtre de fichier annulée

    def test_geopackage(self):
        path = os.path.join(TMP, "choisi")
        result, _ = self.ask("Fichier GeoPackage…", (path, "GeoPackage (*.gpkg)"))
        self.assertEqual(result, path + ".gpkg")
        self.assertEqual(QgsSettings().value(output.GPKG_SETTING), path + ".gpkg")
        _, dialog = self.ask("Fichier GeoPackage…", (path + ".gpkg", ""))
        self.assertEqual(dialog.call_args[0][2], path + ".gpkg")  # dernier fichier proposé


class EndToEnd(Base):
    """Mesures et points triangulés enregistrés dans le même GeoPackage."""

    def setUp(self):
        super().setUp()
        self.canvas = QgsMapCanvas()
        self.addCleanup(self._close_canvas)
        self.path = new_path()
        patcher = mock.patch.object(output, "_ask_storage", return_value=self.path)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _close_canvas(self):
        QgsProject.instance().clear()
        self.canvas.deleteLater()
        self.canvas = None

    def click(self, target, camera=1.9):
        dx, dy = target
        lon, lat = point(0, 0)
        return {"lon": lon, "lat": lat, "pic": "p1", "sequence": "s1", "axis": 0.0,
                "yaw": math.degrees(math.atan2(dx, dy)) % 360,
                "elev": math.degrees(math.atan2(-camera, math.hypot(dx, dy)))}

    def test_measures(self):
        tool = measure_tool.MeasureTool(self.canvas)
        tool.set_mode("width")
        for target in ((-3.0, 8.0), (4.0, 12.0)):
            tool.add_click(self.click(target))
        self.assertEqual(tool.save(), (True, "1 mesure ajoutée dans « Panoramax – mesures »."))
        layer = output.find_layer("measures")
        self.assertIn("layername=mesures", layer.source())
        f = next(layer.getFeatures())
        self.assertAlmostEqual(f["valeur_m"], math.hypot(7.0, 4.0), places=3)
        self.assertEqual(f["type"], "longueur parallèle à la route")
        self.assertEqual(f["photos"], "p1")
        self.assertEqual(tool.save(), (False, "Les mesures affichées sont déjà enregistrées."))
        # Point recalé sur la carte : l'entité est mise à jour, pas dupliquée
        tool.current.relocate(1, *point(4.0, 20.0))
        self.assertEqual(tool.save(), (True, "1 mise à jour dans « Panoramax – mesures »."))
        self.assertEqual(layer.featureCount(), 1)
        self.assertEqual(next(layer.getFeatures())["recales"], 1)

    def measure_tool(self, far=12.0):
        tool = measure_tool.MeasureTool(self.canvas)
        tool.set_mode("width")
        for target in ((-3.0, 8.0), (4.0, far)):
            tool.add_click(self.click(target))
        return tool

    def test_comment_kept_on_update(self):
        tool = self.measure_tool()
        self.assertTrue(tool.save()[0])
        layer = output.find_layer("measures")
        fid = next(layer.getFeatures()).id()
        index = layer.fields().indexOf("commentaire")
        self.assertTrue(layer.dataProvider().changeAttributeValues({fid: {index: "bordure abîmée"}}))
        tool.current.relocate(1, *point(4.0, 20.0))
        self.assertEqual(tool.save(), (True, "1 mise à jour dans « Panoramax – mesures »."))
        f = layer.getFeature(fid)
        self.assertEqual(f["commentaire"], "bordure abîmée")
        self.assertEqual(f["recales"], 1)

    def test_other_layer_not_overwritten(self):
        """Mesure enregistrée, couche retirée, puis enregistrée à nouveau dans un autre GeoPackage
        dont la table a déjà une entité de même fid : celle-ci n'est pas écrasée."""
        tool = self.measure_tool()
        self.assertTrue(tool.save()[0])
        QgsProject.instance().clear()
        other_path = new_path("autre.gpkg")
        with mock.patch.object(output, "_ask_storage", return_value=other_path):
            other = self.measure_tool(far=30.0)
            self.assertTrue(other.save()[0])
            other_layer = output.find_layer("measures")
            before = {f.id(): f["valeur_m"] for f in other_layer.getFeatures()}
            self.assertIn(tool.current.fid, before)  # même fid que la mesure de l'autre fichier
            tool.current.relocate(1, *point(4.0, 20.0))
            self.assertEqual(tool.save(), (True, "1 mesure ajoutée dans « Panoramax – mesures »."))
        self.assertEqual(other_layer.featureCount(), 2)
        self.assertEqual(other_layer.getFeature(next(iter(before)))["valeur_m"], next(iter(before.values())))

    def test_triangulated_point(self):
        tri = measure.Triangulator(self.canvas)
        target = point(15, 30)
        for pic, photo in (("p1", point(0, 0)), ("p2", point(20, 0))):
            x, y = triangulation._local(target[0], target[1], photo[0], photo[1])
            tri.add({"pic": pic, "lon": photo[0], "lat": photo[1], "heading": math.degrees(math.atan2(x, y)),
                     "accuracy": 2.0, "precise": False})
        ok, message = tri.save()
        self.assertTrue(ok, message)
        layer = output.find_layer("triangulation")
        self.assertIn("layername=points_triangules", layer.source())
        p = next(layer.getFeatures()).geometry().asPoint()
        self.assertLess(math.hypot(*triangulation._local(p.x(), p.y(), *target)), 0.01)
        self.assertEqual(next(layer.getFeatures())["nb_visees"], 2)


if __name__ == "__main__":
    unittest.main()
