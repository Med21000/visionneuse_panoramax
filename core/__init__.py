# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Cédric COCHART
# SPDX-License-Identifier: GPL-2.0-or-later
"""Cœur de calcul de la Visionneuse Panoramax, sans QGIS.

Triangulation, points 3D, terrain, précision GPS, calages, inclinaison automatique, axe de
la route, façades et mesures : du Python pur (numpy en option, pour l'inclinaison
automatique), utilisable par le plugin QGIS comme par l'application web web-measures
(Pyodide). Ce qui dépend de l'application hôte (réseau, altitude NGF, décodage d'une photo,
tâches de fond) passe par host.py, que l'hôte remplit au démarrage.

Aucun module de ce paquet n'importe qgis : tests/test_core.py le vérifie.
"""
