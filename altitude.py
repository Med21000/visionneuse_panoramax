# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Cédric COCHART
# SPDX-License-Identifier: GPL-2.0-or-later
"""Altitude NGF de la caméra, à partir de l'altitude GPS d'une photo à position vérifiée précise.

L'altitude GPS est ellipsoïdale (WGS 84) ; les altitudes du terrain IGN sont dans le
système NGF-IGN69. L'écart (le géoïde, environ 40 à 55 m en France) vient de la grille
RAF20 de l'IGN, que PROJ télécharge à la demande par QGIS. Hors de France ou sans grille,
PROJ ne corrige rien : l'altitude n'est alors pas convertie (None).
"""

from qgis.core import QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsProject, QgsVector3D

GEOID_RANGE = (30.0, 70.0)  # écart plausible entre ellipsoïde et NGF-IGN69 en France (m)
_transform = None


def ngf_altitude(lon, lat, ellipsoidal):
    """Altitude NGF-IGN69 (m) d'un point d'altitude ellipsoïdale WGS 84, ou None."""
    global _transform
    if ellipsoidal is None:
        return None
    try:
        if _transform is None:
            ngf, _ = QgsCoordinateReferenceSystem.createCompoundCrs(QgsCoordinateReferenceSystem("EPSG:4326"),
                                                                   QgsCoordinateReferenceSystem("EPSG:5720"))
            _transform = QgsCoordinateTransform(QgsCoordinateReferenceSystem("EPSG:4979"), ngf,
                                                QgsProject.instance())
        z = _transform.transform(QgsVector3D(float(lon), float(lat), float(ellipsoidal))).z()
    except Exception:  # SCR ou transformation indisponibles
        return None
    geoid = ellipsoidal - z
    return z if GEOID_RANGE[0] <= geoid <= GEOID_RANGE[1] else None
