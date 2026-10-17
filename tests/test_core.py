# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Cédric COCHART
# SPDX-License-Identifier: GPL-2.0-or-later
"""Le cœur de calcul (core/) se passe de QGIS : importé dans un processus où qgis est
interdit, il fait une mesure complète sans dessin ni réseau (application web, Pyodide)."""

import math
import os
import subprocess
import sys
import unittest

from support import module, point

CORE = ("host", "metadata", "triangulation", "geometry", "ground", "calibration", "terrain", "precision",
        "roads", "buildings", "autotilt", "measures", "tools", "filters", "capture")


class WithoutQgis(unittest.TestCase):
    def test_no_qgis_import(self):
        here = os.path.dirname(os.path.abspath(__file__))
        code = ("import sys; sys.modules['qgis'] = None\n"  # tout import de qgis échoue
                "sys.path.insert(0, {!r})\n"
                "import support\n"
                "for name in {!r}:\n"
                "    support.module('core.' + name)\n"
                "print('ok')\n").format(here, CORE)
        run = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, text=True)
        self.assertEqual(run.stdout.strip(), "ok", run.stderr)

    def test_measure_without_host(self):
        measures = module("core.measures")
        tool = measures.MeasureTool(None)  # pas de carte : rien n'est dessiné
        lon, lat = point(0.0, 0.0)
        clicks = []
        for x in (-3.5, 3.5):  # chaussée de 7 m, à 10 m devant la caméra (1,90 m)
            yaw = math.degrees(math.atan2(x, 10.0)) % 360
            elev = math.degrees(math.atan2(-1.9, math.hypot(x, 10.0)))
            clicks.append({"lon": lon, "lat": lat, "pic": "p1", "yaw": yaw, "elev": elev, "axis": 0.0,
                           "sequence": "s1"})
        tool.set_mode("road")
        for c in clicks:
            tool.add_click(c)
        self.assertAlmostEqual(tool.current.result["value"], 7.0, places=6)
        self.assertIn("Largeur perpendiculaire à la chaussée : 7,00 m", tool.status())


if __name__ == "__main__":
    unittest.main()
