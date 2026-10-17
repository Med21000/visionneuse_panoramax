# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Cédric COCHART
# SPDX-License-Identifier: GPL-2.0-or-later
"""Classe principale du plugin Visionneuse Panoramax."""

import json
import math
import os

from qgis.core import (
    Qgis,
    QgsApplication,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsCsException,
    QgsPointXY,
    QgsProject,
    QgsRectangle,
    QgsSettings,
    QgsUnitTypes,
)
from qgis.gui import QgsMapToolEmitPoint, QgsMapToolPan, QgsVertexMarker
from qgis.PyQt.QtCore import QUrl, Qt, pyqtSignal
from qgis.PyQt.QtGui import QAction, QColor, QDesktopServices, QIcon
from qgis.PyQt.QtWidgets import QPushButton

from . import api, filters, layers, qgis_host
from .cursor import ViewCursor
from .core.calibration import Calibration
from .core.session import CALIBRATION_MODES, GROUND_MODES, Session
from .measure import CalibrationTool, PointsItem, Triangulator, save_points
from .measure_tool import MeasureTool, save_links
from .viewer_dock import PanoramaxDock

WGS84 = QgsCoordinateReferenceSystem("EPSG:4326")
MENU = "&Panoramax"
PROJECT_SCOPE = "visionneuse_panoramax"  # section du plugin dans le projet QGIS
CALIBRATION_KEY = "calibration"  # calage enregistré dans les projets par les versions précédentes
CALIBRATION_SETTING = "visionneuse_panoramax/calibration"  # calage conservé dans le profil QGIS
AUTO_TILT_FILE = os.path.join("visionneuse_panoramax", "inclinaisons.json")  # dans le dossier du profil


def auto_tilt_path():
    """Fichier des inclinaisons automatiques, dans le dossier du profil QGIS."""
    return os.path.join(QgsApplication.qgisSettingsDirPath(), AUTO_TILT_FILE)


class LandmarkTool(QgsMapToolPan):
    """Pointage d'un repère sur la carte, carte libre : glisser déplace la carte
    (molette pour zoomer), un clic sans glisser pointe le repère, Échap annule."""

    pointed = pyqtSignal(object)  # QgsPointXY, dans le SCR de la carte
    cancelled = pyqtSignal()
    CLICK_TOLERANCE = 4  # pixels : au-delà, c'est un glisser

    def __init__(self, canvas):
        super().__init__(canvas)
        self._press = None
        self.setCursor(Qt.CursorShape.CrossCursor)

    def canvasPressEvent(self, event):  # noqa: N802
        self._press = event.pos() if event.button() == Qt.MouseButton.LeftButton else None
        super().canvasPressEvent(event)

    def canvasReleaseEvent(self, event):  # noqa: N802
        press, self._press = self._press, None
        if (press is not None and event.button() == Qt.MouseButton.LeftButton
                and (event.pos() - press).manhattanLength() <= self.CLICK_TOLERANCE):
            self.pointed.emit(self.toMapCoordinates(event.pos()))
            return  # pas de recentrage de l'outil main sur un simple clic
        super().canvasReleaseEvent(event)

    def keyPressEvent(self, event):  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self.cancelled.emit()
            return
        super().keyPressEvent(event)


class MeasureMapTool(QgsMapToolPan):
    """Outil de la carte pendant les mesures : glisser le point d'une mesure le recale à
    l'endroit lâché ; glisser ailleurs déplace la carte ; un simple clic ailleurs ouvre la
    photo la plus proche."""

    clicked = pyqtSignal(object)  # QgsPointXY, dans le SCR de la carte
    CLICK_TOLERANCE = 4  # pixels : au-delà, c'est un glisser

    def __init__(self, canvas, find, move):
        super().__init__(canvas)
        self.find = find  # (QgsPointXY) -> poignée ou None
        self.move = move  # (poignée, QgsPointXY) -> None
        self._handle = None
        self._press = None
        self._marker = None

    def canvasPressEvent(self, event):  # noqa: N802
        self._press = event.pos() if event.button() == Qt.MouseButton.LeftButton else None
        if self._press is not None:
            self._handle = self.find(event.mapPoint())
            if self._handle is not None:
                self._marker = QgsVertexMarker(self.canvas())
                self._marker.setIconType(QgsVertexMarker.IconType.ICON_CIRCLE)
                self._marker.setColor(QColor(229, 57, 53))
                self._marker.setIconSize(14)
                self._marker.setPenWidth(3)
                self._marker.setCenter(event.mapPoint())
                return
        super().canvasPressEvent(event)

    def canvasMoveEvent(self, event):  # noqa: N802
        if self._handle is not None:
            self._marker.setCenter(event.mapPoint())
            return
        if self._press is None:  # survol : main sur une poignée
            hover = self.find(event.mapPoint()) is not None
            self.canvas().setCursor(Qt.CursorShape.PointingHandCursor if hover else Qt.CursorShape.OpenHandCursor)
        super().canvasMoveEvent(event)

    def canvasReleaseEvent(self, event):  # noqa: N802
        press, self._press = self._press, None
        handle, self._handle = self._handle, None
        if handle is not None:
            self.canvas().scene().removeItem(self._marker)
            self._marker = None
            if press is not None and (event.pos() - press).manhattanLength() > self.CLICK_TOLERANCE:
                self.move(handle, event.mapPoint())
            return
        if (press is not None and event.button() == Qt.MouseButton.LeftButton
                and (event.pos() - press).manhattanLength() <= self.CLICK_TOLERANCE):
            self.clicked.emit(event.mapPoint())
            return  # pas de recentrage de l'outil main sur un simple clic
        super().canvasReleaseEvent(event)


class PanoramaxPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.canvas = iface.mapCanvas()
        self.plugin_dir = os.path.dirname(__file__)
        self.actions = []
        self.dock = None
        self.tool = None
        self.cursor = None
        self.session = None  # session de mesure du cœur (core/session.py), créée avec le panneau
        self.points_item = None  # points triangulés gardés, distances, droite de mesure (measure.PointsItem)
        self._saved_points, self._saved_links = set(), set()  # déjà enregistrés dans les couches
        self._framed = None  # points cadrés sur la carte en dernier (voir _frame)
        self.edit_tool = None  # outil de la carte pendant les mesures (voir MeasureMapTool)
        self._before_edit = None  # outil de la carte avant les mesures
        qgis_host.install()  # réseau, altitude NGF, photos et tâches de QGIS pour le cœur de calcul
        self.calibration = Calibration()  # conservé dans le profil QGIS
        self._saved_auto = None  # inclinaisons automatiques telles qu'enregistrées (texte)
        self.calibration.changed = self._save_calibration
        self.landmark_tool = None  # clic du repère sur la carte (calage du cap)
        self._previous_tool = None
        self._unloaded = False  # plugin déchargé : les réponses tardives sont ignorées

    # ------------------------------------------------------------------
    # Cycle de vie
    # ------------------------------------------------------------------
    def initGui(self):  # noqa: N802
        icon = QIcon(os.path.join(self.plugin_dir, "icon.png"))

        # Instance choisie lors d'une session précédente
        settings = QgsSettings()
        api.set_instance(settings.value("visionneuse_panoramax/instance_name", "") or None,
                         settings.value("visionneuse_panoramax/instance_url", "") or None)

        self.act_viewer = self._add_action(icon, "Visionneuse Panoramax", self.toggle_viewer, checkable=True)
        self.act_pick = self._add_action(icon, "Ouvrir la photo la plus proche (clic sur la carte)",
                                         self.activate_pick_tool, checkable=True, toolbar=False)
        self._add_action(icon, "Ajouter le filaire Panoramax (tuiles vectorielles)", self.add_tile_layer,
                         toolbar=False)

        self.tool = QgsMapToolEmitPoint(self.canvas)
        self.tool.setAction(self.act_pick)
        self.tool.canvasClicked.connect(self._on_canvas_clicked)
        self.landmark_tool = LandmarkTool(self.canvas)
        self.landmark_tool.pointed.connect(self._on_landmark_clicked)
        self.landmark_tool.cancelled.connect(self._on_landmark_cancelled)
        self.edit_tool = MeasureMapTool(self.canvas, self._find_handle, self._move_handle)
        self.edit_tool.clicked.connect(lambda point: self._on_canvas_clicked(point, Qt.MouseButton.LeftButton))

        # Calage conservé dans le profil d'une session et d'un projet à l'autre ; celui d'un
        # projet enregistré par une version précédente y est ajouté à l'ouverture du projet
        QgsProject.instance().readProject.connect(self._import_project_calibration)
        self._load_calibration()

        # Opacité du filaire mémorisée quand l'utilisateur la change (voir layers.watch_opacity)
        QgsProject.instance().layersAdded.connect(layers.watch_opacity)
        layers.watch_opacity(QgsProject.instance().mapLayers().values())

    def unload(self):
        self._unloaded = True
        api.abort_all()  # requêtes en cours abandonnées : leurs callbacks ne seront pas appelés
        for signal, slot in ((QgsProject.instance().readProject, self._import_project_calibration),
                             (QgsProject.instance().layersAdded, layers.watch_opacity)):
            try:
                signal.disconnect(slot)
            except (TypeError, RuntimeError):
                pass
        self.calibration.changed = None
        for action in self.actions:
            self.iface.removePluginWebMenu(MENU, action)
            self.iface.removeWebToolBarIcon(action)
        self.actions = []
        if self.tool is not None and self.canvas.mapTool() is self.tool:
            self.canvas.unsetMapTool(self.tool)
        self.tool = None
        self._end_landmark_tool()
        self.landmark_tool = None
        self._clear_marker()
        if self.session is not None:
            for tool in (self.session.triangulator, self.session.tool, self.session.calibrator):
                tool.remove()
            self.session = None
        if self.points_item is not None:
            self.points_item.remove()
            self.points_item = None
        self._end_edit_tool()
        self.edit_tool = None
        if self.dock is not None:
            self.iface.removeDockWidget(self.dock)
            self.dock.deleteLater()
            self.dock = None

    def _add_action(self, icon, text, callback, checkable=False, toolbar=True):
        action = QAction(icon, text, self.iface.mainWindow())
        action.setCheckable(checkable)
        action.triggered.connect(callback)
        self.iface.addPluginToWebMenu(MENU, action)
        if toolbar:
            self.iface.addWebToolBarIcon(action)
        self.actions.append(action)
        return action

    # ------------------------------------------------------------------
    # Panneau visionneuse
    # ------------------------------------------------------------------
    def _ensure_dock(self):
        if self.dock is None:
            self.dock = PanoramaxDock(self.iface.mainWindow())
            self.dock.calibration = self.calibration
            self.dock.pictureChanged.connect(self._on_picture_changed)
            self.dock.pickToolRequested.connect(self.activate_pick_tool)
            self.dock.instanceChanged.connect(self._on_instance_changed)
            self.dock.viewChanged.connect(self._on_view_changed)
            self.dock.message.connect(self._show_message)
            self.dock.measureToggled.connect(self._on_measure_toggled)
            self.dock.measureSaveRequested.connect(self._on_measure_save)
            self.dock.measureClearRequested.connect(self._on_measure_clear)
            self.dock.measureClearAllRequested.connect(self._on_measure_clear_all)
            self.dock.measureModeChanged.connect(self._on_measure_mode)
            self.dock.keepRequested.connect(self._on_keep)
            self.dock.newMeasureRequested.connect(self._on_new_measure)
            self.dock.newReferenceRequested.connect(self._on_new_reference)
            self.dock.photoPointed.connect(self._on_photo_pointed)
            self.dock.keyPressed.connect(self._on_key)
            self.dock.itemChanged.connect(self._on_item_changed)
            self.dock.cameraHeightChanged.connect(self._on_camera_height)
            self.dock.filterChanged.connect(self._on_filter_changed)
            self.dock.tiltChanged.connect(lambda pic: self._refresh())
            self.dock.visibilityChanged.connect(self.act_viewer.setChecked)
            self.iface.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.dock)
        return self.dock

    # ------------------------------------------------------------------
    # Calage conservé dans le profil QGIS
    # ------------------------------------------------------------------
    def _save_calibration(self):
        """Écrit le calage dans le profil : il est retrouvé aux sessions suivantes, quel que
        soit le projet, jusqu'à ce qu'il soit refait ou effacé. Les inclinaisons automatiques
        (une par photo analysée, des milliers à terme) vont dans un fichier à part, réécrit
        seulement quand elles changent : les réglages de QGIS restent légers."""
        data = self.calibration.to_dict()
        auto = data.pop("auto", {})
        QgsSettings().setValue(CALIBRATION_SETTING, json.dumps(data, separators=(",", ":")))
        text = json.dumps(auto, separators=(",", ":"))
        if text != self._saved_auto:
            path = auto_tilt_path()
            try:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path + ".tmp", "w", encoding="utf-8") as f:
                    f.write(text)
                os.replace(path + ".tmp", path)  # remplacement d'un bloc : jamais de fichier à moitié écrit
                self._saved_auto = text
            except OSError as exc:
                self.iface.messageBar().pushMessage(
                    "Panoramax", "Inclinaisons automatiques non enregistrées : {}".format(exc),
                    level=Qgis.MessageLevel.Warning, duration=6)

    def _load_calibration(self):
        text = QgsSettings().value(CALIBRATION_SETTING, "") or ""
        try:
            data = json.loads(text) if text else None
        except ValueError:
            data = None
            self.iface.messageBar().pushMessage("Panoramax", "Calage enregistré illisible : ignoré.",
                                                level=Qgis.MessageLevel.Warning, duration=6)
        try:
            with open(auto_tilt_path(), encoding="utf-8") as f:
                self._saved_auto = f.read()
            auto = json.loads(self._saved_auto)
        except (OSError, ValueError):
            auto = None
        if isinstance(data, dict) and isinstance(auto, dict):
            # Inclinaisons du fichier, complétées par celles qu'une version précédente avait
            # rangées dans le réglage (reprises au prochain enregistrement)
            data["auto"] = dict(data.get("auto") or {}, **auto)
        elif isinstance(auto, dict):
            data = {"format": Calibration.FORMAT, "auto": auto}
        self.calibration.load(data)
        if isinstance(data, dict) and "auto" in json.loads(text or "{}"):
            self._save_calibration()  # migration : inclinaisons sorties du réglage
        self._calibration_updated()

    def _import_project_calibration(self, *args):
        """Projet enregistré par une version précédente : son calage complète celui en mémoire (pour
        les photos et séquences qui n'en ont pas encore), puis il est retiré du projet."""
        project = QgsProject.instance()
        text, found = project.readEntry(PROJECT_SCOPE, CALIBRATION_KEY, "")
        if not found or not text:
            return
        try:
            added = self.calibration.merge(json.loads(text))
        except ValueError:
            added = 0
        project.removeEntry(PROJECT_SCOPE, CALIBRATION_KEY)
        if added:
            self._save_calibration()
            self._calibration_updated()
            self.iface.messageBar().pushMessage(
                "Panoramax", "Calage du projet repris pour {} photo{} ou séquence{}.".format(
                    added, *(("s", "s") if added > 1 else ("", ""))),
                level=Qgis.MessageLevel.Info, duration=6)

    def _calibration_updated(self):
        if self.session is not None:
            self.session.calibrator.reset()  # clics en attente et visées vers les repères
        if self.dock is not None:
            self.dock.update_calibration_label()
            self._refresh()

    def toggle_viewer(self, checked=True):
        dock = self._ensure_dock()
        dock.setVisible(bool(checked))
        if checked:
            dock.raise_()
            if layers.find_tile_layer() is None:
                self.add_tile_layer()

    def _show_message(self, text, level, path):
        levels = {0: Qgis.MessageLevel.Info, 1: Qgis.MessageLevel.Warning, 3: Qgis.MessageLevel.Success}
        bar = self.iface.messageBar()
        if path:
            widget = bar.createMessage("Panoramax", text)
            btn = QPushButton("Ouvrir l'image", widget)
            btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(path)))
            widget.layout().addWidget(btn)
            btn2 = QPushButton("Ouvrir le dossier", widget)
            btn2.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(path))))
            widget.layout().addWidget(btn2)
            bar.pushWidget(widget, levels.get(level, Qgis.MessageLevel.Info), 8)
        else:
            bar.pushMessage("Panoramax", text, level=levels.get(level, Qgis.MessageLevel.Info), duration=6)

    def _on_instance_changed(self):
        self._clear_marker()
        if layers.retarget_tile_layer() is None:
            self.add_tile_layer()
        label = "méta-catalogue (toutes les instances)" if api.INSTANCE_NAME == "meta" else api.INSTANCE_NAME
        self.iface.messageBar().pushMessage("Panoramax", "Instance : {}".format(label),
                                            level=Qgis.MessageLevel.Info, duration=3)

    # ------------------------------------------------------------------
    # Couche filaire
    # ------------------------------------------------------------------
    def _photo_filter(self):
        """Filtre des photos : celui du panneau, ou le dernier mémorisé."""
        return self.dock.photo_filter() if self.dock is not None else filters.PhotoFilter.load()

    def _on_filter_changed(self, photo_filter):
        if layers.apply_filter(photo_filter) is None and photo_filter.active():
            self.add_tile_layer()
        text = ("Filtre des photos : {}.".format(photo_filter.describe()) if photo_filter.active()
                else "Filtre des photos désactivé : toutes les photos sont affichées.")
        self.iface.statusBarIface().showMessage("Panoramax : " + text, 5000)

    def add_tile_layer(self):
        layer = layers.create_tile_layer(self._photo_filter())
        if layer is None:
            self.iface.messageBar().pushMessage(
                "Panoramax", "Impossible de créer la couche de tuiles vectorielles.",
                level=Qgis.MessageLevel.Critical, duration=6)

    # ------------------------------------------------------------------
    # Outil clic
    # ------------------------------------------------------------------
    def activate_pick_tool(self, *args):
        self._ensure_dock().show()
        self.canvas.setMapTool(self.tool)

    def _on_canvas_clicked(self, point, button):
        if button != Qt.MouseButton.LeftButton:
            return
        to_wgs = QgsCoordinateTransform(self.canvas.mapSettings().destinationCrs(), WGS84,
                                        QgsProject.instance())
        try:
            p = to_wgs.transform(point)
        except QgsCsException:  # point hors du domaine du SCR de la carte
            self.iface.statusBarIface().showMessage("Panoramax : point hors du domaine du SCR de la carte.", 5000)
            return
        lon, lat = p.x(), p.y()
        # Rayon de recherche adapté à l'échelle (≈ 15 pixels, entre 10 et 150 m)
        radius = min(150.0, max(10.0, self.canvas.mapUnitsPerPixel() * 15 *
                                self._meters_per_map_unit()))
        photo_filter = self._photo_filter()
        accept = photo_filter.matches if photo_filter.active() else None
        # Photo déjà connue tout près du clic (séquence préchargée) : aucun appel à l'API
        cached = api.nearest_cached(lon, lat, radius / 3.0, accept)
        if cached is not None:
            flon, flat = cached["geometry"]["coordinates"][:2]
            self._ensure_dock().show_picture(cached["id"], flon, flat)
            return
        self.iface.statusBarIface().showMessage("Panoramax : recherche de la photo la plus proche…", 3000)

        def done(data, error):
            if self._unloaded:
                return
            if error or not data:
                self.iface.messageBar().pushMessage(
                    "Panoramax", "Recherche impossible : {}".format(error or "réponse vide"),
                    level=Qgis.MessageLevel.Warning, duration=6)
                return
            features = data.get("features", [])
            feat = api.nearest_feature([f for f in features if accept is None or accept(f)], lon, lat)
            if feat is not None:
                api.remember_items([feat])  # sa fiche ne sera pas redemandée
            dock = self._ensure_dock()
            if feat is None:
                text = "Aucune photo à moins de {:.0f} m".format(radius)
                if accept is not None and features:
                    text += " ne correspond au filtre ({})".format(photo_filter.describe())
                self.iface.messageBar().pushMessage("Panoramax", text + ".", level=Qgis.MessageLevel.Info, duration=4)
                dock.show_location(lon, lat)
                return
            flon, flat = feat["geometry"]["coordinates"][:2]
            dock.show_picture(feat["id"], flon, flat)

        # Filtre actif : plus de photos demandées, pour en garder assez une fois filtrées
        api.fetch_json(api.search_url(lon, lat, radius_m=radius, limit=100 if accept else 20), done)

    def _meters_per_map_unit(self):
        crs = self.canvas.mapSettings().destinationCrs()
        if crs.isGeographic():
            return 111320.0
        return QgsUnitTypes.fromUnitToUnitFactor(crs.mapUnits(), Qgis.DistanceUnit.Meters)

    # ------------------------------------------------------------------
    # Curseur de vue sur la carte
    # ------------------------------------------------------------------
    def _ensure_cursor(self):
        if self.cursor is None:
            self.cursor = ViewCursor(self.canvas)
        return self.cursor

    def _on_picture_changed(self, pic_id, lon, lat, heading):
        cursor = self._ensure_cursor()
        cursor.set_position(lon, lat)
        if heading is not None:
            cursor.set_view(heading)

        if self.dock is not None and self.dock.chk_follow.isChecked():
            to_canvas = QgsCoordinateTransform(WGS84, self.canvas.mapSettings().destinationCrs(),
                                               QgsProject.instance())
            center = to_canvas.transform(QgsPointXY(lon, lat))
            # On ne recentre que si la photo sort de la partie centrale de la carte
            ext = self.canvas.extent()
            inner = ext.buffered(-0.2 * min(ext.width(), ext.height()))
            if self.canvas.scale() > 5000:
                self.canvas.setCenter(center)
                self.canvas.zoomScale(2000)
            elif not inner.contains(center):
                self.canvas.setCenter(center)
                self.canvas.refresh()

    def _on_view_changed(self, heading, fov):
        if self.cursor is not None:
            self.cursor.set_view(heading, fov)

    def _clear_marker(self):
        if self.cursor is not None:
            self.cursor.remove()
            self.cursor = None

    # ------------------------------------------------------------------
    # Mesures : session du cœur (core/session.py), reliée au panneau, à la visionneuse et à la carte
    # ------------------------------------------------------------------
    def _ensure_session(self):
        if self.session is None:
            dock = self._ensure_dock()
            self.session = Session(
                notify=self._refresh,
                tool=MeasureTool(self.canvas), triangulator=Triangulator(self.canvas),
                calibrator=CalibrationTool(self.canvas, self.calibration,
                                           lambda: self.session.current_ids() if self.session else (None, None)),
                calibration_store=self.calibration, precision_checker=dock.precision, tilt_checker=dock.tilts)
            self.session.set_camera_height(dock.camera_height())
            self.session.set_mode(dock.measure_mode())
            self.points_item = PointsItem(self.canvas)
            if dock._current_item:
                self.session.set_picture(dock._current_item)
        return self.session

    # Consigne courte en haut de la visionneuse, comme l'application web
    HINTS = {
        "tri": "Mesure libre : visez chaque point depuis plusieurs photos (flèches), puis « Valider le point triangulé »",
        ("height", True): "Hauteur, étape 1 : visez le pied depuis plusieurs photos (flèches), puis « Valider le point "
                          "triangulé »",
        ("height", False): "Hauteur, étape 2 : cliquez le sommet (le pied est le point visé) ; autre photo : par la carte",
        ("road", True): "Largeur, étape 1 : visez le premier bord depuis plusieurs photos (flèches), puis « Valider le "
                        "point triangulé »",
        ("road", False): "Largeur, étape 2 : cliquez le bord opposé ; autre photo, prise avant ou après : par la carte",
        "tilt": "Calage de l'inclinaison : pied puis sommet de deux objets verticaux, à 90° l'un de l'autre",
        "heading": "Calage du cap : un repère dans la photo, puis le même sur la carte (3 pour la position)",
    }

    def _refresh(self):
        """Redessine tout d'après la session : ligne d'aide, repères et habillage de la
        visionneuse, points et droite sur la carte, cadrage, outil de la carte."""
        if self._unloaded or self.dock is None or self.session is None:
            return
        s, dock = self.session, self.dock
        state = s.state()
        measuring = dock.btn_measure.isChecked()
        dock.set_measure_status(s.status())
        dock.update_calibration_label()
        dock.set_measure_marks(s.viewer_marks())
        dock.set_aiming(state["aiming"])
        dock.measure_box.set_reference_state(s.ref_point is not None)
        mode = s.mode
        kind = ("calage" if mode in CALIBRATION_MODES else "visee" if state["aiming"]
                else "prise" if mode in GROUND_MODES else "") if measuring else ""
        hint = self.HINTS.get(mode) or self.HINTS.get((mode, state["aiming"]), "")
        if state["new_measure"]:
            hint = ("Largeur faite : cliquez une de ses croix pour l'ajuster ; autre largeur : « Nouvelle mesure "
                    "depuis {} »").format(state["new_measure"])
        if state["waiting_map"]:
            hint = "Cliquez maintenant ce repère sur la carte QGIS (Échap pour annuler)."
        dock.set_overlay({
            "kind": kind, "hint": hint if measuring else "",
            "label": {"visee": "Triangulation en cours", "prise": "Mesure en cours"}.get(kind, ""),
            "point": measuring and state["aiming"] and state["point"],
            "new_measure": state["new_measure"] if measuring else None,
            "tilted": dict(state["lights"]).get("Inclinaison") == "ok", "grab": state["point_sel"]})
        # Carte : points triangulés gardés, distances (au mètre près), droite de mesure de la largeur
        points = [(lon, lat, v["nom"]) for lon, lat, v in s.points]
        links = [(s.points[i][:2], s.points[j][:2], "{}↔{} {} m".format(
            s.points[i][2]["nom"], s.points[j][2]["nom"], round(v["valeur_m"]))) for i, j, v in s.links]
        road = (s.ref_point or {}).get("road") if measuring and mode == "road" else None
        guide = None
        if road:
            from .core import widths
            guide = widths.guide(s.ref_point, road["axis"])
        self.points_item.set_data(points, links, guide)
        self._frame(s.focus())
        self._update_edit_tool()
        if s.waiting_map():
            self._start_landmark_tool()

    def _frame(self, focus):
        """Carte cadrée sur la mesure en cours (Session.focus), seulement quand ces points changent
        et si « Centrer la carte QGIS sur la photo » est coché : la carte reste libre entre-temps."""
        if focus == self._framed:
            return
        self._framed = list(focus)
        points = [p for p in focus if isinstance(p, list)]
        if len(points) < 2 or not self.dock.chk_follow.isChecked():
            return
        ct = QgsCoordinateTransform(WGS84, self.canvas.mapSettings().destinationCrs(), QgsProject.instance())
        try:
            pts = [ct.transform(QgsPointXY(lon, lat)) for lon, lat in points]
        except QgsCsException:
            return
        xs, ys = [p.x() for p in pts], [p.y() for p in pts]
        cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
        metre = 1.0 / self._meters_per_map_unit()
        half = max(max(xs) - min(xs), max(ys) - min(ys)) / 2 * (1.5 if focus[-1] == "segment" else 1.3) + 3 * metre
        self.canvas.setExtent(QgsRectangle(cx - half, cy - half, cx + half, cy + half))
        self.canvas.refresh()

    # Repère du calage de cap : clic sur la carte, puis retour à l'outil précédent
    def _start_landmark_tool(self):
        if self.canvas.mapTool() is not self.landmark_tool:
            self._previous_tool = self.canvas.mapTool()
            self.canvas.setMapTool(self.landmark_tool)

    def _end_landmark_tool(self):
        """Fin d'un pointage du repère sur la carte : retour à l'outil précédent."""
        tool = self.canvas.mapTool()
        if tool is not None and tool is self.landmark_tool:
            if self._previous_tool is not None:
                self.canvas.setMapTool(self._previous_tool)
            else:
                self.canvas.unsetMapTool(tool)
        self._previous_tool = None

    def _to_wgs(self, point):
        p = QgsCoordinateTransform(self.canvas.mapSettings().destinationCrs(), WGS84,
                                   QgsProject.instance()).transform(point)
        return p.x(), p.y()

    # Outil de la carte pendant les hauteurs et largeurs : glisser un point le recale, glisser
    # ailleurs déplace la carte
    def _update_edit_tool(self):
        active = (self.dock is not None and self.dock.btn_measure.isChecked()
                  and self.dock.measure_mode() in GROUND_MODES)
        if active and self.edit_tool is not None and self.canvas.mapTool() is not self.edit_tool:
            if self.canvas.mapTool() is self.landmark_tool:
                return
            self._before_edit = self.canvas.mapTool()
            self.canvas.setMapTool(self.edit_tool)
        elif not active:
            self._end_edit_tool()

    def _end_edit_tool(self):
        if self.edit_tool is not None and self.canvas.mapTool() is self.edit_tool:
            if self._before_edit is not None:
                self.canvas.setMapTool(self._before_edit)
            else:
                self.canvas.unsetMapTool(self.edit_tool)
        self._before_edit = None

    def _find_handle(self, point):
        """N° de la poignée (point de mesure) à moins de 10 pixels du point de la carte, ou None."""
        if self.session is None:
            return None
        ct = QgsCoordinateTransform(WGS84, self.canvas.mapSettings().destinationCrs(), QgsProject.instance())
        best, best_d = None, 10.0 * self.canvas.mapUnitsPerPixel()
        for index, (lon, lat) in enumerate(self.session.handles()):
            q = ct.transform(QgsPointXY(lon, lat))
            d = math.hypot(q.x() - point.x(), q.y() - point.y())
            if d < best_d:
                best, best_d = index, d
        return best

    def _move_handle(self, index, point):
        self.session.move_handle(index, *self._to_wgs(point))
        self._refresh()

    def _on_landmark_cancelled(self):
        self._end_landmark_tool()
        if self.session is not None:
            self.session.cancel_landmark()
            self._refresh()

    def _on_landmark_clicked(self, point):
        if self.session is None:
            return
        self._end_landmark_tool()
        self.session.map_click(*self._to_wgs(point))
        self._refresh()

    # Panneau et visionneuse -------------------------------------------------------------
    def _on_item_changed(self, item):
        if self.session is not None:
            self.session.set_picture(item)
            self._refresh()

    def _on_measure_toggled(self, checked):
        self._ensure_session().set_active(checked)
        if not checked:
            self._end_landmark_tool()
        self._refresh()

    def _on_measure_mode(self, mode):
        self._end_landmark_tool()
        self._ensure_session().set_mode(mode)
        self._refresh()

    def _on_photo_pointed(self, yaw, pitch, right):
        """Clic dans la photo : visée, mesure, ou sélection d'un point (clic droit, ou clic sur
        sa croix)."""
        session = self._ensure_session()
        if right:
            session.select_point(yaw, pitch)
        else:
            session.click(yaw, pitch)
        self._refresh()

    def _on_key(self, key):
        session = self._ensure_session()
        if key == "enter":
            session.finish_point()
        else:
            session.nudge_point(key)
        self._refresh()

    def _on_keep(self):
        self._ensure_session().keep_point()
        self._refresh()

    def _on_new_measure(self):
        self._ensure_session().new_measure()
        self._refresh()

    def _on_new_reference(self):
        self._ensure_session().new_reference()
        self._refresh()

    def _on_camera_height(self, value):
        self._ensure_session().set_camera_height(value)
        self._refresh()

    def _on_measure_save(self):
        """« Enregistrer les mesures » : hauteurs et largeurs terminées, points triangulés gardés et
        distances entre eux."""
        session, parent = self._ensure_session(), self.iface.mainWindow()
        ok, msg = session.tool.save(parent)
        n_points, error_points = save_points(session.points, self._saved_points, parent)
        n_links, error_links = save_links(session.points, session.links, self._saved_links, parent)
        parts = [msg] if ok and msg else []
        if n_points:
            parts.append("{} point{} triangulé{} enregistré{}".format(n_points, *(("s",) * 3 if n_points > 1 else ("",) * 3)))
        if n_links:
            parts.append("{} distance{} enregistrée{}".format(n_links, *(("s",) * 2 if n_links > 1 else ("",) * 2)))
        errors = [e for e in (error_points, error_links) if e] + ([msg] if not ok and msg and not
                                                                  (n_points or n_links) else [])
        if parts or errors:
            self.iface.messageBar().pushMessage(
                "Panoramax", " ; ".join(parts + errors),
                level=Qgis.MessageLevel.Warning if errors else Qgis.MessageLevel.Success, duration=6)
        self._refresh()

    def _on_measure_clear_all(self):
        """« Tout effacer » : mesures terminées et en cours, points gardés, visées."""
        self._end_landmark_tool()
        self._ensure_session().clear_all()
        self._saved_points, self._saved_links = set(), set()
        self._refresh()

    def _on_measure_clear(self):
        self._end_landmark_tool()
        self._ensure_session().clear()
        self._refresh()
