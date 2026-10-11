# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Cédric COCHART
# SPDX-License-Identifier: GPL-2.0-or-later
"""Plugin QGIS Visionneuse Panoramax — Cédric COCHART."""


def classFactory(iface):  # noqa: N802 (nom imposé par QGIS)
    # Options du moteur web pour WebGL, avant toute vue web (voir webengine.py)
    from . import webengine

    webengine.configure()

    from .plugin import PanoramaxPlugin

    return PanoramaxPlugin(iface)
