# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Cédric COCHART
# SPDX-License-Identifier: GPL-2.0-or-later
"""Calage conservé dans le profil QGIS, d'une session et d'un projet à l'autre, et reprise
du calage d'un projet enregistré par une version précédente (plugin.py), avec le vrai QGIS."""

import json
import os
import tempfile
import unittest
from unittest import mock

from qgis_support import TMP, module

from qgis.core import QgsProject, QgsSettings

plugin = module("plugin")
calibration = module("calibration")


def new_plugin():
    """Plugin réduit à son calage : sans interface QGIS ni panneau."""
    p = plugin.PanoramaxPlugin.__new__(plugin.PanoramaxPlugin)
    p.iface = mock.Mock()
    p.calibrator = p.dock = None
    p.calibration = calibration.Calibration()
    p._saved_auto = None
    p.calibration.changed = p._save_calibration
    p._load_calibration()
    return p


class Storage(unittest.TestCase):
    def setUp(self):
        QgsSettings().remove(plugin.CALIBRATION_SETTING)
        QgsProject.instance().clear()
        # Inclinaisons automatiques : fichier temporaire, pas celui du profil de l'utilisateur
        self.auto_path = os.path.join(tempfile.mkdtemp(dir=TMP), "inclinaisons.json")
        patcher = mock.patch.object(plugin, "auto_tilt_path", lambda: self.auto_path)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_auto_tilts_in_their_own_file(self):
        first = new_plugin()
        entry = {"sequence": "s", "heading": 10.0, "lidar": [0.5, -0.2],
                 "verticals": {"up": [0.01, 0.0, 1.0], "count": 50, "sigma": 0.1}}
        first.calibration.set_auto("p1", entry)
        self.assertNotIn("auto", json.loads(QgsSettings().value(plugin.CALIBRATION_SETTING)))  # réglage léger
        with open(self.auto_path, encoding="utf-8") as f:
            self.assertIn("p1", json.load(f))
        self.assertEqual(new_plugin().calibration.tilt("p1")["source"], "verticales")  # session suivante
        mtime = os.stat(self.auto_path).st_mtime_ns
        os.utime(self.auto_path, ns=(0, 0))
        first.calibration.add_vertical("p2", (10.0, -10.0), (10.0, 20.0))  # autre calage : fichier inchangé
        self.assertEqual(os.stat(self.auto_path).st_mtime_ns, 0)
        self.assertGreater(mtime, 0)

    def test_migration_from_setting(self):
        # Version précédente : inclinaisons rangées dans le réglage
        old = {"format": 1, "auto": {"p1": {"sequence": "s", "heading": 0.0, "lidar": None,
                                            "verticals": {"up": [0.0, 0.01, 1.0], "count": 40, "sigma": 0.2}}}}
        QgsSettings().setValue(plugin.CALIBRATION_SETTING, json.dumps(old))
        p = new_plugin()
        self.assertEqual(p.calibration.tilt("p1")["source"], "verticales")
        self.assertNotIn("auto", json.loads(QgsSettings().value(plugin.CALIBRATION_SETTING)))
        with open(self.auto_path, encoding="utf-8") as f:
            self.assertIn("p1", json.load(f))

    def test_kept_between_sessions_and_projects(self):
        first = new_plugin()
        first.calibration.add_camera_height("s1", "ground", [
            {"lon": 5.0, "lat": 47.0, "yaw": 0.0, "elev": -14.7, "pic": "p"},
            {"lon": 5.0, "lat": 47.0, "yaw": 0.0, "elev": -10.8, "pic": "p"}], 3.0)
        height = first.calibration.camera("s1")["height"]
        QgsProject.instance().clear()  # nouveau projet : rien n'est vidé
        self.assertEqual(first.calibration.camera("s1")["height"], height)
        second = new_plugin()  # session suivante
        self.assertAlmostEqual(second.calibration.camera("s1")["height"], height)
        second.calibration.clear_camera("s1")  # effacé : plus retrouvé ensuite
        self.assertIsNone(new_plugin().calibration.camera("s1"))

    def test_project_of_previous_version(self):
        old = calibration.Calibration()
        old.heights["s1"], old.heights["s2"] = [(1.5, 0.05)], [(2.5, 0.05)]
        project = QgsProject.instance()
        project.writeEntry(plugin.PROJECT_SCOPE, plugin.CALIBRATION_KEY, json.dumps(old.to_dict()))
        path = os.path.join(tempfile.mkdtemp(dir=TMP), "ancien.qgz")
        self.assertTrue(project.write(path))
        project.clear()

        p = new_plugin()
        p.calibration.heights["s1"] = [(2.1, 0.05)]  # calage plus récent en mémoire
        project.readProject.connect(p._import_project_calibration)
        try:
            self.assertTrue(project.read(path))
        finally:
            project.readProject.disconnect(p._import_project_calibration)
        self.assertEqual(p.calibration.camera("s1")["height"], 2.1)  # le plus récent l'emporte
        self.assertEqual(p.calibration.camera("s2")["height"], 2.5)  # repris du projet
        self.assertFalse(project.readEntry(plugin.PROJECT_SCOPE, plugin.CALIBRATION_KEY, "")[1])  # retiré du projet
        p.iface.messageBar().pushMessage.assert_called_once()
        self.assertAlmostEqual(new_plugin().calibration.camera("s2")["height"], 2.5)  # gardé dans le profil

    def test_damaged_setting(self):
        QgsSettings().setValue(plugin.CALIBRATION_SETTING, "{abîmé")
        p = new_plugin()
        self.assertEqual(p.calibration.to_dict()["heights"], {})
        p.iface.messageBar().pushMessage.assert_called_once()


if __name__ == "__main__":
    unittest.main()
