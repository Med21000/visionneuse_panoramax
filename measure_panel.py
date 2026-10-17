# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Cédric COCHART
# SPDX-License-Identifier: GPL-2.0-or-later
"""Zone des mesures du panneau : choix du mode et boutons de la mesure en cours.

Mode (hauteur, largeur, mesure libre, calages), nouveau point de référence, enregistrement,
effacement, hauteur de caméra (placement des repères projetés), rappel du calage de la photo
affichée et ligne d'aide. Les mesures s'appuient sur des points triangulés (voir
core/session.py) : « Valider le point triangulé » est dans la visionneuse. Les réglages sont
enregistrés et chaque modification est signalée au panneau.
"""

from qgis.core import QgsSettings
from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .core.tools import calibration_text

MEASURE_MODES = (
    ("Largeur perpendiculaire à la chaussée", "road"),
    ("Hauteur d'un objet", "height"),
    ("Mesure libre (points triangulés)", "tri"),
    ("Calage : inclinaison (objets verticaux)", "tilt"),
    ("Calage : cap et position (repères sur la carte)", "heading"),
)

GROUND_MODES = ("road", "height")  # mesures appuyées sur un point de référence (voir core/session.py)
STATUS_LINES = 3  # hauteur réservée à la ligne d'aide (voir set_status)
REEXPORTED = (calibration_text,)  # importée d'ici par le panneau et les tests



class MeasurePanel(QWidget):
    # Boutons
    newReferenceClicked = pyqtSignal()  # hauteur, largeur : trianguler un autre point de référence
    saveClicked = pyqtSignal()
    clearClicked = pyqtSignal()
    clearAllClicked = pyqtSignal()  # efface toutes les mesures conservées à l'écran
    # Réglages modifiés (déjà enregistrés)
    modeChanged = pyqtSignal(str)
    cameraHeightChanged = pyqtSignal(float)

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
        self.btn_new_reference = QPushButton("Nouveau point de référence")
        self.btn_new_reference.setToolTip("Trianguler un autre point de référence : les mesures déjà faites restent")
        self.btn_new_reference.clicked.connect(self.newReferenceClicked)
        mlay.addWidget(self.btn_new_reference)
        self._reference_set = False  # point de référence validé (voir set_reference_state)
        self.btn_save = QPushButton("Enregistrer les mesures")
        self.btn_save.setToolTip("Ajouter les mesures terminées et les points triangulés aux couches « Panoramax – "
                                 "mesures » et « Panoramax – points triangulés » (une mesure déjà enregistrée puis "
                                 "recalée y est mise à jour)")
        self.btn_save.clicked.connect(self.saveClicked)
        mlay.addWidget(self.btn_save)
        self.lbl_camera = QLabel("Caméra à")
        mlay.addWidget(self.lbl_camera)
        self.spin_camera = QDoubleSpinBox()
        self.spin_camera.setRange(0.3, 6.0)
        self.spin_camera.setSingleStep(0.1)
        self.spin_camera.setDecimals(2)
        self.spin_camera.setSuffix(" m")
        self.spin_camera.setToolTip("Hauteur de la caméra au-dessus du sol (1,90 m par défaut) : place les "
                                    "repères des mesures dans les autres photos que celle du clic ; les mesures "
                                    "calées sur un point triangulé n'en dépendent pas")
        self.spin_camera.setValue(float(QgsSettings().value("visionneuse_panoramax/camera_height", 1.9)))
        self.spin_camera.valueChanged.connect(self._on_camera_height)
        mlay.addWidget(self.spin_camera)
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
        self.terrain_row = QWidget(self)  # ligne de la hauteur de caméra calculée
        tlay = QHBoxLayout(self.terrain_row)
        tlay.setContentsMargins(0, 0, 0, 0)
        tlay.addStretch(1)
        # Hauteur de caméra calculée, proposée sur cette ligne (la première est pleine)
        self.btn_camera_auto = QPushButton("")
        self.btn_camera_auto.clicked.connect(self._use_camera_suggestion)
        self._camera_suggestion = None
        tlay.addWidget(self.btn_camera_auto)
        self.terrain_row.setMinimumHeight(self.btn_camera_auto.sizeHint().height())  # pas de saut à son apparition
        mbox.addWidget(self.terrain_row)
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

    def _on_mode(self, index):
        self.update_widgets()
        self.modeChanged.emit(self.measure_mode())

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

    # Affichage ------------------------------------------------------------------
    def update_widgets(self):
        """Boutons et réglages du mode choisi."""
        mode = self.measure_mode()
        self.btn_new_reference.setVisible(mode in GROUND_MODES and self._reference_set)
        self.btn_save.setVisible(mode in ("tri",) + GROUND_MODES)
        for w in (self.lbl_camera, self.spin_camera):
            w.setVisible(mode in GROUND_MODES)
        self._update_camera_button()
        self.terrain_row.setVisible(mode in GROUND_MODES)

    def set_reference_state(self, validated):
        """Point de référence validé (hauteur, largeur) : « Nouveau point de référence » proposé."""
        if validated != self._reference_set:
            self._reference_set = validated
            self.update_widgets()

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
