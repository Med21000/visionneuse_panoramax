# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Cédric COCHART
# SPDX-License-Identifier: GPL-2.0-or-later
"""Scripts injectés dans la visionneuse web Panoramax (voir viewer_dock.py).

Chaînes JavaScript exécutées par QWebEngineView.page().runJavaScript : test de WebGL,
lecture de la vue, filtrage anisotrope, clics et touches de mesure, repères des
mesures, navigation pendant une mesure et habillage de la visionneuse (liseré, état,
consigne, bouton « Valider le point triangulé »). Les marqueurs __XXX__ sont remplacés
avant exécution.
"""

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
            // Éléments du plugin (habillage de la mesure) : gardés
            if (c !== node && !keep[c.tagName] && !(c.id && c.id.indexOf('qgis-') === 0))
              c.style.setProperty('display', 'none', 'important');
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

# Clics et touches dans la visionneuse pendant une mesure : un clic sans glisser est mis en file
# avec l'orientation de la vue à cet instant, sa position par rapport au centre de la
# visionneuse et son bouton (droit : sélection d'un point) ; pas un clic sur une flèche de
# navigation ni sur le bouton du plugin. Les flèches du clavier et Entrée sont mises en file
# quand un point est sélectionné (__qgisKeyGrab), sans aller à la visionneuse ; le bouton
# « Valider le point triangulé » met « keep » en file d'actions. Les files sont relues (et
# vidées) par la lecture régulière : {"c": clics, "k": touches, "a": actions}.
CLICKS_JS = r"""
(function(){
  if (!window.__qgisClickHook) {
    window.__qgisClickHook = true;
    window.__qgisClicks = [];
    window.__qgisKeys = [];
    window.__qgisActions = window.__qgisActions || [];
    var down = null;
    function skip(e){  // flèche de navigation ou élément du plugin : pas un clic de mesure
      var path = e.composedPath ? e.composedPath() : [];
      for (var i = 0; i < path.length; i++) {
        var el = path[i];
        if (el.classList && el.classList.contains('psv-virtual-tour-link')) return true;
        if (el.id && String(el.id).indexOf('qgis-') === 0 && el.id !== 'qgis-border') return true;
      }
      return false;
    }
    window.addEventListener('pointerdown', function(e){
      down = skip(e) ? null : {x: e.clientX, y: e.clientY, t: Date.now(), b: e.button};
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
        pyaw: pos ? pos.yaw : null, ppitch: pos ? pos.pitch : null, right: down.b === 2,
        x: p.x, y: p.y, z: p.z || 0, vfov: (v.psv.state && v.psv.state.vFov) || null,
        dx: e.clientX - (r.left + r.width / 2), dy: e.clientY - (r.top + r.height / 2),
        w: r.width, h: r.height});
    }, true);
    window.addEventListener('contextmenu', function(e){ if (window.__qgisClickOn) e.preventDefault(); }, true);
    var KEYS = {ArrowLeft: 'left', ArrowRight: 'right', ArrowUp: 'up', ArrowDown: 'down', Enter: 'enter'};
    window.addEventListener('keydown', function(e){
      var key = KEYS[e.key];
      if (!key || !window.__qgisClickOn || !window.__qgisKeyGrab) return;
      if (e.target && e.target.closest && e.target.closest('input, select, textarea')) return;
      e.preventDefault();
      e.stopPropagation();
      window.__qgisKeys.push(key);
    }, true);
  }
  window.__qgisClickOn = true;
  var out = JSON.stringify({c: window.__qgisClicks || [], k: window.__qgisKeys || [], a: window.__qgisActions || []});
  window.__qgisClicks = [];
  window.__qgisKeys = [];
  window.__qgisActions = [];
  return out;
})()
"""
CLICKS_OFF_JS = r"""
(function(){
  window.__qgisClickOn = false;
  window.__qgisClicks = [];
  window.__qgisKeys = [];
  window.__qgisActions = [];
})()
"""

# Repères de mesure dans la visionneuse (module de repères du moteur 360°), accrochés à la
# photo : croix jaunes des points de mesure (bleues pour les visées de triangulation), trait
# rouge et étiquette du résultat ; lignes guides en tirets, sans croix et sous les traits :
# droite de mesure d'une largeur (bleue), verticale d'une hauteur (verte). Point sélectionné :
# cercle autour de sa croix. Redessinés seulement si l'état ou la photo change, ou si la
# visionneuse les a effacés (elle vide ses repères en changeant de photo).
MARKS_JS = r"""
(function(state){
  var v = window.__pnxViewer;
  var M = v && v.psv && v.psv._myMarkers;
  if (!M || !M.addMarker) return;
  var pic = v.psv.getPictureId ? (v.psv.getPictureId() || '') : '';
  // Repères de la photo affichée : ceux d'autres photos attendent
  var measures = (state.measures || []).map(function(m){
    return {label: m.label, beside: m.beside, guide: !!m.guide, vertical: !!m.vertical, sighting: !!m.sighting,
            pts: (m.points || []).filter(function(p){ return p.pic === pic && p.yaw !== null && p.pitch !== null; })};
  }).filter(function(m){ return m.pts.length; });
  // Étiquette à côté du trait (hauteur) : du côté du centre de la vue
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
  var present = !measures.length || !!(M.markers && window.__qgisMarkIds && window.__qgisMarkIds.length
                                       && M.markers[window.__qgisMarkIds[0]]);
  if (key === window.__qgisMarkKey && present) return;
  window.__qgisMarkKey = key;
  (window.__qgisMarkIds || []).forEach(function(id){ if (M.markers && M.markers[id]) M.removeMarker(id); });
  var ids = [];
  function add(marker){ ids.push(marker.id); M.addMarker(marker); }
  // Guides d'abord (dessous), en tirets
  measures.forEach(function(m, k){
    if (!m.guide || m.pts.length < 2) return;
    add({id: 'qgis-m' + k + '-guide', polyline: m.pts.map(function(p){ return [p.yaw, p.pitch]; }), zIndex: 0,
         svgStyle: {stroke: m.vertical ? '#2ecc40' : '#00bce6', strokeWidth: '2px', strokeDasharray: '8 5', fill: 'none'}});
  });
  var cross = 'M0 9H18M9 0V18';
  measures.forEach(function(m, k){
    if (m.guide) return;
    m.pts.forEach(function(p, i){
      add({id: 'qgis-m' + k + '-p' + i, position: {yaw: p.yaw, pitch: p.pitch}, path: cross, zIndex: 1,
           anchor: 'center center', svgStyle: {stroke: m.sighting ? '#00bce6' : '#ffeb00', strokeWidth: '1.5px', fill: 'none'}});
      if (p.sel) add({id: 'qgis-m' + k + '-s' + i, position: {yaw: p.yaw, pitch: p.pitch}, zIndex: 3,
                      path: 'M1 12a11 11 0 1 0 22 0a11 11 0 1 0 -22 0', anchor: 'center center',
                      svgStyle: {stroke: '#ffd54f', strokeWidth: '2.5px', fill: 'none'}});
    });
  });
  var lines = [];
  measures.forEach(function(m, k){
    if (m.guide || m.pts.length !== 2) return;
    var id = 'qgis-m' + k + '-line';
    add({id: id, polyline: [[m.pts[0].yaw, m.pts[0].pitch], [m.pts[1].yaw, m.pts[1].pitch]], zIndex: 2,
         svgStyle: {stroke: '#ff5f52', strokeWidth: '2.5px', fill: 'none'}});
    lines.push(id);
  });
  lines.forEach(function(id){  // traits devant les croix
    var mk = M.markers && M.markers[id];
    var el = mk && (mk.domElement || mk.element || mk.$el);
    if (el && el.parentNode) el.parentNode.appendChild(el);
  });
  measures.forEach(function(m, k){
    var pts = m.pts, prefix = 'qgis-m' + k + '-';
    if (m.guide || !m.label) return;
    var at = pts[0];
    if (pts.length === 2) {  // milieu du trait (moyenne des directions)
      var x = 0, y = 0, z = 0;
      pts.forEach(function(p){
        x += Math.cos(p.pitch) * Math.sin(p.yaw); y += Math.sin(p.pitch); z += Math.cos(p.pitch) * Math.cos(p.yaw);
      });
      at = {yaw: Math.atan2(x, z), pitch: Math.atan2(y, Math.sqrt(x * x + z * z))};
    }
    var text = String(m.label).replace(/[&<>]/g, function(c){ return {'&': '&amp;', '<': '&lt;', '>': '&gt;'}[c]; });
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

# Navigation pendant une mesure : un clic sur la photo ne change pas de photo (on écarte
# l'événement « click » du moteur 360°, sauf sur une flèche de navigation), sans toucher au
# glisser ; flèches de navigation masquées pendant la prise de mesure (pas pendant les visées,
# qui demandent de changer de photo) ; curseur « aller ici » qui suit la souris sur le sol masqué.
NAV_BLOCK_JS = r"""
(function(on, hideArrows, hideCursor){
  window.__qgisBlockNav = on;
  var v = window.__pnxViewer;
  if (v && v.psv && !v.psv.__qgisNavPatch2) {
    var orig = v.psv.dispatchEvent;
    v.psv.dispatchEvent = function(e){
      if (window.__qgisBlockNav && e && e.type === 'click') {
        var t = e.data && e.data.target;
        var arrow = t && t.closest && t.closest('.psv-virtual-tour-link');
        if (!arrow) return true;
      }
      return orig.call(this, e);
    };
    v.psv.__qgisNavPatch2 = true;
  }
  // Curseur au sol : image ajoutée au conteneur du moteur 360°, en position absolue,
  // au-dessus (z-index 10) et sans souris ; « visibility », la visionneuse changeant « display »
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
  if (window.__qgisNavState === hideArrows && now - (window.__qgisNavTime || 0) < 1000) return;
  window.__qgisNavState = hideArrows;
  window.__qgisNavTime = now;
  var roots = [document];
  while (roots.length) {
    var r = roots.pop();
    var arrows = r.querySelectorAll ? r.querySelectorAll('.psv-virtual-tour-arrows') : [];
    for (var i = 0; i < arrows.length; i++) {
      if (hideArrows) arrows[i].style.setProperty('visibility', 'hidden', 'important');
      else arrows[i].style.removeProperty('visibility');
    }
    var els = r.querySelectorAll ? r.querySelectorAll('*') : [];
    for (var j = 0; j < els.length; j++) { if (els[j].shadowRoot) roots.push(els[j].shadowRoot); }
  }
})(__ON__, __ARROWS__, __CURSOR__)
"""

# Habillage de la visionneuse pendant une mesure, comme l'application web : liseré (bleu pendant
# les visées, jaune pendant la prise de mesure, orange en calage), état animé en haut à gauche,
# consigne en haut au centre, bouton « ✔ Valider le point triangulé » (halo) dessous quand les
# visées donnent un point, ou « Nouvelle mesure depuis A » une fois la largeur faite, pointeur de
# mesure (cercle de la couleur du liseré, vert en mesure si l'inclinaison est calée) ; rien hors
# mesure. state : {kind: "visee" | "prise" | "calage" | "", label, hint, point, new_measure (nom du
# point de référence ou null), tilted, grab} ; grab : un point est sélectionné (flèches et Entrée
# captées).
OVERLAY_JS = r"""
(function(state){
  window.__qgisKeyGrab = !!state.grab;
  var body = document.body;
  if (!body) return;
  var COLORS = {visee: '#00bce6', prise: '#ffeb00', calage: '#ff6f00'};
  var color = COLORS[state.kind] || '';
  if (!document.getElementById('qgis-overlay-style')) {
    var st = document.createElement('style');
    st.id = 'qgis-overlay-style';
    st.textContent = '#qgis-border{position:fixed;inset:0;border:4px solid;pointer-events:none;z-index:2147483640}'
      + '#qgis-top{position:fixed;top:8px;left:50%;transform:translateX(-50%);display:flex;flex-direction:column;'
      + 'align-items:center;gap:5px;max-width:56%;pointer-events:none;z-index:2147483641;font-family:sans-serif}'
      + '#qgis-hint{margin:0;padding:5px 12px;border-radius:5px;background:rgba(32,33,36,.55);color:#fff;'
      + 'font:600 15px/1.3 sans-serif;text-align:center;text-shadow:0 0 2px rgba(0,0,0,.9)}'
      + '#qgis-keep{pointer-events:auto;padding:6px 14px;border:2px solid #00bce6;border-radius:5px;background:#00bce6;'
      + 'color:#fff;font:600 14px sans-serif;cursor:pointer;text-shadow:0 0 2px rgba(0,0,0,.6);'
      + 'animation:qgishalo 1.6s ease-in-out infinite}'
      + '#qgis-keep:hover{background:#0098bb;border-color:#0098bb}'
      + '#qgis-new{pointer-events:auto;padding:6px 14px;border:2px solid #ffeb00;border-radius:5px;background:#ffeb00;'
      + 'color:#202124;font:600 14px sans-serif;cursor:pointer;box-shadow:0 1px 4px rgba(0,0,0,.5)}'
      + '#qgis-new:hover{background:#e6d200;border-color:#e6d200}'
      + '@keyframes qgishalo{0%,100%{box-shadow:0 1px 4px rgba(0,0,0,.5),0 0 0 0 rgba(0,188,230,.55)}'
      + '50%{box-shadow:0 1px 4px rgba(0,0,0,.5),0 0 10px 5px rgba(0,188,230,.55)}}'
      + '#qgis-state{position:fixed;top:8px;left:10px;display:flex;align-items:center;padding:3px 8px;'
      + 'border-radius:4px;background:rgba(32,33,36,.55);font:500 13px sans-serif;pointer-events:none;'
      + 'z-index:2147483641;text-shadow:0 0 2px rgba(0,0,0,.9)}'
      + '#qgis-state::before{content:"";width:10px;height:10px;margin-right:6px;border-radius:50%;'
      + 'background:currentColor;animation:qgispulse 1.2s ease-in-out infinite}'
      + '@keyframes qgispulse{0%,100%{opacity:1;transform:scale(1)}50%{opacity:.45;transform:scale(.7)}}';
    document.head.appendChild(st);
  }
  function el(id, tag, parent){
    var e = document.getElementById(id);
    if (!e) { e = document.createElement(tag); e.id = id; parent.appendChild(e); }
    return e;
  }
  var border = el('qgis-border', 'div', body);
  border.style.display = color ? 'block' : 'none';
  border.style.borderColor = color;
  var top = el('qgis-top', 'div', body);
  top.style.display = state.hint || state.point || state.new_measure ? 'flex' : 'none';
  var hint = el('qgis-hint', 'p', top);
  hint.textContent = state.hint || '';
  hint.style.display = state.hint ? 'block' : 'none';
  var keep = el('qgis-keep', 'button', top);
  keep.textContent = '✔ Valider le point triangulé';
  keep.style.display = state.point ? 'inline-block' : 'none';
  if (!keep.__qgis) {
    keep.__qgis = true;
    keep.addEventListener('click', function(e){
      e.stopPropagation();
      (window.__qgisActions = window.__qgisActions || []).push('keep');
    });
  }
  var again = el('qgis-new', 'button', top);
  again.textContent = '↺ Nouvelle mesure depuis ' + (state.new_measure || '');
  again.style.display = state.new_measure ? 'inline-block' : 'none';
  if (!again.__qgis) {
    again.__qgis = true;
    again.addEventListener('click', function(e){
      e.stopPropagation();
      (window.__qgisActions = window.__qgisActions || []).push('new_measure');
    });
  }
  var lab = el('qgis-state', 'div', body);
  lab.textContent = state.label || '';
  lab.style.display = state.label ? 'flex' : 'none';
  lab.style.color = color;
  // Pointeur de mesure dans la visionneuse (son shadow DOM)
  var v = window.__pnxViewer;
  var box = v && v.psv && v.psv.container;
  var root = box && box.getRootNode ? box.getRootNode() : null;
  if (root) {
    var css = '';
    if (state.kind) {
      var ring = state.kind === 'prise' && state.tilted ? '#2ecc40' : color;
      css = '.psv-container, .psv-container * { cursor: url("data:image/svg+xml;utf8,'
        + "<svg xmlns='http://www.w3.org/2000/svg' width='33' height='33'>"
        + "<circle cx='16.5' cy='16.5' r='15' fill='none' stroke='" + ring.replace('#', '%23') + "' stroke-width='2.5'/>"
        + "<path d='M16.5 2v11M16.5 20v11M2 16.5h11M20 16.5h11' stroke='%23ff8a80' stroke-width='1.5'/>"
        + "<circle cx='16.5' cy='16.5' r='1' fill='white'/></svg>"
        + '") 16 16, crosshair !important; } .psv-container .psv-virtual-tour-link,'
        + ' .psv-container .psv-virtual-tour-link * { cursor: pointer !important; }';
    }
    var cs = root.getElementById ? root.getElementById('qgis-cursor-style') : null;
    if (!cs && css) {
      cs = document.createElement('style');
      cs.id = 'qgis-cursor-style';
      (root === document ? document.head : root).appendChild(cs);
    }
    if (cs && cs.textContent !== css) cs.textContent = css;
  }
})(__STATE__)
"""
