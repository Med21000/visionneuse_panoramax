# Tests automatiques

Ces tests vérifient les calculs du plugin : triangulation, points 3D, mesures au sol,
calage des photos, profil du terrain, précision GPS, mesures et leur recalage sur la carte,
filtre des photos. Une seconde série, avec le vrai QGIS, vérifie l'enregistrement des
mesures dans les couches et le GeoPackage, la conservation du calage, l'altitude NGF,
l'opacité du filaire et le panneau, avec la visionneuse de secours. La visionneuse web
(QtWebEngine ne fonctionne pas hors écran) et le dessin sur la carte ne sont pas testés.

Ils ne sont pas livrés avec le plugin : le dossier `tests/` est exclu du zip de release
(`.gitattributes`).

## Lancer les tests

Depuis le dossier du plugin, avec Python 3.12 ou plus récent, sans QGIS ; numpy (fourni avec
QGIS) est nécessaire aux tests des verticales de `test_autotilt.py`, sautés sans lui :

```
python3 -m unittest discover -s tests
```

Options utiles :

```
python3 -m unittest discover -s tests -v                      # détail de chaque test
python3 -m unittest discover -s tests -p "test_terrain.py"     # un seul fichier
python3 -m unittest discover -s tests -k test_oblique_road     # tests dont le nom contient ce motif
```

Les tests se lancent avec `discover -s tests` (et pas `python3 -m unittest tests/…`) :
les fichiers importent `support.py` comme un module voisin.

Sur GitHub, le workflow `.github/workflows/tests.yml` les lance à chaque push sur `main`
ou `dev` et à chaque pull request, sous Python 3.12, 3.13 et 3.14, avec numpy. Il vérifie aussi que
tout le code se compile et passe pyflakes (noms inconnus, imports inutilisés).

### Tests avec QGIS

Les tests de `tests/qgis/` demandent une installation de QGIS 4 dont Python importe
`qgis` (PyQGIS). Ils se lancent à part, sur la machine du développeur :

```
python3 -m unittest discover -s tests/qgis
```

Ils démarrent QGIS sans fenêtre (`QT_QPA_PLATFORM=offscreen`) et redirigent ses réglages
vers un dossier temporaire : le profil QGIS de l'utilisateur n'est ni lu ni modifié. Ils
ne tournent pas sur GitHub, où QGIS n'est pas installé, et la série principale ne les
lance pas (le dossier n'est pas un paquet Python). Ils ne peuvent pas tourner dans le
même processus que la série principale, qui remplace `qgis` par un faux paquet.

## Principe : des scènes à réponse connue

Chaque test construit une scène synthétique dont on connaît la vérité, en déduit ce
que l'utilisateur aurait cliqué, puis vérifie que le plugin retrouve la vérité. Par
exemple, pour une largeur de chaussée :

1. on place deux bords de route au sol, à 7 m l'un de l'autre en travers de l'axe ;
2. on calcule le cap et l'élévation sous lesquels la caméra (à 1,90 m) voit chacun
   d'eux : ce sont les clics ;
3. on donne ces clics à l'outil de mesure et on vérifie qu'il trouve 7 m.

Les scènes sont placées à Dijon (`support.LON0`, `support.LAT0`), sur un sol plat à
l'altitude 0, sauf quand le terrain est l'objet du test. Les positions s'écrivent en
mètres (est, nord) autour de ce point, et `support.point(est, nord)` les convertit en
longitude et latitude.

Les tests contrôlent aussi les refus : clics incohérents, visées parallèles, point
trop loin, repère aberrant… Le plugin doit alors lever l'erreur prévue, avec un
message destiné à l'utilisateur, et ne pas renvoyer un résultat faux.

## Fonctionnement sans QGIS

`support.py` est importé en premier par chaque fichier de test. Il :

- enregistre le dossier du dépôt comme paquet `visionneuse_panoramax`, quel que soit
  son nom sur le disque, pour que les imports relatifs des modules (`from . import
  ground`) fonctionnent ;
- fournit `install_qgis_stub()`, un faux paquet `qgis` pour les modules qui importent
  QGIS (`terrain.py`, `measure_tool.py`, `measure_panel.py`). Toute classe Qt ou QGIS y est une coquille
  vide (`Stub`) : on peut l'instancier, l'appeler, lire n'importe quel attribut ou en
  hériter. Les calculs s'exécutent donc réellement, et ce qui touche à l'affichage ou
  au réseau est remplacé dans les tests eux-mêmes ;
- fournit les outils des scènes : `point`, `bearing` (cap d'un point vers un autre),
  `distance`.

Les modules `triangulation.py`, `geometry.py`, `ground.py` et `calibration.py` ne
dépendent pas de QGIS : ils sont testés directement, sans faux paquet.

## Contenu

| Fichier | Tests | Ce qui est vérifié |
|---|---|---|
| `test_triangulation.py` | 9 | Conversions de coordonnées, intersection de deux ou trois visées, visées bruitées, incertitude qui augmente avec l'erreur GPS, erreur de visée selon le matériel. Refus : une seule visée, visées parallèles, croisement derrière les photos, point à plus de 500 m. |
| `test_ground.py` | 10 | Direction d'un pixel de la vue, intersection du rayon avec un sol plat, en pente ou avec un talus, hauteur d'un poteau ou d'un mur cliqué de côté, profil recalé sur le sol sous la caméra. Refus : visée qui ne touche pas le sol, pied et sommet sur deux photos, sommet sous le pied. |
| `test_geometry.py` | 12 | Points sur une façade, sur le plan vertical face à la caméra, sur un plan horizontal ; triangulation 3D ; décomposition d'une distance (horizontale, verticale, le long de la route, en travers) ; résolution d'un système 3 × 3. Refus : visée parallèle au plan, plan derrière la caméra ou trop loin. |
| `test_calibration.py` | 19 | Inclinaison retrouvée à partir d'un ou deux objets verticaux, correction des directions ; hauteur de caméra à partir d'une longueur au sol ou d'une hauteur connue ; recalage du cap et de la position par des repères, portée de 300 m ; refus des repères trop proches, invraisemblables ou contredits par les précédents ; enregistrement et relecture du calage, données abîmées, ajout d'un calage enregistré ailleurs sans écraser le calage en cours. |
| `test_terrain.py` | 19 | Profil réel du LiDAR HD (trottoir suivi, points aberrants écartés, prolongement au-delà des données, profil lacunaire refusé) ; droite de pente robuste (insensible à un fossé ou à une rivière sous un pont), qui suit la rue près de la photo et non le relief lointain, altitudes manquantes ; option « Mesure sur un pont » (sol plat, aucun service interrogé) ; MNT LiDAR HD d'abord, repli silencieux sur le RGE ALTI ; enchaînement des sources : IGN, puis OpenTopoData, puis Open-Meteo, puis sol plat, avec la raison de chaque source écartée ; cache de la session : profil d'une même visée calculé une fois, requêtes simultanées regroupées, source sans données plus interrogée dans la zone, sol plat dû à des services injoignables non gardé. Les services sont simulés par un faux `api.fetch_json` (`FakeServices`) qui renvoie les altitudes d'un terrain de pente connue. |
| `test_measure_tool.py` | 24 | Préréglages (largeur perpendiculaire à la chaussée, sur une route droite ou en biais, longueur parallèle à la route, hauteur), hauteur de caméra calée, mesures gardées à l'écran ; mesure libre sur le sol, une façade, le plan vertical, un plan horizontal, par triangulation 3D ; recalage d'un point sur la carte et nouvelle visée ; deux clics dont les profils de terrain diffèrent sous la caméra (valeur exacte, repères à l'endroit cliqué) ; hauteur de caméra déduite de l'altitude GPS (position vérifiée), prioritaire sur le calage, ignorée si le sol est imprécis ou la hauteur invraisemblable. ; clics corrigés après coup quand l'inclinaison automatique arrive ; largeur perpendiculaire avec l'axe de la route lu au milieu des bords (BD TOPO), d'abord avec la direction du véhicule en attendant. Le dessin sur la carte est remplacé par `FakeItem`. |
| `test_autotilt.py` | 13 | Inclinaison automatique : inclinaison d'une photo synthétique (pans de mur tout autour d'une caméra penchée de façon connue) retrouvée à 0,05° près, photo redressée, cap de la photo ajouté ; refus avec trop peu de segments ; un seul pan de mur ou des arêtes d'un seul côté : incertitude annoncée qui couvre l'erreur. Modèle LiDAR : composantes avant et droite, pente et dévers du plan du sol (grille sous le véhicule), modèle de la séquence (décalage de fixation, trois photos au moins, refus si la caméra ne suit pas le véhicule), ordre de priorité (à la main, verticales, LiDAR), enregistrement et relecture. Vérificateur : résultat rangé, rien pour une photo classique ou déjà analysée, panne du LiDAR non gardée, requêtes partagées. Sauté sans numpy. |
| `test_roads.py` | 11 | Axe de la route au point mesuré : tronçon BD TOPO droit, le plus proche de deux rues parallèles, virage suivi au point mesuré (et non à son entrée), carrefour en croix (la rue que traverse la mesure), rien trop loin ou parallèle au segment cliqué, lecture du GeoJSON ; trajectoire de la séquence au point mesuré (axe principal des photos voisines), refus si trop peu de photos, à l'arrêt ou d'une autre séquence ; fournisseur : lecture puis cache par maille, trajectoire quand la BD TOPO n'a rien, panne non réessayée tout de suite. |
| `test_api.py` | 9 | Métadonnées des photos : précision déclarée dans l'EXIF (sinon celle de Panoramax), correction différentielle, altitude GPS (au-dessus ou au-dessous de l'ellipsoïde), recherche limitée à une séquence ; réponse JSON (une erreur du callback n'est pas prise pour un JSON invalide) ; cache des fiches : requête partagée, fiche arrivée dans le désordre, réponse de l'ancienne instance ignorée. |
| `test_calibration_tool.py` | 11 | Outil de calage, du clic au calage enregistré : inclinaison (correction partielle puis complète, sommet sur une autre photo, clics inversés), hauteur de caméra sur une longueur au sol ou une hauteur connue, repère cliqué dans la photo puis sur la carte (position GPS d'origine, matériel de relevé), effacement du calage, repères dans la visionneuse. |
| `test_filters.py` | 11 | Expressions du filtre sur les tuiles (séquences, photos, grille de densité, apostrophes), filtre des fiches STAC (chaque critère, valeurs absentes), résumé du filtre, producteur d'une photo, mémorisation des réglages (simulés en mémoire). |
| `test_precision.py` | 16 | Précision GPS vérifiée sur la séquence (services simulés) : séquence RTK reconnue, DGPS non reconnu, correction différentielle exigée, précision déclarée dans l'EXIF (LiDAR contrôlé quand même), trop peu d'altitudes, une vérification par séquence, service en panne (résultat non gardé, vérification refaite), photos sans séquence vérifiées chacune ; hauteur de caméra calculée proposée (séquence vérifiée ou précision déclarée, refusée si invraisemblable) ; mode dégradé dès qu'il manque la position vérifiée ou le LiDAR HD, raisons et ligne d'état, avec l'inclinaison (avertissement si elle n'est pas calée). |
| `test_output.py` | 5 | Sans QGIS (couches simulées) : mise à jour d'une entité sans toucher au commentaire ni aux champs ajoutés par l'utilisateur ; une mesure enregistrée à nouveau dans une autre couche y est ajoutée, sans écraser l'entité de même fid. |
| `test_measure_panel.py` | 5 | Texte du calage rappelé sous les mesures : inclinaison (à la main, par les verticales, par le LiDAR), hauteur de caméra, cap et position recalés, recalage hors de portée. |
| `qgis/test_panel.py` | 14 | Avec QGIS, panneau hors écran (visionneuse de secours, services simulés) : widgets de chaque mode de mesure et de chaque surface, réglages et boutons (signaux émis), mode compact (icônes, lignes masquées, aide dans la bulle, boutons alignés), filtre réglé puis remis à zéro, photo affichée et navigation, clic de mesure et visée, rappel du calage, image qui ne bouge pas (résultat long, consigne suivante, rappel du calage), fenêtre du mode dégradé (à l'activation de la mesure, pas au changement de photo, « ne plus afficher », vérification en cours), hauteur de caméra calculée proposée (bouton selon la photo et le mode, champ rempli au clic seulement, sans faire sauter l'image), inclinaison automatique (lancée en mesure seulement, ligne d'état pendant et après le calcul, clic suivant corrigé). |
| `qgis/test_autotilt.py` | 2 | Avec QGIS : photo JPEG synthétique décodée réduite et rognée à la bande utile ; inclinaison calculée en tâche de fond (QgsTask) et rangée dans le calage, à 0,05° près. |
| `qgis/test_output.py` | 16 | Avec QGIS : couche temporaire ou GeoPackage, deux tables dans le même fichier, table existante réutilisée ou de structure incompatible, annulation ; ajout et mise à jour d'entités (couche en édition ou non) ; boîte de dialogue du choix (boutons et fenêtre de fichier simulés) ; enregistrement de bout en bout d'une mesure, puis mise à jour après recalage (commentaire conservé), et d'un point triangulé ; mesure enregistrée à nouveau dans un autre GeoPackage sans écraser l'entité de même fid. |
| `qgis/test_altitude.py` | 2 | Avec QGIS : altitude GPS ellipsoïdale convertie en altitude NGF par la grille RAF20 (Pouilly-en-Auxois : géoïde 47,59 m, caméra à 1,88 m du sol LiDAR HD ; test sauté si la grille ne peut pas être téléchargée), rien hors de France. |
| `qgis/test_layers.py` | 4 | Avec QGIS : couche filaire créée à 60 % d'opacité, puis à la dernière opacité choisie ; opacité conservée par le filtre ; couche chargée avec un projet : opacité du projet gardée, changement mémorisé ; réglage abîmé. |
| `qgis/test_calibration_storage.py` | 3 | Avec QGIS : calage conservé dans le profil d'une session et d'un projet à l'autre, effacement, réglage abîmé ; reprise du calage d'un vrai projet `.qgz` enregistré par une version précédente, sans écraser le calage plus récent, puis retrait du projet. |

## Ajouter un test

1. Choisir le fichier du module concerné, ou créer `test_<module>.py` en commençant
   par `from support import …` (et `install_qgis_stub()` si le module importe QGIS).
   Ce qui a besoin du vrai QGIS (couches, fichiers, géométries) va dans `tests/qgis/`,
   en commençant par `from qgis_support import module`.
2. Construire la scène à partir de la vérité : placer les objets, puis calculer les
   clics avec les outils du fichier (`click` dans `test_measure_tool.py`, `sighting`
   dans `test_triangulation.py`, `vertical_object` dans `test_calibration.py`…).
3. Comparer au résultat attendu avec une tolérance adaptée : `places=6` (au micromètre)
   pour un calcul exact, plus large quand le calcul est volontairement approché
   (l'inclinaison est légèrement amortie, l'a priori freine le recalage du cap).
4. Vérifier que le test détecte bien une erreur : modifier temporairement le calcul
   testé (changer un signe, inverser sin et cos) et s'assurer que le test échoue.
   Une scène trop symétrique peut masquer une erreur : une route orientée plein nord
   ne détecte pas une erreur sur la composante est, d'où le test de la route en biais.
