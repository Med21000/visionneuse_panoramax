# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Cédric COCHART
# SPDX-License-Identifier: GPL-2.0-or-later
"""Panneau ancré contenant la visionneuse Panoramax.

Deux modes :
- QtWebEngine disponible : la vraie visionneuse explore.panoramax.fr est
  embarquée, et ses changements d'URL (photo courante) sont suivis ;
- sinon : visualiseur intégré (image de la photo + navigation dans la
  séquence) et bouton pour ouvrir la visionneuse dans le navigateur.

Le panneau assemble : la barre d'outils, le choix de l'instance, la ligne du filtre
(filter_row.py), la zone des mesures (measure_panel.py), les options et la
visionneuse, web (scripts dans viewer_js.py) ou de secours (native_viewer.py). Il
relaie au plugin les demandes de l'utilisateur et les clics dans la photo.
"""

from datetime import datetime

from qgis.core import Qgis, QgsMessageLog, QgsSettings
from qgis.PyQt.QtCore import QStandardPaths, QTimer, QUrl, Qt, pyqtSignal
from qgis.PyQt.QtGui import QDesktopServices, QImage
from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDockWidget,
    QFileDialog,
    QInputDialog,
    QMenu,
    QToolButton,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

import json
import math
import os
import re

from . import api
from .calibration import Calibration
from .filter_row import FilterRow
from .measure_panel import MeasurePanel, calibration_text
from .native_viewer import NativeViewer
from .viewer_js import (
    ANISO_JS,
    CLICKS_JS,
    CLICKS_OFF_JS,
    CROSSHAIR_JS,
    LIVE_VIEW_JS,
    MARKS_JS,
    NAV_BLOCK_JS,
    WEBGL_ERROR_JS,
    WEBGL_TEST_JS,
)


def _is_blank(img):
    """Image vide ou d'une seule couleur (capture d'un rendu non accessible)."""
    if img is None or img.isNull() or img.width() < 2 or img.height() < 2:
        return True
    first = img.pixel(0, 0)
    for i in range(1, 11):
        for j in range(1, 11):
            if img.pixel(img.width() * i // 12, img.height() * j // 12) != first:
                return False
    return True


def capture_dir():
    """Dossier des captures (par défaut : Images/Panoramax)."""
    default = os.path.join(
        QStandardPaths.writableLocation(QStandardPaths.StandardLocation.PicturesLocation)
        or os.path.expanduser("~"), "Panoramax")
    path = QgsSettings().value("visionneuse_panoramax/capture_dir", "") or default
    os.makedirs(path, exist_ok=True)
    return path

try:  # QtWebEngine n'est pas livré avec toutes les installations de QGIS 4
    from qgis.PyQt.QtWebEngineWidgets import QWebEngineView

    HAS_WEBENGINE = True
except Exception:  # ImportError, RuntimeError selon les plateformes
    QWebEngineView = None
    HAS_WEBENGINE = False

try:
    from qgis.PyQt.QtWebEngineCore import QWebEngineSettings
except Exception:
    try:
        from PyQt6.QtWebEngineCore import QWebEngineSettings
    except Exception:
        QWebEngineSettings = None

# Résultat du test WebGL pour la session (None = pas encore testé)
WEBGL_OK = None

NO_WEBENGINE_TEXT = (
    "QtWebEngine n'est pas disponible dans cette installation de QGIS : "
    "la visionneuse interactive s'ouvre dans le navigateur (bouton « Navigateur »), "
    "et la photo sélectionnée s'affiche ci-dessous.")
NO_WEBGL_TEXT = (
    "WebGL n'est pas disponible dans le moteur web de QGIS (pilote graphique non pris en "
    "charge) : visionneuse intégrée au plugin. La visionneuse complète reste accessible "
    "par le bouton « Navigateur ».")


class PanoramaxDock(QDockWidget):
    # Émis quand la photo affichée change : (id, lon, lat, cap ou None)
    pictureChanged = pyqtSignal(str, float, float, object)
    # Demandes de l'utilisateur relayées au plugin
    pickToolRequested = pyqtSignal()
    # Message pour la barre de QGIS : (texte, niveau 0 info / 1 avertissement / 3 succès, chemin)
    message = pyqtSignal(str, int, str)
    # Émis après un changement d'instance (api.set_instance déjà appliqué)
    instanceChanged = pyqtSignal()
    # Orientation de la vue : (cap absolu en degrés, ouverture horizontale en degrés)
    viewChanged = pyqtSignal(float, float)
    # Triangulation : mode activé/désactivé, visée (dict), enregistrement, effacement
    measureToggled = pyqtSignal(bool)
    aimRequested = pyqtSignal(object)
    measureSaveRequested = pyqtSignal()
    measureClearRequested = pyqtSignal()
    measureClearAllRequested = pyqtSignal()  # efface toutes les mesures conservées à l'écran
    measureModeChanged = pyqtSignal(str)
    photoClicked = pyqtSignal(object)  # clic de mesure : dict pic, lon, lat, yaw, elev, axis
    cameraHeightChanged = pyqtSignal(float)
    # Mesure libre 3D : surface choisie, façade à (re)définir, hauteur du plan horizontal
    surfaceChanged = pyqtSignal(str)
    facadeRequested = pyqtSignal()
    planeHeightChanged = pyqtSignal(float)
    referenceChanged = pyqtSignal()  # référence du calage de la hauteur de caméra modifiée
    terrainChanged = pyqtSignal()  # service IGN activé ou désactivé (voir use_ign)
    filterChanged = pyqtSignal(object)  # filtre des photos modifié (filters.PhotoFilter)

    def __init__(self, parent=None):
        super().__init__("Visionneuse Panoramax", parent)
        self.setObjectName("VisionneusePanoramaxDock")
        self.current_url = api.explore_url(lat=46.6, lon=2.4, zoom=5.5, focus="map")
        self._current_pic = None
        self._current_item = None
        self._img_reply = None
        self.web = None
        self._poll = None
        self._last_xyz = None
        self._live_ok = False  # True dès que la lecture directe de la visionneuse fonctionne
        self._view = None  # dernière vue connue : cap, inclinaison, zoom, champ vertical
        self._marks = []  # mesures affichées dans la visionneuse (voir set_measure_marks)
        self.calibration = Calibration()  # inclinaison des photos et cap des séquences (session)

        root = QWidget(self)
        layout = QVBoxLayout(root)
        self._root, self._layout = root, layout
        layout.setContentsMargins(4, 4, 4, 4)

        # Barre d'outils du panneau
        bar = QHBoxLayout()
        self.btn_pick = QPushButton("Choisir sur la carte")
        self.btn_pick.setToolTip("Cliquez ensuite sur la carte QGIS pour ouvrir la photo la plus proche")
        self.btn_pick.clicked.connect(self.pickToolRequested)
        bar.addWidget(self.btn_pick)
        self.btn_filter = QToolButton()
        self.btn_filter.setCheckable(True)
        self.btn_filter.setToolTip("Filtrer les photos du filaire et du clic sur la carte : date, type de caméra, "
                                   "précision GPS, producteur")
        self.btn_filter.toggled.connect(lambda on: self.filter_row.setVisible(on))
        bar.addWidget(self.btn_filter)
        bar.addStretch(1)
        # Capture Full HD de la vue affichée (menu : dossier de destination)
        self.btn_capture = QToolButton()
        self.btn_capture.setText("📷 Capture HD")
        self.btn_capture.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.btn_capture.setToolTip("Enregistrer la vue affichée en image Full HD (1920 × 1080)")
        self.btn_capture.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        self.btn_capture.clicked.connect(self.capture)
        menu = QMenu(self.btn_capture)
        menu.addAction("Choisir le dossier des captures…", self.choose_capture_dir)
        menu.addAction("Ouvrir le dossier des captures",
                       lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(capture_dir())))
        self.btn_capture.setMenu(menu)
        bar.addWidget(self.btn_capture)
        btn_thumb = QPushButton("🖼 Capture vignette")
        btn_thumb.setToolTip("Enregistrer la visionneuse telle qu'affichée (avec les mesures) en image PNG, "
                             "copiée aussi dans le presse-papiers")
        btn_thumb.clicked.connect(self.capture_thumbnail)
        bar.addWidget(btn_thumb)
        self.btn_measure = QToolButton()
        self.btn_measure.setText("📐 Mesure")
        self.btn_measure.setCheckable(True)
        self.btn_measure.setToolTip("Triangulation, largeur ou hauteur d'un objet")
        self.btn_measure.toggled.connect(self._on_measure_toggled)
        bar.addWidget(self.btn_measure)
        btn_browser = QPushButton("Navigateur")
        btn_browser.setToolTip("Ouvrir la vue actuelle dans le navigateur")
        btn_browser.clicked.connect(self.open_in_browser)
        bar.addWidget(btn_browser)
        self.btn_compact = QToolButton()
        self.btn_compact.setAutoRaise(True)
        self.btn_compact.setCheckable(True)
        self.btn_compact.toggled.connect(self._set_compact)
        bar.addWidget(self.btn_compact)
        layout.addLayout(bar)
        # Libellés complets et réduits (mode compact) des boutons de la barre
        self._labels = {self.btn_pick: ("Choisir sur la carte", "📍"), self.btn_filter: ("Filtre", "🔎"),
                        self.btn_capture: ("📷 Capture HD", "📷"), btn_thumb: ("🖼 Capture vignette", "🖼"),
                        self.btn_measure: ("📐 Mesure", "📐"), btn_browser: ("Navigateur", "🌐")}

        self.instance_row = QWidget(root)
        inst = QHBoxLayout(self.instance_row)
        inst.setContentsMargins(0, 0, 0, 0)
        inst.addWidget(QLabel("Instance :"))
        self.cmb_instance = QComboBox()
        self.cmb_instance.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.cmb_instance.setToolTip("Serveur Panoramax interrogé (filaire, recherche, visionneuse)")
        inst.addWidget(self.cmb_instance, 1)
        btn_reload = QPushButton("↻")
        btn_reload.setToolTip("Recharger la liste des instances")
        btn_reload.setFixedWidth(30)
        btn_reload.clicked.connect(lambda: self.load_instances(force=True))
        inst.addWidget(btn_reload)
        layout.addWidget(self.instance_row)
        self._fill_instances([])
        self.cmb_instance.activated.connect(self._on_instance_activated)
        self.load_instances()
        self.filter_row = FilterRow(root)
        self.filter_row.setVisible(False)
        self.filter_row.changed.connect(self._on_filter_changed)
        layout.addWidget(self.filter_row)
        self._update_filter_button()

        self.measure_box = MeasurePanel(root)
        mp = self.measure_box
        self.spin_camera, self.spin_plane = mp.spin_camera, mp.spin_plane  # lus par le plugin
        mp.aimClicked.connect(self._aim)
        mp.saveClicked.connect(self.measureSaveRequested)
        mp.clearClicked.connect(self.measureClearRequested)
        mp.clearAllClicked.connect(self.measureClearAllRequested)
        mp.modeChanged.connect(self._on_measure_mode)
        mp.surfaceChanged.connect(self._on_surface)
        for signal in ("cameraHeightChanged", "facadeRequested", "planeHeightChanged", "referenceChanged",
                       "terrainChanged"):
            getattr(mp, signal).connect(getattr(self, signal))
        self.measure_box.setVisible(False)
        self._update_measure_widgets()
        layout.addWidget(self.measure_box)

        self.options_row = QWidget(root)
        opts = QHBoxLayout(self.options_row)
        opts.setContentsMargins(0, 0, 0, 0)
        self.chk_follow = QCheckBox("Centrer la carte QGIS sur la photo")
        self.chk_follow.setChecked(True)
        opts.addWidget(self.chk_follow)
        self.chk_aniso = QCheckBox("Filtrage anisotrope")
        self.chk_aniso.setToolTip("Filtrage anisotrope 4x des textures : atténue le crénelage et le scintillement "
                                  "des détails fins vus de loin, en gardant l'image nette. "
                                  "Peut rendre la navigation un peu moins fluide.")
        self.chk_aniso.setChecked(QgsSettings().value("visionneuse_panoramax/anisotropic", False, type=bool))
        self.chk_aniso.toggled.connect(self._on_aniso)
        opts.addWidget(self.chk_aniso)
        opts.addStretch(1)
        layout.addWidget(self.options_row)
        self.btn_compact.setChecked(QgsSettings().value("visionneuse_panoramax/compact", False, type=bool))
        self._set_compact(self.btn_compact.isChecked())

        if HAS_WEBENGINE and WEBGL_OK is not False:
            self.web = QWebEngineView(root)
            if QWebEngineSettings is not None:
                try:
                    st = self.web.settings()
                    st.setAttribute(QWebEngineSettings.WebAttribute.WebGLEnabled, True)
                    st.setAttribute(QWebEngineSettings.WebAttribute.Accelerated2dCanvasEnabled, True)
                except Exception:
                    pass
            self.web.urlChanged.connect(self._on_url_changed)
            self.web.loadFinished.connect(self._check_webgl)
            self.web.setUrl(QUrl(self.current_url))
            layout.addWidget(self.web, 1)
            # La visionneuse met à jour son URL (cap, zoom) sans forcément déclencher
            # urlChanged : on la relit régulièrement tant que le panneau est visible.
            self._poll = QTimer(self)
            self._poll.setInterval(100)
            self._poll.timeout.connect(self._poll_url)
            self._poll.start()
        else:
            text = NO_WEBGL_TEXT if HAS_WEBENGINE else NO_WEBENGINE_TEXT
            layout.addWidget(self._build_fallback(root, text), 1)

        self.setWidget(root)

    # ------------------------------------------------------------------
    # Mode de secours (sans QtWebEngine)
    # ------------------------------------------------------------------
    def _check_webgl(self, ok=True):
        """Après chargement : si WebGL est absent, bascule sur la visionneuse native."""
        if self.web is None or WEBGL_OK is False:
            return
        if WEBGL_OK is None:
            self.web.page().runJavaScript(WEBGL_TEST_JS, self._on_webgl_result)
        # La visionneuse s'initialise après le chargement : on relit la page un peu plus tard
        for delay in (2500, 6000):
            QTimer.singleShot(delay, self._check_viewer_error)

    def _check_viewer_error(self):
        if self.web is not None:
            self.web.page().runJavaScript(WEBGL_ERROR_JS,
                                          lambda found: self._on_webgl_result(not found) if found else None)

    def _on_webgl_result(self, result):
        global WEBGL_OK
        if self.web is None:
            return
        WEBGL_OK = bool(result)
        if not WEBGL_OK:
            self._switch_to_native()

    def _switch_to_native(self):
        if self._poll is not None:
            self._poll.stop()
        if self.web is not None:
            self._layout.removeWidget(self.web)
            self.web.deleteLater()
            self.web = None
        self._layout.addWidget(self._build_fallback(self._root, NO_WEBGL_TEXT), 1)
        if self._current_pic:
            api.get_item(self._current_pic, self._on_item)

    def _build_fallback(self, parent, text):
        self.native = NativeViewer(text, parent)
        self.img = self.native.img
        self.img.viewChanged.connect(self.viewChanged)
        self.img.crosshair = self.btn_measure.isChecked() and self.measure_mode() == "tri"
        self.img.clicked.connect(self._on_native_click)
        self.native.pictureRequested.connect(lambda pid: api.get_item(pid, self._on_item))
        return self.native

    # ------------------------------------------------------------------
    # Mesures
    # ------------------------------------------------------------------
    def measure_mode(self):
        return self.measure_box.measure_mode()

    def camera_height(self):
        return self.measure_box.camera_height()

    def use_ign(self):
        return self.measure_box.use_ign()

    def surface(self):
        """Surface de la mesure libre 3D (voir measure_tool.FREE_SURFACES)."""
        return self.measure_box.surface()

    def reference(self):
        """Référence du calage de la hauteur de caméra : ("ground" | "height", longueur en m)."""
        return self.measure_box.reference()

    def _set_label(self, button, full, short):
        """Libellé d'un bouton de la barre, réduit à son icône en mode compact."""
        self._labels[button] = (full, short)
        button.setText(short if self.btn_compact.isChecked() else full)

    def _set_compact(self, compact):
        """Mode compact : boutons réduits à leur icône, lignes Instance et Options masquées."""
        QgsSettings().setValue("visionneuse_panoramax/compact", bool(compact))
        for button, (full, short) in self._labels.items():
            button.setText(short if compact else full)
            if isinstance(button, QPushButton):  # largeur minimale du style trop grande pour une icône
                button.setMaximumWidth(button.fontMetrics().horizontalAdvance(short) + 24 if compact else 16777215)
        self.instance_row.setVisible(not compact)
        self.options_row.setVisible(not compact)
        self.measure_box.show_status(not compact)
        self._update_measure_tooltip()
        self.btn_compact.setArrowType(Qt.ArrowType.DownArrow if compact else Qt.ArrowType.UpArrow)
        self.btn_compact.setToolTip("Déployer le panneau" if compact else
                                    "Compacter le panneau : boutons réduits à leur icône, instance, options et "
                                    "aide de la mesure masquées")

    def _on_aniso(self, checked):
        QgsSettings().setValue("visionneuse_panoramax/anisotropic", bool(checked))
        if self.web is not None:
            self.web.page().runJavaScript(ANISO_JS.replace("__ON__", "true" if checked else "false"))

    def _nav_js(self, active):
        """Navigation de la visionneuse pendant une mesure : clic et flèches bloqués pour les
        mesures par clics ; curseur au sol masqué dans tous les modes (il cache le réticule)."""
        clicks = active and self.measure_mode() != "tri"
        return NAV_BLOCK_JS.replace("__ON__", "true" if clicks else "false").replace(
            "__CURSOR__", "true" if active else "false")

    def _on_measure_toggled(self, checked):
        self.measure_box.setVisible(checked)
        self._update_measure_widgets()
        self.measureToggled.emit(checked)

    def _on_measure_mode(self, mode):
        self._update_measure_widgets()
        self.measureModeChanged.emit(mode)

    def _on_surface(self, surface):
        self._update_measure_widgets()
        self.surfaceChanged.emit(surface)

    # Filtre des photos ---------------------------------------------------------
    def photo_filter(self):
        """Filtre des photos réglé dans le panneau (filters.PhotoFilter)."""
        return self.filter_row.photo_filter()

    def _on_filter_changed(self, flt):
        self._update_filter_button()
        self.filterChanged.emit(flt)

    def _update_filter_button(self):
        flt = self.photo_filter()
        active = flt.active()
        self._set_label(self.btn_filter, "Filtre ●" if active else "Filtre", "🔎●" if active else "🔎")
        self.btn_filter.setToolTip(("Filtre actif : {}.".format(flt.describe()) if active else
                                    "Filtrer les photos du filaire et du clic sur la carte : date, type de caméra, "
                                    "précision GPS, producteur"))

    def _update_measure_widgets(self):
        """Boutons du mode choisi ; réticule (triangulation) ou clics (mesures au sol)."""
        mode = self.measure_mode()
        active = self.btn_measure.isChecked()
        self.measure_box.update_widgets()
        self.update_calibration_label()
        crosshair = active and mode == "tri"
        if self.web is not None:
            page = self.web.page()
            page.runJavaScript(CROSSHAIR_JS.replace("__SHOW__", "true" if crosshair else "false"))
            if not active or mode == "tri":
                page.runJavaScript(CLICKS_OFF_JS)
            page.runJavaScript(self._nav_js(active))
        elif hasattr(self, "img"):
            self.img.crosshair = crosshair
            self.img.set_measuring(active and mode != "tri")

    def _marks_js(self):
        return MARKS_JS.replace("__STATE__", json.dumps({"measures": self._marks}))

    def set_measure_marks(self, marks):
        """Mesures affichées dans la visionneuse, chacune sur la photo où elle a été prise :
        liste de {"points": [dicts pic, yaw, pitch (position dans la photo, radians),
        abs_yaw, elev (degrés)], "label": texte, "beside": étiquette à côté du trait}."""
        self._marks = list(marks or [])
        if self.web is not None:
            self.web.page().runJavaScript(self._marks_js())
        elif hasattr(self, "img"):
            measures = []
            for m in self._marks:
                pts = [p for p in m.get("points", []) if p.get("pic") == self._current_pic]
                measures.append({"pts": [(p["abs_yaw"], p["elev"]) for p in pts],
                                 "sel": [i for i, p in enumerate(pts) if p.get("sel")],
                                 "label": m.get("label", ""), "beside": m.get("beside", False)})
            self.img.set_marks(measures)

    def current_ids(self):
        """(photo, séquence) affichées, ou (None, None)."""
        item = self._current_item
        if not item or item.get("id") != self._current_pic:
            return None, None
        return item.get("id"), item.get("collection")

    def update_calibration_label(self):
        """Calage de la photo et de la séquence affichées, rappelé sous les mesures."""
        pic, sequence = self.current_ids()
        lon, lat = self._current_position()
        self.measure_box.set_calibration_text(calibration_text(self.calibration, pic, sequence, lon, lat))

    def _current_position(self):
        """(lon, lat) GPS de la photo affichée, ou (None, None)."""
        try:
            lon, lat = self._current_item["geometry"]["coordinates"][:2]
            return float(lon), float(lat)
        except (KeyError, TypeError, ValueError, IndexError):
            return None, None

    def _recalibrated(self, sequence, lon, lat, item):
        """Position de la photo (recalée si possible) et sa précision en m."""
        fixed = self.calibration.position(sequence, lon, lat)
        if fixed:
            return fixed
        return lon, lat, api.item_accuracy(item)

    def _clicked_direction(self, yaw, elev, pic=None, pos=None):
        """Émet photoClicked si la photo cliquée est bien la photo courante.

        yaw/elev : direction brute dans la photo ; le clic émis porte la direction
        corrigée du calage (inclinaison, cap) et garde la brute dans "raw"."""
        item = self._current_item
        if not item or item.get("id") != self._current_pic or (pic and pic != self._current_pic):
            self.message.emit("Photo en cours de chargement : cliquez à nouveau.", 1, "")
            return
        try:
            lon, lat = item["geometry"]["coordinates"][:2]
        except (KeyError, TypeError, ValueError):
            return
        axis, axis_source = api.sequence_axis(item), "séquence"
        if axis is None:
            axis, axis_source = api.item_heading(item), "orientation de la photo"
        pic_id, sequence = item.get("id", ""), item.get("collection")
        lon, lat = float(lon), float(lat)
        cyaw, celev = self.calibration.correct(pic_id, sequence, float(yaw), float(elev), lon, lat)
        clon, clat, accuracy = self._recalibrated(sequence, lon, lat, item)
        self.photoClicked.emit({"pic": pic_id, "lon": clon, "lat": clat,
                                "gps": [lon, lat], "gps_accuracy": api.item_accuracy(item),
                                "yaw": cyaw, "elev": celev, "raw": [float(yaw) % 360, float(elev)],
                                "sequence": sequence, "accuracy": accuracy,
                                "precise": api.precise_heading(item) is not None,
                                "axis": axis, "axis_source": axis_source, "pos": pos})

    def _on_native_click(self, yaw, elev):
        if self.btn_measure.isChecked() and self.measure_mode() != "tri":
            self._clicked_direction(yaw, elev)

    def _on_viewer_clicks(self, value):
        from .ground import view_direction

        try:
            clicks = json.loads(value) if isinstance(value, str) and value else []
        except ValueError:
            return
        item = self._current_item or {}
        azimuth = api.precise_heading(item) or api.item_heading(item) or 0.0
        for c in clicks:
            try:
                vfov = float(c.get("vfov") or 0)
                if vfov <= 0:  # champ vertical déduit du zoom et des proportions de la vue
                    hfov = math.radians(self._zoom_to_fov(float(c.get("z") or 0)))
                    vfov = math.degrees(2 * math.atan(math.tan(hfov / 2) * c["h"] / max(c["w"], 1)))
                yaw, elev = view_direction(float(c["x"]), float(c["y"]), vfov,
                                           float(c["dx"]), float(c["dy"]), float(c["h"]))
            except (KeyError, TypeError, ValueError, ZeroDivisionError):
                continue
            pos = None
            if c.get("pyaw") is not None and c.get("ppitch") is not None:
                pos = [float(c["pyaw"]), float(c["ppitch"])]
                # Direction exacte calculée par la visionneuse (lancer de rayon sur la sphère),
                # dans le repère de la photo : cap = angle dans la photo + cap de la photo (EXIF
                # précis si disponible, sinon view:azimuth)
                calc_yaw, calc_elev = yaw, elev
                yaw = (math.degrees(pos[0]) + azimuth) % 360
                elev = math.degrees(pos[1])
                QgsMessageLog.logMessage(
                    "Clic : visionneuse cap {:.2f}° élév. {:.2f}° | calcul écran cap {:.2f}° élév. {:.2f}° "
                    "(écart {:.2f}° / {:.2f}°) · champ vertical {:.1f}° · vue {:.0f}×{:.0f} px".format(
                        yaw, elev, calc_yaw, calc_elev, (calc_yaw - yaw + 540) % 360 - 180, calc_elev - elev,
                        vfov, float(c.get("w") or 0), float(c.get("h") or 0)),
                    "Panoramax", Qgis.MessageLevel.Info)
            self._clicked_direction(yaw, elev, c.get("pic") or None, pos)

    def set_measure_status(self, text):
        self.measure_box.set_status(text)
        self._update_measure_tooltip()

    def _update_measure_tooltip(self):
        """En mode compact, la ligne d'aide de la mesure passe dans la bulle d'aide du bouton."""
        text = self.measure_box.status() if self.btn_compact.isChecked() else ""
        self.btn_measure.setToolTip(text or "Triangulation, largeur ou hauteur d'un objet")

    def current_sighting(self):
        """Visée depuis la photo affichée (centre de la vue), ou None."""
        item = self._current_item
        if not item or item.get("id") != self._current_pic:
            return None  # fiche de la photo pas encore reçue
        try:
            lon, lat = item["geometry"]["coordinates"][:2]
        except (KeyError, TypeError, ValueError):
            return None
        precise = api.precise_heading(item)
        pitch = 0.0  # visionneuse de secours : centre de la vue sur l'horizon
        if self.web is None:
            if self.img.pixmap is None:
                return None  # image pas encore affichée : cap inconnu
            heading = self.img.current_view()[0]  # image déjà orientée sur le cap précis
        elif self._view is not None:
            heading, pitch = self._view["heading"], float(self._view.get("pitch") or 0.0)
            rounded = api.item_heading(item)
            if precise is not None and rounded is not None:
                heading += precise - rounded  # la visionneuse web s'oriente sur view:azimuth (arrondi)
        else:
            heading = precise if precise is not None else api.item_heading(item)
        if heading is None:
            return None
        pic_id, sequence = item.get("id", ""), item.get("collection")
        lon, lat = float(lon), float(lat)
        heading, _ = self.calibration.correct(pic_id, sequence, float(heading), pitch, lon, lat)
        clon, clat, accuracy = self._recalibrated(sequence, lon, lat, item)
        return {"pic": pic_id, "lon": clon, "lat": clat, "heading": heading,
                "accuracy": accuracy, "precise": precise is not None,
                "heading_error": self.calibration.heading_error(sequence, lon, lat)}

    def _aim(self):
        sighting = self.current_sighting()
        if sighting is None:
            self.message.emit("Aucune photo affichée (ou orientation inconnue) : impossible de viser.", 1, "")
            return
        self.aimRequested.emit(sighting)

    # ------------------------------------------------------------------
    # Capture Full HD
    # ------------------------------------------------------------------
    def choose_capture_dir(self):
        path = QFileDialog.getExistingDirectory(self, "Dossier des captures Panoramax", capture_dir())
        if path:
            QgsSettings().setValue("visionneuse_panoramax/capture_dir", path)

    def _current_view(self, item):
        """(cap, inclinaison, champ vertical) de la vue affichée."""
        ratio = 1080.0 / 1920.0
        if self.web is None:
            heading, hfov = self.img.current_view()
            pitch = 0.0
        elif self._view is not None:
            heading, pitch = self._view["heading"], self._view["pitch"]
            if self._view.get("vfov"):
                return heading, pitch, self._view["vfov"]
            hfov = self._zoom_to_fov(self._view["zoom"])
        else:
            heading, pitch, hfov = api.item_heading(item) or 0.0, 0.0, 90.0
        vfov = math.degrees(2 * math.atan(math.tan(math.radians(hfov) / 2) * ratio))
        return heading, pitch, vfov

    def capture(self):
        item = self._current_item
        if not item:
            self.message.emit("Aucune photo affichée : choisissez d'abord une photo.", 1, "")
            return
        href = ((item.get("assets") or {}).get("hd") or {}).get("href") or api.item_image_url(item)
        if not href:
            self.message.emit("Pas d'image haute définition pour cette photo.", 1, "")
            return
        view = self._current_view(item)
        self.btn_capture.setEnabled(False)
        self._set_label(self.btn_capture, "📷 Téléchargement…", "⏳")
        api.fetch(href, lambda data, error: self._on_capture_image(item, view, data, error))

    def capture_thumbnail(self):
        """Capture d'écran de la visionneuse, mesures et réticule compris."""
        from qgis.PyQt.QtWidgets import QApplication

        widget = self.web if self.web is not None else getattr(self, "img", None)
        item = self._current_item
        if widget is None or not item:
            self.message.emit("Aucune photo affichée : choisissez d'abord une photo.", 1, "")
            return
        img = widget.grab().toImage()
        if _is_blank(img):
            # Rendu web non capturable directement : on tente une copie de l'écran
            screen = widget.screen() if hasattr(widget, "screen") else QApplication.primaryScreen()
            top_left = widget.mapToGlobal(widget.rect().topLeft()) - screen.geometry().topLeft()
            img = screen.grabWindow(0, top_left.x(), top_left.y(), widget.width(), widget.height()).toImage()
        if _is_blank(img):
            self.message.emit("Capture de la visionneuse impossible sur cet affichage (Wayland ?) : "
                              "utilisez « Capture HD ».", 1, "")
            return
        img = img.convertToFormat(QImage.Format.Format_RGB32)
        try:
            from . import capture
            heading = self._view["heading"] if self._view else api.item_heading(item)
            img = capture.add_caption(img, capture.caption_for(item, heading))
        except ImportError:  # numpy absent : capture sans cartouche
            pass
        props = item.get("properties", {}) or {}
        date = re.sub(r"[^0-9]", "", (props.get("datetime") or ""))[:8] or "sansdate"
        name = "panoramax_vignette_{}_{}_{}.png".format(
            date, (item.get("id") or "")[:8], datetime.now().strftime("%H%M%S"))
        path = os.path.join(capture_dir(), name)
        if img.save(path, "PNG"):
            QApplication.clipboard().setImage(img)
            self.message.emit("Vignette enregistrée et copiée : {}".format(path), 3, path)
        else:
            self.message.emit("Impossible d'écrire {}".format(path), 1, "")

    def _on_capture_image(self, item, view, data, error):
        from qgis.PyQt.QtWidgets import QApplication

        from . import capture

        self.btn_capture.setEnabled(True)
        self._set_label(self.btn_capture, "📷 Capture HD", "📷")
        if error or not data:
            self.message.emit("Téléchargement de la photo impossible : {}".format(error), 1, "")
            return
        src = QImage()
        if not src.loadFromData(data):
            self.message.emit("Format d'image non reconnu.", 1, "")
            return
        heading, pitch, vfov = view
        fov = self._item_fov(item)
        is360 = (fov or 0) >= 360 or src.width() >= 1.9 * src.height()
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            out = capture.render_view(capture.qimage_to_array(src), api.item_heading(item) or 0.0,
                                      is360, heading, pitch, vfov, fov if not is360 else None)
            img = capture.add_caption(capture.array_to_qimage(out), capture.caption_for(item, heading))
            props = item.get("properties", {}) or {}
            date = re.sub(r"[^0-9]", "", (props.get("datetime") or ""))[:8] or "sansdate"
            name = "panoramax_{}_cap{:03.0f}_{}.jpg".format(date, heading % 360, (item.get("id") or "")[:8])
            path = os.path.join(capture_dir(), name)
            ok = img.save(path, "JPG", 92)
        except Exception as exc:  # numpy absent, mémoire…
            ok, path = False, ""
            self.message.emit("Capture impossible : {}".format(exc), 1, "")
            return
        finally:
            QApplication.restoreOverrideCursor()
        if ok:
            self.message.emit("Capture enregistrée : {}".format(path), 3, path)
        else:
            self.message.emit("Impossible d'écrire {}".format(path), 1, "")

    # ------------------------------------------------------------------
    # Choix de l'instance
    # ------------------------------------------------------------------
    OTHER = "__other__"

    def load_instances(self, force=False):
        def done(data, error):
            if error:
                self.cmb_instance.setToolTip("Liste des instances indisponible : {}".format(error))
                return
            self._fill_instances(api.parse_instances(data))

        api.get_instances(done, force=bool(force))

    def _fill_instances(self, instances):
        settings = QgsSettings()
        saved_url = settings.value("visionneuse_panoramax/instance_url", "") or ""
        saved_name = settings.value("visionneuse_panoramax/instance_name", "") or ""

        self.cmb_instance.blockSignals(True)
        self.cmb_instance.clear()
        self.cmb_instance.addItem("Toutes les instances (méta-catalogue)", ("meta", ""))
        known = set()
        for name, url in instances:
            self.cmb_instance.addItem("{}  –  {}".format(name, url.replace("https://", "")), (name, url))
            known.add(url.rstrip("/"))
        if saved_url and saved_url.rstrip("/") not in known:
            # Instance saisie à la main lors d'une session précédente
            self.cmb_instance.addItem("{}  –  {}".format(saved_name, saved_url), (saved_name, saved_url))
        self.cmb_instance.addItem("Autre instance (saisir l'adresse)…", (self.OTHER, ""))

        index = 0
        for i in range(self.cmb_instance.count()):
            data = self.cmb_instance.itemData(i)
            if data and data[1] and data[1].rstrip("/") == saved_url.rstrip("/"):
                index = i
        self.cmb_instance.setCurrentIndex(index)
        self._last_index = index
        self.cmb_instance.blockSignals(False)

    def _on_instance_activated(self, index):
        name, url = self.cmb_instance.itemData(index) or ("meta", "")
        if name == self.OTHER:
            url, ok = QInputDialog.getText(
                self, "Autre instance Panoramax",
                "Adresse du site de l'instance (ex. https://panoramax.ign.fr) :")
            url = (url or "").strip()
            if not ok or not url.startswith("http"):
                self.cmb_instance.setCurrentIndex(self._last_index)
                return
            name = url.split("//", 1)[-1].split("/", 1)[0]
            self.cmb_instance.insertItem(index, "{}  –  {}".format(name, url), (name, url))
            self.cmb_instance.setCurrentIndex(index)
        self._last_index = self.cmb_instance.currentIndex()

        settings = QgsSettings()
        settings.setValue("visionneuse_panoramax/instance_name", "" if name == "meta" else name)
        settings.setValue("visionneuse_panoramax/instance_url", url)
        api.set_instance(name, url)

        # Recharge la visionneuse sur la même zone, servie par la nouvelle instance
        view = api.parse_viewer_url(self.web.url().toString() if self.web is not None else self.current_url)
        z, lat, lon = view.get("map") or (5.5, 46.6, 2.4)
        self._current_pic = None
        self.current_url = api.explore_url(lat=lat, lon=lon, zoom=z, focus="map")
        if self.web is not None:
            self.web.setUrl(QUrl(self.current_url))
        self.instanceChanged.emit()

    # ------------------------------------------------------------------
    # API publique
    # ------------------------------------------------------------------
    def show_picture(self, pic_id, lon, lat):
        """Affiche une photo (appelé après un clic sur la carte)."""
        self.current_url = api.explore_url(pic_id=pic_id, lat=lat, lon=lon)
        if self.web is not None:
            self.web.setUrl(QUrl(self.current_url))  # _on_url_changed suivra
        else:
            api.get_item(pic_id, self._on_item)

    def show_location(self, lon, lat):
        """Aucune photo à proximité : on centre la visionneuse sur le point."""
        self.current_url = api.explore_url(lat=lat, lon=lon, zoom=17, focus="map")
        if self.web is not None:
            self.web.setUrl(QUrl(self.current_url))
        else:
            self.native.show_message("Aucune photo Panoramax à proximité de ce point.")

    def open_in_browser(self):
        if self.web is not None:
            self.current_url = self.web.url().toString()
        QDesktopServices.openUrl(QUrl(self.current_url))

    # ------------------------------------------------------------------
    # Synchronisation
    # ------------------------------------------------------------------
    def _poll_url(self):
        if self.isVisible() and self.web is not None:
            page = self.web.page()
            page.runJavaScript(LIVE_VIEW_JS, self._on_live_view)
            # Filtrage anisotrope : une lecture sur dix (toutes les secondes) suffit. Décoché,
            # rien à faire : les nouvelles tuiles gardent le filtre d'origine.
            self._aniso_tick = (getattr(self, "_aniso_tick", 0) + 1) % 10
            if self._aniso_tick == 0 and self.chk_aniso.isChecked():
                page.runJavaScript(ANISO_JS.replace("__ON__", "true"))
            if self.btn_measure.isChecked():  # la page peut avoir été rechargée
                # Triangulation : on change de photo pour viser sous un autre angle, la
                # navigation reste libre. Mesures par clics : un clic ne doit pas changer de photo.
                page.runJavaScript(self._nav_js(True))
                if self.measure_mode() == "tri":
                    page.runJavaScript(CROSSHAIR_JS.replace("__SHOW__", "true"))
                else:
                    page.runJavaScript(CLICKS_JS, self._on_viewer_clicks)
            if self._marks:  # mesures conservées à l'écran, aussi hors mode mesure
                page.runJavaScript(self._marks_js())
            page.runJavaScript("window.location.href", self._on_js_href)

    def _on_live_view(self, value):
        """État lu directement dans la visionneuse : mise à jour immédiate du curseur."""
        if not isinstance(value, str) or not value:
            return
        try:
            pic, x, y, z, vfov = (value.split("|") + [""])[:5]
            xyz = (float(x) % 360, float(y), float(z))
            self._view = {"heading": xyz[0], "pitch": xyz[1], "zoom": xyz[2],
                          "vfov": float(vfov) if vfov else None}
        except ValueError:
            return
        self._live_ok = True
        if pic and pic != self._current_pic:
            self._current_pic = pic
            self._last_xyz = None
            api.get_item(pic, self._on_item)
        if pic and xyz != self._last_xyz:
            self._last_xyz = xyz
            self.viewChanged.emit(xyz[0], self._zoom_to_fov(xyz[2]))

    def _on_js_href(self, href):
        if isinstance(href, str) and href and href != self.current_url:
            self._process_url(href)

    def _on_url_changed(self, qurl):
        self._process_url(qurl.toString())

    def _process_url(self, url):
        self.current_url = url
        if self._live_ok:
            return  # l'URL est en retard sur l'état lu directement : on l'ignore
        state = api.parse_viewer_url(url)
        pic = state.get("pic")
        if pic and pic != self._current_pic:
            self._current_pic = pic
            self._last_xyz = None
            api.get_item(pic, self._on_item)
        xyz = state.get("xyz")
        if xyz and not self._live_ok:
            self._view = {"heading": xyz[0] % 360, "pitch": xyz[1], "zoom": xyz[2], "vfov": None}
        if pic and xyz and xyz != self._last_xyz and not self._live_ok:
            self._last_xyz = xyz
            self.viewChanged.emit(xyz[0] % 360, self._zoom_to_fov(xyz[2]))

    @staticmethod
    def _zoom_to_fov(zoom):
        # Zoom de la visionneuse : 0 = vue large (~90°), 100 = zoom max (~30°)
        z = max(0.0, min(100.0, zoom))
        return 90.0 - 0.6 * z

    @staticmethod
    def _item_fov(item):
        props = item.get("properties", {}) or {}
        try:
            fov = float((props.get("pers:interior_orientation") or {}).get("field_of_view"))
        except (TypeError, ValueError):
            return None
        return fov if fov > 0 else None

    def _on_item(self, item, error):
        if error or not item:
            if self.web is None:
                self.native.show_error(error)
            return
        try:
            lon, lat = item["geometry"]["coordinates"][:2]
        except (KeyError, TypeError, ValueError):
            return
        pic_id = item.get("id", "")
        heading = api.item_heading(item)
        self._current_item = item
        self._current_pic = pic_id
        self.filter_row.show_item(item)
        self.pictureChanged.emit(pic_id, float(lon), float(lat), heading)
        self.update_calibration_label()
        if self.web is None and hasattr(self, "img"):
            self.set_measure_marks(self._marks)
        item_fov = self._item_fov(item)

        if self.web is not None:
            # En attendant que la visionneuse publie son orientation (xyz)
            if heading is not None and self._last_xyz is None:
                self.viewChanged.emit(heading % 360, item_fov if item_fov and item_fov < 360 else 90.0)
        else:
            self.current_url = api.explore_url(pic_id=pic_id, lat=lat, lon=lon)
            heading = api.precise_heading(item) or heading  # clics et visées au cap précis
            self.native.show_item(item, heading, item_fov)
