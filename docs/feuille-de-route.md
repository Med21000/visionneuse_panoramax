# Feuille de route et décisions

Journal des revues et des choix d'architecture, à lire avant un travail de fond. Le plus
récent en premier. Non livré avec le plugin (exclu du zip de release).

## 2026-10-12 — Revue de code, corrections, application web pour les agents

### Revue de code (version 1.2.0)

État de départ : 129 tests verts, pyflakes propre, modules bien découpés.

Corrigé dans la PR #17 (`fix/revue-robustesse` → `dev`) :

- **Pertes de données**
  - `output.write_feature` réécrivait tous les champs à la mise à jour, dont `commentaire`
    (vide) : le commentaire saisi par l'utilisateur était effacé après un recalage. Seuls
    les champs calculés (`managed`) sont réécrits ; `fid` et `commentaire` jamais.
  - `Measure.fid` était réutilisé dans n'importe quelle couche : un nouvel enregistrement
    dans un autre GeoPackage écrasait l'entité de même fid. Le fid est lié à
    `Measure.layer_id`.
- **API** (`api.py`, `viewer_dock.py`)
  - `fetch_json` appelait le callback dans le `try` : une `ValueError` du callback le
    faisait rappeler avec « JSON invalide ».
  - Fiches arrivées dans le désordre : ignorées (`api.requested_id()`), sans effet sur le cache.
  - Changement d'instance : réponses d'avant ignorées (génération du cache).
  - `api.abort_all()` au déchargement ; lecture de la page suspendue fenêtre réduite.
- **Précision GPS** (`core/precision.py`) : un résultat obtenu malgré une panne n'est plus gardé
  pour la session (champ `transient`) ; photos sans séquence vérifiées chacune.
- **Terrain** (`core/terrain.py`) : cache des profils par visée (lon/lat à 1e-6, cap au dixième de degré),
  requêtes simultanées regroupées, source sans données mémorisée par maille de ~100 m
  (`COVERAGE_CELL`) ; les pannes ne sont pas mémorisées.
- **Plugin** : réponses tardives ignorées après `unload` ; `QgsCsException` interceptée au
  clic sur la carte ; « Recherche impossible : réponse vide » au lieu de « None ».
- Tests : 146 sans QGIS (17 nouveaux, dont `tests/test_output.py` avec couches simulées),
  4 nouveaux dans `tests/qgis/test_output.py`, exécutés avec QGIS 4 à la fusion dans `dev` (verts).
- Fusion dans `dev` avec le profil réel LiDAR HD et la hauteur de caméra proposée : cache des
  profils au dixième de degré (au degré, le profil réel pourrait passer de l'autre côté d'une
  bordure), hauteur de caméra calculée gardée avec `transient`, faux `api.requested_id` dans
  `tests/qgis/test_panel.py`.

Relevé mais non traité :

- `viewer_dock._poll_url` : jusqu'à 5–6 `runJavaScript` toutes les 100 ms pendant une mesure,
  et parcours complet du DOM et des shadow roots chaque seconde dans `LIVE_VIEW_JS`. Piste :
  un seul script par tick, intervalle 200–250 ms quand rien ne bouge.
- `viewer_js.py` dépend d'internes de Photo Sphere Viewer et de `<pnx-viewer>` :
  `psv._myMarkers`, `psv.state.vFov`, `renderer.scene`, `dispatchEvent` patché, curseur au sol
  repéré par `z-index: 10`, constantes three.js 1006/1008. Une mise à jour de
  explore.panoramax.fr peut casser les mesures sans erreur visible (les `try` JS renvoient
  `''`). Au minimum : détecter l'absence de ces points d'accroche et l'afficher dans la
  ligne d'état.
- Instance saisie à la main : `http://` accepté (laissé volontairement pour d'éventuelles
  instances internes).
- Tests `tests/qgis` absents de la CI (piste : image Docker QGIS 4).

### Plugin ou application autonome ?

Décision : **garder le plugin QGIS** pour les utilisateurs de QGIS. Sa valeur vient de QGIS
(fonds de carte pour le recalage et le calage, SCR, couches, GeoPackage, croisement avec
les données métier). Une application de bureau Python/Qt aurait les mêmes faiblesses
(QtWebEngine, WebGL, site piloté de l'extérieur).

Amélioration envisagée pour le plugin : embarquer la visionneuse au lieu de piloter
explore.panoramax.fr — page HTML locale chargeant `@panoramax/web-viewer`, dialogue avec
Python par QWebChannel (événements au lieu d'interrogations), API publique au lieu
d'internes.

### Application web pour les agents (au bureau)

Besoin : des agents qui n'utilisent pas QGIS, au bureau (poste fixe, grand écran, souris,
connexion filaire), pour préparer et vérifier leurs interventions. Pas de tablette ni de
4G à prévoir.

Choix proposés :

- **Application web** (pas d'installation, postes verrouillés par la DSI), site statique
  si possible : services publics appelés depuis le navigateur.
- Carte **MapLibre** : filaire Panoramax (mêmes tuiles vectorielles que le plugin), fonds
  Géoplateforme IGN (orthophoto, Plan IGN, cadastre), sans clé.
- Visionneuse officielle **`@panoramax/web-viewer`** intégrée à la page.
- **Cœur de calcul dans Pyodide** : le même code Python que le plugin, sans portage ; les
  tests existants restent la référence unique. (Au bureau, le téléchargement initial de
  Pyodide est acceptable ; un portage TypeScript ne se justifierait que si le démarrage
  gênait.)
- Interface réduite : largeur, hauteur, longueur, position d'un objet ; calage avancé
  seulement si nécessaire.
- **Indicateur de fiabilité** très visible, d'après `core/precision.py` : vert (position RTK
  vérifiée + LiDAR HD, ex. imajbox), orange (à caler), rouge (mesure indicative).
- Export CSV / GeoJSON des mesures ; écriture dans une base partagée plus tard.

Condition préalable, utile au plugin quelle que soit la suite : **séparer le cœur de QGIS**.

- Déjà indépendants : `triangulation`, `geometry`, `ground`, `calibration`.
- À découpler : `terrain` et `precision` (passent par `api.py`, réseau QGIS) et la logique
  de mesure de `core/measures.py` (mêlée au dessin sur la carte).
- Cible : un paquet `core` sans import QGIS, recevant une fonction d'accès réseau en
  paramètre, et des adaptateurs fins (QGIS : `QgsNetworkAccessManager` ; web : `fetch`).

À vérifier tôt : depuis un poste d'agent, que le proxy ou le pare-feu du département laisse
passer, depuis le navigateur, Panoramax, la Géoplateforme IGN et les services d'altitude de
secours (OpenTopoData, Open-Meteo), y compris leurs en-têtes CORS.

Étapes :

1. Séparer le cœur de QGIS dans le plugin (sans changement de comportement, tests verts) :
   **fait le 13/10/2026** (branche `core`). Paquet `core/` sans import de QGIS
   (triangulation, géométrie, sol, calage, métadonnées des photos, terrain, précision, axe de
   la route, façades, inclinaison automatique, logique de mesure) ; services de l'hôte dans
   `core/host.py` (réseau, altitude NGF, décodage d'une photo, tâches de fond), remplis par
   `qgis_host.py` dans le plugin.
2. Maquette web : MapLibre, visionneuse officielle, cœur dans Pyodide, largeur, hauteur,
   export CSV/GeoJSON.
3. Indicateur de fiabilité, puis triangulation.
