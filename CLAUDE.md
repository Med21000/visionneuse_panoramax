# Visionneuse Panoramax — notes pour Claude Code

Plugin QGIS 4 (Python, Qt6) qui intègre la visionneuse Panoramax et permet des mesures à
l'écran (largeurs, hauteurs, distances 3D, triangulation, calage des photos). Auteur :
Cédric COCHART. Utilisé dans un contexte de conseil départemental (voirie).

Historique des décisions, revue de code et feuille de route : `docs/feuille-de-route.md`.
À lire avant tout travail de fond sur le plugin ou sur l'application web envisagée.

## Règles du projet

- Nom : « Visionneuse Panoramax » partout. Jamais « Panoramax Viewer » (nom d'un autre plugin).
- QGIS 4.0 minimum, Qt6 : énumérations qualifiées (`Qt.MouseButton.LeftButton`,
  `QgsVectorFileWriter.WriterError.NoError`…).
- Français partout : interface, messages, docstrings, commentaires, messages de commit.
  Typographie française (« », espace avant `:`, `;`, `?`).
- En-tête SPDX dans chaque fichier Python (`GPL-2.0-or-later`, © Cédric COCHART).
- Réseau : uniquement par `api.fetch` / `api.fetch_json` (asynchrone, `QgsNetworkAccessManager`).
  Minimiser les appels à l'API : caches de `api.py` (photo courante + voisines),
  `terrain.TerrainProvider` (profils, zones sans données), `precision.PrecisionChecker`
  (par séquence), `autotilt.TiltChecker` (inclinaison par photo, conservée dans le calage),
  `roads.RoadAxisProvider` (tronçons BD TOPO par maille), `buildings.BuildingProvider`
  (bâtiments du cadastre et grilles MNS/MNT LiDAR par maille). Ne jamais mettre en cache un résultat obtenu malgré une panne.
- Réglages QGIS sous le préfixe `visionneuse_panoramax/`.
- Couches de résultats : `output.write_feature` ne réécrit jamais `fid` ni `commentaire`
  à la mise à jour ; un fid de mesure n'est valable que dans sa couche (`Measure.layer_id`).
- Callbacks asynchrones : vérifier qu'ils ne sont pas périmés (photo courante :
  `api.requested_id()`, plugin déchargé : `self._unloaded`).

## Tests

```
python3 -m unittest discover -s tests          # sans QGIS (faux paquet qgis) — lancés en CI
python3 -m unittest discover -s tests/qgis     # avec QGIS 4, sur le poste uniquement
pyflakes .
```

Principe : scènes synthétiques à réponse connue (voir `tests/README.md`, tenir son tableau
à jour). Tout correctif s'accompagne d'un test qui échoue sur l'ancien code.

## Git

- Branches : `main` (publiée), `dev` (intégration). Les PR visent `dev`.
- CI GitHub : tests Python 3.12 à 3.14 + pyflakes, à chaque push et PR.
- Release : tag `vX.Y.Z`, zip `visionneuse_panoramax-X.Y.Z.zip` ; version et changelog
  dans `metadata.txt` (décidés par l'auteur). `tests/`, `.github/`, `CLAUDE.md` et
  `docs/feuille-de-route.md` sont exclus du zip (`.gitattributes`).
