# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Cédric COCHART
# SPDX-License-Identifier: GPL-2.0-or-later
"""Zone des mesures du panneau : choix du mode et réglages de la mesure en cours.

Mode (mesures, triangulation, calages), hauteur de caméra, référence du calage de la
hauteur, service d'altimétrie, surface de la mesure libre 3D, rappel du calage de la
photo affichée et ligne d'aide. Les widgets affichés suivent le mode choisi ; les
réglages sont enregistrés et chaque modification est signalée au panneau.
"""

from qgis.core import QgsSettings
from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .core.tools import calibration_text
from .measure_tool import FREE_SURFACES

MEASURE_MODES = (
    ("Largeur perpendiculaire à la chaussée", "road"),
    ("Longueur parallèle à la route", "width"),
    ("Hauteur d'un objet", "height"),
    ("Triangulation d'un objet", "tri"),
    ("Mesure libre 3D", "free"),
    ("Calage : inclinaison (objets verticaux)", "tilt"),
    ("Calage : cap et position (repères sur la carte)", "heading"),
    ("Calage : hauteur de caméra (longueur connue)", "camera"),
)

GROUND_MODES = ("width", "road", "height", "free")  # mesures par lancer de rayon (GroundMeasure, FreeMeasure)
STATUS_LINES = 3  # hauteur réservée à la ligne d'aide (voir set_status)
REEXPORTED = (calibration_text,)  # importée d'ici par le panneau et les tests



class MeasurePanel(QWidget):
    # Boutons
    aimClicked = pyqtSignal()
    saveClicked = pyqtSignal()
    clearClicked = pyqtSignal()
    clearAllClicked = pyqtSignal()  # efface toutes les mesures conservées à l'écran
    # Réglages modifiés (déjà enregistrés)
    modeChanged = pyqtSignal(str)
    cameraHeightChanged = pyqtSignal(float)
    surfaceChanged = pyqtSignal(str)
    facadeRequested = pyqtSignal()
    wallFacadeRequested = pyqtSignal()  # façade du cadastre, d'un clic sur le mur (mesure libre)
    wallChanged = pyqtSignal(bool)  # hauteur sur la façade du cadastre
    planeHeightChanged = pyqtSignal(float)
    referenceChanged = pyqtSignal()  # référence du calage de la hauteur de caméra modifiée
    terrainChanged = pyqtSignal()  # option « Mesure sur un pont » changée (voir on_bridge)

    def __init__(self, parent=None):
        super().__init__(parent)
        mbox = QVBoxLayout(self)
        mbox.setContentsMargins(0, 0, 0, 0)
        mlay = QHBoxLayout()
        self.cmb_measure = QComboBox()
        for label, mode in MEASURE_MODES:
            self.cmb_measure.addItem(label, mode)
        self.cmb_measure.currentIndexChanged.connect(self._on_mode)
        mlay.addWidget(self.cmb_measure)
        self.btn_aim = QPushButton("🎯 Viser")
        self.btn_aim.setToolTip("Enregistrer la direction du réticule (centre de la vue) depuis la photo affichée")
        self.btn_aim.clicked.connect(self.aimClicked)
        mlay.addWidget(self.btn_aim)
        self.btn_save = QPushButton("Enregistrer le point")
        self.btn_save.setToolTip("Ajouter le point triangulé à la couche « Panoramax – points triangulés »")
        self.btn_save.clicked.connect(self.saveClicked)
        mlay.addWidget(self.btn_save)
        self.lbl_camera = QLabel("Caméra à")
        mlay.addWidget(self.lbl_camera)
        self.spin_camera = QDoubleSpinBox()
        self.spin_camera.setRange(0.3, 6.0)
        self.spin_camera.setSingleStep(0.1)
        self.spin_camera.setDecimals(2)
        self.spin_camera.setSuffix(" m")
        self.spin_camera.setToolTip("Hauteur de la caméra au-dessus du sol (1,90 m par défaut) : environ "
                                    "2,2 m sur le toit d'une voiture, 1,7 à 2 m à pied ou à vélo")
        self.spin_camera.setValue(float(QgsSettings().value("visionneuse_panoramax/camera_height", 1.9)))
        self.spin_camera.valueChanged.connect(self._on_camera_height)
        mlay.addWidget(self.spin_camera)
        self.cmb_reference = QComboBox()
        self.cmb_reference.addItem("au sol", "ground")
        self.cmb_reference.addItem("en hauteur", "height")
        self.cmb_reference.setToolTip("Longueur connue au sol (deux clics) ou hauteur d'un objet (pied puis sommet)")
        self.cmb_reference.setCurrentIndex(max(0, self.cmb_reference.findData(
            QgsSettings().value("visionneuse_panoramax/reference_kind", "ground"))))
        self.cmb_reference.currentIndexChanged.connect(self._on_reference)
        mlay.addWidget(self.cmb_reference)
        self.spin_reference = QDoubleSpinBox()
        self.spin_reference.setRange(0.1, 50.0)
        self.spin_reference.setSingleStep(0.1)
        self.spin_reference.setDecimals(2)
        self.spin_reference.setSuffix(" m")
        self.spin_reference.setToolTip(
            "Longueur réelle de la référence. Exemples : place de stationnement 2,30 à 2,50 m de large, "
            "bande de passage piéton 0,50 m, trait de marquage 3 m. Une longueur visible sur l'orthophoto "
            "peut aussi se mesurer avec l'outil de mesure de QGIS.")
        self.spin_reference.setValue(float(QgsSettings().value("visionneuse_panoramax/reference_length", 2.5)))
        self.spin_reference.valueChanged.connect(self._on_reference)
        mlay.addWidget(self.spin_reference)
        mlay.addStretch(1)
        btn_clear = QPushButton("Effacer")
        btn_clear.setToolTip("Effacer la mesure en cours (ou le calage du mode choisi)")
        btn_clear.clicked.connect(self.clearClicked)
        mlay.addWidget(btn_clear)
        btn_clear_all = QPushButton("Tout effacer")
        btn_clear_all.setToolTip("Effacer toutes les mesures affichées dans la visionneuse et sur la carte")
        btn_clear_all.clicked.connect(self.clearAllClicked)
        mlay.addWidget(btn_clear_all)
        mbox.addLayout(mlay)
        self.terrain_row = QWidget(self)
        tlay = QHBoxLayout(self.terrain_row)
        tlay.setContentsMargins(0, 0, 0, 0)
        tlay.addWidget(QLabel("Terrain :"))
        self.chk_bridge = QCheckBox("Mesure sur un pont")
        self.chk_bridge.setToolTip(
            "Photo prise sur un pont : sol plat et horizontal sous la caméra. Les altitudes du terrain (RGE ALTI de "
            "l'IGN, terrain nu) donnent sinon le fond du cours d'eau sous le tablier. Décoché : altitudes IGN, "
            "puis service de secours (Open-Meteo), puis sol plat.")
        self.chk_bridge.toggled.connect(self.terrainChanged)
        tlay.addWidget(self.chk_bridge)
        self.chk_wall = QCheckBox("Façade du cadastre")
        self.chk_wall.setToolTip(
            "Hauteur d'un bâtiment dont le pied est caché (voiture, haie, muret) : le mur est le premier bâtiment "
            "du cadastre traversé par la visée, contrôlé par le LiDAR HD. Le pied est pris au bas du mur, à "
            "l'altitude du sol, même si le clic tombe devant ; la façade s'affiche sur la carte. Quand le pied "
            "est visible, le cliquer reste le plus précis.")
        self.chk_wall.toggled.connect(self.wallChanged)
        tlay.addWidget(self.chk_wall)
        tlay.addStretch(1)
        # Hauteur de caméra calculée, proposée sur cette ligne (la première est pleine)
        self.btn_camera_auto = QPushButton("")
        self.btn_camera_auto.clicked.connect(self._use_camera_suggestion)
        self._camera_suggestion = None
        tlay.addWidget(self.btn_camera_auto)
        self.terrain_row.setMinimumHeight(self.btn_camera_auto.sizeHint().height())  # pas de saut à son apparition
        mbox.addWidget(self.terrain_row)
        self.free_row = QWidget(self)
        flay = QHBoxLayout(self.free_row)
        flay.setContentsMargins(0, 0, 0, 0)
        flay.addWidget(QLabel("Surface :"))
        self.cmb_surface = QComboBox()
        for label, key in FREE_SURFACES:
            self.cmb_surface.addItem(label, key)
        self.cmb_surface.setToolTip(
            "Surface sur laquelle se trouvent les points cliqués : le sol, une façade (plan vertical défini "
            "par deux clics au pied du mur), le plan vertical face à la caméra passant par le premier point "
            "(cliqué au sol), un plan horizontal à une hauteur donnée, ou aucune (triangulation depuis deux photos)")
        self.cmb_surface.currentIndexChanged.connect(self._on_surface)
        flay.addWidget(self.cmb_surface)
        self.btn_facade = QPushButton("Définir la façade")
        self.btn_facade.setToolTip("Cliquer ensuite dans la photo au pied du mur, à ses deux extrémités ; ses "
                                   "extrémités se recalent ensuite en les glissant sur la carte")
        self.btn_facade.clicked.connect(self.facadeRequested)
        flay.addWidget(self.btn_facade)
        self.btn_wall_facade = QPushButton("Façade du cadastre")
        self.btn_wall_facade.setToolTip("Cliquer ensuite sur le mur dans la photo : la façade est celle du bâtiment "
                                        "du cadastre traversé par la visée, contrôlée par le LiDAR HD ; ses "
                                        "extrémités se recalent en les glissant sur la carte")
        self.btn_wall_facade.clicked.connect(self.wallFacadeRequested)
        flay.addWidget(self.btn_wall_facade)
        self.lbl_plane = QLabel("à")
        flay.addWidget(self.lbl_plane)
        self.spin_plane = QDoubleSpinBox()
        self.spin_plane.setRange(-20.0, 50.0)
        self.spin_plane.setSingleStep(0.1)
        self.spin_plane.setDecimals(2)
        self.spin_plane.setSuffix(" m du sol")
        self.spin_plane.setValue(1.0)
        self.spin_plane.setToolTip("Hauteur du plan horizontal au-dessus du sol sous la caméra")
        self.spin_plane.valueChanged.connect(lambda v: self.planeHeightChanged.emit(float(v)))
        flay.addWidget(self.spin_plane)
        flay.addStretch(1)
        mbox.addWidget(self.free_row)
        self.lbl_calibration = QLabel("")
        self.lbl_calibration.setWordWrap(True)
        mbox.addWidget(self.lbl_calibration)
        self.lbl_quality = QLabel("")  # avertissement du mode dégradé (voir core/precision.py)
        self.lbl_quality.setWordWrap(True)
        self.lbl_quality.setStyleSheet("font-weight: bold")
        mbox.addWidget(self.lbl_quality)
        self.measure_status = QLabel("")
        self.measure_status.setWordWrap(True)
        self.measure_status.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.reset_heights()
        mbox.addWidget(self.measure_status)
        self.update_widgets()

    # Réglages ------------------------------------------------------------------
    def measure_mode(self):
        return self.cmb_measure.currentData() or "tri"

    def camera_height(self):
        return self.spin_camera.value()

    def on_bridge(self):
        """Option « Mesure sur un pont » : sol plat (voir terrain.TerrainProvider.bridge)."""
        return self.chk_bridge.isChecked()

    def surface(self):
        """Surface de la mesure libre 3D (voir core/measures.FREE_SURFACES)."""
        return self.cmb_surface.currentData() or "ground"

    def reference(self):
        """Référence du calage de la hauteur de caméra : ("ground" | "height", longueur en m)."""
        return self.cmb_reference.currentData() or "ground", self.spin_reference.value()

    def _on_mode(self, index):
        self.update_widgets()
        self.modeChanged.emit(self.measure_mode())

    def _on_surface(self, index):
        self.update_widgets()
        self.surfaceChanged.emit(self.surface())

    def _on_camera_height(self, value):
        QgsSettings().setValue("visionneuse_panoramax/camera_height", float(value))
        self._update_camera_button()
        self.cameraHeightChanged.emit(float(value))

    def set_camera_suggestion(self, suggestion):
        """Hauteur de caméra calculée pour la photo affichée, (hauteur, explication) ou None : un
        bouton la propose à côté du champ tant qu'elle en diffère (voir precision.camera_suggestion)."""
        self._camera_suggestion = None
        if suggestion is not None:
            height, tip = suggestion
            self._camera_suggestion = round(height, 2)
            self.btn_camera_auto.setText("Caméra à {:.2f} m d'après l'altitude GPS : utiliser".format(
                self._camera_suggestion).replace(".", ","))
            self.btn_camera_auto.setToolTip(tip)
        self._update_camera_button()

    def _use_camera_suggestion(self):
        if self._camera_suggestion is not None:
            self.spin_camera.setValue(self._camera_suggestion)

    def _update_camera_button(self):
        h = self._camera_suggestion
        self.btn_camera_auto.setVisible(self.measure_mode() in GROUND_MODES and h is not None and
                                        abs(self.spin_camera.value() - h) >= 0.005)

    def _on_reference(self, *args):
        kind, length = self.reference()
        settings = QgsSettings()
        settings.setValue("visionneuse_panoramax/reference_kind", kind)
        settings.setValue("visionneuse_panoramax/reference_length", float(length))
        self.referenceChanged.emit()

    # Affichage ------------------------------------------------------------------
    def update_widgets(self):
        """Boutons et réglages du mode choisi."""
        mode = self.measure_mode()
        self.btn_aim.setVisible(mode == "tri")
        self.btn_save.setVisible(mode in ("tri",) + GROUND_MODES)
        if mode == "tri":
            self.btn_save.setText("Enregistrer le point")
            self.btn_save.setToolTip("Ajouter le point triangulé à la couche « Panoramax – points triangulés »")
        else:
            self.btn_save.setText("Enregistrer les mesures")
            self.btn_save.setToolTip("Ajouter les mesures terminées affichées à la couche « Panoramax – mesures » "
                                     "(une mesure déjà enregistrée puis recalée y est mise à jour)")
        for w in (self.lbl_camera, self.spin_camera):
            w.setVisible(mode in GROUND_MODES)
        self._update_camera_button()
        self.terrain_row.setVisible(mode in GROUND_MODES + ("camera",))
        for w in (self.cmb_reference, self.spin_reference):
            w.setVisible(mode == "camera")
        surface = self.surface()
        self.free_row.setVisible(mode == "free")
        self.btn_facade.setVisible(surface == "facade")
        self.btn_wall_facade.setVisible(surface == "facade")
        self.chk_wall.setVisible(mode == "height")
        for w in (self.lbl_plane, self.spin_plane):
            w.setVisible(surface == "horizontal")

    def set_calibration_text(self, text):
        """Rappel du calage de la photo affichée. Une fois apparu pendant une séance de mesure,
        il reste (vide au besoin) et ne rapetisse pas : sinon la visionneuse change de taille
        d'une photo à l'autre (voir set_status)."""
        label = self.lbl_calibration
        label.setText(text)
        if text:
            label.setVisible(True)
            self._keep_height(label)

    def set_quality_text(self, text):
        """Avertissement du mode dégradé de la photo affichée ; comme le rappel du calage, une
        fois apparu pendant une séance de mesure, il reste (vide au besoin)."""
        label = self.lbl_quality
        label.setText(text)
        if text:
            label.setVisible(True)
            self._keep_height(label)

    def set_status(self, text):
        """Ligne d'aide : consigne de clic ou résultat de la mesure.

        Sa hauteur ne diminue pas au fil des mesures (voir reset_heights) : sinon la
        visionneuse, en dessous, change de taille et l'image saute sous le curseur au
        moment où le résultat, plus long que la consigne, s'affiche."""
        self.measure_status.setText(text)
        self._keep_height(self.measure_status)

    @staticmethod
    def _keep_height(label):
        """La hauteur minimale du texte suit sa plus grande hauteur depuis reset_heights."""
        if label.width() > 0:
            label.setMinimumHeight(max(label.minimumHeight(), label.heightForWidth(label.width())))

    def reset_heights(self):
        """Début d'une séance de mesure : ligne d'aide ramenée à trois lignes, celle d'un résultat
        à la largeur habituelle du panneau ; rappel du calage affiché seulement s'il y en a un."""
        self.measure_status.setMinimumHeight(STATUS_LINES * self.measure_status.fontMetrics().lineSpacing())
        for label in (self.lbl_calibration, self.lbl_quality):
            label.setMinimumHeight(0)
            label.setVisible(bool(label.text()))

    def status(self):
        return self.measure_status.text()

    def show_status(self, visible):
        self.measure_status.setVisible(visible)
