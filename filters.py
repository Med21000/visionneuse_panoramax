# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Cédric COCHART
# SPDX-License-Identifier: GPL-2.0-or-later
"""Filtre des photos (voir core/filters.py), mémorisé dans les réglages de QGIS."""

from qgis.core import QgsSettings

from .core import filters
from .core.filters import GPS_LIMITS, KINDS, TILE_TYPES, item_producer

SETTINGS = "visionneuse_panoramax/filter_"
REEXPORTED = (GPS_LIMITS, KINDS, TILE_TYPES, item_producer)  # utilisés par le panneau et les tests


class PhotoFilter(filters.PhotoFilter):
    """Filtre des photos, mémorisé dans les réglages de QGIS."""

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
