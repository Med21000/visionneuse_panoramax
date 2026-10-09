# Visionneuse Panoramax pour QGIS

Plugin QGIS (3.40 et plus, 4.x) qui intègre la visionneuse [Panoramax](https://panoramax.fr) et le filaire des prises de vue.

## Fonctionnalités

- Visionneuse Panoramax dans un panneau ancré, avec choix de l'instance.
- Synchronisation carte ↔ visionneuse : curseur de vue (position, direction et ouverture) sur la carte, clic sur la carte pour ouvrir la photo la plus proche.
- Filaire des séquences en tuiles vectorielles.
- Capture Full HD de la vue, recalculée à partir de la photo originale.
- Mesures (bouton « 📐 Mesure ») :
  - triangulation : positionner un objet (panneau, poteau, regard…) sur la carte en le visant depuis deux photos ou plus ;
  - largeur (de route, de trottoir…) et hauteur d'un objet, en deux clics dans la photo.
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

### Largeur et hauteur

- **Largeur** : cliquer au pied d'un bord (bordure, marquage, limite de chaussée), puis au pied du bord opposé. Les deux clics n'ont pas besoin d'être exactement en face : la largeur est prise perpendiculairement à l'axe de la route, donné par la direction de la séquence (photos précédente et suivante) ou, à défaut, par l'orientation de la photo. La distance en biais est aussi indiquée.
- **Hauteur d'un objet** : cliquer au pied de l'objet (au sol), puis à son sommet, sur la même photo.

Pendant la mesure, le curseur devient un réticule fin et un clic ne change plus de photo. Les points cliqués, le trait qui les relie et la valeur mesurée s'affichent directement dans la visionneuse (accrochés à la photo, ils suivent la vue), ainsi que dans le panneau et sur la carte. Chaque clic est prolongé jusqu'au sol (lancer de rayon), depuis une caméra placée à la hauteur indiquée (1,90 m par défaut, réglable : environ 2,2 m sur le toit d'une voiture, 1,7 à 2 m à pied ou à vélo).

Altitude du terrain : sources essayées dans l'ordre jusqu'à la première qui répond (délai de 5 secondes chacune) :

1. le service d'altimétrie de l'IGN (RGE ALTI 1 m, France) : case « Service d'altimétrie IGN », cochée par défaut ;
2. s'il est décoché, ne répond pas ou ne couvre pas la zone : des services en ligne de secours, OpenTopoData EU-DEM 25 m (Europe), puis Open-Meteo Copernicus 90 m (monde). Leurs mailles sont grossières : seule la pente générale de la rue est retenue (droite ajustée sur 180 m autour de la photo). Ce sont des modèles de surface, qui incluent les bâtiments : une pente supérieure à 12 % est jugée invraisemblable et la source est écartée ;
3. à défaut, un sol plat et horizontal.

Google Elevation n'est pas proposé : il exige une clé API et un compte de facturation, et ses conditions interdisent d'utiliser les données hors d'une carte Google.

La source utilisée, et la raison d'un éventuel repli, sont indiquées avec le résultat.

Le plugin retient la pente de la route autour de la photo (droite ajustée de façon robuste sur 120 m) plutôt que le profil brut du terrain : un modèle de terrain nu ignore les ponts (il donne le fond du cours d'eau sous le tablier) et contient fossés, talus et bruit, qui fausseraient fortement les mesures éloignées. Les rues en pente sont ainsi prises en compte. L'inclinaison propre de la caméra (véhicule penché) n'est pas corrigée, et le MNT décrit le sol nu (ni voitures, ni murets). La précision baisse vite avec la distance : à réserver aux objets situés à moins de 15–20 m. L'incertitude affichée correspond à ±0,5° d'inclinaison.

## Installation

Copier le dossier `visionneuse_panoramax` dans le répertoire des plugins de votre profil QGIS, par exemple :

- Linux : `~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/`
- Windows : `%APPDATA%\QGIS\QGIS3\profiles\default\python\plugins\`

Remplacer `QGIS3` par `QGIS4` pour QGIS 4. Activer ensuite le plugin dans *Extensions › Installer/Gérer les extensions*.

## Licence

GNU General Public License, version 2 ou ultérieure (voir [LICENSE](LICENSE)).

Cette licence ne couvre que le code du plugin. Les photos affichées restent sous la licence choisie par leur instance Panoramax (Licence Ouverte Etalab 2.0, CC-BY-SA…), qui impose de citer l'auteur et la source : les captures de vue portent cette attribution.

## Auteur

Cédric COCHART
