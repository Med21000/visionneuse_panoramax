# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Cédric COCHART
# SPDX-License-Identifier: GPL-2.0-or-later
"""Tests de webengine.py : options du moteur web selon l'option « Accélération matérielle »,
rendu logiciel demandé au pilote, diagnostic."""

import os
import unittest
from unittest import mock

from support import install_qgis_stub, module

install_qgis_stub()
webengine = module("webengine")

AMD = "ANGLE (AMD, AMD Radeon 680M (radeonsi rembrandt ACO), OpenGL ES 3.2)"
LLVMPIPE = "ANGLE (Mesa, llvmpipe (LLVM 23.1.0 256 bits), OpenGL ES 3.2)"
SWIFTSHADER = "ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device (Subzero) (0x0000C0DE)), SwiftShader driver)"


class FakeSettings:
    hardware = False

    def value(self, key, default=None, type=None):
        return self.hardware if key == webengine.HARDWARE_SETTING else default


class Base(unittest.TestCase):
    def configure(self, hardware=False, env=None):
        FakeSettings.hardware = hardware
        if env is not None:
            os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = env
        with mock.patch.object(webengine, "QgsSettings", FakeSettings):
            mode = webengine.configure()
        return mode, os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "").split()

    def setUp(self):
        patcher = mock.patch.dict(os.environ, {}, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(webengine.end_software)


class Configure(Base):
    def test_software_by_default(self):
        mode, flags = self.configure()
        self.assertEqual(mode, "software")
        self.assertEqual(flags, list(webengine.SOFTWARE_FLAGS))

    def test_hardware_option(self):
        mode, flags = self.configure(hardware=True)
        self.assertEqual(mode, "hardware")
        self.assertIn("--use-angle=default", flags)
        self.assertNotIn("--use-angle=swiftshader", flags)
        self.assertIn("--disable-gpu-compositing", flags)  # sinon le moteur web plante sous QGIS

    def test_other_flags_kept(self):
        mode, flags = self.configure(hardware=True, env="--lang=fr --ignore-gpu-blocklist")
        self.assertEqual(flags[:2], ["--lang=fr", "--ignore-gpu-blocklist"])
        self.assertEqual(flags.count("--ignore-gpu-blocklist"), 1)

    def test_user_choice_respected(self):
        for env in ("--use-gl=egl", "--disable-gpu"):
            mode, flags = self.configure(hardware=True, env=env)
            self.assertEqual((mode, flags), ("user", [env]))


class SoftwareEnvironment(Base):
    def test_set_then_restored(self):
        self.configure()
        webengine.begin_software()
        self.assertEqual(os.environ.get("LIBGL_ALWAYS_SOFTWARE"), "1")
        webengine.begin_software()  # sans effet : déjà posé
        webengine.end_software()
        self.assertNotIn("LIBGL_ALWAYS_SOFTWARE", os.environ)
        webengine.end_software()  # sans effet

    def test_previous_value_restored(self):
        self.configure()
        os.environ["LIBGL_ALWAYS_SOFTWARE"] = "0"
        webengine.begin_software()
        self.assertEqual(os.environ["LIBGL_ALWAYS_SOFTWARE"], "1")
        webengine.end_software()
        self.assertEqual(os.environ["LIBGL_ALWAYS_SOFTWARE"], "0")

    def test_not_set_otherwise(self):
        for hardware, env in ((True, None), (False, "--use-gl=egl")):
            self.configure(hardware=hardware, env=env)
            webengine.begin_software()
            self.assertNotIn("LIBGL_ALWAYS_SOFTWARE", os.environ)


class Describe(unittest.TestCase):
    def describe(self, mode, renderer):
        with mock.patch.object(webengine, "MODE", mode):
            return webengine.describe(renderer)

    def test_renderers(self):
        self.assertFalse(webengine.is_software(AMD))
        self.assertTrue(webengine.is_software(SWIFTSHADER))
        self.assertTrue(webengine.is_software(LLVMPIPE))
        self.assertEqual(self.describe("software", ""), "WebGL : moteur inconnu.")

    def test_expected_rendering(self):
        self.assertEqual(self.describe("hardware", AMD), "WebGL : {} (accélération matérielle, par la carte "
                                                         "graphique, option « Accélération matérielle » "
                                                         "cochée).".format(AMD))
        self.assertEqual(self.describe("software", LLVMPIPE), "WebGL : {} (rendu logiciel, par le processeur, "
                                                              "option « Accélération matérielle » "
                                                              "décochée).".format(LLVMPIPE))

    def test_unexpected_rendering(self):
        self.assertTrue(self.describe("software", AMD).endswith("Rendu logiciel indisponible avec ce moteur web et "
                                                                "ce pilote graphique."))
        self.assertIn("Accélération matérielle indisponible", self.describe("hardware", SWIFTSHADER))
        self.assertNotIn("indisponible", self.describe("user", AMD))


if __name__ == "__main__":
    unittest.main()
