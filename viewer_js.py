# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Cédric COCHART
# SPDX-License-Identifier: GPL-2.0-or-later
"""Scripts injectés dans la visionneuse web Panoramax (voir viewer_dock.py).

Chaînes JavaScript exécutées par QWebEngineView.page().runJavaScript : test de WebGL,
lecture de la vue, filtrage anisotrope, réticule, clics de mesure, repères des
mesures et blocage de la navigation pendant une mesure. Les marqueurs __XXX__ sont
remplacés avant exécution.
"""

WEBGL_TEST_JS = (
    "(function(){try{var c=document.createElement('canvas');"
    "return !!(c.getContext('webgl2')||c.getContext('webgl')||c.getContext('experimental-webgl'));"
    "}catch(e){return false;}})()"
)

# Moteur WebGL réellement utilisé (carte graphique ou rendu logiciel), pour le diagnostic
WEBGL_RENDERER_JS = (
    "(function(){try{var c=document.createElement('canvas');"
    "var gl=c.getContext('webgl2')||c.getContext('webgl');if(!gl)return '';"
    "var d=gl.getExtension('WEBGL_debug_renderer_info');"
    "return String(d?gl.getParameter(d.UNMASKED_RENDERER_WEBGL):gl.getParameter(gl.RENDERER));"
    "}catch(e){return '';}})()"
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
    + '<circle cx="24" cy="24" r="21.5" stroke="#ffeb00" stroke-width="1.5"/>'  // jaune vif, la croix entière à l'intérieur
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
      + "<circle cx='16.5' cy='16.5' r='15.5' fill='none' stroke='%23ffeb00' stroke-width='1.5'/>"
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
  // Points en croix jaune vif fines (visibles sur la peinture routière blanche) centrées sur le clic, le trait va d'un centre à l'autre.
  // Les traits passent devant toutes les croix : ajoutés après elles (même couche SVG,
  // ordre d'ajout), avec un zIndex plus haut, et replacés en fin de leur conteneur.
  var cross = 'M0 9H18M9 0V18';
  measures.forEach(function(m, k){
    m.pts.forEach(function(p, i){
      add({id: 'qgis-m' + k + '-p' + i, position: {yaw: p.yaw, pitch: p.pitch}, path: cross, zIndex: 1,
           anchor: 'center center', svgStyle: {stroke: '#ffeb00', strokeWidth: '1.5px', fill: 'none'}});
      // Point sélectionné pour être visé à nouveau : cercle jaune autour de la croix
      if (p.sel) add({id: 'qgis-m' + k + '-s' + i, position: {yaw: p.yaw, pitch: p.pitch}, zIndex: 3,
                      path: 'M1 12a11 11 0 1 0 22 0a11 11 0 1 0 -22 0', anchor: 'center center',
                      svgStyle: {stroke: '#ffd54f', strokeWidth: '2.5px', fill: 'none'}});
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
