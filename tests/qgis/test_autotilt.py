# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Cédric COCHART
# SPDX-License-Identifier: GPL-2.0-or-later
"""Tests de autotilt.py avec le vrai QGIS : photo JPEG décodée réduite et rognée à la bande
utile (QImageReader), calcul en tâche de fond (QgsTask) et résultat rangé dans le calage.

La photo est synthétique (voir tests/test_autotilt.py) : des pans de mur tout autour d'une
caméra inclinée de façon connue, encodée en JPEG."""

import math
import time
import unittest
from unittest import mock

from qgis_support import APP, module

from qgis.PyQt.QtCore import QBuffer, QByteArray, QIODevice
from qgis.PyQt.QtGui import QImage

autotilt = module("autotilt")
calibration = module("calibration")
api = module("api")


def seen(up, yaw, elev):
    """Direction vue par une caméra de verticale vraie `up` (comme tests/test_calibration.py)."""
    v = calibration.vector(yaw, elev)
    k = (-up[1], up[0], 0.0)
    s = math.hypot(k[0], k[1])
    if s < 1e-12:
        return yaw % 360, elev
    k, c = (k[0] / s, k[1] / s, 0.0), up[2]
    kxv, kv = calibration._cross(k, v), calibration._dot(k, v)
    return calibration.angles(tuple(v[i] * c + kxv[i] * s + k[i] * kv * (1 - c) for i in range(3)))

W, H = 4096, 2048
FORWARD, RIGHT, HEADING = 1.8, -0.9, 40.0


def photo():
    """JPEG d'une photo 360° prise au cap HEADING par une caméra penchée de FORWARD° vers
    l'avant et RIGHT° vers la droite."""
    import numpy as np

    up = calibration.up_from_components(FORWARD, RIGHT, 0.0)  # repère de l'image : cap 0 au centre
    rows = np.arange(H)[:, None]
    xs = np.arange(W)[None, :]
    img = np.full((H, W), 80.0, np.float32)

    def edge(a):
        pts = [seen(up, a, e) for e in np.linspace(-60.0, 80.0, 400)]
        px = np.array([((yaw + 180) % 360) / 360 * W - 0.5 for yaw, _ in pts])
        py = np.array([(90 - elev) / 180 * H - 0.5 for _, elev in pts])
        order = np.argsort(py)
        return np.interp(rows[:, 0], py[order], px[order])[:, None]

    for a in range(3, 360, 8):
        img += 90.0 * np.clip(xs - edge(a) + 0.5, 0, 1) * np.clip(edge(a + 3.0) + 0.5 - xs, 0, 1)
    gray = np.ascontiguousarray(np.clip(img, 0, 255).astype(np.uint8))
    qimg = QImage(gray.data, W, H, W, QImage.Format.Format_Grayscale8).copy()
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    qimg.save(buf, "JPG", 92)
    return bytes(data)


ITEM = {"id": "pic", "collection": "seq", "geometry": {"coordinates": [4.5546, 47.2623]},
        "assets": {"hd": {"href": "https://example.org/hd.jpg"}},
        "properties": {"view:azimuth": HEADING, "pers:interior_orientation": {"field_of_view": 360}}}


class Background(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.jpeg = photo()

    def test_read_band(self):
        band, row0, height, width = autotilt.read_band(self.jpeg, width=2048)
        self.assertEqual((width, height), (2048, 1024))
        self.assertEqual(row0, int((90 - autotilt.ELEV_MAX) / 180 * height))
        self.assertEqual(band.shape, (int((90 - autotilt.ELEV_MIN) / 180 * height) - row0, 2048))
        self.assertIsNone(autotilt.read_band(b"pas une image"))

    def test_estimate_in_task(self):
        cal = calibration.Calibration()
        checker = autotilt.TiltChecker()
        done = []
        lidar = {"elevations": [{"z": 300 + a * 0.1 / 3} for a, _ in calibration.LIDAR_GRID]}
        with mock.patch.object(api, "fetch_json", lambda url, cb, **kw: cb(lidar, None)), \
                mock.patch.object(api, "fetch", lambda url, cb, **kw: cb(self.jpeg, None)):
            checker.check(ITEM, cal, lambda: done.append(1))
            limit = time.time() + 60
            while not done and time.time() < limit:
                APP.processEvents()
                time.sleep(0.01)
        self.assertEqual(done, [1])
        tilt = cal.tilt("pic")
        self.assertEqual(tilt["source"], "verticales")
        f, r = calibration.tilt_components(tilt["up"], HEADING)
        self.assertAlmostEqual(f, FORWARD, delta=0.05)
        self.assertAlmostEqual(r, RIGHT, delta=0.05)
        self.assertAlmostEqual(cal.auto["pic"]["lidar"][0], math.degrees(math.atan(0.1 / 3.0)), places=9)


if __name__ == "__main__":
    unittest.main()
