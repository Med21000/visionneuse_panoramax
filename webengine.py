# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Cédric COCHART
# SPDX-License-Identifier: GPL-2.0-or-later
"""Options du moteur web (Qt WebEngine) pour le rendu WebGL de la visionneuse.

La visionneuse Panoramax a besoin de WebGL. QGIS 4 force Qt Quick en rendu logiciel
(contournement de QTBUG-139109) : Qt WebEngine en déduit qu'il ne doit utiliser aucun
GPU et ajoute --disable-gpu --use-gl=disabled, ce qui coupe WebGL. On impose donc
WebGL par ANGLE, la composition de la page restant logicielle, compatible avec le
choix de QGIS (sans --disable-gpu-compositing, le moteur web plante).

Option « Accélération matérielle » (expérimentale, décochée par défaut) :
- décochée, rendu logiciel, par le processeur : SwiftShader quand le moteur web
  l'embarque (Windows notamment). Les moteurs web livrés sans SwiftShader (paquets
  Linux, par exemple Fedora) se rabattent sinon sur la carte graphique : on y demande
  en plus le rendu logiciel de Mesa (llvmpipe) par LIBGL_ALWAYS_SOFTWARE, le temps que
  le moteur web lance son processus graphique (voir begin_software / end_software) ;
- cochée, moteur graphique du système (OpenGL, Direct3D…) : nettement plus rapide,
  au risque d'un écran noir ou d'un plantage avec certains pilotes.
Le moteur obtenu est vérifié dans la page et signalé dans le journal (describe).

Ces options ne sont lues qu'au démarrage du moteur web, une fois par session de QGIS :
configure() est appelée au chargement du plugin, avant toute vue web, et un
changement d'option prend effet au redémarrage de QGIS.
"""

import os

from qgis.core import QgsSettings

HARDWARE_SETTING = "visionneuse_panoramax/hardware_acceleration"
SOFTWARE_FLAGS = (
    "--use-gl=angle",
    "--use-angle=swiftshader",
    "--enable-unsafe-swiftshader",
    "--disable-gpu-compositing",
    "--ignore-gpu-blocklist",
)
HARDWARE_FLAGS = (
    "--use-gl=angle",
    "--use-angle=default",
    "--disable-gpu-compositing",
    "--ignore-gpu-blocklist",
)
# Rendu logiciel de Mesa (pilotes libres sous Linux) : sans effet ailleurs
SOFTWARE_ENV = {"LIBGL_ALWAYS_SOFTWARE": "1"}
SOFTWARE_NAMES = ("swiftshader", "llvmpipe", "softpipe", "software")

MODE = None  # "software", "hardware" ou "user" (options choisies par l'utilisateur), après configure()
_saved_env = None  # variables remplacées par begin_software, à rétablir


def configure():
    """Options du moteur web pour la session, selon l'option « Accélération matérielle »."""
    global MODE
    current = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "").split()
    if any(f.startswith("--use-gl=") or f == "--disable-gpu" for f in current):
        MODE = "user"  # réglage GPU explicitement choisi par l'utilisateur : respecté
        return MODE
    MODE = "hardware" if QgsSettings().value(HARDWARE_SETTING, False, type=bool) else "software"
    flags = HARDWARE_FLAGS if MODE == "hardware" else SOFTWARE_FLAGS
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = " ".join(current + [f for f in flags if f not in current])
    return MODE


def begin_software():
    """Rendu logiciel demandé aussi à Mesa, juste avant la création de la vue web : le moteur
    web lance son processus graphique avec ces variables. À rétablir par end_software dès
    que ce processus tourne, pour ne pas passer en logiciel les vues 3D de QGIS."""
    global _saved_env
    if MODE != "software" or _saved_env is not None:
        return
    _saved_env = {name: os.environ.get(name) for name in SOFTWARE_ENV}
    os.environ.update(SOFTWARE_ENV)


def end_software():
    global _saved_env
    if _saved_env is None:
        return
    for name, value in _saved_env.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value
    _saved_env = None


def is_software(renderer):
    """Le moteur WebGL rapporté par la page est-il un rendu logiciel ?"""
    return any(name in (renderer or "").lower() for name in SOFTWARE_NAMES)


def describe(renderer):
    """Phrase du diagnostic : moteur WebGL de la visionneuse, options, et écart éventuel
    entre le rendu demandé et le rendu obtenu."""
    if not renderer:
        return "WebGL : moteur inconnu."
    software = is_software(renderer)
    kind = "rendu logiciel, par le processeur" if software else "accélération matérielle, par la carte graphique"
    options = {"software": "option « Accélération matérielle » décochée",
               "hardware": "option « Accélération matérielle » cochée",
               "user": "options de QTWEBENGINE_CHROMIUM_FLAGS"}.get(MODE, "options inconnues")
    text = "WebGL : {} ({}, {}).".format(renderer, kind, options)
    if MODE == "software" and not software:
        text += " Rendu logiciel indisponible avec ce moteur web et ce pilote graphique."
    elif MODE == "hardware" and software:
        text += " Accélération matérielle indisponible : pilote graphique non pris en charge par le moteur web."
    return text
