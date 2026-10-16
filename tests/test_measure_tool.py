# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Cédric COCHART
# SPDX-License-Identifier: GPL-2.0-or-later
"""Tests de measure_tool.py : largeurs, longueur, hauteur, recalage sur la carte.

Scène : sol plat à l'altitude 0, route nord-sud (axe 0°), caméra à CAMERA m. Les clics
sont calculés depuis des points 3D connus, que les mesures doivent retrouver.
"""

import math
import unittest
from unittest import mock

from support import Stub, distance, install_qgis_stub, module, point

install_qgis_stub()
measure_tool = module("measure_tool")
calibration = module("core.calibration")

CAMERA = 1.9


class FakeItem:
    """Dessin sur la carte remplacé : garde seulement les dernières données."""

    def __init__(self, canvas, wall=False):
        self.data, self.wall = None, wall

    def set_data(self, *data):
        self.data = data

    def remove(self):
        pass


def click(target, photo=(0.0, 0.0), pic="p1", camera=CAMERA, **extra):
    """Clic depuis la photo en photo (est, nord) vers target (est, nord, altitude)."""
    dx, dy, dz = target[0] - photo[0], target[1] - photo[1], target[2] - camera
    lon, lat = point(*photo)
    c = {"lon": lon, "lat": lat, "pic": pic, "yaw": math.degrees(math.atan2(dx, dy)) % 360,
         "elev": math.degrees(math.atan2(dz, math.hypot(dx, dy))), "axis": 0.0, "axis_source": "séquence",
         "sequence": "s1"}
    c.update(extra)
    return c


class Base(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(measure_tool, "MeasureItem", FakeItem)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.tool = measure_tool.MeasureTool(Stub())
        self.tool.camera_height = CAMERA

    def measure(self, mode, clicks):
        self.tool.set_mode(mode)
        for c in clicks:
            self.tool.add_click(c)
        return self.tool.current

    def assertValue(self, m, expected, delta=1e-6):
        self.assertIsNone(m.error)
        self.assertIsNotNone(m.result)
        self.assertAlmostEqual(m.result["value"], expected, delta=delta)


class Presets(Base):
    def test_road_width(self):
        m = self.measure("road", [click((-3.0, 8.0, 0.0)), click((4.0, 12.0, 0.0))])
        self.assertValue(m, 7.0)
        self.assertGreater(m.result["uncertainty"], 0.0)
        # Trait à mi-chemin des deux bords le long de la route (nord), relié en pointillés à chacun
        start, end = m.result["points"]
        self.assertLess(distance(start, point(-3.0, 10.0)), 1e-6)
        self.assertLess(distance(end, point(4.0, 10.0)), 1e-6)
        self.assertEqual(m.result["extra"], m.result["picks"])
        self.assertIn("Largeur perpendiculaire", m.status())

    def test_oblique_road(self):
        a = math.radians(30.0)  # axe de la route orienté à 30° : bords séparés de 6 m en travers
        along, across = (math.sin(a), math.cos(a)), (math.cos(a), -math.sin(a))
        p1 = (-2.0, 9.0)
        p2 = (p1[0] + 6.0 * across[0] + 3.0 * along[0], p1[1] + 6.0 * across[1] + 3.0 * along[1])
        m = self.measure("road", [click(p1 + (0.0,), axis=30.0), click(p2 + (0.0,), axis=30.0)])
        self.assertValue(m, 6.0)
        # Bords décalés de 3 m le long de la route : trait à mi-chemin (1,5 m de chacun)
        start = (p1[0] + 1.5 * along[0], p1[1] + 1.5 * along[1])
        end = (start[0] + 6.0 * across[0], start[1] + 6.0 * across[1])
        for got, expected in zip(m.result["points"], (start, end)):
            self.assertLess(distance(got, point(*expected)), 1e-6)

    def test_profiles_with_different_ground_under_camera(self):
        # Droite de pente du terrain ajustée dans chaque direction visée : son altitude sous la
        # caméra varie (ici de 0,6 m). Les deux clics doivent partir de la même caméra.
        targets = ((-3.0, 8.0, 0.0), (4.0, 12.0, 0.0))
        clicks = [click(targets[0], profile=[(0.0, 0.0), (60.0, 0.0)]),
                  click(targets[1], profile=[(0.0, 0.6), (60.0, 0.6)])]
        m = self.measure("width", clicks)
        self.assertValue(m, math.hypot(7.0, 4.0))
        self.assertAlmostEqual(m.result["decomposition"]["vertical"], 0.0, places=6)
        for dyaw, delev in m.result["shifts"]:  # repères à l'endroit cliqué
            self.assertAlmostEqual(dyaw, 0.0, places=6)
            self.assertAlmostEqual(delev, 0.0, places=6)

    def test_road_without_axis(self):
        m = self.measure("road", [click((-3.0, 8.0, 0.0), axis=None), click((4.0, 12.0, 0.0), axis=None)])
        self.assertIsNotNone(m.error)
        self.assertIsNone(m.result)

    def test_width(self):
        m = self.measure("width", [click((-3.0, 8.0, 0.0)), click((4.0, 12.0, 0.0))])
        self.assertValue(m, math.hypot(7.0, 4.0))
        for r, target in zip(m.result["ranges"], ((-3.0, 8.0), (4.0, 12.0))):
            self.assertAlmostEqual(r, math.hypot(*target), places=6)

    def test_height(self):
        m = self.measure("height", [click((5.0, 5.0, 0.0)), click((5.0, 5.0, 4.0))])
        self.assertValue(m, 4.0)

    def test_height_errors(self):
        m = self.measure("height", [click((5.0, 5.0, 0.0)), click((5.0, 5.0, 4.0), pic="p2")])
        self.assertIsNotNone(m.error)
        self.assertEqual(len(m.clicks), 1)  # clic refusé, à refaire
        m = self.measure("height", [click((5.0, 5.0, 0.0)), click((5.0, 5.0, 0.0)), click((5.0, 5.0, -1.0))])
        self.assertIsNotNone(m.error)

    def test_calibrated_camera_height(self):
        cal = calibration.Calibration()
        cal.heights["s1"] = [(2.4, 0.01)]
        self.tool.calibration = cal
        clicks = [click((-3.0, 8.0, 0.0), camera=2.4), click((4.0, 12.0, 0.0), camera=2.4)]
        m = self.measure("road", clicks)
        self.assertValue(m, 7.0)
        self.assertIn("(calée)", m.status())

    def test_finished_measures_kept(self):
        self.measure("width", [click((-3.0, 8.0, 0.0)), click((4.0, 12.0, 0.0))])
        self.tool.add_click(click((0.0, 6.0, 0.0)))  # nouvelle mesure : la précédente est gardée
        self.assertEqual(len(self.tool.done), 1)
        self.assertEqual(len(self.tool.measures()), 2)


class CameraFromAltitude(Base):
    """Caméra de relevé : hauteur de caméra = altitude NGF de la caméra − sol sous la photo."""

    def clicks(self, camera, ground=100.0, **extra):
        """Largeur de 7 m au sol vue depuis une caméra réellement à `camera` m, sol à `ground` m."""
        extra.setdefault("camera_alt", ground + camera)
        extra.setdefault("precise_ground", True)
        profile = [(0.0, ground), (60.0, ground)]
        return [click((-3.0, 8.0, 0.0), camera=camera, profile=profile, **extra),
                click((4.0, 12.0, 0.0), camera=camera, profile=profile, **extra)]

    def test_height_from_altitude(self):
        m = self.measure("road", self.clicks(2.4))  # réglage du panneau : 1,90 m, faux
        self.assertValue(m, 7.0)
        self.assertIn("caméra à 2,40 m (altitude GPS)", m.status())

    def test_before_calibration(self):
        cal = calibration.Calibration()
        cal.heights["s1"] = [(1.5, 0.01)]
        self.tool.calibration = cal
        self.assertValue(self.measure("road", self.clicks(2.4)), 7.0)

    def test_ignored(self):
        # Sol des services de secours (mailles de 25 à 90 m) : trop imprécis sous la caméra,
        # même avec une altitude plausible (2,40 m au-dessus du sol)
        m = self.measure("road", self.clicks(CAMERA, precise_ground=False, camera_alt=102.4))
        self.assertValue(m, 7.0)
        self.assertIn("caméra à 1,90 m.", m.status())
        # Hauteur invraisemblable (altitude ou géoïde faux) : réglage du panneau
        self.tool.finish()
        self.tool.current = None
        m = self.measure("road", self.clicks(CAMERA, camera_alt=150.0))
        self.assertValue(m, 7.0)


class Relocation(Base):
    def test_height_relocated_on_map(self):
        # Hauteur de caméra fausse : la hauteur mesurée l'est aussi...
        clicks = [click((5.0, 5.0, 0.0)), click((5.0, 5.0, 4.0))]
        self.tool.camera_height = 1.5
        m = self.measure("height", clicks)
        self.assertNotAlmostEqual(m.result["value"], 4.0, places=1)
        # ... mais plus une fois le pied recalé sur la carte à sa vraie place
        m.relocate(0, *point(5.0, 5.0))
        self.assertValue(m, 4.0)
        self.assertEqual(len(m.picks()), 1)  # seul le pied est recalable

    def test_height_moves_as_one(self):
        # Pied et sommet (décalé de côté) glissés ensemble du même décalage : la hauteur garde sa
        # forme sur la carte, puis une nouvelle visée de chaque point reste à cette profondeur
        m = self.measure("height", [click((5.0, 5.0, 0.0)), click((3.0, 5.0, 4.0))])
        before = m.points()[0]
        m.relocate(0, *point(5.0, 8.0))
        after = m.points()[0]
        for p, q in zip(before, after):
            self.assertAlmostEqual(q[0] - p[0], 0.0, places=6)
            self.assertAlmostEqual(q[1] - p[1], 3.0, places=6)
        top = after[1]
        m.reaim(1, click((top[0], top[1], 4.0)))
        m.reaim(0, click((5.0, 8.0, 0.3)))
        foot, top = m.points()[0]
        self.assertLess(math.hypot(top[0] - after[1][0], top[1] - after[1][1]), 1e-6)
        self.assertLess(math.hypot(foot[0] - 5.0, foot[1] - 8.0), 1e-6)
        self.assertValue(m, 3.7)

    def test_height_anchored_on_triangulated_point(self):
        # Hauteur de caméra fausse, mais pied calé sur le point triangulé : hauteur exacte, et
        # incertitude croissante avec celle de la triangulation
        clicks = [click((5.0, 5.0, 0.0)), click((5.0, 5.0, 4.0))]
        self.tool.camera_height = 1.5
        m = self.measure("height", clicks)
        m.anchor(*point(5.0, 5.0), 0.1, "A")
        self.assertValue(m, 4.0)
        self.assertIn("calée sur le point triangulé A (±0,10 m)", m.status())
        self.assertIn("hauteur de caméra sans effet", m.status())
        close = m.result["uncertainty"]
        m.anchor(*point(5.0, 5.0), 1.0, "A")
        self.assertValue(m, 4.0)
        # 1 m de profondeur en plus ou en moins sur 7 m : environ 4/7 m sur la hauteur
        self.assertGreater(m.result["uncertainty"], close + 0.4)
        # Pied glissé ensuite à la main : le point triangulé ne vaut plus
        m.relocate(0, *point(5.0, 6.0))
        self.assertIsNone(m.reference)
        self.assertIn("2 points recalés sur la carte", m.status())

    def test_lift(self):
        # Sommet monté de 3 cm puis descendu de 1 cm au clavier : 4,02 m, repère déplacé vers le
        # haut dans la photo ; une nouvelle visée annule le décalage ; sommet jamais sous le pied
        m = self.measure("height", [click((5.0, 5.0, 0.0)), click((5.0, 5.0, 4.0))])
        m.anchor(*point(5.0, 5.0), 0.1, "A")
        for dz in (0.01, 0.01, 0.01, -0.01):
            m.lift(1, dz)
        self.assertValue(m, 4.02)
        self.assertIn("sommet monté de 2 cm au clavier", m.status())
        self.assertGreater(m.result["shifts"][1][1], 0.0)
        m.lift(1, -10.0)
        self.assertIsNotNone(m.error)
        self.assertAlmostEqual(m.result["value"], 4.02, places=6)
        m.reaim(1, click((5.0, 5.0, 4.0)))
        self.assertValue(m, 4.0)
        self.assertEqual(m.lifts, {})

    def test_width_relocated(self):
        m = self.measure("width", [click((-3.0, 8.0, 0.0)), click((4.0, 12.0, 0.0))])
        m.relocate(1, *point(4.0, 20.0))
        # Position de la carte, altitude du rayon de visée d'origine prolongé jusque-là
        z = CAMERA - CAMERA * math.hypot(4.0, 20.0) / math.hypot(4.0, 12.0)
        self.assertValue(m, math.sqrt(7.0 ** 2 + 12.0 ** 2 + z ** 2))
        self.assertIn("recalé", m.status())
        dyaw, delev = m.result["shifts"][1]  # le repère se déplace de côté dans la visionneuse
        self.assertAlmostEqual(dyaw, math.degrees(math.atan2(4, 20) - math.atan2(4, 12)), places=6)
        self.assertAlmostEqual(delev, 0.0, places=6)

    def test_reaim_on_map_plane(self):
        # Second point cliqué au sol, mais en fait sur un mur à 14 m. Glissé sur la carte puis visé
        # à nouveau, il doit rester dans le plan calé sur la carte (y = 14), pas revenir au sol
        m = self.measure("width", [click((3.0, 10.0, 0.0)), click((-2.0, 12.0, 0.0))])
        x = -2.0 * 14 / 12
        m.relocate(1, *point(x, 14.0))
        m.reaim(1, click((x, 14.0, 1.8)))
        self.assertIsNone(m.error)
        p = m.points()[0][1]
        self.assertAlmostEqual(p[1], 14.0, places=6)
        self.assertAlmostEqual(p[2], 1.8, places=6)

    def test_reaim(self):
        m = self.measure("width", [click((-3.0, 8.0, 0.0)), click((4.0, 12.0, 0.0))])
        m.reaim(1, click((4.0, 16.0, 0.0)))
        self.assertValue(m, math.hypot(7.0, 8.0))


class RoadAxisAtMeasure(Base):
    """Axe de la route lu au point mesuré (roads.py) plutôt que la direction du véhicule."""

    def test_axis_at_measured_point(self):
        a = math.radians(30.0)  # la route tourne : axe de 30° au point mesuré, 0° à la caméra
        along, across = (math.sin(a), math.cos(a)), (math.cos(a), -math.sin(a))
        p1 = (-2.0, 9.0)
        p2 = (p1[0] + 6.0 * across[0] + 3.0 * along[0], p1[1] + 6.0 * across[1] + 3.0 * along[1])
        asked = []

        def lookup(lon, lat, crossing, sequence):
            asked.append((lon, lat, crossing, sequence))
            return {"axis": 210.0, "source": "BD TOPO, D16"}  # même droite, autre sens

        self.tool.road_lookup = lookup
        m = self.measure("road", [click(p1 + (0.0,)), click(p2 + (0.0,))])
        self.assertValue(m, 6.0)
        lon, lat, crossing, sequence = asked[-1]
        self.assertLess(distance((lon, lat), point((p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2)), 1e-6)  # milieu
        self.assertAlmostEqual(crossing, math.degrees(math.atan2(p2[0] - p1[0], p2[1] - p1[1])) % 360, places=6)
        self.assertEqual(sequence, "s1")
        self.assertIn("axe 30° (BD TOPO, D16)", m.status())
        start = (p1[0] + 1.5 * along[0], p1[1] + 1.5 * along[1])  # trait perpendiculaire au nouvel axe
        self.assertLess(distance(m.result["points"][0], point(*start)), 1e-6)

    def test_pending_then_ready(self):
        answer = ["pending"]
        self.tool.road_lookup = lambda *args: answer[0]
        a = math.radians(30.0)
        p1, p2 = (-2.0, 9.0), (-2.0 + 6.0 * math.cos(a), 9.0 - 6.0 * math.sin(a))
        m = self.measure("road", [click(p1 + (0.0,)), click(p2 + (0.0,))])
        self.assertNotAlmostEqual(m.result["value"], 6.0, places=2)  # axe de la caméra (0°) en attendant
        self.assertIn("(séquence, lecture de la BD TOPO…)", m.status().replace("axe 0° (", "("))
        answer[0] = {"axis": 30.0, "source": "BD TOPO"}
        self.assertEqual(self.tool.recompute_roads(), 1)
        self.assertValue(m, 6.0)
        answer[0] = None  # rien de lisible : axe de la caméra, sans attente annoncée
        self.tool.recompute_roads()
        self.assertNotIn("lecture", m.status())


class Uncertainty(Base):
    """Incertitude affichée : inclinaison de la photo (commune à ses clics), hauteur de caméra,
    visée, façade du cadastre."""

    def road(self):
        return self.measure("road", [click((-3.5, 10.0, 0.0)), click((3.5, 10.0, 0.0))])

    def calibrated(self, sigma):
        cal = calibration.Calibration()
        cal.set_auto("p1", {"sequence": "s1", "heading": 0.0, "lidar": None,
                            "verticals": {"up": [0.0, 0.0, 1.0], "count": 100, "sigma": sigma}})
        self.tool.calibration = cal

    def test_uncalibrated_photo(self):
        # Photo non calée (1 à 3° sur l'imajbox) : 2° de tangage commun fausse 7 m de plus d'un mètre
        unc = self.road().result["uncertainty"]
        self.assertGreater(unc, 1.0)
        self.assertLess(unc, 2.0)  # tangage de +2° : +1,58 m ; hauteur de caméra saisie : 0,55 m

    def test_calibrated_photo(self):
        self.calibrated(0.1)
        unc = self.road().result["uncertainty"]
        self.assertGreater(unc, 0.1)  # hauteur de caméra saisie (±15 cm) : environ 0,55 m sur 7 m à 10 m
        self.assertLess(unc, 0.65)
        # Hauteur de caméra par l'altitude GPS (±5 cm) : bien plus précise
        clicks = [click((-3.5, 10.0, 0.0), camera_alt=100.0 + CAMERA, precise_ground=True,
                        profile=[(0.0, 100.0), (60.0, 100.0)]),
                  click((3.5, 10.0, 0.0), camera_alt=100.0 + CAMERA, precise_ground=True,
                        profile=[(0.0, 100.0), (60.0, 100.0)])]
        m = self.measure("road", clicks)
        self.assertAlmostEqual(m.result["value"], 7.0, places=6)
        self.assertLess(m.result["uncertainty"], 0.3)
        self.assertLess(m.result["uncertainty"], unc / 2)

    def test_common_tilt(self):
        # L'inclinaison est commune aux clics d'une photo : son effet sur une largeur dans l'axe
        # double par rapport à un seul clic (ancien modèle)
        self.calibrated(1.0)
        self.tool.camera_height = CAMERA
        m = self.road()
        north = abs(m._value(clicks=m._tilted_clicks(m.clicks, "p1", 1.0, 0.0)) - 7.0)
        single = [dict(c) for c in m.clicks]
        single[1]["elev"] += 1.0
        self.assertGreater(north, 1.8 * abs(m._value(clicks=single) - 7.0))

    def test_cadastre_wall(self):
        self.calibrated(0.1)
        clicks = [click((8.0, 0.0, 1.0)), click((12.0, 0.0, 6.5))]
        wall = dict(CadastreWall.WALL)
        self.tool.wall_lookup = lambda *args: wall
        self.tool.use_wall = True
        confirmed = self.measure("height", clicks).result["uncertainty"]
        self.tool.clear_all()
        wall["warning"] = True
        unchecked = self.measure("height", [dict(c) for c in clicks]).result["uncertainty"]
        self.assertGreater(unchecked, confirmed)
        self.assertGreater(confirmed, 0.5 * (6.5 - CAMERA) / 12)  # mur à ±0,5 m, vu sous tan e = 0,38


class CadastreWall(Base):
    """Façade du cadastre (buildings.py) : hauteur au pied caché, façade d'un seul clic."""

    WALL = {"distance": 12.0, "points": [point(12.0, -5.0), point(12.0, 5.0)],
            "check": "façade confirmée par le LiDAR HD", "warning": False}

    def test_hidden_foot(self):
        # Pied du mur caché par une voiture : le clic tombe sur sa carrosserie, 8 m devant
        clicks = [click((8.0, 0.0, 1.0)), click((12.0, 0.0, 6.5))]
        m = self.measure("height", clicks)
        self.assertGreater(abs(m.result["value"] - 6.5), 0.5)  # pied pris au sol derrière la voiture
        self.tool.wall_lookup = lambda lon, lat, yaw: self.WALL
        self.assertEqual(self.tool.set_use_wall(True), 1)
        self.assertValue(m, 6.5)
        self.assertIn("façade du cadastre à 12,0 m (façade confirmée par le LiDAR HD)", m.status())
        self.assertEqual(m.wall_item.data[0], self.WALL["points"])  # façade tracée sur la carte
        self.tool.set_use_wall(False)
        self.assertGreater(abs(m.result["value"] - 6.5), 0.5)
        self.assertEqual(m.wall_item.data[0], [])

    def test_pending_and_missing(self):
        answer = ["pending"]
        self.tool.wall_lookup = lambda *args: answer[0]
        self.tool.use_wall = True
        m = self.measure("height", [click((12.0, 0.0, 0.0)), click((12.0, 0.0, 6.5))])
        self.assertValue(m, 6.5)  # pied visible et cliqué : même résultat en attendant
        self.assertIn("lecture du cadastre", m.status())
        answer[0] = None
        self.tool.recompute_walls()
        self.assertIn("aucune façade du cadastre dans la visée : pied cliqué", m.status())


class Recorrect(Base):
    def test_tilt_arrived_after_clicks(self):
        """Inclinaison automatique arrivée après les clics : ils sont corrigés, la mesure aussi."""
        from test_calibration import seen

        up = calibration.up_from_components(2.0, 1.0, 0.0)
        clicks = []
        for target in ((-3.0, 8.0, 0.0), (4.0, 8.0, 0.0)):
            c = click(target)
            c["yaw"], c["elev"] = seen(up, c["yaw"], c["elev"])  # vu par la caméra inclinée
            c["raw"] = [c["yaw"], c["elev"]]
            clicks.append(c)
        m = self.measure("road", clicks)
        self.assertGreater(abs(m.result["value"] - 7.0), 0.1)  # faussée par l'inclinaison
        before = m.result["value"]
        n = self.tool.recorrect("p1", lambda c: calibration.untilt(up, *c["raw"]))
        self.assertEqual(n, 1)
        self.assertValue(m, 7.0)
        self.assertNotEqual(m.result["value"], before)
        self.assertEqual(self.tool.recorrect("p3", lambda c: (0.0, 0.0)), 0)  # autre photo : rien


if __name__ == "__main__":
    unittest.main()
