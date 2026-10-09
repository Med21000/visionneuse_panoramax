# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Cédric COCHART
# SPDX-License-Identifier: GPL-2.0-or-later
"""Classe principale du plugin Visionneuse Panoramax."""

import json
import os

from qgis.core import (
    Qgis,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsPointXY,
    QgsProject,
    QgsSettings,
    QgsUnitTypes,
)
from qgis.gui import QgsMapToolEmitPoint, QgsMapToolPan
from qgis.PyQt.QtCore import QUrl, Qt, pyqtSignal
from qgis.PyQt.QtGui import QAction, QDesktopServices, QIcon
from qgis.PyQt.QtWidgets import QPushButton

from . import api, layers
from .cursor import ViewCursor
from .calibration import Calibration
from .measure import CalibrationTool, GroundMeasure, Triangulator
from .terrain import TerrainProvider
from .viewer_dock import PanoramaxDock

WGS84 = QgsCoordinateReferenceSystem("EPSG:4326")
MENU = "&Panoramax"
PROJECT_SCOPE = "visionneuse_panoramax"  # section du plugin dans le projet QGIS
CALIBRATION_KEY = "calibration"


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


class PanoramaxPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.canvas = iface.mapCanvas()
        self.plugin_dir = os.path.dirname(__file__)
        self.actions = []
        self.dock = None
        self.tool = None
        self.cursor = None
        self.triangulator = None
        self.ground = None
        self.calibrator = None
        self.calibration = Calibration()  # enregistré dans le projet QGIS
        self.calibration.changed = self._save_calibration
        self.landmark_tool = None  # clic du repère sur la carte (calage du cap)
        self._previous_tool = None
        self.terrain = TerrainProvider()

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

        # Calage enregistré dans le projet : relu à l'ouverture, vidé pour un nouveau projet
        project = QgsProject.instance()
        project.readProject.connect(self._load_calibration)
        project.cleared.connect(self._load_calibration)
        self._load_calibration()

    def unload(self):
        project = QgsProject.instance()
        for signal in (project.readProject, project.cleared):
            try:
                signal.disconnect(self._load_calibration)
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
        for tool in (self.triangulator, self.ground, self.calibrator):
            if tool is not None:
                tool.remove()
        self.triangulator = self.ground = self.calibrator = None
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
            self.dock.aimRequested.connect(self._on_aim)
            self.dock.measureSaveRequested.connect(self._on_measure_save)
            self.dock.measureClearRequested.connect(self._on_measure_clear)
            self.dock.measureClearAllRequested.connect(self._on_measure_clear_all)
            self.dock.measureModeChanged.connect(self._on_measure_mode)
            self.dock.photoClicked.connect(self._on_photo_clicked)
            self.dock.cameraHeightChanged.connect(self._on_camera_height)
            self.dock.terrainChanged.connect(self._on_terrain_changed)
            self.dock.referenceChanged.connect(self._on_reference_changed)
            self.terrain.use_ign = self.dock.use_ign()
            self.dock.visibilityChanged.connect(self.act_viewer.setChecked)
            self.iface.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.dock)
        return self.dock

    # ------------------------------------------------------------------
    # Calage enregistré dans le projet QGIS
    # ------------------------------------------------------------------
    def _save_calibration(self):
        """Écrit le calage dans le projet (qui passe en « modifié », à enregistrer)."""
        QgsProject.instance().writeEntry(PROJECT_SCOPE, CALIBRATION_KEY,
                                         json.dumps(self.calibration.to_dict(), separators=(",", ":")))

    def _load_calibration(self, *args):
        text, _ = QgsProject.instance().readEntry(PROJECT_SCOPE, CALIBRATION_KEY, "")
        try:
            data = json.loads(text) if text else None
        except ValueError:
            data = None
            self.iface.messageBar().pushMessage("Panoramax", "Calage du projet illisible : ignoré.",
                                                level=Qgis.MessageLevel.Warning, duration=6)
        self.calibration.load(data)
        if self.calibrator is not None:
            self.calibrator.reset()  # clics en attente et visées vers les repères
        if self.dock is not None:
            self.dock.update_calibration_label()
            if self.dock.btn_measure.isChecked():
                self._show_measure(self._active_measure().status())

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
    def add_tile_layer(self):
        layer = layers.create_tile_layer()
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
        p = to_wgs.transform(point)
        lon, lat = p.x(), p.y()
        # Rayon de recherche adapté à l'échelle (≈ 15 pixels, entre 10 et 150 m)
        radius = min(150.0, max(10.0, self.canvas.mapUnitsPerPixel() * 15 *
                                self._meters_per_map_unit()))
        # Photo déjà connue tout près du clic (séquence préchargée) : aucun appel à l'API
        cached = api.nearest_cached(lon, lat, radius / 3.0)
        if cached is not None:
            flon, flat = cached["geometry"]["coordinates"][:2]
            self._ensure_dock().show_picture(cached["id"], flon, flat)
            return
        self.iface.statusBarIface().showMessage("Panoramax : recherche de la photo la plus proche…", 3000)

        def done(data, error):
            if error or not data:
                self.iface.messageBar().pushMessage(
                    "Panoramax", "Recherche impossible : {}".format(error),
                    level=Qgis.MessageLevel.Warning, duration=6)
                return
            feat = api.nearest_feature(data.get("features", []), lon, lat)
            if feat is not None:
                api.remember_items([feat])  # sa fiche ne sera pas redemandée
            dock = self._ensure_dock()
            if feat is None:
                self.iface.messageBar().pushMessage(
                    "Panoramax", "Aucune photo à moins de {:.0f} m.".format(radius),
                    level=Qgis.MessageLevel.Info, duration=4)
                dock.show_location(lon, lat)
                return
            flon, flat = feat["geometry"]["coordinates"][:2]
            dock.show_picture(feat["id"], flon, flat)

        api.fetch_json(api.search_url(lon, lat, radius_m=radius), done)

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
    # Mesures : triangulation, largeur, hauteur
    # ------------------------------------------------------------------
    def _ensure_triangulator(self):
        if self.triangulator is None:
            self.triangulator = Triangulator(self.canvas)
        return self.triangulator

    def _ensure_ground(self):
        if self.ground is None:
            self.ground = GroundMeasure(self.canvas)
            self.ground.camera_height = self.dock.camera_height()
            self.ground.calibration = self.dock.calibration
        return self.ground

    def _ensure_calibrator(self):
        if self.calibrator is None:
            self.calibrator = CalibrationTool(self.canvas, self.dock.calibration, self.dock.current_ids,
                                              self.dock.reference)
        return self.calibrator

    def _active_measure(self):
        mode = self.dock.measure_mode()
        if mode == "tri":
            return self._ensure_triangulator()
        if mode in ("tilt", "heading", "camera"):
            tool = self._ensure_calibrator()
            if tool.mode != mode:
                tool.set_mode(mode)
            return tool
        ground = self._ensure_ground()
        if ground.mode != mode:
            ground.set_mode(mode)
        return ground

    def _show_measure(self, text):
        """Texte de la mesure dans le panneau et repères dans la visionneuse."""
        self.dock.set_measure_status(text)
        self.dock.update_calibration_label()
        mode = self.dock.measure_mode()
        tool = {"width": self.ground, "height": self.ground, "tilt": self.calibrator,
                "heading": self.calibrator, "camera": self.calibrator}.get(mode) if self.dock.btn_measure.isChecked() else None
        # Mesures terminées, gardées à l'écran jusqu'à « Tout effacer », puis mesure en cours
        marks = self.ground.done_marks() if self.ground is not None else []
        current = tool.viewer_marks() if tool is not None else None
        if current and current["points"]:
            marks = marks + [current]
        self.dock.set_measure_marks(marks)

    def _clear_measures(self):
        """Termine les mesures en cours (réussies, largeurs et hauteurs restent affichées) ;
        le calage déjà fait est conservé."""
        self._end_landmark_tool()
        if self.triangulator is not None:
            self.triangulator.clear()
        if self.ground is not None:
            self.ground.finish()  # réussie, la mesure en cours reste affichée
        if self.calibrator is not None:
            self.calibrator.reset()

    # Repère du calage de cap : clic sur la carte, puis retour à l'outil précédent
    def _start_landmark_tool(self):
        if self.canvas.mapTool() is not self.landmark_tool:
            self._previous_tool = self.canvas.mapTool()
            self.canvas.setMapTool(self.landmark_tool)

    def _end_landmark_tool(self):
        if self.landmark_tool is not None and self.canvas.mapTool() is self.landmark_tool:
            if self._previous_tool is not None:
                self.canvas.setMapTool(self._previous_tool)
            else:
                self.canvas.unsetMapTool(self.landmark_tool)
        self._previous_tool = None

    def _on_landmark_cancelled(self):
        self._end_landmark_tool()
        if self.calibrator is not None:
            self.calibrator.reset()
            self._show_measure(self.calibrator.status())

    def _on_landmark_clicked(self, point):
        if self.calibrator is None:
            return
        to_wgs = QgsCoordinateTransform(self.canvas.mapSettings().destinationCrs(), WGS84,
                                        QgsProject.instance())
        p = to_wgs.transform(point)
        self._end_landmark_tool()
        self._show_measure(self.calibrator.add_map_point(p.x(), p.y()))

    def _on_measure_toggled(self, checked):
        if not checked:
            self._clear_measures()
            if self.calibrator is not None:
                self.calibrator.remove()  # visées vers les repères, redessinées au besoin
        self._show_measure(self._active_measure().status())

    def _on_measure_mode(self, mode):
        self._clear_measures()
        self._show_measure(self._active_measure().status())

    def _on_aim(self, sighting):
        self._show_measure(self._ensure_triangulator().add(sighting))

    def _on_photo_clicked(self, click):
        tool = self._active_measure()
        if not isinstance(tool, (GroundMeasure, CalibrationTool)) or not tool.needs_profile():
            self._show_measure(tool.add_click(click))  # sommet d'un objet ou calage : pas de terrain
            if isinstance(tool, CalibrationTool):
                if tool.waiting_map():
                    self._start_landmark_tool()
                else:
                    self._end_landmark_tool()
            return
        self._show_measure("Altitude du terrain le long de la visée…")
        mode = tool.mode

        def done(profile, label, warning):
            if self.dock is None or self.dock.measure_mode() != mode or tool.mode != mode:
                return  # mesure effacée ou mode changé entre-temps
            click.update(profile=profile, terrain=label)
            self._show_measure(tool.add_click(click, warning))

        self.terrain.profile(click["lon"], click["lat"], click["yaw"], done)

    def _on_terrain_changed(self):
        self.terrain.use_ign = self.dock.use_ign()
        if self.ground is not None:
            self.ground.clear()  # les profils des clics viennent de l'ancienne source
        if self.calibrator is not None:
            self.calibrator.reset()
            if self.dock.measure_mode() in ("width", "height"):
                self._show_measure(self.ground.status())

    def _on_reference_changed(self):
        if self.calibrator is not None and self.dock.measure_mode() == "camera":
            self.calibrator.reset()  # clics en attente faits pour l'ancienne référence
            self._show_measure(self.calibrator.status())

    def _on_camera_height(self, value):
        ground = self._ensure_ground()
        ground.set_camera_height(value)
        if self.dock.measure_mode() in ("width", "height"):
            self._show_measure(ground.status())

    def _on_measure_save(self):
        tri = self._ensure_triangulator()
        ok, msg = tri.save()
        self.iface.messageBar().pushMessage(
            "Panoramax", msg, level=Qgis.MessageLevel.Success if ok else Qgis.MessageLevel.Warning, duration=6)
        self._show_measure(tri.status())

    def _on_measure_clear_all(self):
        """« Tout effacer » : mesures terminées et en cours, visées de triangulation."""
        self._end_landmark_tool()
        if self.triangulator is not None:
            self.triangulator.clear()
        if self.ground is not None:
            self.ground.clear_all()
        if self.calibrator is not None:
            self.calibrator.reset()
        self._show_measure(self._active_measure().status())

    def _on_measure_clear(self):
        self._end_landmark_tool()
        tool = self._active_measure()
        tool.clear()
        self._show_measure(tool.status())
