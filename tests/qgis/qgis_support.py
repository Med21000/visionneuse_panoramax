# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Cédric COCHART
# SPDX-License-Identifier: GPL-2.0-or-later
"""Tests avec le vrai QGIS : application QGIS hors écran, réglages isolés, plugin chargé.

À lancer à part (python3 -m unittest discover -s tests/qgis) : les autres tests
remplacent qgis par un faux paquet, incompatible avec celui-ci dans le même processus.
Les réglages QGIS sont redirigés vers un dossier temporaire : le profil de
l'utilisateur n'est ni lu ni modifié.
"""

import atexit
import importlib
import importlib.util
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from qgis.core import QgsApplication, QgsProject  # noqa: E402
from qgis.PyQt.QtCore import QSettings  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PACKAGE = "visionneuse_panoramax"
TMP = tempfile.mkdtemp(prefix="panoramax-tests-")

APP = QgsApplication.instance()
if APP is None:
    APP = QgsApplication([], True)
    APP.initQgis()

    @atexit.register
    def _exit_qgis():
        """Couches du projet libérées avant QGIS : sinon plantage à la sortie de Python."""
        QgsProject.instance().clear()
        APP.exitQgis()
# Après initQgis, qui pointe les réglages sur le profil de l'utilisateur
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, os.path.join(TMP, "settings"))

if PACKAGE not in sys.modules:
    _spec = importlib.util.spec_from_file_location(PACKAGE, os.path.join(ROOT, "__init__.py"),
                                                   submodule_search_locations=[ROOT])
    _pkg = importlib.util.module_from_spec(_spec)
    sys.modules[PACKAGE] = _pkg
    _spec.loader.exec_module(_pkg)


def module(name):
    return importlib.import_module(PACKAGE + "." + name)
