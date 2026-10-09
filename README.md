# Visionneuse Panoramax pour QGIS

Plugin QGIS (3.40 et plus, 4.x) qui intègre la visionneuse [Panoramax](https://panoramax.fr) et le filaire des prises de vue.

## Fonctionnalités

- Visionneuse Panoramax dans un panneau ancré, avec choix de l'instance.
- Synchronisation carte ↔ visionneuse : curseur de vue (position, direction et ouverture) sur la carte, clic sur la carte pour ouvrir la photo la plus proche.
- Filaire des séquences en tuiles vectorielles.
- Extraction des séquences et des photos en couches vecteur.
- Capture Full HD de la vue, recalculée à partir de la photo originale.
- Sans QtWebEngine (cas de QGIS 3 sous Windows), une visionneuse native intégrée prend le relais.

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
