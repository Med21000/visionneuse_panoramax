# -*- coding: utf-8 -*-
"""Panneau ancré contenant la visionneuse Panoramax.

Deux modes :
- QtWebEngine disponible : la vraie visionneuse explore.panoramax.fr est
  embarquée, et ses changements d'URL (photo courante) sont suivis ;
- sinon : visualiseur intégré (image de la photo + navigation dans la
  séquence) et bouton pour ouvrir la visionneuse dans le navigateur.
"""

from collections import OrderedDict

from qgis.core import QgsSettings
from qgis.PyQt.QtCore import QStandardPaths, QTimer, QUrl, Qt, pyqtSignal
from qgis.PyQt.QtGui import QDesktopServices, QImage, QPixmap
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

import math
import os
import re

from . import api
from .pano_widget import PanoWidget


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

WEBGL_TEST_JS = (
    "(function(){try{var c=document.createElement('canvas');"
    "return !!(c.getContext('webgl2')||c.getContext('webgl')||c.getContext('experimental-webgl'));"
    "}catch(e){return false;}})()"
)

# La visionneuse affiche son propre message quand WebGL est refusé
# (ex. rendu logiciel jugé trop lent) : on le repère dans la page.
WEBGL_ERROR_JS = (
    "(function(){var t=(document.body&&document.body.innerText)||'';"
    "return /WebGL n.est pas support|WebGL is not supported|WebGL not supported/i.test(t);})()"
)

# Lecture directe de l'état de la visionneuse (sans attendre la mise à jour de
# l'URL, que la visionneuse ne fait qu'après ~2 s d'inactivité). Le composant
# <pnx-viewer> peut être niché dans des shadow DOM : on le cherche et on le
# mémorise, et on masque le reste du site (bandeaux) pour ne garder qu'elle.
# Renvoie "pic|x|y|z" (x = cap absolu) ou "" si indisponible.
LIVE_VIEW_JS = r"""
(function(){
  try {
    var v = window.__pnxViewer;
    if (!v || !v.isConnected) {
      v = null;
      var stack = [document];
      while (stack.length && !v) {
        var root = stack.pop();
        var found = root.querySelector && root.querySelector('pnx-viewer, pnx-photo-viewer');
        if (found) { v = found; break; }
        var all = root.querySelectorAll ? root.querySelectorAll('*') : [];
        for (var i = 0; i < all.length; i++) { if (all[i].shadowRoot) stack.push(all[i].shadowRoot); }
      }
      window.__pnxViewer = v;
    }
    if (v) {
      // Visionneuse seule : on remonte jusqu'à la racine du document et on masque,
      // à chaque niveau, tout ce qui n'est pas sur le chemin de la visionneuse
      // (en-tête, pied de page, menus du site). Refait régulièrement, car le site
      // peut réafficher ses bandeaux.
      var now = Date.now();
      if (!window.__qgisIsoTime || now - window.__qgisIsoTime > 1000 || !v.__qgisIso) {
        window.__qgisIsoTime = now;
        v.__qgisIso = true;
        var keep = {STYLE:1, SCRIPT:1, LINK:1, META:1, TITLE:1, HEAD:1, TEMPLATE:1};
        var node = v;
        while (node && node !== document.documentElement) {
          var parent = node.parentNode;
          if (!parent) break;
          var kids = parent.children || [];
          for (var k = 0; k < kids.length; k++) {
            var c = kids[k];
            if (c !== node && !keep[c.tagName] && c.id !== 'qgis-crosshair') c.style.setProperty('display', 'none', 'important');
          }
          var holder = (parent instanceof ShadowRoot) ? parent.host : parent;
          if (holder && holder.style && holder !== document.documentElement) {
            ['margin', 'padding', 'border'].forEach(function(p){ holder.style.setProperty(p, '0', 'important'); });
            holder.style.setProperty('height', '100%', 'important');
            holder.style.setProperty('max-height', 'none', 'important');
            holder.style.setProperty('overflow', 'hidden', 'important');
          }
          node = (parent instanceof ShadowRoot) ? parent.host : parent;
        }
        document.documentElement.style.setProperty('height', '100%', 'important');
        document.documentElement.style.setProperty('overflow', 'hidden', 'important');
        v.style.setProperty('display', 'block', 'important');
        v.style.setProperty('width', '100%', 'important');
        v.style.setProperty('height', '100vh', 'important');
        v.style.setProperty('max-height', 'none', 'important');
        v.style.setProperty('margin', '0', 'important');
        if (!v.__qgisResized) {
          v.__qgisResized = true;
          setTimeout(function(){ window.dispatchEvent(new Event('resize')); }, 50);
        }
      }
    }
    if (!v || !v.psv || !v.psv.getXYZ) return '';
    var p = v.psv.getXYZ();
    var id = v.psv.getPictureId ? (v.psv.getPictureId() || '') : '';
    var vfov = (v.psv.state && v.psv.state.vFov) || '';
    return id + '|' + p.x + '|' + p.y + '|' + (p.z || 0) + '|' + vfov;
  } catch (e) { return ''; }
})()
"""

# Réticule de visée (triangulation), centré sur la vue : c'est la direction lue
# par getXYZ(). Ajouté au document, au-dessus de la visionneuse et sans capter la souris.
CROSSHAIR_JS = r"""
(function(show){
  var e = document.getElementById('qgis-crosshair');
  if (!show) { if (e) e.remove(); return; }
  if (e || !document.body) return;
  e = document.createElement('div');
  e.id = 'qgis-crosshair';
  e.innerHTML = '<svg width="44" height="44" viewBox="0 0 44 44" fill="none" stroke-linecap="round">'
    + '<path d="M22 3v14M22 27v14M3 22h14M27 22h14" stroke="#fff" stroke-width="4.5" opacity=".75"/>'
    + '<path d="M22 3v14M22 27v14M3 22h14M27 22h14" stroke="#e53935" stroke-width="2"/>'
    + '<circle cx="22" cy="22" r="2" fill="#e53935" stroke="#fff"/></svg>';
  e.style.cssText = 'position:fixed;left:50%;top:50%;width:44px;height:44px;margin:-22px 0 0 -22px;'
    + 'pointer-events:none;z-index:2147483647';
  document.body.appendChild(e);
})(__SHOW__)
"""

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
    extractRequested = pyqtSignal(str)
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
        btn_seq = QPushButton("Extraire séquences")
        btn_seq.setToolTip("Créer une couche vecteur des séquences de l'emprise courante")
        btn_seq.clicked.connect(lambda: self.extractRequested.emit("sequences"))
        bar.addWidget(btn_seq)
        btn_pic = QPushButton("Extraire photos")
        btn_pic.setToolTip("Créer une couche de points des photos de l'emprise courante (zoom fort requis)")
        btn_pic.clicked.connect(lambda: self.extractRequested.emit("pictures"))
        bar.addWidget(btn_pic)
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
        self.btn_measure = QToolButton()
        self.btn_measure.setText("📐 Triangulation")
        self.btn_measure.setCheckable(True)
        self.btn_measure.setToolTip("Positionner un objet sur la carte en le visant depuis plusieurs photos")
        self.btn_measure.toggled.connect(self._on_measure_toggled)
        bar.addWidget(self.btn_measure)
        btn_browser = QPushButton("Navigateur")
        btn_browser.setToolTip("Ouvrir la vue actuelle dans le navigateur")
        btn_browser.clicked.connect(self.open_in_browser)
        bar.addWidget(btn_browser)
        layout.addLayout(bar)

        inst = QHBoxLayout()
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
        layout.addLayout(inst)
        self._fill_instances([])
        self.cmb_instance.activated.connect(self._on_instance_activated)
        self.load_instances()

        self.measure_box = QWidget(root)
        mlay = QHBoxLayout(self.measure_box)
        mlay.setContentsMargins(0, 0, 0, 0)
        btn_aim = QPushButton("🎯 Viser")
        btn_aim.setToolTip("Enregistrer la direction du réticule (centre de la vue) depuis la photo affichée")
        btn_aim.clicked.connect(self._aim)
        mlay.addWidget(btn_aim)
        btn_save = QPushButton("Enregistrer le point")
        btn_save.setToolTip("Ajouter le point triangulé à la couche « Panoramax – points triangulés »")
        btn_save.clicked.connect(self.measureSaveRequested)
        mlay.addWidget(btn_save)
        btn_clear = QPushButton("Effacer")
        btn_clear.setToolTip("Effacer les visées en cours")
        btn_clear.clicked.connect(self.measureClearRequested)
        mlay.addWidget(btn_clear)
        self.measure_status = QLabel("")
        self.measure_status.setWordWrap(True)
        mlay.addWidget(self.measure_status, 1)
        self.measure_box.setVisible(False)
        layout.addWidget(self.measure_box)

        opts = QHBoxLayout()
        self.chk_follow = QCheckBox("Centrer la carte QGIS sur la photo")
        self.chk_follow.setChecked(True)
        opts.addWidget(self.chk_follow)
        opts.addStretch(1)
        layout.addLayout(opts)

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
        box = QWidget(parent)
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        info = QLabel(text)
        info.setWordWrap(True)
        lay.addWidget(info)
        self.img = PanoWidget(box)
        self.img.setToolTip("Photo 360° : glisser pour tourner, molette pour zoomer")
        self.img.viewChanged.connect(self.viewChanged)
        self.img.crosshair = self.btn_measure.isChecked()
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
        self._pending_image = None
        self._image_cache = OrderedDict()  # href -> QPixmap (3 dernières : avant, courante, après)
        return box

    def _navigate(self, rel):
        if self._current_item is None:
            return
        pid = api.id_from_href(api.item_link(self._current_item, rel))
        if pid:
            api.get_item(pid, self._on_item)

    # ------------------------------------------------------------------
    # Triangulation
    # ------------------------------------------------------------------
    def _on_measure_toggled(self, checked):
        self.measure_box.setVisible(checked)
        if self.web is not None:
            self.web.page().runJavaScript(CROSSHAIR_JS.replace("__SHOW__", "true" if checked else "false"))
        elif hasattr(self, "img"):
            self.img.crosshair = checked
            self.img.update()
        self.measureToggled.emit(checked)

    def set_measure_status(self, text):
        self.measure_status.setText(text)

    def current_sighting(self):
        """Visée depuis la photo affichée (centre de la vue), ou None."""
        item = self._current_item
        if not item or item.get("id") != self._current_pic:
            return None  # fiche de la photo pas encore reçue
        try:
            lon, lat = item["geometry"]["coordinates"][:2]
        except (KeyError, TypeError, ValueError):
            return None
        if self.web is None:
            if self.img.pixmap is None:
                return None  # image pas encore affichée : cap inconnu
            heading = self.img.current_view()[0]
        elif self._view is not None:
            heading = self._view["heading"]
        else:
            heading = api.item_heading(item)
        if heading is None:
            return None
        return {"pic": item.get("id", ""), "lon": float(lon), "lat": float(lat), "heading": float(heading) % 360}

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
        self.btn_capture.setText("📷 Téléchargement…")
        api.fetch(href, lambda data, error: self._on_capture_image(item, view, data, error))

    def _on_capture_image(self, item, view, data, error):
        from qgis.PyQt.QtWidgets import QApplication

        from . import capture

        self.btn_capture.setEnabled(True)
        self.btn_capture.setText("📷 Capture HD")
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
            self.img.set_message("Aucune photo Panoramax à proximité de ce point.")
            self.meta.setText("")

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
            if self.btn_measure.isChecked():  # la page peut avoir été rechargée
                page.runJavaScript(CROSSHAIR_JS.replace("__SHOW__", "true"))
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
                self.meta.setText("Erreur lors de la lecture de la photo : {}".format(error))
            return
        try:
            lon, lat = item["geometry"]["coordinates"][:2]
        except (KeyError, TypeError, ValueError):
            return
        pic_id = item.get("id", "")
        heading = api.item_heading(item)
        self._current_item = item
        self._current_pic = pic_id
        self.pictureChanged.emit(pic_id, float(lon), float(lat), heading)
        item_fov = self._item_fov(item)

        if self.web is not None:
            # En attendant que la visionneuse publie son orientation (xyz)
            if heading is not None and self._last_xyz is None:
                self.viewChanged.emit(heading % 360, item_fov if item_fov and item_fov < 360 else 90.0)
        else:
            self.current_url = api.explore_url(pic_id=pic_id, lat=lat, lon=lon)
            props = item.get("properties", {}) or {}
            self.meta.setText("Photo {}<br>Date : {}<br>Cap : {}".format(
                pic_id, props.get("datetime", "?"),
                "{:.0f}°".format(heading) if heading is not None else "?"))
            self.btn_prev.setEnabled(bool(api.item_link(item, "prev")))
            self.btn_next.setEnabled(bool(api.item_link(item, "next")))
            href = api.item_image_url(item)
            if href:
                self._pending_image = href
                is360 = (item_fov or 0) >= 360
                pix = self._image_cache.get(href)
                if pix is not None:
                    self._image_cache.move_to_end(href)
                    self.img.set_image(pix, heading, is360, item_fov)
                else:
                    api.fetch(href, lambda data, error, h=href: self._on_image(
                        h, data, error, heading, is360, item_fov), prefer_cache=True)

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
