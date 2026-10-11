# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Cédric COCHART
# SPDX-License-Identifier: GPL-2.0-or-later
"""Chargement des modules de calcul du plugin sans QGIS, et outils des scènes de test.

Le dossier du dépôt est enregistré comme paquet « visionneuse_panoramax », quel que
soit son nom sur le disque, pour que les imports relatifs des modules fonctionnent.
Les modules qui importent QGIS (terrain.py, measure_tool.py) se chargent avec un faux
paquet qgis (voir install_qgis_stub) : leurs calculs s'exécutent, l'affichage et le
réseau sont remplacés dans les tests.
"""

import importlib
import importlib.util
import math
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGE = "visionneuse_panoramax"



class _StubMeta(type):
    def __getattr__(cls, name):
        return Stub()


class Stub(metaclass=_StubMeta):
    """Objet Qt ou QGIS factice : tout attribut, appel ou sous-classe est accepté."""

    def __init__(self, *args, **kwargs):
        pass

    def __call__(self, *args, **kwargs):
        return Stub()

    def __getattr__(self, name):
        return Stub()


def _stub_module(name):
    module = types.ModuleType(name)
    module.__path__ = []
    module.__getattr__ = lambda attr: type(attr, (Stub,), {})  # une classe par nom
    return module


def install_qgis_stub():
    """Faux paquet qgis, si QGIS n'est pas déjà chargé."""
    if "qgis" in sys.modules:
        return
    for name in ("qgis", "qgis.core", "qgis.gui", "qgis.PyQt", "qgis.PyQt.QtCore", "qgis.PyQt.QtGui",
                 "qgis.PyQt.QtWidgets", "qgis.PyQt.QtNetwork"):
        sys.modules[name] = _stub_module(name)


if PACKAGE not in sys.modules:
    _spec = importlib.util.spec_from_file_location(PACKAGE, os.path.join(ROOT, "__init__.py"),
                                                   submodule_search_locations=[ROOT])
    _pkg = importlib.util.module_from_spec(_spec)
    sys.modules[PACKAGE] = _pkg
    _spec.loader.exec_module(_pkg)


def module(name):
    return importlib.import_module(PACKAGE + "." + name)


triangulation = module("triangulation")

LON0, LAT0 = 5.0415, 47.3220  # Dijon


def point(east, north, lon0=LON0, lat0=LAT0):
    """(lon, lat) du point situé à (est, nord) mètres de (lon0, lat0)."""
    return triangulation._geographic(east, north, lon0, lat0)


def bearing(frm, to):
    """Cap (degrés) de frm vers to, deux (lon, lat)."""
    x, y = triangulation._local(to[0], to[1], frm[0], frm[1])
    return math.degrees(math.atan2(x, y)) % 360


def distance(a, b):
    return math.hypot(*triangulation._local(b[0], b[1], a[0], a[1]))
