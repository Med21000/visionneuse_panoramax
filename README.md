# Visionneuse Panoramax pour QGIS

Plugin QGIS (3.40 et plus, 4.x) qui intègre la visionneuse [Panoramax](https://panoramax.fr) et le filaire des prises de vue.

## Fonctionnalités

- Visionneuse Panoramax dans un panneau ancré, avec choix de l'instance.
- Synchronisation carte ↔ visionneuse : curseur de vue (position, direction et ouverture) sur la carte, clic sur la carte pour ouvrir la photo la plus proche.
- Filaire des séquences en tuiles vectorielles.
- Extraction des séquences et des photos en couches vecteur.
- Capture Full HD de la vue, recalculée à partir de la photo originale.
- Mesures (bouton « 📐 Mesure ») :
  - triangulation : positionner un objet (panneau, poteau, regard…) sur la carte en le visant depuis deux photos ou plus ;
  - distance au sol et hauteur d'un objet, en deux clics dans la photo.
- Sans QtWebEngine (cas de QGIS 3 sous Windows), une visionneuse native intégrée prend le relais.

## Mesures

Cliquer sur « 📐 Mesure » puis choisir le mode.

### Triangulation

1. Mode « Triangulation » : un réticule rouge apparaît au centre de la visionneuse.
2. Tourner la vue pour placer le réticule sur l'objet, puis « 🎯 Viser ».
3. Passer à une autre photo, idéalement décalée sur le côté de l'objet, et viser à nouveau le même objet.
4. Le point d'intersection s'affiche sur la carte avec l'angle de croisement et une incertitude estimée (pour ±1° de visée). Une troisième visée améliore et contrôle le résultat.
5. « Enregistrer le point » l'ajoute à la couche « Panoramax – points triangulés » (couche temporaire à sauvegarder).

La précision dépend surtout de celle du GPS des photos (souvent de l'ordre du mètre) et de l'angle entre les visées : visez de préférence avec un croisement d'au moins 30°.

### Distance au sol et hauteur

- **Distance au sol** : cliquer dans la photo sur le sol au premier point, puis au second (largeur de chaussée, de trottoir…). Les deux points peuvent être pris sur deux photos différentes.
- **Hauteur d'un objet** : cliquer au pied de l'objet (au sol), puis à son sommet, sur la même photo.

Le résultat s'affiche dans le panneau et sur la carte. Chaque clic est prolongé jusqu'au sol (lancer de rayon), depuis une caméra placée à la hauteur indiquée (1,90 m par défaut, réglable : environ 2,2 m sur le toit d'une voiture, 1,7 à 2 m à pied ou à vélo).

Altitude du terrain (liste « Terrain »), dans l'ordre :

1. une couche raster MNT du projet, si elle est choisie (RGE ALTI, LiDAR HD…) : rapide et hors ligne ;
2. sinon le service d'altimétrie de l'IGN (RGE ALTI, France entière, connexion requise) ;
3. hors couverture, un sol plat et horizontal.

Les rues en pente sont ainsi prises en compte, et la distance entre deux points indique aussi le dénivelé. L'inclinaison propre de la caméra (véhicule penché) n'est pas corrigée, et le MNT décrit le sol nu (ni voitures, ni murets). La précision baisse vite avec la distance : à réserver aux objets situés à moins de 15–20 m. L'incertitude affichée correspond à ±0,5° d'inclinaison.

## Installation

Copier le dossier `visionneuse_panoramax` dans le répertoire des plugins de votre profil QGIS, par exemple :

- Linux : `~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/`
- Windows : `%APPDATA%\QGIS\QGIS3\profiles\default\python\plugins\`

Remplacer `QGIS3` par `QGIS4` pour QGIS 4. Activer ensuite le plugin dans *Extensions › Installer/Gérer les extensions*.

## Licence

GNU General Public License, version 2 ou ultérieure (voir [LICENSE](LICENSE)).

## Auteur

Cédric COCHART
