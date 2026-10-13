# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Cédric COCHART
# SPDX-License-Identifier: GPL-2.0-or-later
"""Tests de autotilt.py et de l'inclinaison automatique du calage (calibration.py).

Les photos sont synthétiques : des arêtes verticales du monde tout autour d'une caméra
inclinée de façon connue, dessinées dans la bande analysée d'une image équirectangulaire.
Le calcul des verticales demande numpy (fourni avec QGIS) : ces tests sont sautés sans lui.
"""

import importlib.util
import math
import unittest
from unittest import mock

from support import LAT0, LON0, install_qgis_stub, module
from test_calibration import seen

install_qgis_stub()
autotilt = module("autotilt")
calibration = module("calibration")
HAS_NUMPY = importlib.util.find_spec("numpy") is not None
W, H = 2048, 1024  # image entière (degré : 0,176 px⁻¹)


def band_image(up, azimuths, width=W, height=H):
    """Bande ELEV_MIN..ELEV_MAX d'une photo prise par une caméra de verticale vraie `up`
    (repère de la photo, cap 0 au centre), avec à chaque azimut un pan de mur clair de 3° de
    large (ses deux bords sont des verticales du monde). Retourne (bande, première ligne)."""
    import numpy as np

    row0 = int((90 - autotilt.ELEV_MAX) / 180 * height)
    row1 = int((90 - autotilt.ELEV_MIN) / 180 * height)
    rows = np.arange(row0, row1)
    img = np.full((len(rows), width), 90.0, np.float32)
    xs = np.arange(width)[None, :]

    def edge(a):
        """Colonne (au sous-pixel) de la verticale du monde d'azimut a, sur chaque ligne."""
        pts = [seen(up, a, e) for e in np.linspace(-5.0, 60.0, 300)]
        px = np.array([((yaw + 180) % 360) / 360 * width - 0.5 for yaw, _ in pts])
        py = np.array([(90 - elev) / 180 * height - 0.5 for _, elev in pts])
        order = np.argsort(py)
        return np.interp(rows, py[order], px[order])[:, None]

    for a in azimuths:
        left, right = edge(a), edge(a + 3.0)
        img += 70.0 * np.clip(xs - left + 0.5, 0, 1) * np.clip(right + 0.5 - xs, 0, 1)
    return img, row0


def components(up):
    return calibration.tilt_components(up, 0.0)


@unittest.skipUnless(HAS_NUMPY, "numpy absent")
class Verticals(unittest.TestCase):
    def estimate(self, up, azimuths):
        band, row0 = band_image(up, azimuths)
        return autotilt.fit(autotilt.segments(band, row0, H, W, 0.0))

    def test_tilt_recovered(self):
        for forward, right in ((2.0, -1.0), (-0.5, 1.5), (0.0, 0.0)):
            up = calibration.up_from_components(forward, right, 0.0)
            res = self.estimate(up, range(5, 360, 9))
            self.assertIsNotNone(res)
            f, r = components(res["up"])
            self.assertAlmostEqual(f, forward, delta=0.05)
            self.assertAlmostEqual(r, right, delta=0.05)
            self.assertTrue(res["auto"])
            self.assertLess(res["sigma"], autotilt.MAX_SIGMA)

    def test_correction_levels_the_photo(self):
        # Une arête vue par la caméra inclinée redevient verticale une fois corrigée
        up = calibration.up_from_components(1.5, 1.0, 0.0)
        res = self.estimate(up, range(5, 360, 9))
        bottom, top = seen(up, 120.0, 2.0), seen(up, 120.0, 30.0)
        yaw_b, _ = calibration.untilt(res["up"], *bottom)
        yaw_t, _ = calibration.untilt(res["up"], *top)
        self.assertAlmostEqual((yaw_t - yaw_b + 540) % 360 - 180, 0.0, delta=0.05)

    def test_heading_added(self):
        # Directions brutes : cap de la photo ajouté, comme les clics
        up = calibration.up_from_components(2.0, 0.0, 0.0)
        band, row0 = band_image(up, range(5, 360, 9))
        res = autotilt.fit(autotilt.segments(band, row0, H, W, 90.0))
        f, r = calibration.tilt_components(res["up"], 90.0)
        self.assertAlmostEqual(f, 2.0, delta=0.05)
        self.assertAlmostEqual(r, 0.0, delta=0.05)

    def test_refused(self):
        up = calibration.up_from_components(1.0, -0.5, 0.0)
        self.assertIsNone(autotilt.fit([]))
        self.assertIsNone(autotilt.fit([((10.0, 5.0), (10.0, 20.0))] * 30))  # trop peu de segments
        # Un seul pan de mur, ou des arêtes d'un seul côté : une composante est mal contrainte.
        # Le calcul peut aboutir, mais l'incertitude annoncée doit alors couvrir l'erreur.
        for azimuths in ([100.0], [a * 0.5 for a in range(0, 160, 4)]):
            res = self.estimate(up, azimuths)
            if res is not None:
                f, r = components(res["up"])
                self.assertLess(math.hypot(f - 1.0, r + 0.5), 3 * res["sigma"] + 0.02)


class LidarModel(unittest.TestCase):
    def test_components(self):
        for heading in (0.0, 75.0, 250.0):
            up = calibration.up_from_components(1.2, -0.7, heading)
            f, r = calibration.tilt_components(up, heading)
            self.assertAlmostEqual(f, 1.2, places=6)
            self.assertAlmostEqual(r, -0.7, places=6)
        # En montée vers le nord, la verticale vraie penche vers l'avant (nord)
        up = calibration.up_from_components(2.0, 0.0, 0.0)
        self.assertGreater(up[1], 0)
        self.assertAlmostEqual(up[0], 0.0, places=9)

    def test_slopes(self):
        # Sol en montée de 3 % vers l'avant, plus haut de 2 % à droite, avec un caniveau
        z = [100 + 0.03 * a + 0.02 * c + (-0.05 if (a, c) == (0, -calibration.LIDAR_ACROSS) else 0)
             for a, c in calibration.LIDAR_GRID]
        f, r = calibration.lidar_slopes(z)
        self.assertAlmostEqual(f, math.degrees(math.atan(0.03)), places=9)
        self.assertAlmostEqual(r, math.degrees(math.atan(0.02 + 0.05 / 5 / (2 * calibration.LIDAR_ACROSS))),
                               places=9)  # un point bas ne pèse que pour sa part
        pts = calibration.lidar_points(LON0, LAT0, 90.0)
        self.assertEqual(len(pts), 15)
        front = pts[calibration.LIDAR_GRID.index((calibration.LIDAR_ALONG, 0))]
        right = pts[calibration.LIDAR_GRID.index((0, calibration.LIDAR_ACROSS))]
        self.assertGreater(front[0], LON0)  # cap 90 : devant = à l'est
        self.assertAlmostEqual(front[1], LAT0, places=9)
        self.assertLess(right[1], LAT0)  # droite = au sud
        self.assertEqual(pts[calibration.LIDAR_GRID.index((0, 0))], (LON0, LAT0))

    def entry(self, forward, right, lidar, heading=30.0, sequence="seq", verticals=True):
        v = ({"up": list(calibration.up_from_components(forward, right, heading)), "count": 120, "sigma": 0.08}
             if verticals else None)
        return {"sequence": sequence, "heading": heading, "verticals": v, "lidar": lidar}

    def test_model(self):
        entries = [self.entry(f + 0.3, r - 0.2, [f, r]) for f, r in ((1.0, 0.5), (-2.0, 1.0), (0.4, -1.1))]
        model = calibration.lidar_model(entries)
        self.assertAlmostEqual(model["offset"][0], 0.3, places=6)
        self.assertAlmostEqual(model["offset"][1], -0.2, places=6)
        self.assertEqual(model["count"], 3)
        self.assertEqual(model["sigma"], calibration.LIDAR_MIN_SIGMA)
        self.assertIsNone(calibration.lidar_model(entries[:2]))  # trop peu de photos
        # Caméra qui ne suit pas le véhicule (portée à pied) : écarts trop grands
        loose = [self.entry(f, r, [0.0, 0.0]) for f, r in ((1.5, -1.0), (-1.5, 1.2), (0.2, 1.8), (-0.8, -1.6))]
        self.assertIsNone(calibration.lidar_model(loose))

    def test_priority(self):
        cal = calibration.Calibration()
        for i, (f, r) in enumerate(((1.0, 0.5), (-2.0, 1.0), (0.4, -1.1))):
            cal.set_auto("v{}".format(i), self.entry(f + 0.3, r - 0.2, [f, r]))
        # Photo sans verticales : pente et dévers LiDAR, plus le décalage de fixation
        cal.set_auto("p", self.entry(0, 0, [1.5, -0.5], verticals=False))
        t = cal.tilt("p")
        self.assertEqual(t["source"], "lidar")
        f, r = calibration.tilt_components(t["up"], 30.0)
        self.assertAlmostEqual(f, 1.8, places=6)
        self.assertAlmostEqual(r, -0.7, places=6)
        self.assertEqual(cal.tilt("v0")["source"], "verticales")
        # Autre séquence, sans modèle : rien
        cal.set_auto("q", self.entry(0, 0, [1.5, -0.5], sequence="autre", verticals=False))
        self.assertIsNone(cal.tilt("q"))
        # Calage à la main sur deux objets : prioritaire ; sur un seul, complété par l'automatique
        up = calibration.up_from_components(0.5, 0.0, 0.0)
        cal.add_vertical("p", seen(up, 10.0, -10.0), seen(up, 10.0, 20.0))
        self.assertEqual(cal.tilt("p")["source"], "clics + lidar")
        cal.add_vertical("p", seen(up, 100.0, -10.0), seen(up, 100.0, 20.0))
        self.assertEqual(cal.tilt("p")["source"], "clics")
        self.assertTrue(cal.manual_complete("p"))
        cal.clear_tilt("p")
        self.assertEqual(cal.tilt("p")["source"], "lidar")
        # Correction appliquée aux clics
        yaw, elev = cal.untilted("v0", 30.0, -10.0)
        self.assertNotAlmostEqual(elev, -10.0, places=2)

    def test_one_object_completes_automatic(self):
        """Un objet vertical cliqué corrige la composante qu'il voit ; l'autre reste celle du calcul
        automatique, au lieu de revenir à zéro."""
        truth = calibration.up_from_components(1.2, 0.8, 0.0)
        cal = calibration.Calibration()
        auto = calibration.up_from_components(1.0, 0.8, 0.0)  # tangage un peu faux, roulis juste
        cal.set_auto("p", {"sequence": "s", "heading": 0.0, "lidar": None,
                           "verticals": {"up": list(auto), "count": 80, "sigma": 0.15}})
        # Objet à l'est : il voit le tangage (avant), pas le roulis
        cal.add_vertical("p", seen(truth, 90.0, -10.0), seen(truth, 90.0, 25.0))
        t = cal.tilt("p")
        self.assertEqual(t["source"], "clics + verticales")
        f, r = calibration.tilt_components(t["up"], 0.0)
        self.assertAlmostEqual(r, 0.8, delta=0.02)  # roulis du calcul automatique conservé
        self.assertGreater(f, 1.05)  # tangage tiré vers l'objet cliqué
        self.assertLess(f, 1.2)
        self.assertAlmostEqual(t["sigma"], 0.15, delta=0.01)  # roulis : l'a priori seul le contraint
        alone = calibration.tilt_from_verticals(cal.verticals["p"])
        self.assertTrue(alone["partial"])
        self.assertAlmostEqual(calibration.tilt_components(alone["up"], 0.0)[1], 0.0, delta=0.05)  # sans a priori
        self.assertEqual(alone["sigma"], calibration.UNCALIBRATED)

    def test_storage(self):
        cal = calibration.Calibration()
        for i, (f, r) in enumerate(((1.0, 0.5), (-2.0, 1.0), (0.4, -1.1))):
            cal.set_auto("v{}".format(i), self.entry(f + 0.3, r - 0.2, [f, r]))
        cal.set_auto("p", self.entry(0, 0, [1.5, -0.5], verticals=False))
        data = cal.to_dict()
        data["auto"]["abime"] = {"heading": "nord"}
        data["auto"]["abime2"] = {"heading": 10, "verticals": {"up": [0, 0]}}
        other = calibration.Calibration()
        other.load(data)
        self.assertEqual(set(other.auto), {"v0", "v1", "v2", "p"})
        self.assertAlmostEqual(other.tilt("p")["tilt"], cal.tilt("p")["tilt"], places=9)
        merged = calibration.Calibration()
        merged.set_auto("v0", self.entry(1.3, 0.3, [1.0, 0.5], heading=31.0))
        merged.merge(data)
        self.assertEqual(merged.auto["v0"]["heading"], 31.0)  # le calage en cours l'emporte
        self.assertEqual(merged.tilt("p")["source"], "lidar")  # modèle recalculé après l'ajout


ITEM = {"id": "pic", "collection": "seq", "geometry": {"coordinates": [LON0, LAT0]},
        "assets": {"hd": {"href": "https://example.org/hd.jpg"}},
        "properties": {"view:azimuth": 30, "pers:interior_orientation": {"field_of_view": 360}}}


class FakeNetwork:
    def __init__(self, lidar_down=False, image=b"jpeg"):
        self.calls, self.lidar_down, self.image = [], lidar_down, image

    def fetch_json(self, url, callback, **kwargs):
        self.calls.append("lidar")
        if self.lidar_down:
            callback(None, "délai dépassé")
        else:
            callback({"elevations": [{"z": 100 + a * 0.2 / 3} for a, _ in calibration.LIDAR_GRID]}, None)

    def fetch(self, url, callback, **kwargs):
        self.calls.append("image")
        callback(self.image, None)


class Checker(unittest.TestCase):
    def check(self, item, cal, network, result=None):
        checker = autotilt.TiltChecker()
        runs = []
        checker.run = lambda function, args, done: runs.append(args) or done(result)
        done = []
        with mock.patch.object(autotilt.api, "fetch_json", network.fetch_json), \
                mock.patch.object(autotilt.api, "fetch", network.fetch), \
                mock.patch.object(autotilt, "HAS_NUMPY", True):
            checker.check(item, cal, lambda: done.append(1))
        self.assertEqual(done, [1])
        return runs

    def test_stored(self):
        cal = calibration.Calibration()
        up = calibration.up_from_components(1.0, 0.0, 30.0)
        runs = self.check(ITEM, cal, FakeNetwork(), {"up": up, "count": 80, "sigma": 0.1})
        self.assertEqual(runs, [(b"jpeg", 30.0)])  # photo et cap transmis au calcul
        entry = cal.auto["pic"]
        self.assertEqual(entry["sequence"], "seq")
        self.assertAlmostEqual(entry["lidar"][0], math.degrees(math.atan(0.2 / 3.0)), places=9)
        self.assertEqual(cal.tilt("pic")["source"], "verticales")

    def test_nothing_to_do(self):
        cal = calibration.Calibration()
        network = FakeNetwork()
        flat = dict(ITEM, properties={"view:azimuth": 30, "pers:interior_orientation": {"field_of_view": 70}})
        self.check(flat, cal, network)  # photo classique
        no_heading = dict(ITEM, properties={"pers:interior_orientation": {"field_of_view": 360}})
        self.check(no_heading, cal, network)
        cal.set_auto("pic", {"sequence": "seq", "heading": 30.0, "verticals": None, "lidar": None})
        self.check(ITEM, cal, network)  # déjà analysée
        self.assertEqual(network.calls, [])

    def test_lidar_down_not_kept(self):
        cal = calibration.Calibration()
        self.check(ITEM, cal, FakeNetwork(lidar_down=True), None)
        self.assertNotIn("pic", cal.auto)  # à refaire : le service peut revenir
        self.check(ITEM, cal, FakeNetwork(), None)
        self.assertIsNone(cal.auto["pic"]["verticals"])  # pas de verticales : gardé quand même

    def test_left_photo_abandoned(self):
        """Photo quittée avant la fin : téléchargement interrompu, rien d'enregistré ni calculé ;
        reprise si on y revient."""
        cal, checker, pending, runs, aborted = calibration.Calibration(), autotilt.TiltChecker(), {}, [], []

        class Reply:
            def __init__(self, cb):
                self.cb = cb

            def abort(self):
                aborted.append(1)
                self.cb(None, "Operation canceled")

        def fetch(url, cb, **kw):
            pending["image"] = Reply(cb)
            return pending["image"]

        checker.run = lambda function, args, cb: runs.append(args) or cb(None)
        other = dict(ITEM, id="autre")
        with mock.patch.object(autotilt.api, "fetch_json", lambda url, cb, **kw: pending.__setitem__("lidar", cb)), \
                mock.patch.object(autotilt.api, "fetch", fetch), mock.patch.object(autotilt, "HAS_NUMPY", True):
            checker.check(ITEM, cal)
            first_lidar = pending["lidar"]
            checker.check(other, cal)  # on passe à une autre photo
            self.assertEqual(aborted, [1])
            self.assertFalse(checker.pending("pic"))
            first_lidar({"elevations": [100.0] * len(calibration.LIDAR_GRID)}, None)  # réponse tardive
            self.assertNotIn("pic", cal.auto)
            self.assertEqual(runs, [])
            checker.check(ITEM, cal)  # retour sur la photo : analysée à nouveau
            self.assertTrue(checker.pending("pic"))

    def test_requests_shared(self):
        cal, checker, pending, done = calibration.Calibration(), autotilt.TiltChecker(), [], []
        checker.run = lambda function, args, cb: cb(None)
        with mock.patch.object(autotilt.api, "fetch_json", lambda url, cb, **kw: pending.append(cb)), \
                mock.patch.object(autotilt.api, "fetch", lambda url, cb, **kw: pending.append(cb)), \
                mock.patch.object(autotilt, "HAS_NUMPY", True):
            checker.check(ITEM, cal, lambda: done.append("a"))
            self.assertTrue(checker.pending("pic"))
            checker.check(ITEM, cal, lambda: done.append("b"))
        self.assertEqual(len(pending), 2)  # LiDAR et photo, demandés une fois
        pending[0]({"elevations": [100.0] * len(calibration.LIDAR_GRID)}, None)
        self.assertEqual(done, [])
        pending[1](b"jpeg", None)
        self.assertEqual(done, ["a", "b"])
        self.assertFalse(checker.pending("pic"))


if __name__ == "__main__":
    unittest.main()
