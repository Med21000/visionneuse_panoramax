# Visionneuse Panoramax pour QGIS

[![Tests](https://github.com/medlab-code/visionneuse_panoramax/actions/workflows/tests.yml/badge.svg)](https://github.com/medlab-code/visionneuse_panoramax/actions/workflows/tests.yml)

Plugin QGIS 4 qui intègre la visionneuse [Panoramax](https://panoramax.fr) avec des mesures appuyées sur des points triangulés : hauteur d'un objet, largeur d'une chaussée, distance entre deux points, directement dans la photo.

> **Prérequis : QGIS 4.0 ou plus récent.** L'extension ne fonctionne qu'avec QGIS 4 et les versions suivantes.

## Fonctionnalités

- Mesures (bouton « 📐 Mesure »), appuyées sur des points **triangulés** (visés depuis au moins deux photos) :
  - hauteur d'un objet, largeur perpendiculaire à la chaussée, mesure libre (distance entre points triangulés) ;
  - points, traits et valeurs affichés dans la visionneuse (sur toutes les photos) et sur la carte, conservés à l'écran jusqu'à « Tout effacer » ;
  - recalage d'un point par un clic sur sa croix, puis un nouveau clic ou les flèches du clavier (pas de 1 cm) ;
  - enregistrement des mesures, des points triangulés et des distances dans des couches QGIS, dans un fichier GeoPackage ou en couche temporaire ;
  - calage des photos : correction de l'inclinaison de la caméra (automatique ou sur des objets verticaux), recalage du cap et de la position sur des repères de la carte.
- Visionneuse Panoramax dans un panneau ancré, avec choix de l'instance.
- Mode compact du panneau : le chevron à droite de la barre réduit les boutons à leur icône et masque les lignes Instance, options et aide de la mesure (l'aide passe dans la bulle du bouton « 📐 Mesure »). Le choix est mémorisé.
- Filaire Panoramax (séquences et photos) en couche de tuiles vectorielles, semi-transparent à la création (opacité de 60 %) pour laisser voir le fond de carte ; l'opacité qu'on lui donne ensuite (*Propriétés › Symbologie › Rendu de la couche*) est mémorisée pour les filaires créés plus tard.
- Filtre des photos (bouton « Filtre ») : n'afficher dans le filaire, et n'ouvrir par un clic sur la carte, que les photos prises depuis une date, en 360° ou classiques, de précision GPS donnée (≤ 2 m pour le matériel de relevé, ≤ 4 m), ou d'un producteur (liste déroulante qui propose celui de la photo affichée). Le filtre est mémorisé ; « Filtre ● » signale qu'il est actif. Aux petites échelles (grille de densité), seul le type de caméra est filtré.
- Synchronisation carte ↔ visionneuse : curseur de vue (position, direction et ouverture) sur la carte, clic sur la carte pour ouvrir la photo la plus proche, carte QGIS recentrée sur la photo et cadrée sur la mesure en cours (option « Centrer la carte QGIS sur la photo »).
- Capture Full HD de la vue (« 📷 Capture HD »), recalculée à partir de la photo originale.
- Capture vignette (« 🖼 Capture vignette ») : la visionneuse telle qu'affichée, mesures comprises, enregistrée en PNG et copiée dans le presse-papiers.
- Option « Filtrage anisotrope » (décochée par défaut, mémorisée) : atténue le crénelage et le scintillement des détails fins vus de loin en gardant l'image nette, au prix d'une navigation un peu moins fluide.
- Sans QtWebEngine ou sans WebGL, le panneau explique ce qui manque (voir ci-dessous).

### Composants nécessaires : QtWebEngine et WebGL

La visionneuse Panoramax est une page web : le plugin l'affiche avec le moteur web de Qt, **QtWebEngine** (module Python PyQt6-WebEngine), qui doit prendre en charge **WebGL** (accélération graphique). S'il manque l'un ou l'autre sur le poste, le panneau affiche, à la place de la visionneuse, un message qui dit ce qui manque et comment l'installer :

- QtWebEngine absent : sous Windows, relancer l'installateur OSGeo4W en mode avancé et ajouter le paquet *python3-pyqt6-webengine* (ou *qt6-webengine*) ; sous Linux, installer le paquet de la distribution (*python3-pyqt6.qtwebengine* sous Debian et Ubuntu, *python3-pyqt6-webengine* sous Fedora) ; sous macOS, l'installateur officiel de QGIS le fournit. Redémarrer ensuite QGIS.
- WebGL absent : mettre à jour le pilote de la carte graphique, vérifier que l'accélération 3D n'est pas désactivée (bureau à distance, machine virtuelle, pilote générique), puis redémarrer QGIS.

En attendant, le bouton « Ouvrir la visionneuse dans le navigateur » ouvre la photo choisie sur la carte ; le curseur de vue, le filtre et la capture HD restent disponibles, **pas les mesures** ni la capture vignette.

## Mesures

Cliquer sur « 📐 Mesure » puis choisir le mode. Toutes les mesures s'appuient sur des **points triangulés** : un même point est visé depuis au moins deux photos, et sa position est l'intersection des visées. La profondeur vient ainsi de la triangulation, et non du terrain ni de la hauteur de caméra, auxquels les mesures sur une seule photo étaient trop sensibles. Les méthodes de calcul, les fonctions de calage, les sources d'erreur et les validations sont détaillées dans [docs/MESURES.md](docs/MESURES.md).

La précision dépend donc de celle de la position et du cap des photos : les mesures sont fiables sur des photos à **position GPS RTK vérifiée** (comme celles de l'imajbox). Ailleurs, elles restent possibles, mais le résultat prévient qu'elles seront probablement incohérentes.

![Mesures dans la visionneuse : hauteur d'une porte (2,53 m), largeur du passage piéton (5,00 m) et d'une bande (0,47 m), points en croix jaunes](docs/images/mesures.png)

### Viser un point

Pendant les visées, un clic dans la photo vise l'objet (croix bleue) ; les flèches de navigation de la visionneuse restent utilisables pour changer de photo, et une seule visée est gardée par photo. Dès que deux visées se croisent, le bouton **« ✔ Valider le point triangulé »** apparaît en haut de la visionneuse ; une troisième visée, plus latérale, améliore et contrôle le point. Les visées sont tracées sur la carte (pointillés rouges), avec l'angle de croisement et l'incertitude du point. Chaque visée est pondérée : une visée lointaine ou une photo mal positionnée compte moins. Quand l'EXIF de la photo donne le cap au centième de degré, il remplace le cap arrondi au degré de Panoramax.

### Modes

- **Hauteur d'un objet**
  1. Viser le **pied** de l'objet depuis au moins deux photos, puis « Valider le point triangulé » : c'est le point de référence, et le pied de la hauteur.
  2. Cliquer seulement le **sommet**, depuis une photo qui a servi à la visée : le pied est la visée faite depuis elle. Depuis une autre photo, cliquer le pied puis le sommet.

  Une fois la hauteur faite, les clics ne servent plus qu'à l'ajuster (clic sur une de ses croix, puis nouveau clic sur la photo de la mesure ou flèches ↑ ↓ : 1 cm sur la verticale). Le point de référence se recale de même dès qu'il est validé, sur sa verticale (seule l'élévation du clic compte). Pour une autre hauteur depuis le même point, depuis n'importe quelle photo, cliquer « ↺ Nouvelle mesure depuis A » dans la visionneuse ; pour un autre point, « Nouveau point de référence ». Une verticale verte le repère dans la photo.
- **Largeur perpendiculaire à la chaussée**
  1. Viser le **premier bord** (pied de bordure, marquage…) depuis au moins deux photos, puis « Valider le point triangulé ». Le plugin trace la **droite de mesure**, perpendiculaire à l'axe de la route en ce point (tronçon de la BD TOPO de l'IGN, sinon trajectoire de la séquence), en bleu dans la photo et sur la carte.
  2. Cliquer le **bord opposé** depuis une photo prise quelques mètres avant ou après le point, pas juste en face : seule la direction du clic compte, le bord est là où elle coupe la droite.

  Une fois la largeur faite, les clics ne servent plus qu'à l'ajuster (clic sur une de ses croix, puis nouveau clic ou flèches : 1 cm le long de la droite de mesure). Pour une autre largeur depuis le même point, cliquer « ↺ Nouvelle mesure depuis A » dans la visionneuse ; pour un autre point, « Nouveau point de référence ».
- **Mesure libre (points triangulés)** : viser chaque point depuis au moins deux photos, puis « Valider le point triangulé » ; à partir du deuxième, la distance horizontale au point précédent s'affiche, et la carte se cadre sur le segment.

À l'étape 2 de la hauteur et de la largeur, les flèches de navigation sont masquées (elles gênent les clics de mesure) : on change de photo par la carte.

**Repères visuels** : liseré de la visionneuse, étiquette animée en haut à gauche et cercle du pointeur **bleus** pendant les visées, **jaunes** pendant la prise de mesure (cercle vert si l'inclinaison de la photo est calée), **orange** en calage ; consigne en haut au centre. Points, traits et étiquettes restent visibles sur toutes les photos : là où ils ont été cliqués, ou projetés au sol (à la hauteur de caméra du réglage « Caméra à ») sur les autres photos. Sur la carte : points triangulés gardés (disque rouge et nom), distances entre eux, droite de mesure (bande bleue), visées du calage du cap en pointillés noirs.

**Recaler un point** : un clic (gauche ou droit) sur sa croix le sélectionne (cercle jaune), puis :

- clic : nouvelle position (largeur : bord ou point de référence, là où la direction du clic coupe la droite de mesure, dont ils ne sortent jamais ; le point de référence se recale ainsi dès qu'il est validé ; hauteur : sur la photo de la mesure) ;
- flèches du clavier, par pas de 1 cm : largeur, le long de la droite de mesure (← → dans le sens vu sur la photo, ↑ ↓ en s'éloignant ou en se rapprochant de la caméra) ; hauteur, ↑ ↓ montent ou descendent le point ;
- **Entrée** pour terminer.

Le pied d'une hauteur peut aussi être glissé sur la carte QGIS : la hauteur perd alors son calage sur le point triangulé. « Effacer » supprime la mesure en cours (ou la sélection), « Tout effacer » toutes les mesures et tous les points affichés.

**Enregistrer les mesures** : le bouton « Enregistrer les mesures » ajoute à la couche « Panoramax – mesures » les hauteurs et largeurs terminées, et les distances entre points triangulés ; les points gardés vont dans la couche « Panoramax – points triangulés » (nom, nombre de visées, croisement, incertitude, photos). Chaque mesure y est un segment étiqueté de sa valeur, avec ses attributs : type, valeur et incertitude, décomposition, distance à la photo, hauteur de caméra, axe de la route d'une largeur, identifiants des photos, date et un champ `commentaire` libre. Une mesure déjà enregistrée n'est pas ajoutée deux fois ; recalée ensuite, elle est mise à jour au prochain enregistrement.

**Où sont enregistrées les couches** : au premier enregistrement, le plugin demande de choisir entre un **fichier GeoPackage**, conservé à la fermeture de QGIS, et une **couche temporaire**, perdue si on ne la sauvegarde pas avant. Le GeoPackage choisi est proposé à nouveau les fois suivantes ; mesures et points triangulés y sont deux tables (`mesures` et `points_triangules`). Choisir un GeoPackage qui contient déjà ces tables y ajoute les nouvelles entités.

**Précision GPS vérifiée et inclinaison** : le plugin vérifie la précision des photos d'après leurs métadonnées (précision déclarée, ou correction différentielle contrôlée sur les photos voisines : altitude GPS moins sol LiDAR HD constante) ; la ligne d'état sous les boutons de mesure l'indique. Les photos imajbox ne sont pas de niveau (1 à 3° avec le véhicule) : pendant la mesure, le plugin calcule l'inclinaison de chaque photo 360° d'après ses verticales (façades, poteaux), à ±0,1–0,2°, en téléchargeant la photo HD en arrière-plan ; en campagne, d'après la pente et le dévers du LiDAR HD sous le véhicule (±0,5°). Détails dans [docs/MESURES.md](docs/MESURES.md#61-bis-inclinaison-automatique-par-photo).

Précision mesurée sur deux photos imajbox à position RTK (±0,10 m) de Pouilly-en-Auxois, distantes de 5 m : point de référence à ±0,17 m (visées croisées à 54°), hauteur 3,46 m ± 0,11 m pour 3,464 m, largeur 8,00 m ± 0,48 m pour 8 m, distance 3,60 m ± 0,24 m pour 3,606 m. L'incertitude de la largeur vient surtout de l'axe de la route (±2° supposés pour la BD TOPO) : un clic depuis une photo plus éloignée, qui voit la droite plus en face, la réduit.

### Calage des photos

Deux modes corrigent les défauts d'une photo avant de mesurer ; la correction s'applique ensuite à toutes les mesures et est conservée dans le profil QGIS, d'une session et d'un projet à l'autre, jusqu'à ce qu'elle soit refaite ou effacée (le calage d'un projet enregistré par une version précédente est repris à son ouverture). Le calage en cours est rappelé sous les boutons de mesure, la consigne dit l'étape en cours et ce qui est déjà fait, et « Effacer » le supprime.

- **Calage : inclinaison (objets verticaux)**, pour la photo affichée : cliquer le pied puis le sommet d'un objet bien vertical (poteau, angle de façade). Un objet corrige la pente vue dans sa direction ; un second objet, à environ 90° du premier, corrige toute l'inclinaison. L'inclinaison est en général calculée automatiquement (voir plus haut) ; ce calage à la main l'emporte sur elle, pour une photo où la ligne d'état l'indique non calée ou pour contrôler.
- **Calage : cap et position (repères sur la carte)** : cliquer dans la photo un repère net, visible aussi sur la carte (poteau, angle de bâtiment), puis ce même repère sur la carte (glisser pour déplacer la carte, molette pour zoomer, Échap pour annuler). Les repères donnent, par relèvement, le décalage de cap et de position GPS de la séquence : un repère seul corrige surtout le cap (le choisir lointain) ; trois repères ou plus, bien répartis autour de la photo (devant, derrière, sur les côtés), recalent aussi sa position. Un repère incohérent avec les précédents est refusé. Le recalage ne vaut que pour les photos situées à moins de 300 m de celles qui l'ont servi. La triangulation n'est jamais meilleure que le cap et la position des photos : à 20 m, 1° d'erreur de cap décale une visée de 35 cm.

## Installation

Prérequis : **QGIS 4.0 ou plus récent**. L'extension ne fonctionne qu'avec QGIS 4 et les versions suivantes ; le gestionnaire d'extensions de QGIS refuse de l'installer sur une version antérieure.

### Depuis la release (recommandé)

1. Télécharger le fichier `visionneuse_panoramax-<version>.zip` de la [dernière release](https://github.com/medlab-code/visionneuse_panoramax/releases/latest) (toutes les versions : [releases](https://github.com/medlab-code/visionneuse_panoramax/releases)).
2. Dans QGIS : *Extensions › Installer/Gérer les extensions › Installer depuis un ZIP*, choisir le fichier téléchargé, puis « Installer l'extension ».

### Depuis les sources

Copier le dossier `visionneuse_panoramax` dans le répertoire des plugins de votre profil QGIS, par exemple :

- Linux : `~/.local/share/QGIS/QGIS4/profiles/default/python/plugins/`
- Windows : `%APPDATA%\QGIS\QGIS4\profiles\default\python\plugins\`

Activer ensuite le plugin dans *Extensions › Installer/Gérer les extensions*.

## Tests

Les calculs (triangulation, points 3D, mesures au sol, calage, profil du terrain, mesures et leur recalage sur la carte, filtre des photos) sont vérifiés par des tests automatiques sur des scènes synthétiques dont la réponse est connue. Ils ne demandent ni QGIS ni bibliothèque supplémentaire (QGIS, l'affichage et les services d'altitude y sont simulés) ; depuis le dossier du plugin :

```
python3 -m unittest discover -s tests
```

Ils sont lancés automatiquement sur GitHub à chaque push et pull request (Python 3.12 à 3.14), avec une analyse statique du code (pyflakes).

L'enregistrement dans les couches et le GeoPackage, ainsi que la conservation du calage, sont vérifiés par une seconde série, avec QGIS, à lancer sur son poste : `python3 -m unittest discover -s tests/qgis`.

Principe, contenu et ajout de tests : voir [tests/README.md](tests/README.md).

## Licence

GNU General Public License, version 2 ou ultérieure (voir [LICENSE](LICENSE)).

Cette licence ne couvre que le code du plugin. Les photos affichées restent sous la licence choisie par leur instance Panoramax (Licence Ouverte Etalab 2.0, CC-BY-SA…), qui impose de citer l'auteur et la source : les captures de vue portent cette attribution.

## Auteur

Cédric COCHART
