# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Cédric COCHART
# SPDX-License-Identifier: GPL-2.0-or-later
"""Tests du panneau (viewer_dock.py et ses modules) avec le vrai QGIS, hors écran.

La visionneuse web (QtWebEngine) ne fonctionne pas hors écran : le panneau est testé avec
la visionneuse de secours (native_viewer.py). Les services sont simulés : liste des
instances, fiches et images des photos, vérification de la précision GPS.
"""

import json
import unittest
from unittest import mock

from qgis_support import APP, module

from qgis.PyQt.QtGui import QColor, QImage
from qgis.PyQt.QtCore import QBuffer, QByteArray, QIODevice
from qgis.PyQt.QtWidgets import QAbstractButton

viewer_dock = module("viewer_dock")
api = module("api")
calibration = module("calibration")
precision = module("precision")
filters = module("filters")


def png():
    img = QImage(400, 200, QImage.Format.Format_RGB32)
    img.fill(QColor(90, 120, 150))
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return bytes(data)


IMAGE = png()


def item(pid, sequence="seq", prev=None, nxt=None, producer=("prod-1", "CD21")):
    links = [{"rel": rel, "href": "https://x/api/collections/{}/items/{}".format(sequence, p)}
             for rel, p in (("prev", prev), ("next", nxt)) if p]
    return {"id": pid, "collection": sequence, "geometry": {"type": "Point", "coordinates": [5.0415, 47.322]},
            "properties": {"datetime": "2025-05-28T10:00:00Z", "view:azimuth": 90, "quality:horizontal_accuracy": 4.0,
                           "pers:interior_orientation": {"field_of_view": 360}},
            "providers": [{"name": producer[1], "id": producer[0], "roles": ["producer"]}],
            "links": links, "assets": {"sd": {"href": "https://x/{}.jpg".format(pid)}}}


ITEMS = {"p1": item("p1", nxt="p2"), "p2": item("p2", prev="p1"), "q1": item("q1", sequence="rtk")}
QUALITY = {"seq": precision.result(False, lidar=True, reason="pas de correction différentielle déclarée"),
           "rtk": precision.result(True, 0.1, True, "position vérifiée", 20, 0.02, 1.874)}


class FakeChecker:
    """Vérification de la précision : résultat fixé par séquence, immédiat ou différé."""

    def __init__(self):
        self.later = None  # liste : rappels différés (vérification en cours)

    def known(self, it):
        return None

    def check(self, it, callback):
        res = QUALITY[it["collection"]]
        if self.later is not None:
            self.later.append(lambda: callback(res))
        else:
            callback(res)


class FakeTilts:
    """Inclinaison automatique : rien à calculer (voir test_autotilt.py)."""

    def pending(self, pic):
        return False

    def check(self, item, cal, done=None):
        if done:
            done()


class Base(unittest.TestCase):
    def setUp(self):
        self.requests = []
        for target, attr, value in (
                (viewer_dock, "HAS_WEBENGINE", False),
                (api, "get_instances", lambda done, force=False: done(None, "hors ligne")),
                (api, "get_item", self._get_item),
                (api, "requested_id", lambda: self.requests[-1] if self.requests else None),
                (api, "fetch", lambda url, cb, prefer_cache=False, timeout_ms=None: cb(IMAGE, None))):
            patcher = mock.patch.object(target, attr, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.dock = viewer_dock.PanoramaxDock()
        self.dock.calibration = calibration.Calibration()
        self.dock.precision = FakeChecker()
        self.dock.tilts = FakeTilts()
        self.popups = []
        self.dock._show_degraded_popup = lambda reasons: self.popups.append(reasons) or False
        self.signals = []
        for name in ("pictureChanged", "measureToggled", "measureModeChanged", "photoClicked", "aimRequested",
                     "measureSaveRequested", "measureClearRequested", "measureClearAllRequested",
                     "cameraHeightChanged", "surfaceChanged", "planeHeightChanged", "terrainChanged",
                     "filterChanged", "message", "tiltChanged", "wallChanged", "wallFacadeRequested"):
            getattr(self.dock, name).connect(lambda *args, n=name: self.signals.append((n, args)))
        self.dock.resize(1092, 990)
        self.dock.show()
        APP.processEvents()
        self.addCleanup(self.dock.deleteLater)

    def _get_item(self, pid, callback):
        self.requests.append(pid)
        callback(ITEMS.get(pid), None if pid in ITEMS else "absente")

    def emitted(self, name):
        return [args for n, args in self.signals if n == name]

    def button(self, text):
        return next(b for b in self.dock.widget().findChildren(QAbstractButton) if b.text() == text)

    def visible(self, widget):
        APP.processEvents()
        return widget.isVisibleTo(self.dock)


class MeasureModes(Base):
    def test_widgets_follow_the_mode(self):
        mp = self.dock.measure_box
        self.dock.btn_measure.setChecked(True)
        self.assertTrue(self.emitted("measureToggled"))
        expected = {  # mode : (Viser, Caméra à, ligne Terrain, référence, ligne Surface)
            "road": (False, True, True, False, False), "width": (False, True, True, False, False),
            "height": (False, True, True, False, False), "tri": (True, False, False, False, False),
            "free": (False, True, True, False, True), "tilt": (False, False, False, False, False),
            "heading": (False, False, False, False, False), "camera": (False, False, True, True, False)}
        for i in range(mp.cmb_measure.count()):
            mp.cmb_measure.setCurrentIndex(i)
            mode = self.dock.measure_mode()
            shown = tuple(self.visible(w) for w in (mp.btn_aim, mp.spin_camera, mp.terrain_row, mp.spin_reference,
                                                    mp.free_row))
            self.assertEqual(shown, expected[mode], mode)
        self.assertEqual([a[0] for a in self.emitted("measureModeChanged")],
                         ["width", "height", "tri", "free", "tilt", "heading", "camera"])

    def test_free_surfaces(self):
        mp = self.dock.measure_box
        self.dock.btn_measure.setChecked(True)
        mp.cmb_measure.setCurrentIndex(mp.cmb_measure.findData("free"))
        for i in range(mp.cmb_surface.count()):
            mp.cmb_surface.setCurrentIndex(i)
            surface = self.dock.surface()
            self.assertEqual(self.visible(mp.btn_facade), surface == "facade")
            self.assertEqual(self.visible(mp.spin_plane), surface == "horizontal")
        self.assertEqual(len(self.emitted("surfaceChanged")), mp.cmb_surface.count() - 1)

    def test_settings_and_buttons(self):
        mp = self.dock.measure_box
        self.dock.spin_camera.setValue(2.2)
        self.dock.spin_plane.setValue(1.5)
        mp.chk_bridge.setChecked(True)
        self.assertEqual(self.emitted("cameraHeightChanged"), [(2.2,)])
        self.assertEqual(self.emitted("planeHeightChanged"), [(1.5,)])
        self.assertTrue(self.dock.on_bridge())
        self.assertEqual(len(self.emitted("terrainChanged")), 1)
        for text, signal in (("Effacer", "measureClearRequested"), ("Tout effacer", "measureClearAllRequested"),
                             ("Enregistrer les mesures", "measureSaveRequested")):
            self.button(text).click()
            self.assertEqual(len(self.emitted(signal)), 1, text)


class CadastreWall(Base):
    def test_controls(self):
        box = self.dock.measure_box
        self.dock.btn_measure.setChecked(True)
        for mode, surface, chk, btn in (("road", None, False, False), ("height", None, True, False),
                                        ("free", "facade", False, True), ("free", "ground", False, False)):
            box.cmb_measure.setCurrentIndex(box.cmb_measure.findData(mode))
            if surface:
                box.cmb_surface.setCurrentIndex(box.cmb_surface.findData(surface))
            self.assertEqual((self.visible(box.chk_wall), self.visible(box.btn_wall_facade)), (chk, btn), mode)
        box.chk_wall.setChecked(True)
        self.assertEqual(self.emitted("wallChanged"), [(True,)])
        box.btn_wall_facade.click()
        self.assertEqual(len(self.emitted("wallFacadeRequested")), 1)


class Compact(Base):
    def test_compact_mode(self):
        dock = self.dock
        dock.btn_measure.setChecked(True)
        dock.set_measure_status("Cliquez au pied du premier bord.")
        dock.btn_compact.setChecked(True)
        self.assertEqual([b.text() for b in dock._labels], ["📍", "🔎", "📷", "🖼️", "📐", "🌐"])
        self.assertFalse(self.visible(dock.instance_row))
        self.assertFalse(self.visible(dock.options_row))
        self.assertFalse(self.visible(dock.measure_box.measure_status))
        self.assertEqual(dock.btn_measure.toolTip(), "Cliquez au pied du premier bord.")  # aide dans la bulle
        heights = {b.height() for b in dock._labels}
        self.assertLessEqual(max(heights) - min(heights), 2)  # boutons alignés (🖼 compris)
        dock.btn_compact.setChecked(False)
        self.assertEqual(dock.btn_pick.text(), "Choisir sur la carte")
        self.assertTrue(self.visible(dock.instance_row))
        self.assertTrue(self.visible(dock.measure_box.measure_status))


class Filter(Base):
    def test_set_and_reset(self):
        row = self.dock.filter_row
        self.dock.btn_filter.setChecked(True)
        self.assertTrue(self.visible(row))
        self.dock.show_picture("p1", 5.0415, 47.322)  # propose le producteur de la photo affichée
        row.chk_since.setChecked(True)
        row.cmb_kind.setCurrentIndex(row.cmb_kind.findData("360"))
        row.cmb_producer.setCurrentIndex(row.cmb_producer.findData("prod-1"))
        row.cmb_producer.activated.emit(row.cmb_producer.currentIndex())
        flt = self.dock.photo_filter()
        self.assertEqual((flt.kind, flt.producer), ("360", ("prod-1", "CD21")))
        self.assertTrue(self.dock.btn_filter.text().startswith("Filtre ●"))
        self.assertEqual(self.emitted("filterChanged")[-1][0], flt)
        self.button("Aucun filtre").click()
        self.assertEqual(self.dock.photo_filter(), filters.PhotoFilter())
        self.assertEqual(self.dock.btn_filter.text(), "Filtre")


class NativeViewer(Base):
    def test_picture_and_navigation(self):
        dock = self.dock
        dock.show_picture("p1", 5.0415, 47.322)
        self.assertIn("Photo p1", dock.native.meta.text())
        self.assertEqual(self.emitted("pictureChanged")[-1][0], "p1")
        self.assertTrue(dock.native.btn_next.isEnabled())
        self.assertFalse(dock.native.btn_prev.isEnabled())
        dock.native.btn_next.click()
        self.assertEqual(self.requests[-1], "p2")
        self.assertIn("Photo p2", dock.native.meta.text())
        dock.show_location(5.0, 47.0)
        self.assertEqual(dock.native.meta.text(), "")

    def test_click_and_aim(self):
        dock = self.dock
        dock.show_picture("p1", 5.0415, 47.322)
        dock.btn_measure.setChecked(True)
        mp = dock.measure_box
        mp.cmb_measure.setCurrentIndex(mp.cmb_measure.findData("width"))
        dock._on_native_click(95.0, -10.0)
        click = self.emitted("photoClicked")[-1][0]
        self.assertEqual((click["pic"], click["sequence"], click["raw"]), ("p1", "seq", [95.0, -10.0]))
        self.assertEqual(click["accuracy"], 4.0)
        self.assertIsNone(click["camera_alt"])  # position non vérifiée : pas d'altitude de caméra
        mp.cmb_measure.setCurrentIndex(mp.cmb_measure.findData("tri"))
        dock.img.current_view = lambda: (91.0, 70.0)
        self.button("🎯 Viser").click()
        sighting = self.emitted("aimRequested")[-1][0]
        self.assertEqual((sighting["pic"], sighting["heading"]), ("p1", 91.0))

    def test_screen_click_on_precise_heading(self):
        # Clic calculé depuis l'écran (sans position fournie par la visionneuse) : la visionneuse
        # s'oriente sur le cap arrondi, la direction est ramenée au cap précis (EXIF)
        dock = self.dock
        dock.btn_measure.setChecked(True)
        dock.show_picture("p1", 5.0415, 47.322)
        props = dock._current_item.setdefault("properties", {})
        props["view:azimuth"] = 90
        props["exif"] = {"Exif.GPSInfo.GPSImgDirection": "9040/100"}
        dock._on_viewer_clicks(json.dumps([{"x": 120.0, "y": -10.0, "vfov": 60, "dx": 0, "dy": 0, "h": 600, "w": 800}]))
        raw = self.emitted("photoClicked")[-1][0]["raw"]
        self.assertAlmostEqual(raw[0], 120.4, places=6)
        self.assertAlmostEqual(raw[1], -10.0, places=6)

    def test_calibration_line(self):
        dock = self.dock
        dock.show_picture("p1", 5.0415, 47.322)
        dock.btn_measure.setChecked(True)
        dock.calibration.heights["seq"] = [(2.1, 0.05)]
        dock.update_calibration_label()
        self.assertEqual(dock.measure_box.lbl_calibration.text(), "Calage : caméra à 2,10 m (±0,05 m, 1 référence), dont 1 d'une "
                         "version précédente, sans l'inclinaison automatique : refaites le calage")


class StableImage(Base):
    """La visionneuse ne doit pas changer de taille pendant une mesure (l'image sauterait sous le
    curseur) : ni au résultat, plus long que la consigne, ni d'une photo à l'autre."""

    def photo_top(self):
        APP.processEvents()
        return self.dock.img.mapTo(self.dock, self.dock.img.rect().topLeft()).y()

    def test_status_and_calibration_lines(self):
        dock = self.dock
        dock.show_picture("p1", 5.0415, 47.322)
        dock.btn_measure.setChecked(True)
        dock.set_measure_status("Cliquez au pied du premier bord (bordure, marquage, limite de chaussée…).")
        top = self.photo_top()
        dock.set_measure_status("Largeur perpendiculaire à la chaussée : 6,52 m (±0,12 m) · en biais 6,60 m · axe 15° "
                                "(séquence). Terrain : LiDAR HD (IGN), pente de la route, caméra à 1,87 m "
                                "(altitude GPS). Point mal placé : glissez-le sur la carte.")
        self.assertEqual(self.photo_top(), top)
        # Résultat plus long que les trois lignes réservées : la ligne d'aide s'agrandit une fois, puis ne
        # rapetisse plus, à la consigne de la mesure suivante
        dock.set_measure_status(
            "Distance 3D : 12,52 m (±0,35 m) · horizontale 12,40 · verticale 1,71 · le long de la route 11,90 · "
            "en travers 3,84 · surface : plan horizontal · plan à 0,87 m sous la caméra, incidence 12°. Visée "
            "rasante sur le plan (12°) : mesure imprécise, prenez une photo plus en face. Terrain : LiDAR HD (IGN), "
            "pente de la route + RGE ALTI (IGN), pente de la route, caméra à 1,87 m (altitude GPS). Altitudes de "
            "sources différentes : la composante verticale peut être faussée. Point mal placé : glissez-le sur la "
            "carte. (Altitude GPS de la photo : vérification en cours, la hauteur de caméra peut changer.) " * 2)
        top = self.photo_top()
        dock.set_measure_status("Cliquez au pied du premier bord (bordure, marquage, limite de chaussée…).")
        self.assertEqual(self.photo_top(), top)
        dock.calibration.heights["seq"] = [(2.1, 0.05)]
        dock.update_calibration_label()  # le rappel du calage apparaît
        top = self.photo_top()
        dock.calibration.heights.clear()
        dock.update_calibration_label()  # et disparaît sur une photo sans calage
        self.assertEqual(self.photo_top(), top)


class DegradedMode(Base):
    def test_popup_when_measuring(self):
        dock = self.dock
        dock.show_picture("p1", 5.0415, 47.322)
        self.assertEqual(self.popups, [])  # pas de fenêtre hors mesure
        dock.btn_measure.setChecked(True)
        self.assertEqual(self.popups, [["position GPS non vérifiée au décimètre (pas de correction différentielle "
                                        "déclarée)"]])
        self.assertTrue(dock.measure_box.lbl_quality.text().startswith("⚠ Position GPS non vérifiée"))
        dock.show_picture("p2", 5.0415, 47.322)
        self.assertEqual(len(self.popups), 1)  # changement de photo pendant la mesure : pas de nouvelle fenêtre
        dock.btn_measure.setChecked(False)
        dock.btn_measure.setChecked(True)
        self.assertEqual(len(self.popups), 2)  # à chaque activation

    def test_do_not_show_again(self):
        dock = self.dock
        dock._show_degraded_popup = lambda reasons: self.popups.append(reasons) or True  # case cochée
        dock.show_picture("p1", 5.0415, 47.322)
        dock.btn_measure.setChecked(True)
        dock.btn_measure.setChecked(False)
        dock.btn_measure.setChecked(True)
        self.assertEqual(len(self.popups), 1)

    def test_full_mode_and_pending_check(self):
        dock = self.dock
        dock.show_picture("q1", 5.0415, 47.322)
        dock.btn_measure.setChecked(True)
        self.assertEqual(self.popups, [])
        self.assertEqual(dock.measure_box.lbl_quality.text(), "⚠ Position GPS vérifiée (±0,10 m) · LiDAR HD · "
                                                              "inclinaison non calée : calez-la sur deux objets verticaux")
        dock._on_native_click(95.0, -10.0)
        self.assertEqual(self.emitted("photoClicked")[-1][0]["accuracy"], 0.1)
        dock.btn_measure.setChecked(False)
        dock.precision.later = []  # vérification encore en cours au clic sur Mesure
        dock.show_picture("p1", 5.0415, 47.322)
        dock.btn_measure.setChecked(True)
        self.assertEqual(self.popups, [])
        dock.precision.later.pop()()  # résultat arrivé
        self.assertEqual(len(self.popups), 1)

    def test_camera_height_suggested(self):
        dock = self.dock
        box = dock.measure_box
        btn = box.btn_camera_auto
        box.spin_camera.setValue(1.9)
        dock.btn_measure.setChecked(True)
        dock.show_picture("p1", 5.0415, 47.322)
        self.assertFalse(self.visible(btn))  # position non vérifiée : rien à proposer
        row = box.terrain_row.height()
        dock.show_picture("q1", 5.0415, 47.322)
        self.assertTrue(self.visible(btn))
        self.assertEqual(box.terrain_row.height(), row)  # le bouton ne fait pas sauter l'image
        self.assertEqual(btn.text(), "Caméra à 1,87 m d'après l'altitude GPS : utiliser")
        self.assertIn("médiane de 20 photos", btn.toolTip())
        box.cmb_measure.setCurrentIndex(box.cmb_measure.findData("tri"))
        self.assertFalse(self.visible(btn))  # triangulation : pas de hauteur de caméra
        box.cmb_measure.setCurrentIndex(box.cmb_measure.findData("road"))
        self.assertTrue(self.visible(btn))
        btn.click()
        self.assertAlmostEqual(box.spin_camera.value(), 1.87)
        self.assertAlmostEqual(self.emitted("cameraHeightChanged")[-1][0], 1.87)
        self.assertFalse(self.visible(btn))  # champ rempli : plus rien à proposer
        dock.show_picture("p1", 5.0415, 47.322)
        self.assertAlmostEqual(box.spin_camera.value(), 1.87)  # le champ n'est jamais changé sans clic
        self.assertFalse(self.visible(btn))


class DeferredTilts(FakeTilts):
    """Inclinaison automatique en cours de calcul jusqu'à l'appel de self.done."""

    def __init__(self):
        self.done, self.checked = None, []

    def pending(self, pic):
        return self.done is not None

    def check(self, item, cal, done=None):
        self.checked.append(item["id"])
        self.done = done


class AutomaticTilt(Base):
    def test_state_line(self):
        dock = self.dock
        tilts = dock.tilts = DeferredTilts()
        dock.show_picture("q1", 5.0415, 47.322)
        self.assertEqual(tilts.checked, [])  # mesure inactive : pas de téléchargement
        dock.btn_measure.setChecked(True)
        self.assertEqual(tilts.checked, ["q1"])
        self.assertEqual(dock.measure_box.lbl_quality.text(),
                         "Position GPS vérifiée (±0,10 m) · LiDAR HD · inclinaison en cours de calcul…")
        up = calibration.up_from_components(1.2, 0.5, 0.0)
        dock.calibration.set_auto("q1", {"sequence": "rtk", "heading": 0.0, "lidar": None,
                                         "verticals": {"up": list(up), "count": 90, "sigma": 0.12}})
        done, tilts.done = tilts.done, None
        done()
        self.assertEqual(self.emitted("tiltChanged"), [("q1",)])
        self.assertEqual(dock.measure_box.lbl_quality.text(), "Position GPS vérifiée (±0,10 m) · LiDAR HD · "
                                                              "inclinaison calée (verticales de la photo, ±0,1°)")
        self.assertIn("corrigée automatiquement", dock.measure_box.lbl_calibration.text())
        # Clic suivant : direction corrigée de l'inclinaison
        dock._on_native_click(95.0, -10.0)
        click = self.emitted("photoClicked")[-1][0]
        self.assertEqual(click["raw"], [95.0, -10.0])
        self.assertNotAlmostEqual(click["elev"], -10.0, places=2)
        # Photo suivante pendant la mesure : analysée à son tour
        dock.show_picture("p1", 5.0415, 47.322)
        self.assertEqual(tilts.checked, ["q1", "p1"])


if __name__ == "__main__":
    unittest.main()
