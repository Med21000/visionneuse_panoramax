# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Cédric COCHART
# SPDX-License-Identifier: GPL-2.0-or-later
"""Tests de core/session.py : enchaînement des mesures appuyées sur des points triangulés,
comme dans l'application web et le plugin (clics en radians dans le repère de la photo).

Scène : deux photos 360° à 6 m l'une de l'autre sur une route nord-sud (cap 0°), cible à
5 m à l'est entre les deux ; sol plat, caméra à 1,9 m. Les clics visent des points connus.
"""

import math
import unittest

from support import module, point

session = module("core.session")

CAMERA = 1.9


def photo(pic, north):
    lon, lat = point(0.0, north)
    return {"id": pic, "collection": "s1", "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {"view:azimuth": 0, "datetime": "2026-01-01T00:00:00Z",
                           "pers:interior_orientation": {"field_of_view": 360}},
            "links": []}


def direction(from_north, target):
    """(cap relatif à l'image, élévation) en radians, de la photo vers target (est, nord, z)."""
    dx, dy, dz = target[0], target[1] - from_north, target[2] - CAMERA
    return math.atan2(dx, dy), math.atan2(dz, math.hypot(dx, dy))


class Base(unittest.TestCase):
    def setUp(self):
        self.s = session.Session()
        self.s.set_camera_height(CAMERA)
        self.p1, self.p2 = photo("p1", 0.0), photo("p2", 6.0)
        self.s.set_active(True)

    def aim(self, item, north, target):
        self.s.set_picture(item)
        return self.s.click(*direction(north, target))


class Height(Base):
    def test_reference_then_top(self):
        foot, top = (5.0, 3.0, 0.0), (5.0, 3.0, 4.0)
        self.s.set_mode("height")
        self.assertTrue(self.s._aiming())
        self.aim(self.p1, 0.0, foot)
        text = self.aim(self.p2, 6.0, foot)
        self.assertIn("2 visées", text)
        self.assertIn("Valider le point triangulé", text)
        self.assertIn("Point de référence A validé", self.s.keep_point())
        self.assertFalse(self.s._aiming())
        # Seul le sommet se clique : le pied est la visée faite depuis cette photo
        text = self.s.click(*direction(6.0, top))
        self.assertTrue(text.startswith("Hauteur : 4,00 m"), text)
        self.assertIn("calée sur le point triangulé A", text)
        # Le même point sert à une autre hauteur, depuis l'autre photo
        self.s.set_picture(self.p1)
        text = self.s.click(*direction(0.0, (5.0, 3.0, 2.5)))
        self.assertTrue(text.startswith("Hauteur : 2,50 m"), text)
        self.assertEqual(len(self.s._heights()), 2)
        self.assertEqual(sum(1 for m in self.s.viewer_marks() if m.get("vertical")), 1)

    def test_select_and_lift(self):
        foot, top = (5.0, 3.0, 0.0), (5.0, 3.0, 4.0)
        self.s.set_mode("height")
        self.aim(self.p1, 0.0, foot)
        self.aim(self.p2, 6.0, foot)
        self.s.keep_point()
        self.s.click(*direction(6.0, top))
        # Clic sur la croix du sommet : sélectionné ; ↑ ↑ : 2 cm ; Entrée : terminé
        self.s.click(*direction(6.0, top))
        self.assertTrue(self.s.state()["point_sel"])
        self.s.nudge_point("up")
        self.s.nudge_point("up")
        text = self.s.finish_point()
        self.assertTrue(text.startswith("Hauteur : 4,02 m"), text)
        self.assertFalse(self.s.state()["point_sel"])


class FreeMeasure(Base):
    def test_distance_between_points(self):
        a, b = (5.0, 3.0, 0.0), (5.0, 7.0, 0.0)
        self.s.set_mode("tri")
        for target in (a, b):
            self.aim(self.p1, 0.0, target)
            self.aim(self.p2, 6.0, target)
            text = self.s.keep_point()
        self.assertIn("Distance A–B : 4,00 m", text)
        self.assertEqual([r["kind"] for r in self.s.results()], ["point", "point", "distance"])
        # Carte cadrée sur le segment qui vient d'être mesuré
        self.assertEqual(self.s.focus()[-1], "segment")

    def test_one_sighting_per_photo(self):
        self.s.set_mode("tri")
        self.aim(self.p1, 0.0, (5.0, 3.0, 0.0))
        text = self.aim(self.p1, 0.0, (5.0, 3.0, 2.0))
        self.assertIn("Visée de cette photo remplacée", text)
        self.assertEqual(len(self.s.triangulator.sightings), 1)


if __name__ == "__main__":
    unittest.main()
