# -*- coding: utf-8 -*-
"""Plugin QGIS Visionneuse Panoramax — Cédric COCHART."""

import os

# La visionneuse Panoramax a besoin de WebGL.
#
# QGIS 4 force Qt Quick en rendu logiciel (contournement de QTBUG-139109) :
# Qt WebEngine en déduit qu'il ne doit utiliser aucun GPU et ajoute
# --disable-gpu --use-gl=disabled, ce qui coupe WebGL.
#
# On impose donc un WebGL logiciel (ANGLE + SwiftShader) tout en laissant la
# composition de la page en logiciel, compatible avec le choix de QGIS.
# Ces options ne sont lues qu'à l'initialisation du moteur web : elles sont
# définies dès le chargement du plugin, avant toute création de vue web.
_FLAGS = (
    "--use-gl=angle",
    "--use-angle=swiftshader",
    "--enable-unsafe-swiftshader",
    "--disable-gpu-compositing",
    "--ignore-gpu-blocklist",
)
_current = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "").split()
if not any(f.startswith("--use-gl=") or f == "--disable-gpu" for f in _current):
    # On respecte un réglage GPU explicitement choisi par l'utilisateur
    _missing = [f for f in _FLAGS if f not in _current]
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = " ".join(_current + _missing)


def classFactory(iface):  # noqa: N802 (nom imposé par QGIS)
    from .plugin import PanoramaxPlugin

    return PanoramaxPlugin(iface)
