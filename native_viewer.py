# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Cédric COCHART
# SPDX-License-Identifier: GPL-2.0-or-later
"""Visionneuse de secours, intégrée au plugin, quand la visionneuse web est indisponible.

Sans QtWebEngine, ou sans WebGL dans son moteur : la photo est affichée par PanoWidget
(rotation, zoom, réticule, clics de mesure), avec sa fiche et la navigation vers la
photo précédente ou suivante de la séquence. Les trois dernières images sont gardées
en mémoire pour revenir en arrière sans les retélécharger.
"""

from collections import OrderedDict

from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtGui import QPixmap
from qgis.PyQt.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from . import api
from .pano_widget import PanoWidget


class NativeViewer(QWidget):
    pictureRequested = pyqtSignal(str)  # photo précédente ou suivante demandée (identifiant)

    def __init__(self, text, parent=None):
        super().__init__(parent)
        self._item = None
        self._pending_image = None
        self._image_cache = OrderedDict()  # href -> QPixmap (3 dernières : avant, courante, après)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        info = QLabel(text)
        info.setWordWrap(True)
        lay.addWidget(info)
        self.img = PanoWidget(self)
        self.img.setToolTip("Photo 360° : glisser pour tourner, molette pour zoomer")
        lay.addWidget(self.img, 1)
        self.meta = QLabel("")
        self.meta.setWordWrap(True)
        self.meta.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self.meta)
        nav = QHBoxLayout()
        btn_left = QPushButton("⟲ 45°")
        btn_left.clicked.connect(lambda: self.img.turn(-45))
        nav.addWidget(btn_left)
        self.btn_prev = QPushButton("◀ Précédente")
        self.btn_prev.clicked.connect(lambda: self._navigate("prev"))
        self.btn_next = QPushButton("Suivante ▶")
        self.btn_next.clicked.connect(lambda: self._navigate("next"))
        for b in (self.btn_prev, self.btn_next):
            b.setEnabled(False)
            nav.addWidget(b)
        btn_right = QPushButton("45° ⟳")
        btn_right.clicked.connect(lambda: self.img.turn(45))
        nav.addWidget(btn_right)
        lay.addLayout(nav)

    def _navigate(self, rel):
        if self._item is None:
            return
        pid = api.id_from_href(api.item_link(self._item, rel))
        if pid:
            self.pictureRequested.emit(pid)

    def show_item(self, item, heading, fov):
        """Affiche la photo (fiche STAC) orientée au cap `heading`, de champ `fov` (360 : sphérique)."""
        self._item = item
        props = item.get("properties", {}) or {}
        self.meta.setText("Photo {}<br>Date : {}<br>Cap : {}".format(
            item.get("id", ""), props.get("datetime", "?"),
            "{:.0f}°".format(heading) if heading is not None else "?"))
        self.btn_prev.setEnabled(bool(api.item_link(item, "prev")))
        self.btn_next.setEnabled(bool(api.item_link(item, "next")))
        href = api.item_image_url(item)
        if href:
            self._pending_image = href
            is360 = (fov or 0) >= 360
            pix = self._image_cache.get(href)
            if pix is not None:
                self._image_cache.move_to_end(href)
                self.img.set_image(pix, heading, is360, fov)
            else:
                api.fetch(href, lambda data, error, h=href: self._on_image(
                    h, data, error, heading, is360, fov), prefer_cache=True)

    def show_error(self, error):
        self.meta.setText("Erreur lors de la lecture de la photo : {}".format(error))

    def show_message(self, text):
        """Pas de photo à afficher : message à la place de l'image."""
        self.img.set_message(text)
        self.meta.setText("")

    def _on_image(self, href, data, error, azimuth, is360, fov):
        if href != self._pending_image:
            return  # réponse d'une photo déjà dépassée
        if error or not data:
            self.img.set_message("Image indisponible ({})".format(error))
            return
        pix = QPixmap()
        if pix.loadFromData(data):
            self._image_cache[href] = pix
            while len(self._image_cache) > 3:
                self._image_cache.popitem(last=False)
            self.img.set_image(pix, azimuth, is360, fov)
        else:
            self.img.set_message("Format d'image non reconnu.")
