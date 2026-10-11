# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Cédric COCHART
# SPDX-License-Identifier: GPL-2.0-or-later
"""Chargement des modules de calcul du plugin sans QGIS, et outils des scènes de test.

Le dossier du dépôt est enregistré comme paquet « visionneuse_panoramax », quel que
soit son nom sur le disque, pour que les imports relatifs des modules fonctionnent.
"""

import importlib
import importlib.util
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGE = "visionneuse_panoramax"

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
