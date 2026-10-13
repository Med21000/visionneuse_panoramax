# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Cédric COCHART
# SPDX-License-Identifier: GPL-2.0-or-later
"""Services du plugin QGIS pour le cœur de calcul (voir core/host.py) : réseau de QGIS,
altitude NGF par PROJ, décodage des photos par Qt, tâches de fond QGIS. Installés à
l'import de ce module, et à nouveau par install() (plugin et panneau)."""

from qgis.core import QgsApplication, QgsTask
from qgis.PyQt.QtCore import QBuffer, QByteArray, QIODevice, QRect, QSize
from qgis.PyQt.QtGui import QImage, QImageReader

from . import altitude, api
from .core import host


def read_band(data, width, elev_min, elev_max):
    """Photo (octets JPEG) décodée en niveaux de gris, réduite à `width` de large et rognée à
    la bande d'élévations elev_min..elev_max (degrés). Retourne (tableau numpy, première
    ligne, hauteur et largeur de l'image entière) ou None. Utilisable hors du fil principal
    (pas de widget)."""
    import numpy as np

    buf = QBuffer()
    buf.setData(QByteArray(data))
    buf.open(QIODevice.OpenModeFlag.ReadOnly)
    reader = QImageReader(buf)
    size = reader.size()
    if not size.isValid() or size.width() < 2 * size.height() - 2:
        return None  # pas une photo équirectangulaire
    w = min(width, size.width())
    h = round(w * size.height() / size.width())
    row0, row1 = int((90 - elev_max) / 180 * h), int((90 - elev_min) / 180 * h)
    reader.setScaledSize(QSize(w, h))
    reader.setScaledClipRect(QRect(0, row0, w, row1 - row0))
    img = reader.read()
    if img.isNull():
        return None
    img = img.convertToFormat(QImage.Format.Format_Grayscale8)
    bpl = img.bytesPerLine()
    ptr = img.constBits()
    ptr.setsize(img.height() * bpl)
    band = np.frombuffer(ptr, np.uint8).reshape(img.height(), bpl)[:, :img.width()].astype(np.float32)
    return band, row0, h, w


def run_task(function, args, done):
    """function(*args) en arrière-plan (tâche QGIS), puis done(résultat ou None) dans le fil
    principal."""
    def work(task, *a):
        return function(*a)

    def finished(exception, result=None):
        _tasks.discard(task)
        done(None if exception else result)

    task = QgsTask.fromFunction("Visionneuse Panoramax : calcul", work, *args,
                                on_finished=finished, flags=QgsTask.Flag.Silent)
    _tasks.add(task)  # gardée tant qu'elle tourne
    QgsApplication.taskManager().addTask(task)


_tasks = set()


def install():
    host.install(fetch=api.fetch, fetch_json=api.fetch_json, ngf_altitude=altitude.ngf_altitude,
                 decode_band=read_band, run_task=run_task, api_url=api.API_URL)


install()
