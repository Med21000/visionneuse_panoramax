# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Cédric COCHART
# SPDX-License-Identifier: GPL-2.0-or-later
"""Filtre des photos : date, type de caméra, précision GPS, producteur.

Le même filtre s'applique au filaire (expressions sur les attributs des tuiles
vectorielles, voir layers.apply_filter) et aux photos ouvertes par un clic sur la
carte (propriétés des fiches STAC, voir PhotoFilter.matches).

Attributs des tuiles Panoramax utilisés :
- séquences : date (AAAA-MM-JJ), type, gps_accuracy, account_id ;
- photos : ts (AAAA-MM-JJ hh:mm:ss…), type, gps_accuracy, account_id ;
- grille de densité (petites échelles) : nb_360_pictures, nb_flat_pictures, seul le
  type de caméra y est filtrable.
"""

from qgis.core import QgsSettings

SETTINGS = "visionneuse_panoramax/filter_"
KINDS = (("Tous types", None), ("360°", "360"), ("Classiques", "flat"))
GPS_LIMITS = (("Toute précision GPS", None), ("GPS ≤ 2 m (relevé)", 2.0), ("GPS ≤ 4 m", 4.0))
TILE_TYPES = {"360": "equirectangular", "flat": "flat"}


def _quote(text):
    return "'{}'".format(str(text).replace("'", "''"))


class PhotoFilter:
    """Critères du filtre ; None : critère inactif.

    since : date minimale "AAAA-MM-JJ" ; kind : "360" ou "flat" ; max_gps : précision
    GPS maximale (m) ; producer : (identifiant du compte, nom) du producteur."""

    def __init__(self, since=None, kind=None, max_gps=None, producer=None):
        self.since, self.kind, self.max_gps, self.producer = since, kind, max_gps, producer

    def __eq__(self, other):
        return isinstance(other, PhotoFilter) and vars(self) == vars(other)

    def active(self):
        return any(v is not None for v in vars(self).values())

    # Mémorisation -------------------------------------------------------------
    @classmethod
    def load(cls):
        s = QgsSettings()
        gps = s.value(SETTINGS + "max_gps", "") or ""
        producer = s.value(SETTINGS + "producer_id", "") or ""
        try:
            gps = float(gps) if gps != "" else None
        except ValueError:
            gps = None
        return cls(since=s.value(SETTINGS + "since", "") or None,
                   kind=s.value(SETTINGS + "kind", "") or None,
                   max_gps=gps,
                   producer=(producer, s.value(SETTINGS + "producer_name", "") or producer) if producer else None)

    def save(self):
        s = QgsSettings()
        s.setValue(SETTINGS + "since", self.since or "")
        s.setValue(SETTINGS + "kind", self.kind or "")
        s.setValue(SETTINGS + "max_gps", "" if self.max_gps is None else str(self.max_gps))
        s.setValue(SETTINGS + "producer_id", self.producer[0] if self.producer else "")
        s.setValue(SETTINGS + "producer_name", self.producer[1] if self.producer else "")

    # Filaire ------------------------------------------------------------------
    def tile_expression(self, layer):
        """Expression QGIS du filtre pour une couche des tuiles ("sequences", "pictures",
        "grid"), ou "" si rien n'y est à filtrer."""
        parts = []
        if layer == "grid":
            if self.kind == "360":
                parts.append('"nb_360_pictures" > 0')
            elif self.kind == "flat":
                parts.append('"nb_flat_pictures" > 0')
            return " AND ".join(parts)
        if self.since:
            parts.append('"{}" >= {}'.format("date" if layer == "sequences" else "ts", _quote(self.since)))
        if self.kind:
            parts.append('"type" = {}'.format(_quote(TILE_TYPES[self.kind])))
        if self.max_gps is not None:
            parts.append('to_real("gps_accuracy") <= {}'.format(self.max_gps))
        if self.producer:
            parts.append('"account_id" = {}'.format(_quote(self.producer[0])))
        return " AND ".join(parts)

    # Fiches STAC --------------------------------------------------------------
    def matches(self, item):
        """La photo (fiche STAC de l'API) passe-t-elle le filtre ? Une date, une précision GPS
        ou un producteur absents de la fiche font refuser la photo pour ce critère."""
        props = item.get("properties") or {}
        if self.since and str(props.get("datetime") or "")[:10] < self.since:
            return False
        if self.kind:  # angle de vue souvent absent des photos classiques (téléphones) : 360 seulement si déclaré
            fov = (props.get("pers:interior_orientation") or {}).get("field_of_view") or 0
            if (float(fov) >= 360) != (self.kind == "360"):
                return False
        if self.max_gps is not None:
            gps = props.get("quality:horizontal_accuracy")
            if gps is None or float(gps) > self.max_gps:
                return False
        if self.producer:
            ids = {p.get("id") for p in item.get("providers") or props.get("providers") or []}
            if self.producer[0] not in ids:
                return False
        return True

    def describe(self):
        """Résumé du filtre, pour les messages ("" s'il est inactif)."""
        parts = []
        if self.since:
            parts.append("depuis le {}".format("/".join(reversed(self.since.split("-")))))
        if self.kind:
            parts.append(dict((v, k) for k, v in KINDS)[self.kind].lower())
        if self.max_gps is not None:
            parts.append("GPS ≤ {} m".format(str(self.max_gps).rstrip("0").rstrip(".").replace(".", ",")))
        if self.producer:
            parts.append("producteur « {} »".format(self.producer[1]))
        return ", ".join(parts)


def item_producer(item):
    """(identifiant du compte, nom) du producteur de la photo, ou None."""
    for p in (item or {}).get("providers") or ((item or {}).get("properties") or {}).get("providers") or []:
        if "producer" in (p.get("roles") or []) and p.get("id"):
            return p["id"], p.get("name") or p["id"]
    return None
