# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Cédric COCHART
# SPDX-License-Identifier: GPL-2.0-or-later
"""Ligne des critères du filtre des photos dans le panneau (voir filters.PhotoFilter).

Date minimale, type de caméra, précision GPS et producteur. Chaque modification est
enregistrée et signalée par `changed` ; la liste des producteurs propose celui de la
photo affichée (voir show_item).
"""

from qgis.PyQt.QtCore import QDate, pyqtSignal
from qgis.PyQt.QtWidgets import QCheckBox, QComboBox, QDateEdit, QHBoxLayout, QPushButton, QWidget

from . import filters


class FilterRow(QWidget):
    changed = pyqtSignal(object)  # filtre modifié et enregistré (filters.PhotoFilter)

    def __init__(self, parent=None):
        super().__init__(parent)
        flt = filters.PhotoFilter.load()
        self._producers = dict([flt.producer]) if flt.producer else {}  # identifiant -> nom
        self._item = None  # fiche de la photo affichée
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        self.chk_since = QCheckBox("Depuis le")
        self.chk_since.setToolTip("Seulement les photos prises depuis cette date")
        self.chk_since.setChecked(bool(flt.since))
        row.addWidget(self.chk_since)
        self.date_since = QDateEdit()
        self.date_since.setCalendarPopup(True)
        self.date_since.setDisplayFormat("dd/MM/yyyy")
        since = QDate.fromString(flt.since or "", "yyyy-MM-dd")
        self.date_since.setDate(since if since.isValid() else QDate.currentDate().addYears(-2))
        self.date_since.setEnabled(bool(flt.since))
        row.addWidget(self.date_since)
        self.cmb_kind = QComboBox()
        for label, kind in filters.KINDS:
            self.cmb_kind.addItem(label, kind)
        self.cmb_kind.setCurrentIndex(max(0, self.cmb_kind.findData(flt.kind)))
        self.cmb_kind.setToolTip("Photos 360° (équirectangulaires) ou classiques")
        row.addWidget(self.cmb_kind)
        self.cmb_gps = QComboBox()
        for label, limit in filters.GPS_LIMITS:
            self.cmb_gps.addItem(label, limit)
        self.cmb_gps.setCurrentIndex(max(0, self.cmb_gps.findData(flt.max_gps)))
        self.cmb_gps.setToolTip("Précision GPS annoncée par Panoramax : 2 m pour le matériel de relevé, 4 à 5 m "
                                "pour les caméras grand public et les téléphones")
        row.addWidget(self.cmb_gps)
        self.cmb_producer = QComboBox()
        self.cmb_producer.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.cmb_producer.setToolTip("Seulement les photos de ce producteur ; la liste propose celui de la photo "
                                     "affichée")
        row.addWidget(self.cmb_producer)
        self._fill_producers()
        row.addStretch(1)
        btn_reset = QPushButton("Aucun filtre")
        btn_reset.setToolTip("Afficher toutes les photos")
        btn_reset.clicked.connect(self.reset)
        row.addWidget(btn_reset)
        self.chk_since.toggled.connect(self.date_since.setEnabled)
        for signal in (self.chk_since.toggled, self.date_since.dateChanged, self.cmb_kind.currentIndexChanged,
                       self.cmb_gps.currentIndexChanged, self.cmb_producer.activated):
            signal.connect(self._on_edited)

    def photo_filter(self):
        """Filtre réglé (filters.PhotoFilter)."""
        return filters.PhotoFilter(
            since=self.date_since.date().toString("yyyy-MM-dd") if self.chk_since.isChecked() else None,
            kind=self.cmb_kind.currentData(),
            max_gps=self.cmb_gps.currentData(),
            producer=self._selected_producer())

    def show_item(self, item):
        """Photo affichée : son producteur est proposé dans la liste."""
        self._item = item
        self._fill_producers()

    def _selected_producer(self):
        pid = self.cmb_producer.currentData()
        return (pid, self._producers.get(pid, pid)) if pid else None

    def _fill_producers(self):
        """Liste des producteurs : tous, celui choisi, et celui de la photo affichée."""
        selected = self.cmb_producer.currentData() if self.cmb_producer.count() else next(iter(self._producers), None)
        shown = filters.item_producer(self._item)
        if shown:
            self._producers[shown[0]] = shown[1]
        self.cmb_producer.blockSignals(True)
        self.cmb_producer.clear()
        self.cmb_producer.addItem("Tous producteurs", "")
        for pid in dict.fromkeys(p for p in (selected, shown and shown[0]) if p):
            self.cmb_producer.addItem(self._producers.get(pid, pid), pid)
        self.cmb_producer.setCurrentIndex(max(0, self.cmb_producer.findData(selected or "")))
        self.cmb_producer.blockSignals(False)

    def _on_edited(self, *args):
        flt = self.photo_filter()
        flt.save()
        self.changed.emit(flt)

    def reset(self):
        """Aucun filtre."""
        widgets = (self.chk_since, self.date_since, self.cmb_kind, self.cmb_gps, self.cmb_producer)
        for w in widgets:
            w.blockSignals(True)
        self.chk_since.setChecked(False)
        self.date_since.setEnabled(False)
        self.cmb_kind.setCurrentIndex(0)
        self.cmb_gps.setCurrentIndex(0)
        self.cmb_producer.setCurrentIndex(0)
        for w in widgets:
            w.blockSignals(False)
        self._on_edited()
