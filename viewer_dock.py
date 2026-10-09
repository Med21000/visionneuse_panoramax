# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Cédric COCHART
# SPDX-License-Identifier: GPL-2.0-or-later
"""Panneau ancré contenant la visionneuse Panoramax.

Deux modes :
- QtWebEngine disponible : la vraie visionneuse explore.panoramax.fr est
  embarquée, et ses changements d'URL (photo courante) sont suivis ;
- sinon : visualiseur intégré (image de la photo + navigation dans la
  séquence) et bouton pour ouvrir la visionneuse dans le navigateur.
"""

from collections import OrderedDict
from datetime import datetime

from qgis.core import Qgis, QgsMessageLog, QgsSettings
from qgis.PyQt.QtCore import QStandardPaths, QTimer, QUrl, Qt, pyqtSignal
from qgis.PyQt.QtGui import QDesktopServices, QImage, QPixmap
from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDockWidget,
    QDoubleSpinBox,
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
from . import calibration
from .calibration import Calibration
from .pano_widget import PanoWidget


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
        // Widgets de la visionneuse inutiles dans QGIS (la carte QGIS fait office de
        // mini-carte ; zoom à la molette) : ils sont dans des shadow DOM, qu'on parcourt.
        var hide = 'pnx-mini, pnx-widget-zoom, pnx-widget-fullscreen';
        var roots = [document];
        while (roots.length) {
          var r = roots.pop();
          var hits = r.querySelectorAll ? r.querySelectorAll(hide) : [];
          for (var h = 0; h < hits.length; h++) hits[h].style.setProperty('display', 'none', 'important');
          var els = r.querySelectorAll ? r.querySelectorAll('*') : [];
          for (var j = 0; j < els.length; j++) { if (els[j].shadowRoot) roots.push(els[j].shadowRoot); }
        }
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

# Filtrage anisotrope 4x des textures du panorama, contre le crénelage et le scintillement
# des détails fins quand la photo est vue de loin. Photo Sphere Viewer crée ses textures sans
# mipmaps (filtre linéaire, anisotropie 1) : on active les mipmaps (trilinéaire), sans
# lesquels l'anisotropie n'a guère d'effet. Relancé régulièrement pour les tuiles chargées au
# fil de la navigation ; chaque texture n'est traitée qu'une fois. __ON__ = false rétablit le
# filtre d'origine quand on décoche l'option.
ANISO_JS = r"""
(function(){
  var v = window.__pnxViewer;
  var r = v && v.psv && v.psv.renderer;
  if (!r || !r.scene || !r.scene.traverse) return;
  var caps = r.renderer && r.renderer.capabilities;
  var max = (caps && caps.getMaxAnisotropy && caps.getMaxAnisotropy()) || 1;
  var on = __ON__;
  var changed = false;
  r.scene.traverse(function(o){
    var mats = Array.isArray(o.material) ? o.material : (o.material ? [o.material] : []);
    mats.forEach(function(m){
      var texs = [m.map];  // matériau simple, ou textures passées au shader de l'adaptateur
      if (m.uniforms) Object.keys(m.uniforms).forEach(function(k){
        var u = m.uniforms[k] && m.uniforms[k].value;
        if (u && u.isTexture) texs.push(u);
      });
      texs.forEach(function(t){
        if (!t || !t.isTexture || !t.image || !!t.__qgisAniso === on) return;
        t.__qgisAniso = on;
        t.anisotropy = on ? Math.min(4, max) : 1;
        t.generateMipmaps = on;
        t.minFilter = on ? 1008 : 1006;  // THREE.LinearMipmapLinearFilter : THREE.LinearFilter
        t.needsUpdate = true;
        changed = true;
      });
    });
  });
  if (changed && v.psv.needsUpdate) v.psv.needsUpdate();
})()
"""

# Réticule de visée (triangulation), centré sur la vue : c'est la direction lue
# par getXYZ(). Ajouté au document, au-dessus de la visionneuse et sans capter la souris.
CROSSHAIR_JS = r"""
(function(show){
  var e = document.getElementById('qgis-crosshair');
  if (!show) { if (e) e.remove(); return; }
  if (!document.body) return;
  var html = '<svg width="48" height="48" viewBox="0 0 48 48" fill="none">'
    + '<circle cx="24" cy="24" r="21.5" stroke="#fff" stroke-width="1.5"/>'  // la croix entière à l'intérieur
    + '<path d="M24 4v16M24 28v16M4 24h16M28 24h16" stroke="#ff8a80" stroke-width="1.5"/>'
    + '<circle cx="24" cy="24" r="1.2" fill="#fff"/></svg>';
  if (e) { if (e.innerHTML !== html) e.innerHTML = html; return; }  // mis à jour si la page l'a déjà
  e = document.createElement('div');
  e.id = 'qgis-crosshair';
  e.innerHTML = html;
  e.style.cssText = 'position:fixed;left:50%;top:50%;width:48px;height:48px;margin:-24px 0 0 -24px;'
    + 'pointer-events:none;z-index:2147483647';
  document.body.appendChild(e);
})(__SHOW__)
"""

# Clics dans la visionneuse (mesures au sol) : un clic sans glisser est mis en file
# avec l'orientation de la vue à cet instant et sa position par rapport au centre
# de la visionneuse. La file est relue (et vidée) par la lecture régulière.
CLICKS_JS = r"""
(function(){
  if (!window.__qgisClickHook) {
    window.__qgisClickHook = true;
    window.__qgisClicks = [];
    var down = null;
    window.addEventListener('pointerdown', function(e){
      down = {x: e.clientX, y: e.clientY, t: Date.now()};
    }, true);
    window.addEventListener('pointerup', function(e){
      if (!window.__qgisClickOn || !down) return;
      var moved = Math.abs(e.clientX - down.x) + Math.abs(e.clientY - down.y);
      if (moved > 5 || Date.now() - down.t > 700) return;
      var v = window.__pnxViewer;
      if (!v || !v.psv || !v.psv.getXYZ) return;
      // Rectangle du moteur 360° lui-même (son champ vertical s'applique à sa hauteur)
      var r = (v.psv.container || v).getBoundingClientRect();
      var p = v.psv.getXYZ();
      var pos = null;
      try { pos = v.psv.getPositionFromEvent ? v.psv.getPositionFromEvent(e) : null; } catch (err) {}
      window.__qgisClicks.push({
        pic: v.psv.getPictureId ? (v.psv.getPictureId() || '') : '',
        pyaw: pos ? pos.yaw : null, ppitch: pos ? pos.pitch : null,
        x: p.x, y: p.y, z: p.z || 0, vfov: (v.psv.state && v.psv.state.vFov) || null,
        dx: e.clientX - (r.left + r.width / 2), dy: e.clientY - (r.top + r.height / 2),
        w: r.width, h: r.height});
    }, true);
  }
  window.__qgisClickOn = true;
  // Curseur fin de mesure sur la photo (la visionneuse impose sinon main / flèche)
  var v = window.__pnxViewer;
  var box = v && v.psv && v.psv.container;
  var root = box && box.getRootNode ? box.getRootNode() : null;
  if (root) {
    var css = '.psv-container, .psv-container * { cursor: url("data:image/svg+xml;utf8,'
      + "<svg xmlns='http://www.w3.org/2000/svg' width='33' height='33'>"
      + "<circle cx='16.5' cy='16.5' r='15.5' fill='none' stroke='white' stroke-width='1.5'/>"
      + "<path d='M16.5 2v11M16.5 20v11M2 16.5h11M20 16.5h11' stroke='%23ff8a80' stroke-width='1.5'/>"
      + "<circle cx='16.5' cy='16.5' r='1' fill='white'/></svg>"
      + '") 16 16, crosshair !important; }';
    var st = root.getElementById ? root.getElementById('qgis-cursor-style') : null;
    if (!st) {
      st = document.createElement('style');
      st.id = 'qgis-cursor-style';
      (root === document ? document.head : root).appendChild(st);
    }
    if (st.textContent !== css) st.textContent = css;  // mis à jour si la page l'a déjà
  }
  var out = JSON.stringify(window.__qgisClicks || []);
  window.__qgisClicks = [];
  return out;
})()
"""
CLICKS_OFF_JS = r"""
(function(){
  window.__qgisClickOn = false;
  window.__qgisClicks = [];
  var v = window.__pnxViewer;
  var box = v && v.psv && v.psv.container;
  var root = box && box.getRootNode ? box.getRootNode() : document;
  var st = root.getElementById ? root.getElementById('qgis-cursor-style') : null;
  if (st) st.remove();
})()
"""

# Repères de mesure dans la visionneuse (module de repères du moteur 360°) : points
# de départ et d'arrivée, trait entre eux et étiquette du résultat. Ils sont accrochés
# à la photo et suivent la vue. Redessinés seulement si l'état ou la photo change, ou
# si la visionneuse les a effacés (elle vide ses repères en changeant de photo).
MARKS_JS = r"""
(function(state){
  var v = window.__pnxViewer;
  var M = v && v.psv && v.psv._myMarkers;
  if (!M || !M.addMarker) return;
  var pic = v.psv.getPictureId ? (v.psv.getPictureId() || '') : '';
  // Mesures de la photo affichée : celles prises sur d'autres photos restent en attente
  var measures = (state.measures || []).map(function(m){
    return {label: m.label, beside: m.beside,
            pts: (m.points || []).filter(function(p){ return p.pic === pic && p.yaw !== null && p.pitch !== null; })};
  }).filter(function(m){ return m.pts.length; });
  // Étiquette à côté du trait (hauteur) : du côté du centre de la vue, recalculé quand
  // la vue tourne de l'autre côté du trait
  var view = v.psv.getPosition ? v.psv.getPosition().yaw : 0;
  measures.forEach(function(m){
    m.side = '';
    if (m.beside && m.pts.length === 2) {
      var d = (m.pts[0].yaw + m.pts[1].yaw) / 2 - view;
      if (Math.abs(m.pts[0].yaw - m.pts[1].yaw) > Math.PI) d += Math.PI;
      d = Math.atan2(Math.sin(d), Math.cos(d));
      m.side = d > 0 ? 'left' : 'right';
    }
  });
  var key = JSON.stringify(measures) + '|' + pic;
  var present = !measures.length || !!(M.markers && M.markers['qgis-m0-p0']);
  if (key === window.__qgisMarkKey && present) return;
  window.__qgisMarkKey = key;
  // Repères précédents, y compris ceux d'une version antérieure du plugin
  var old = (window.__qgisMarkIds || []).concat(['qgis-ext0', 'qgis-ext1', 'qgis-line-halo', 'qgis-line',
             'qgis-h0', 'qgis-h1', 'qgis-p0', 'qgis-p1', 'qgis-label']);
  old.forEach(function(id){ if (M.markers && M.markers[id]) M.removeMarker(id); });
  var ids = [];
  function add(marker){ ids.push(marker.id); M.addMarker(marker); }
  // Points en croix blanches fines centrées sur le clic, le trait va d'un centre à l'autre.
  // Les traits passent devant toutes les croix : ajoutés après elles (même couche SVG,
  // ordre d'ajout), avec un zIndex plus haut, et replacés en fin de leur conteneur.
  var cross = 'M0 9H18M9 0V18';
  measures.forEach(function(m, k){
    m.pts.forEach(function(p, i){
      add({id: 'qgis-m' + k + '-p' + i, position: {yaw: p.yaw, pitch: p.pitch}, path: cross, zIndex: 1,
           anchor: 'center center', svgStyle: {stroke: '#ffffff', strokeWidth: '1.5px', fill: 'none'}});
    });
  });
  var lines = [];
  measures.forEach(function(m, k){
    if (m.pts.length !== 2) return;
    var id = 'qgis-m' + k + '-line';
    add({id: id, polyline: [[m.pts[0].yaw, m.pts[0].pitch], [m.pts[1].yaw, m.pts[1].pitch]], zIndex: 2,
         svgStyle: {stroke: '#ff5f52', strokeWidth: '2.5px', fill: 'none'}});
    lines.push(id);
  });
  lines.forEach(function(id){
    var mk = M.markers && M.markers[id];
    var el = mk && (mk.domElement || mk.element || mk.$el);
    if (el && el.parentNode) el.parentNode.appendChild(el);
  });
  measures.forEach(function(m, k){
    var pts = m.pts, prefix = 'qgis-m' + k + '-';
    if (!m.label) return;
    var at = pts[0];
    if (pts.length === 2) {  // milieu du trait (moyenne des directions)
      var x = 0, y = 0, z = 0;
      pts.forEach(function(p){
        x += Math.cos(p.pitch) * Math.sin(p.yaw); y += Math.sin(p.pitch); z += Math.cos(p.pitch) * Math.cos(p.yaw);
      });
      at = {yaw: Math.atan2(x, z), pitch: Math.atan2(y, Math.sqrt(x * x + z * z))};
    }
    var text = String(m.label).replace(/[&<>]/g, function(c){ return {'&': '&amp;', '<': '&lt;', '>': '&gt;'}[c]; });
    // Flèche de tête allongée au double dans son sens. ↔ (largeur) : la flèche cachée en double
    // réserve exactement la place, la visible est étirée depuis le bord gauche. ↕ (hauteur) :
    // une ligne de 2em lui fait la place, l'étiquette grandit d'autant.
    text = text.replace(/^↔/, '<span style="position:relative;display:inline-block">'
      + '<span style="visibility:hidden">↔↔</span><span style="position:absolute;left:0;top:0;'
      + 'transform:scaleX(2);transform-origin:0 50%">↔</span></span>');
    text = text.replace(/^↕/, '<span style="display:inline-block;line-height:2em;vertical-align:middle;'
      + 'transform:scaleY(2)">↕</span>');
    var side = m.side;
    var anchor = side === 'left' ? 'center right' : side === 'right' ? 'center left' : 'bottom center';
    var margin = side === 'left' ? 'margin-right:14px;' : side === 'right' ? 'margin-left:14px;' : 'margin-bottom:10px;';
    add({id: prefix + 'label', position: {yaw: at.yaw, pitch: at.pitch}, anchor: anchor, zIndex: 101,
         html: '<div style="' + margin + 'padding:2px 7px;border-radius:4px;background:rgba(255,255,255,.92);'
           + 'color:#b71c1c;font:600 13px sans-serif;white-space:nowrap;box-shadow:0 1px 3px rgba(0,0,0,.4)">'
           + text + '</div>'});
  });
  window.__qgisMarkIds = ids;
})(__STATE__)
"""

# Pendant une mesure par clics (largeur, hauteur ; pas la triangulation, qui
# demande de changer de photo), un clic ne doit pas changer de photo : on écarte l'événement
# « click » du moteur 360° (la visionneuse y va à la photo voisine dans la direction
# cliquée) sans toucher au glisser, et on masque les flèches de navigation au sol
# ainsi que le curseur « aller ici » qui suit la souris sur le sol.
NAV_BLOCK_JS = r"""
(function(on, hideCursor){
  // on : mesure par clics (clic sans navigation, flèches masquées) ;
  // hideCursor : curseur au sol masqué (aussi en triangulation, où il gêne la visée)
  window.__qgisBlockNav = on;
  var v = window.__pnxViewer;
  if (v && v.psv && !v.psv.__qgisNavPatch) {
    var orig = v.psv.dispatchEvent;
    v.psv.dispatchEvent = function(e){
      if (window.__qgisBlockNav && e && e.type === 'click') return true;
      return orig.call(this, e);
    };
    v.psv.__qgisNavPatch = true;
  }
  // Curseur au sol : image ajoutée au conteneur du moteur 360°, en position absolue,
  // au-dessus (z-index 10) et sans souris ; sa taille varie avec l'inclinaison.
  // La visionneuse change son « display » à chaque mouvement : on joue sur « visibility ».
  var box = v && v.psv && v.psv.container;
  var kids = box ? box.children : [];
  for (var k = 0; k < kids.length; k++) {
    var img = kids[k];
    if (!img.__qgisCursor) {
      if (img.tagName !== 'IMG' || img.style.pointerEvents !== 'none' || img.style.position !== 'absolute'
          || String(img.style.zIndex) !== '10') continue;
      img.__qgisCursor = true;
    }
    if (hideCursor) img.style.setProperty('visibility', 'hidden', 'important');
    else img.style.removeProperty('visibility');
  }
  // Flèches : reprises à chaque changement d'état, puis chaque seconde (photo suivante)
  var now = Date.now();
  if (window.__qgisNavState === on && now - (window.__qgisNavTime || 0) < 1000) return;
  window.__qgisNavState = on;
  window.__qgisNavTime = now;
  var roots = [document];
  while (roots.length) {
    var r = roots.pop();
    var arrows = r.querySelectorAll ? r.querySelectorAll('.psv-virtual-tour-arrows') : [];
    for (var i = 0; i < arrows.length; i++) {
      if (on) arrows[i].style.setProperty('visibility', 'hidden', 'important');
      else arrows[i].style.removeProperty('visibility');
    }
    var els = r.querySelectorAll ? r.querySelectorAll('*') : [];
    for (var j = 0; j < els.length; j++) { if (els[j].shadowRoot) roots.push(els[j].shadowRoot); }
  }
})(__ON__, __CURSOR__)
"""

MEASURE_MODES = (
    ("Triangulation", "tri"),
    ("Largeur (route…)", "width"),
    ("Hauteur d'un objet", "height"),
    ("Calage : inclinaison (objets verticaux)", "tilt"),
    ("Calage : cap et position (repères sur la carte)", "heading"),
    ("Calage : hauteur de caméra (longueur connue)", "camera"),
)

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
    referenceChanged = pyqtSignal()  # référence du calage de la hauteur de caméra modifiée
    terrainChanged = pyqtSignal()  # service IGN activé ou désactivé (voir use_ign)

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
        mbox = QVBoxLayout(self.measure_box)
        mbox.setContentsMargins(0, 0, 0, 0)
        mlay = QHBoxLayout()
        self.cmb_measure = QComboBox()
        for label, mode in MEASURE_MODES:
            self.cmb_measure.addItem(label, mode)
        self.cmb_measure.currentIndexChanged.connect(self._on_measure_mode)
        mlay.addWidget(self.cmb_measure)
        self.btn_aim = QPushButton("🎯 Viser")
        self.btn_aim.setToolTip("Enregistrer la direction du réticule (centre de la vue) depuis la photo affichée")
        self.btn_aim.clicked.connect(self._aim)
        mlay.addWidget(self.btn_aim)
        self.btn_save = QPushButton("Enregistrer le point")
        self.btn_save.setToolTip("Ajouter le point triangulé à la couche « Panoramax – points triangulés »")
        self.btn_save.clicked.connect(self.measureSaveRequested)
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
        btn_clear.clicked.connect(self.measureClearRequested)
        mlay.addWidget(btn_clear)
        btn_clear_all = QPushButton("Tout effacer")
        btn_clear_all.setToolTip("Effacer toutes les mesures affichées dans la visionneuse et sur la carte")
        btn_clear_all.clicked.connect(self.measureClearAllRequested)
        mlay.addWidget(btn_clear_all)
        mbox.addLayout(mlay)
        self.terrain_row = QWidget(self.measure_box)
        tlay = QHBoxLayout(self.terrain_row)
        tlay.setContentsMargins(0, 0, 0, 0)
        tlay.addWidget(QLabel("Terrain :"))
        self.chk_ign = QCheckBox("Service d'altimétrie IGN")
        self.chk_ign.setToolTip("Altitudes du RGE ALTI (IGN, 1 m, France), interrogées à chaque clic. Désactivé, "
                                "sans réponse en 5 s ou hors couverture : services en ligne de secours "
                                "(OpenTopoData EU-DEM 25 m, puis Open-Meteo Copernicus 90 m, pente générale "
                                "seulement), puis sol plat.")
        self.chk_ign.setChecked(QgsSettings().value("visionneuse_panoramax/terrain_ign", True, type=bool))
        self.chk_ign.toggled.connect(self._on_terrain_ign)
        tlay.addWidget(self.chk_ign)
        tlay.addStretch(1)
        mbox.addWidget(self.terrain_row)
        self.lbl_calibration = QLabel("")
        self.lbl_calibration.setWordWrap(True)
        mbox.addWidget(self.lbl_calibration)
        self.measure_status = QLabel("")
        self.measure_status.setWordWrap(True)
        mbox.addWidget(self.measure_status)
        self.measure_box.setVisible(False)
        self._update_measure_widgets()
        layout.addWidget(self.measure_box)

        opts = QHBoxLayout()
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
        self.img.crosshair = self.btn_measure.isChecked() and self.measure_mode() == "tri"
        self.img.clicked.connect(self._on_native_click)
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
    # Mesures
    # ------------------------------------------------------------------
    def measure_mode(self):
        return self.cmb_measure.currentData() or "tri"

    def camera_height(self):
        return self.spin_camera.value()

    def use_ign(self):
        return self.chk_ign.isChecked()

    def _on_terrain_ign(self, checked):
        QgsSettings().setValue("visionneuse_panoramax/terrain_ign", bool(checked))
        self.terrainChanged.emit()

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

    def _on_measure_mode(self, index):
        self._update_measure_widgets()
        self.measureModeChanged.emit(self.measure_mode())

    def reference(self):
        """Référence du calage de la hauteur de caméra : ("ground" | "height", longueur en m)."""
        return self.cmb_reference.currentData() or "ground", self.spin_reference.value()

    def _on_reference(self, *args):
        kind, length = self.reference()
        settings = QgsSettings()
        settings.setValue("visionneuse_panoramax/reference_kind", kind)
        settings.setValue("visionneuse_panoramax/reference_length", float(length))
        self.referenceChanged.emit()

    def _on_camera_height(self, value):
        QgsSettings().setValue("visionneuse_panoramax/camera_height", float(value))
        self.cameraHeightChanged.emit(float(value))

    def _update_measure_widgets(self):
        """Boutons du mode choisi ; réticule (triangulation) ou clics (mesures au sol)."""
        mode = self.measure_mode()
        active = self.btn_measure.isChecked()
        for w in (self.btn_aim, self.btn_save):
            w.setVisible(mode == "tri")
        for w in (self.lbl_camera, self.spin_camera):
            w.setVisible(mode in ("width", "height"))
        self.terrain_row.setVisible(mode in ("width", "height", "camera"))
        for w in (self.cmb_reference, self.spin_reference):
            w.setVisible(mode == "camera")
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
            self.img.set_marks([{"pts": [(p["abs_yaw"], p["elev"]) for p in m.get("points", [])
                                         if p.get("pic") == self._current_pic],
                                 "label": m.get("label", ""), "beside": m.get("beside", False)}
                                for m in self._marks])

    def current_ids(self):
        """(photo, séquence) affichées, ou (None, None)."""
        item = self._current_item
        if not item or item.get("id") != self._current_pic:
            return None, None
        return item.get("id"), item.get("collection")

    def update_calibration_label(self):
        """Calage de la photo et de la séquence affichées, rappelé sous les mesures."""
        pic, sequence = self.current_ids()
        parts = []
        tilt = self.calibration.tilt(pic) if pic else None
        if tilt:
            parts.append("inclinaison {:.1f}° corrigée{} ({} objet{})".format(
                tilt["tilt"], " en partie" if tilt["partial"] else "", tilt["count"],
                "s" if tilt["count"] > 1 else "").replace(".", ","))
        camera = self.calibration.camera(sequence) if sequence else None
        if camera:
            parts.append("caméra à {:.2f} m (±{:.2f} m, {} référence{})".format(
                camera["height"], camera["sigma"], camera["count"],
                "s" if camera["count"] > 1 else "").replace(".", ","))
        lon, lat = self._current_position()
        pose = self.calibration.pose(sequence, lon, lat) if sequence else None
        if pose:
            text = "cap {:+.2f}° (±{:.2f}°)".format(pose["offset"], pose["sigma"])
            if pose["positioned"]:
                sx, sy = pose["shift"]
                text += ", position décalée de {:.2f} m vers {:.0f}° (±{:.2f} m)".format(
                    math.hypot(sx, sy), math.degrees(math.atan2(sx, sy)) % 360, pose["shift_sigma"])
            text += " · {} repère{}".format(pose["count"], "s" if pose["count"] > 1 else "")
            if pose["count"] > 3:
                text += ", écart {:.2f}°".format(pose["residual"])
            parts.append(text.replace(".", ","))
        elif sequence and self.calibration.landmarks.get(sequence):
            parts.append("recalage de la séquence hors de portée (photo à plus de {:.0f} m des repères)".format(
                calibration.REACH))
        self.lbl_calibration.setText("Calage : " + " · ".join(parts) if parts else "")
        self.lbl_calibration.setVisible(bool(parts))

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
        self.btn_capture.setText("📷 Téléchargement…")
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
