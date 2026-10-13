# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Cédric COCHART
# SPDX-License-Identifier: GPL-2.0-or-later
"""Tests de capture.py : vue redressée de l'inclinaison de la photo."""

import importlib.util
import unittest

from support import install_qgis_stub, module
from test_calibration import seen

install_qgis_stub()
calibration = module("calibration")
HAS_NUMPY = importlib.util.find_spec("numpy") is not None


@unittest.skipUnless(HAS_NUMPY, "numpy absent")
class Tilted(unittest.TestCase):
    def test_same_as_camera_view(self):
        """Une direction vraie est cherchée dans la photo là où la caméra penchée la voit."""
        capture = module("capture")
        up = calibration.up_from_components(2.0, -1.5, 30.0)
        for yaw, elev in ((0.0, 0.0), (75.0, -12.0), (200.0, 25.0)):
            v = calibration.vector(yaw, elev)  # est, nord, haut
            x, y, z = capture._tilted_rays(v[0], v[2], v[1], up)
            got = calibration.angles((x, z, y))
            want = seen(up, yaw, elev)
            self.assertAlmostEqual((got[0] - want[0] + 540) % 360 - 180, 0.0, places=9)
            self.assertAlmostEqual(got[1], want[1], places=9)
        self.assertEqual(capture._tilted_rays(1.0, 2.0, 3.0, (0.0, 0.0, 1.0)), (1.0, 2.0, 3.0))


if __name__ == "__main__":
    unittest.main()
