# Mesures et calage

Ce document explique en détail comment la Visionneuse Panoramax mesure à partir des photos 360° : le modèle de caméra, le moteur de calcul commun à toutes les mesures, chaque mode de mesure, le recalage d'une mesure dans un plan défini sur la carte QGIS, les fonctions de calage des photos, les sources d'erreur et les pistes d'amélioration. Le mode d'emploi résumé se trouve dans le [README](../README.md).

Sommaire :

1. [Vue d'ensemble](#1-vue-densemble)
2. [Le modèle de caméra](#2-le-modèle-de-caméra)
3. [Le moteur de mesure](#3-le-moteur-de-mesure)
4. [Les modes de mesure](#4-les-modes-de-mesure)
5. [Recaler une mesure dans un plan défini sur la carte QGIS](#5-recaler-une-mesure-dans-un-plan-défini-sur-la-carte-qgis)
6. [Le calage des photos](#6-le-calage-des-photos)
7. [Affichage et gestion des mesures](#7-affichage-et-gestion-des-mesures)
8. [Sources d'erreur et ordres de grandeur](#8-sources-derreur-et-ordres-de-grandeur)
9. [Pistes d'amélioration des calculs](#9-pistes-damélioration-des-calculs)
10. [Pistes d'amélioration de la prise de vues](#10-pistes-damélioration-de-la-prise-de-vues)
11. [Annexe : paramètres du code](#11-annexe--paramètres-du-code)

---

## 1. Vue d'ensemble

### 1.1 Deux sources complémentaires

Une photo et une carte ne donnent pas la même information :

| Source | Ce qu'elle donne bien | Ce qu'elle donne mal |
|---|---|---|
| **Photo 360°** | une **direction** très précise pour chaque pixel : un angle de fenêtre, un seuil de porte, un sommet, vus de côté | la **distance** : une direction seule ne dit pas à quelle profondeur se trouve l'objet |
| **Carte QGIS** (orthophoto, cadastre) | la **position horizontale** des objets vus de haut : bords de bâtiments, bordures, marquages | les détails vus de côté (fenêtres, seuils, sommets), les hauteurs, ce qui est caché par une toiture ou un arbre |

Toutes les mesures du plugin reposent sur ce partage : la photo donne la direction, une **surface** donne la profondeur. Cette surface est d'abord une hypothèse (le sol, un plan vertical), que la carte QGIS permet ensuite de corriger.

### 1.2 Le principe commun

Un clic dans la photo est une demi-droite partant de la caméra (le **rayon de visée**). Le point mesuré est l'**intersection de ce rayon avec une surface** :

- le **sol**, pour les largeurs ;
- le **plan vertical face à la caméra** passant par le pied d'un objet, pour une hauteur ;
- une **façade**, un **plan horizontal**, ou aucune surface en **triangulation** depuis deux photos, pour la mesure libre ;
- le **plan calé sur la carte**, une fois la mesure recalée (section 5).

La mesure est ensuite un simple calcul sur deux points 3D : distance, composante horizontale, verticale, le long de la route ou en travers.

### 1.3 Les modes

| Mode | Surface des points | Grandeur affichée |
|---|---|---|
| Largeur perpendiculaire à la chaussée | sol | composante en travers de l'axe de la route |
| Longueur parallèle à la route | sol | distance 3D entre les deux points |
| Hauteur d'un objet | pied au sol, sommet dans le plan vertical face à la caméra | composante verticale |
| Triangulation d'un objet | aucune : visées depuis plusieurs photos | position sur la carte |
| Mesure libre 3D | au choix : sol, façade, plan vertical face à la caméra, plan horizontal, triangulation 3D | distance 3D décomposée (hauteur sur le plan vertical) |
| Calage : inclinaison, cap et position, hauteur de caméra | — | corrections appliquées ensuite à toutes les mesures |

Les quatre premiers modes de mesure au sol ou en plan, ainsi que la mesure libre, partagent le même moteur de calcul : un point est le même quel que soit le mode.

---

## 2. Le modèle de caméra

### 2.1 Ce que fournit une photo

| Information | Source | Qualité typique |
|---|---|---|
| Position (lon, lat) | GPS de la caméra | 2 m (matériel de relevé, GPS corrigé) à 4–5 m (caméras grand public, téléphones), indiquée par Panoramax dans `quality:horizontal_accuracy` |
| Cap du centre de l'image | `view:azimuth` (arrondi au degré par l'API) ou EXIF `GPSImgDirection` (au centième quand l'appareil le fournit) | 0,3° (centrale inertielle de relevé) à plusieurs degrés (boussole de téléphone) |
| Image équirectangulaire | photo 360° assemblée | chaque pixel correspond à une direction |

Le plugin ne connaît ni la **hauteur de la caméra** au-dessus du sol, ni son **inclinaison**. Il suppose une caméra de niveau, à la hauteur indiquée dans le panneau (1,90 m par défaut), jusqu'à ce que ces valeurs soient calées (section 6).

### 2.2 Repère et directions

- Les calculs se font dans un **repère local** (est, nord, altitude) en mètres, centré sur la mesure : sur quelques centaines de mètres, l'erreur due à la projection est négligeable devant la précision du GPS.
- Une **direction** est un couple (cap, élévation) en degrés : cap 0 = nord, sens horaire ; élévation positive vers le haut. Son vecteur unitaire est `(sin cap · cos élév, cos cap · cos élév, sin élév)`.

### 2.3 D'un clic à une direction

- **Visionneuse web** (Photo Sphere Viewer) : la visionneuse calcule par lancer de rayon sur la sphère la position du clic dans la photo. Le cap absolu vaut cet angle plus le cap de la photo. Un second calcul, à partir de la position du clic à l'écran, sert de contrôle (journal QGIS « Panoramax »).
- **Visionneuse de secours** (sans QtWebEngine) : l'image équirectangulaire est affichée directement, la direction d'un pixel s'en déduit linéairement.
- **Visée au réticule** (triangulation) : direction du centre de la vue.

### 2.4 Cap précis

L'API Panoramax arrondit `view:azimuth` au degré, soit jusqu'à 0,5° d'erreur de visée (17 cm à 20 m). Quand l'EXIF contient `GPSImgDirection` au centième et qu'il concorde à moins de 1° avec `view:azimuth` (même mesure, non arrondie), le plugin l'utilise. La visionneuse web s'oriente sur la valeur arrondie : le plugin corrige l'écart dans ses calculs.

Sur les photos IMAJING imajbox 360 HD du Conseil départemental de la Côte-d'Or, le cap EXIF suit la trajectoire du véhicule à 0,33° près (écart-type sur 700 photos en ligne droite), avec un décalage constant de 3,2° dû au montage de la caméra : l'EXIF décrit bien l'orientation de l'image (vérifié sur le point de fuite d'une route droite).

### 2.5 Position de la caméra

Le centre optique d'une photo est :

- en plan, à la position de la photo (recalée si un calage de position existe, section 6.3) ;
- en altitude, à `z_caméra = z_sol + h`, somme de deux termes **indépendants**, chacun avec une seule source :

| Terme | Ce que c'est | Source |
|---|---|---|
| `z_sol` | altitude du sol sous la photo | profil de terrain (section 3.1) du **premier clic fait sur cette photo** dans la mesure |
| `h` | hauteur de la caméra au-dessus de ce sol | pour une photo à **position vérifiée précise** (section 2.7), altitude NGF de la caméra moins `z_sol` ; sinon hauteur **calée pour la séquence** (section 6.2) si elle existe ; sinon la valeur « Caméra à » du panneau |

Aucun calage ne modifie `z_sol`, et le terrain ne modifie pas `h` : le calage de hauteur de caméra n'est donc pas en concurrence avec le profil de terrain, il remplace seulement la valeur saisie de `h`.

`z_sol` est figé au premier clic, puis le même pour tous les clics de la photo, y compris pour les points au sol : le profil d'un clic garde sa pente dans la direction visée, mais il est recalé verticalement sur `z_sol`. Le profil d'un clic dépend en effet de la direction visée (de quelques centimètres à quelques décimètres d'écart sous la caméra d'une direction à l'autre) : chaque clic aurait sinon sa propre altitude de caméra, ce qui fausserait les hauteurs mesurées sur un plan et les dénivelés entre points au sol, et décalerait le repère du point dans la visionneuse (7,6° plus haut pour 0,6 m d'écart sur un point au sol à 4 m de la photo).

### 2.6 Corrections appliquées à chaque clic

Dans cet ordre :

1. **inclinaison** de la photo (calée à la main sur des objets verticaux, sinon calculée automatiquement : section 6.1 bis) ;
2. **décalage de cap** de la séquence (calage par repères) ;
3. **position recalée** de la photo (calage par repères, à partir de trois repères) ;
4. **hauteur de caméra** calée pour la séquence.

---

### 2.7 Précision GPS vérifiée et mode dégradé

Panoramax annonce souvent une précision générique (2 m pour le matériel de relevé), alors que certaines photos ont une position recalée en post-traitement (GNSS RTK et centrale inertielle), comme celles de l'imajbox 360 HD du conseil départemental de la Côte-d'Or. Le plugin vérifie la précision de chaque séquence d'après les **métadonnées des photos** (`core/precision.py`) :

- si l'EXIF déclare une précision (`GPSHPositioningError`) d'au plus 0,20 m, elle est retenue telle quelle ;
- sinon, si la position a reçu une **correction différentielle** (`GPSDifferential`), la vérification se fait sur les photos voisines de la séquence (jusqu'à 20, à moins de 60 m) : leur altitude GPS (`GPSAltitude`, ellipsoïdale), convertie en altitude NGF-IGN69 par la grille du géoïde **RAF20** de l'IGN (téléchargée à la demande par PROJ via QGIS), moins le sol LiDAR HD sous chaque photo, donne la hauteur de la caméra. Constante à 20 cm près (écart-type) sur au moins 8 photos, **la même sur les deux moitiés** de la portion vérifiée à 15 cm près (un GPS non recalé peut être lisse sur quelques photos, mais il dérive), et plausible (0,5 à 4 m), elle prouve une position recalée : la séquence est jugée précise à **0,10 m**. Les photos dont la hauteur s'écarte de plus de 4 écarts médians absolus (pont, défaut du LiDAR) sont écartées avant le calcul. Un GPS simple ou un DGPS varie de plusieurs décimètres. Sur la séquence CD21 de Pouilly-en-Auxois : 2 cm sur 20 photos, caméra à 1,87 m. Les seuils, d'abord de 10 cm, refusaient 5 points sur 72 dans Pouilly-en-Auxois (sept séquences imajbox RTK) : 10,5 à 19,8 cm d'écart-type, ou 11 cm de dérive. Le sol LiDAR HD (±10 cm), les bordures, le dévers et la suspension du véhicule suffisent à faire varier la hauteur de caméra de plus de 10 cm. Si la portion autour de la photo est refusée alors que le LiDAR HD y est publié, **deux autres portions** de la même séquence, choisies à moins d'un kilomètre et les plus éloignées possible, sont essayées l'une après l'autre : une seule vérifiée suffit pour toute la séquence, relevée avec le même matériel. Le RTK peut perdre sa correction par endroits (arbres, façades hautes) ; sur les imajbox, la centrale inertielle limite alors l'écart.

La vérification se fait une fois par séquence, en arrière-plan à l'affichage de la photo (deux requêtes : fiches des photos voisines, environ 100 Ko ; altitudes LiDAR, moins de 1 Ko), et son résultat est gardé pour la session.

Pour une photo vérifiée précise :

- **Précision de position** : 0,10 m (ou celle déclarée) au lieu de celle annoncée par Panoramax, dans la triangulation (poids des visées, incertitude) et le calage par repères.
- **Hauteur de caméra sans calage** : `h` = altitude NGF de la caméra − `z_sol`, propre à chaque photo, à environ ±5 cm. Elle n'est retenue que si le sol vient du LiDAR HD (le RGE ALTI, au mètre et plus ancien, peut être décalé de 0,2 à 0,5 m sous la caméra, soit 10 à 25 % d'erreur sur toutes les distances ; le service de secours est plus grossier encore) et si elle est plausible ; sinon, la hauteur calée ou la valeur du panneau s'applique. Le résultat l'indique : « caméra à 1,87 m (altitude GPS) ». Elle passe avant une hauteur calée pour la séquence, moins précise (±0,10 m environ avec plusieurs références).
- **Proposée pour le champ « Caméra à »** : quand la hauteur se calcule ainsi (médiane de la séquence vérifiée, ou photo de précision déclarée, avec le LiDAR HD sous la photo), un bouton « Caméra à 1,87 m d'après l'altitude GPS : utiliser » apparaît sur la ligne « Terrain » tant que le champ en diffère. Le champ n'est jamais modifié sans ce clic. Il sert quand la hauteur propre à chaque photo ne peut pas être calculée, par exemple avec l'option « Mesure sur un pont » (sol plat, sans altitude IGN sous la caméra), et pour les photos suivantes d'une autre séquence de la même caméra.

**Mode dégradé.** Les mesures ne sont pleinement fiables qu'avec **à la fois** une position vérifiée précise et le **LiDAR HD** sous la photo. S'il manque l'un ou l'autre, les mesures dépendent de la hauteur de caméra (à caler), de l'inclinaison et, sans LiDAR, d'un sol approximatif (RGE ALTI) : l'outil est en mode dégradé. Une **fenêtre** l'annonce quand on active la mesure (« 📐 Mesure ») sur une telle photo, avec ce qui manque et les calages à faire ; une case permet de ne plus l'afficher pendant la session. Si la vérification de la photo n'est pas encore terminée, la fenêtre s'ouvre dès qu'elle l'est. Une **ligne d'état** sous les boutons de mesure rappelle en permanence la précision de la position et la présence du LiDAR HD (par exemple « Position GPS vérifiée (±0,10 m) · LiDAR HD », ou « ⚠ Position GPS non vérifiée (±4 m annoncés) · LiDAR HD : mode de mesure dégradé »). La présence du LiDAR HD est contrôlée pour toute photo, y compris quand sa précision est déclarée dans l'EXIF.

L'**inclinaison** (roulis et tangage), mesurée par la centrale, n'est pas transmise dans les métadonnées des photos, et les photos ne sont pas mises à niveau : à Pouilly-en-Auxois, la caméra penche de 1 à 3° avec le véhicule. Elle devient la principale source d'erreur : le plugin la calcule automatiquement (section 6.1 bis) et la **ligne d'état** l'indique (« inclinaison calée (verticales de la photo, ±0,1°) », « inclinaison en cours de calcul… », ou « ⚠ … inclinaison non calée : calez-la sur deux objets verticaux »).

## 3. Le moteur de mesure

### 3.1 Point au sol

**Principe.** On suit le rayon de visée jusqu'à ce qu'il passe sous le terrain. Sur sol plat, la distance horizontale du point vaut `d = h / tan(−e)`, avec `h` la hauteur de caméra et `e` l'élévation du clic.

**Profil du terrain.** Le plugin demande l'altitude du terrain le long de la visée, de 10 m derrière la photo à 25 m devant, à l'une des sources suivantes, essayées dans l'ordre (5 s maximum chacune) :

1. **service d'altimétrie de l'IGN**, 71 points tous les 0,5 m : MNT du **LiDAR HD** (50 cm) là où il est publié, sinon **RGE ALTI** (1 m, France). Le LiDAR HD décrit la chaussée elle-même (bordures, caniveaux) ; tant qu'il n'est pas publié à un endroit, le repli sur le RGE ALTI est silencieux ;
2. **Open-Meteo Copernicus 90 m** (monde), dont seule la pente générale a un sens (OpenTopoData EU-DEM 25 m, qui ne répondait plus, a été retiré) ;
3. à défaut, **sol plat**.

Avec l'option « **Mesure sur un pont** », le sol est pris plat et horizontal sous la caméra, sans interroger les services : sous un pont, le modèle de terrain nu donne le fond du cours d'eau.

**Profil réel avec le LiDAR HD, droite de pente sinon.** Avec le MNT LiDAR HD (50 cm), le sol suit le **profil réel** le long de la visée, depuis la photo : bordures, caniveau et dévers y figurent. Les altitudes sont lissées par une médiane glissante sur trois échantillons (1,5 m), qui écarte les points isolés aberrants ; le profil est prolongé au-delà des données (25 m) par la droite de pente. S'il manque plus de 20 % des altitudes, ou s'il n'y en a pas de part et d'autre de la photo, la droite de pente est retenue. À Pouilly-en-Auxois, un point au sol visé à 6 m était retrouvé entre 5,37 m et 6,31 m selon la direction avec la droite de pente, et à 6,00 m (5,93 m au pire) avec le profil réel. Avec le RGE ALTI ou le service de secours, le plugin retient la droite de pente. Le plugin retient la **droite de pente** du terrain ajustée par la méthode de **Theil-Sen** (pente médiane des paires de points, ordonnée médiane des résidus). Un modèle de terrain nu contient fossés, talus et bruit ; un creux d'un mètre sous la visée allongerait les distances de moitié. La médiane ignore ces accidents tant qu'ils couvrent moins d'un tiers de la fenêtre, soit une dizaine de mètres. La fenêtre reste courte pour suivre le sol de la rue près des points mesurés : ajustée auparavant sur 120 m (de 30 m derrière à 90 m devant), la droite suivait le relief lointain ; à Pouilly-en-Auxois, le sol modélisé à 6 m était jusqu'à 0,3 m trop haut vers une colline, et les mesures de ce côté 15 % trop courtes. Sur 35 m, l'écart y reste sous 0,1 m dans l'axe de la rue et sous 0,2 m de côté. Le service de secours, qui est un modèle de surface (bâtiments compris), est écarté si sa pente dépasse 12 %. Sur la photo de Commarin étudiée, le terrain réel reste à moins de 10 cm de la droite retenue jusqu'à 15 m, devant comme sur le côté.

**Intersection.** Le terrain étant linéaire entre deux échantillons, l'intersection est exacte. Un point qui ne touche pas le sol dans les 60 m est refusé.

**Sensibilité.** `δd ≈ d² / h · δe` (δe en radians). Avec une caméra à 2 m, 0,5° d'erreur d'élévation déplace un point à 8 m de 0,28 m, et un point à 15 m de 0,98 m.

**Le piège du pied mal cliqué.** Le point est sur le sol **là où le rayon le touche**. Si le clic n'est pas exactement au contact du sol (un peu au-dessus sur le mur, ou sur un trottoir, une voiture qui masque le pied), le rayon traverse le mur et ne touche le sol que derrière : le point tombe sur la carte **au-delà de la façade**, et la distance est trop grande. C'est la principale raison d'être du recalage sur la carte (section 5).

### 3.2 Point sur un plan

Le rayon `C + t·u` (centre optique `C`, direction unitaire `u`) coupe le plan passant par `A` de normale `n` en

```
t = n·(A − C) / n·u        point = C + t·u
```

Plans utilisés :

| Plan | Définition |
|---|---|
| Plan vertical face à la caméra | vertical, passant par un point (le pied d'un objet), de normale horizontale dirigée vers la caméra |
| Façade | vertical, passant par deux points au pied du mur |
| Plan horizontal | altitude du sol sous la caméra + hauteur saisie |
| Plan calé sur la carte | vertical, passant par les points glissés sur la carte (section 5) |

**Contrôles.** Visée parallèle au plan, point derrière le plan ou à plus de 100 m : refusés. Une **incidence rasante** (moins de 15° entre la visée et le plan) est signalée : l'erreur croît alors comme `1/sin(incidence)`.

### 3.3 Point triangulé en 3D

Sans surface : chaque point est visé depuis deux photos différentes, et le point retenu est le plus proche des deux visées 3D au sens des moindres carrés :

```
Σᵢ (I − uᵢ uᵢᵀ) X = Σᵢ (I − uᵢ uᵢᵀ) Cᵢ
```

avec `uᵢ` la direction unitaire de la visée depuis la photo `i` et `Cᵢ` le centre optique de cette photo.

Les deux visées doivent se croiser d'au moins 3°, et devant les photos. Le résultat ne dépend ni du sol ni de la hauteur de caméra (elle s'élimine dans les distances quand les deux photos sont de la même séquence) ; sa composante verticale dépend seulement de l'altitude du sol sous chaque photo.

### 3.4 Grandeurs

Entre deux points `P` et `Q`, le moteur calcule la distance 3D, sa composante horizontale, le dénivelé (de `P` à `Q`) et, si l'axe de la route est connu, les composantes le long de la route et en travers. L'**axe de la route** est la direction de la séquence, calculée entre la photo précédente et la suivante ; à défaut, l'orientation de la photo.

### 3.5 Incertitude affichée

Chaque source d'erreur indépendante est prise à ±1 écart-type ; l'écart de la grandeur qu'elle produit (le plus grand des deux sens) entre dans une **somme quadratique** :

- **inclinaison de chaque photo, commune à tous ses clics**, en tangage et en roulis : l'incertitude du calage appliqué (calcul automatique : 0,05 à 0,25° ; LiDAR HD : environ 0,5° ; calage à la main), ou **2°** pour une photo non calée (1 à 3° mesurés sur l'imajbox) ;
- **hauteur de caméra de chaque photo** : ±5 cm par l'altitude GPS d'une position vérifiée, l'incertitude de la hauteur calée, ou ±15 cm pour la valeur saisie ;
- **visée de chaque clic** : ±0,1° en élévation et en cap ; en triangulation 3D, le cap compte aussi l'erreur de visée de la photo ;
- la position de chaque point recalé sur la carte de ±0,5 m (section 5) ;
- les clics de définition de la façade (±0,1°), ou ses extrémités de ±0,5 m une fois recalées sur la carte (ou prises dans le cadastre) ;
- la **façade du cadastre** d'une hauteur : ±0,5 m si le LiDAR HD la confirme, ±1 m sinon ;
- la hauteur saisie du plan horizontal de ±5 cm.

L'inclinaison est commune aux clics d'une photo : une erreur de tangage allonge ou raccourcit tous ses points ensemble, et son effet sur une largeur prise devant la caméra est double de celui d'un seul clic. Ordres de grandeur, largeur de 7 m à 10 m devant la caméra : **±1,7 m** sur une photo non calée, ±0,55 m calée avec la hauteur de caméra saisie, moins de ±0,3 m calée avec la hauteur par l'altitude GPS.

Elle ne compte pas l'erreur du GPS, qui déplace la mesure entière sans changer sa longueur (section 8).

---

## 4. Les modes de mesure

### 4.1 Largeur perpendiculaire à la chaussée

- **Clics.** Pied d'un bord (bordure, marquage, limite de chaussée), puis pied du bord opposé.
- **Calcul.** Deux points au sol ; la largeur est la composante de `PQ` **en travers de l'axe de la route**. Les clics n'ont pas besoin d'être exactement en face l'un de l'autre. Sur la carte, le trait est tracé perpendiculairement à l'axe, à mi-chemin des deux bords cliqués le long de la route, et chaque bout est relié en pointillés à son bord cliqué (point déplaçable). La distance en biais est aussi indiquée.
- **Usage.** En travers de la route uniquement. Le long de la route (deux points d'un mur sur le côté du véhicule), la composante perpendiculaire est presque nulle : utiliser la longueur parallèle à la route.
- **Axe de la route** (`core/roads.py`), pris au **milieu des deux bords cliqués**, pas à la caméra : le point mesuré est souvent 10 à 20 m devant, où la route a déjà tourné.
  1. **BD TOPO** de l'IGN (tronçons de route, service WFS de la Géoplateforme, sans clé) : parmi les tronçons à moins de 15 m du milieu, ceux qui coupent le segment cliqué d'au moins 45° (à un carrefour, la rue que traverse la mesure) ; le plus proche donne l'axe, direction locale du tronçon sur ±10 m. Tronçons lus par maille d'environ 100 m, une fois par session.
  2. À défaut (hors de France, service injoignable, aucun tronçon) : **trajectoire de la séquence** au point mesuré, axe principal des positions de ses photos à moins de 20 m.
  3. À défaut : **direction du véhicule** à la caméra (photos précédente et suivante, ou orientation de la photo).

  La largeur est d'abord calculée avec la direction du véhicule (« lecture de la BD TOPO… »), puis recalculée dès que l'axe arrive. Le résultat indique la source : « axe 47° (BD TOPO, D977BIS) ».

  **Validation** (séquence CD21 de Pouilly-en-Auxois, 12/10/2026). Sur 419 points, BD TOPO et trajectoire du véhicule s'accordent à 0,4° en ligne droite et 0,8° en courbe (médianes) ; aux carrefours où le véhicule tourne, l'orthophoto montre que la BD TOPO suit la route et la trajectoire la coupe. Largeur simulée 15 m devant la caméra, comparée à la direction de la route à cet endroit (trajectoire lissée sur 30 m) :

  | | Axe BD TOPO au point mesuré | Direction du véhicule à la caméra |
  |---|---|---|
  | Courbe (119 points) | 0,9° (90 % sous 2,7°) | 3,0° (6,7°) |
  | Courbe serrée (46 points) | 2,3° (8,2°) | 10,6° (24,5°) |

  Effet sur la largeur : environ `w·(1 − cos δ) + a·sin δ`, avec `a` le décalage des deux bords le long de la route ; pour 7 m et 2 m de décalage, 3° d'erreur d'axe font 11 cm, 10° en font 45.

  **Limites.** Un tronçon BD TOPO est l'axe de toute la route : sur une bretelle ou un tronçon simplifié, la direction peut s'écarter de quelques degrés ; sur la carte, le trait bleu « perpendiculaire à la route » permet de le contrôler sur l'orthophoto.

### 4.2 Longueur parallèle à la route

- **Clics.** Les deux extrémités de l'objet, au sol, typiquement le long de la route sur le côté du véhicule : façade, portail, place de stationnement.
- **Calcul.** Distance 3D entre les deux points au sol (elle suit la pente) ; le dénivelé est indiqué à partir de 5 cm. Le calcul étant une distance directe, il reste juste dans n'importe quelle direction.

### 4.3 Hauteur d'un objet

- **Clics.** Pied de l'objet, **exactement au contact du sol**, puis sommet, sur la même photo.
- **Calcul.** Le pied est un point au sol ; le sommet est pris dans le **plan vertical face à la caméra qui passe par le pied**. À l'écart de cap `Δ` entre pied et sommet, il est à la distance horizontale `d / cos Δ`. Pour un objet fin (poteau, `Δ ≈ 0`), c'est la distance du pied ; pour un mur face à la caméra, le sommet peut être cliqué un peu de côté sans fausser la hauteur. Un sommet à plus de 45° de côté, ou sous le pied, est refusé. La hauteur affichée est la composante verticale.
- **Sensibilité.** Sur sol plat, `H = h · (1 + tan e_sommet / tan(−e_pied))` : la hauteur est proportionnelle à la hauteur de caméra, et très sensible au clic du pied. Quand le pied est caché, le recalage sur la carte (section 5) la rend indépendante de l'un et de l'autre.
- **Façade du cadastre** (case du même nom, `core/buildings.py`), pour un bâtiment dont le pied est caché (voiture, haie, muret). Le mur est le premier bord de bâtiment du cadastre (Parcellaire Express, service WFS de la Géoplateforme) coupé par la visée horizontale du pied ; le pied est pris **au bas de ce mur, à l'altitude du sol** (profil de terrain), même si le clic tombe sur la voiture devant, et le sommet sur le plan du mur. La façade s'affiche sur la carte (tirets violets « façade du cadastre ») ; glisser le pied sur la carte reprend la main. Le résultat indique la distance du mur et son contrôle par le LiDAR HD (ci-dessous).
- **Façade du cadastre en mesure libre** : bouton « Façade du cadastre », puis un clic sur le mur dans la photo ; ses extrémités se recalent ensuite sur la carte comme une façade définie au pied du mur.

#### Validation des façades (12/10/2026)

Référence : points de façades triangulés entre deux photos RTK consécutives du CD21 (5 m d'écart, inclinaison automatique calée), appariés automatiquement (SIFT) et regroupés en plans verticaux ; erreur sur la distance au mur, en médiane (90 % des points sous) :

| | Pouilly-en-Auxois (14 façades) | Autour de Dijon (21) | Nuits-Saint-Georges et Semur-en-Auxois (90) |
|---|---|---|---|
| Cadastre | 0,79 m (1,3 m) | 0,08 m (0,85 m) | **0,18 m (0,83 m)** |
| MNS du LiDAR HD, visée prolongée | 0,87 m trop près | LiDAR non publié | 0,83 m trop près (0 à 2,2 m selon le débord de toit) |
| BD TOPO bâtiments | 0,65 m (2,5 m) | 0,59 m (1,1 m) | 0,70 m (1,2 m) |

Sur la hauteur d'un point de façade à 7–16 m de la photo, le cadastre donne 3 cm en médiane (90 % sous 19 cm) en ville. Il a aussi des erreurs grossières (2 à 18 m) quand le mur visé n'y figure pas (annexe, clôture, autre bâtiment) : d'où le contrôle par le LiDAR et l'affichage sur la carte. Un pied bien visible et bien cliqué reste plus sûr (environ 10 à 20 cm).

#### Contrôle de la façade par le LiDAR HD

Seuls les bâtiments en dur du cadastre servent (pas les constructions légères : abris, appentis, serres). Le MNS du LiDAR HD (surface, bâti compris ; WMS de la Géoplateforme, grille de 0,5 m lue en flottants) marque le bord du toit, pas la façade. Le long de la visée, à partir de 3 m avant la façade du cadastre (un arbre, une haie ou un véhicule plus proche est ignoré), le bâti commence là où le MNS dépasse le MNT de 2 m. La façade est **confirmée** si le cadastre est de 0,5 m avant à 2 m après ce point (débord de toit) ; sinon « cadastre et LiDAR HD divergent de … m : contrôlez la façade sur la carte ». Sans LiDAR publié (agglomération dijonnaise en 2026), la façade est utilisée sans contrôle, et c'est signalé. À Nuits et Semur, 145 visées sur 153 sont confirmées.

### 4.4 Triangulation d'un objet

**But.** Positionner sur la carte un objet visible de plusieurs photos (panneau, poteau, regard), sans hypothèse sur le sol.

**Visées.** Réticule sur l'objet, « 🎯 Viser », puis même chose depuis une autre photo décalée sur le côté. Chaque visée est une demi-droite horizontale (planimétrique).

**Calcul.** Moindres carrés pondérés :

```
Σ wᵢ (I − uᵢ uᵢᵀ) X = Σ wᵢ (I − uᵢ uᵢᵀ) Pᵢ        wᵢ = 1 / σᵢ²,   σᵢ² = (tᵢ · tan εᵢ)² + aᵢ²
```

avec `tᵢ` la distance de la photo au point, `εᵢ` l'erreur de visée (1°, 0,5° pour le matériel de relevé, ou celle issue du calage du cap) et `aᵢ` la précision de position de la photo (GPS, 3 m si inconnue, ou position recalée). Une visée lointaine ou une photo mal positionnée compte moins ; le calcul est itéré (4 passes).

**Contrôles.** Visées presque parallèles (moins de 3°), point derrière une photo ou à plus de 500 m : refusés. Croisement inférieur à 15° ou GPS moins bon que ±5 m : signalés.

**Erreurs communes.** Les photos d'une même séquence ont presque le même décalage de position GPS et la même erreur de cap (fixation de la caméra) : ces erreurs déplacent le point d'un bloc et ne se compensent pas en multipliant les visées. Quand toutes les visées viennent d'une même séquence, l'incertitude ne descend pas sous cette part commune.

**Résultats.** Distances, angle de croisement, incertitude (grand axe de l'ellipse de covariance, au moins la part commune d'une même séquence), écart des visées (à partir de 3), précision GPS. « Enregistrer le point » l'ajoute à la couche « Panoramax – points triangulés » (GeoPackage ou couche temporaire, au choix lors du premier enregistrement).

**Géométrie favorable.** L'erreur vaut à peu près `σ / sin θ` (θ : angle de croisement) : viser avec au moins 30° de croisement, idéalement 60 à 90°, depuis des photos proches de l'objet.

**Ordre de grandeur.** Sur 15 points simulés à partir de vraies photos imajbox du CD21 (visées à 5–30 m), l'écart médian au point visé est de 3 cm (11 cm au plus) avec le cap EXIF précis et la pondération, contre 8 cm (29 cm) auparavant. Ces écarts sont relatifs aux positions GPS des photos : la position absolue reste à la précision du GPS, sauf recalage (section 6.3).

### 4.5 Mesure libre 3D

**But.** Mesurer entre deux points quelconques, dans un plan au choix, avec des résultats cohérents avec les autres modes.

**Surfaces** (liste « Surface ») :

- **Sol** : comme les largeurs ;
- **Façade (plan vertical)** : « Définir la façade », puis deux clics au pied du mur, ou « Façade du cadastre » et un clic sur le mur (section 4.3) ; on mesure ensuite n'importe quoi sur la façade (fenêtres, portes, portails), y compris depuis d'autres photos. Ses extrémités se recalent sur la carte (section 5.5) ;
- **Plan vertical face à la caméra** : premier point au sol, second dans le plan qui passe par lui ; la grandeur affichée est alors la **hauteur**, comme le préréglage ;
- **Plan horizontal** : à la hauteur saisie au-dessus du sol (dessus d'un muret, d'un quai) ;
- **Triangulation 3D (deux photos)** : chaque point cliqué sur deux photos différentes ; mesure indépendante du sol et de la hauteur de caméra.

**Résultat.** Distance 3D (ou hauteur sur le plan vertical), décomposée en horizontale, verticale, le long de la route et en travers.

**Limites.**

- Un point qui n'est pas sur la surface choisie est faux : un balcon ou un appui de fenêtre en saillie est projeté sur le plan de la façade.
- Le **plan horizontal** est vu d'autant plus en rasant qu'il est proche de la hauteur de la caméra : à 1 m sous une caméra à 2 m, les visées y sont deux fois plus rasantes qu'au sol (±0,15 m contre ±0,04 m pour un segment de 3 m à 4 m, sur scène simulée). Le message indique l'écart du plan sous la caméra et l'incidence.
- Des altitudes de sources différentes entre les clics (service IGN et sol plat) sont signalées : la composante verticale peut en être faussée.

**Vérifications sur scène simulée** (caméra à 2 m, mur à 6 m, deux photos à 10 m l'une de l'autre) : au sol 6,01 m comme la longueur parallèle ; plan vertical 3,00 m comme la hauteur ; fenêtre de 2,00 m vue en face ±0,04 m, et ±0,58 m vue à 13° d'incidence ; plan horizontal 3,00 m ; triangulation 3D 3,35 m (3,00 horizontale, 1,50 verticale).

---

## 5. Recaler une mesure dans un plan défini sur la carte QGIS

### 5.1 Le problème

Les mesures au sol et les hauteurs reposent sur le point où le rayon touche le sol. Or ce point est souvent mal placé :

- pied **masqué** (voiture, trottoir, végétation) ou cliqué **un peu haut** sur le mur : le rayon rejoint le sol derrière la façade ;
- **hauteur de caméra** fausse ou **inclinaison** non calée : toutes les distances sont faussées en proportion ;
- **terrain** mal décrit (bordure, trottoir surélevé).

Sur la carte QGIS, ces erreurs se voient immédiatement : les croix jaunes des points tombent dans les bâtiments ou les jardins au lieu d'être sur le bord du bâti.

### 5.2 Le principe : la carte fixe le plan, la photo place le point

La carte vue de haut ne permet qu'un placement grossier, mais elle donne de façon fiable **la profondeur** d'un point : la distance de la caméra au mur, au poteau, à la bordure. La photo, elle, ne donne pas la profondeur, mais donne la **position fine** du point vu de côté (un angle de fenêtre, un seuil, un sommet).

Le recalage combine les deux en deux gestes, que l'on peut alterner librement :

1. **Sur la carte QGIS**, on **glisse** les points de la mesure à peu près à leur place (sur le bord du bâtiment de l'orthophoto ou du cadastre) : cela définit le **plan calé** de la mesure ;
2. **Dans la visionneuse**, on **vise à nouveau** chaque point avec précision : il est replacé à l'intersection de la nouvelle visée et du plan calé.

### 5.3 Glisser un point sur la carte

**Outil de la carte.** Pendant les largeurs, les hauteurs et la mesure libre, un outil propre est actif sur la carte QGIS :

- **glisser un point** (croix jaune) d'une mesure (à moins de 10 pixels) le recale à l'endroit où on le lâche ;
- **glisser une extrémité de façade** déplace la façade (section 5.5) ;
- **glisser ailleurs** déplace la carte, la molette zoome ;
- un **simple clic** ailleurs ouvre la photo la plus proche.

Le curseur devient une main au survol d'un point déplaçable. L'outil précédent revient quand on arrête de mesurer ou qu'on passe en triangulation ou en calage.

**Points concernés.** Tous les points des mesures affichées, mesures terminées comprises. Pour une hauteur (plan vertical face à la caméra), seul le **pied** se glisse : le sommet en découle.

**Calcul d'un point glissé.** Sa position horizontale est celle de la carte ; son altitude est celle du **rayon de visée** à cette distance de la caméra :

```
z = z_caméra + distance · tan(élévation)
```

Le point reste donc sur la direction cliquée dans la photo, à la profondeur lue sur la carte. Pour une hauteur, il en résulte

```
H = distance · (tan e_sommet − tan e_pied)
```

qui ne dépend plus ni du terrain, ni de la hauteur de caméra : seulement de la distance lue sur la carte et des deux angles visés.

### 5.4 Le plan calé de la mesure

Glisser des points définit un **plan vertical**, mémorisé avec la mesure :

| Points glissés | Plan calé |
|---|---|
| un seul | vertical, passant par lui, de normale horizontale orientée vers la photo qui avait visé ce point (elle reste fixe ensuite, même si l'on vise depuis une autre photo) |
| deux (ou plus) | vertical, passant par les deux premiers : c'est **le mur tracé sur la carte** (s'ils sont distants d'au moins 20 cm ; sinon, comme pour un seul point) |

Le plan reste mémorisé jusqu'au prochain glisser, qui le redéfinit. Pour une largeur le long d'un mur, il faut glisser **les deux** points : avec un seul, le plan est face à la caméra et non le long du mur.

### 5.5 Façade recalée sur la carte

En mesure libre, la façade définie dans la photo (deux clics au pied du mur) hérite des défauts du point au sol : un pied masqué ou cliqué un peu haut la repousse derrière le mur. Ses deux extrémités s'affichent sur la carte : en les **glissant sur le bord du bâtiment**, la façade ne dépend plus du sol, du trottoir ni de la hauteur de caméra, et **toutes les mesures prises dessus sont recalculées**. Son incertitude devient alors celle de la carte (±0,5 m à chaque extrémité).

### 5.6 Viser à nouveau dans la visionneuse

**Sélection.** Entre deux mesures (pas au milieu de l'une d'elles), cliquer dans la visionneuse sur le **repère** d'un point, à moins de 1° de lui : un **cercle jaune** l'entoure. Le message indique s'il sera placé dans le plan calé ou sur sa surface d'origine.

**Nouvelle visée.** Le **clic suivant**, même tout près du repère (une correction fine l'est toujours), remplace le clic d'origine du point :

- si la mesure a un plan calé, le point est pris à l'**intersection du nouveau rayon et du plan calé**, qu'il ait été lui-même glissé ou non : la carte donne la profondeur, la photo la position dans le plan, latérale et en hauteur ;
- sinon, il est simplement visé à nouveau sur la surface de la mesure (le sol, par exemple) ;
- en triangulation 3D, il est visé à nouveau depuis l'une des photos qui l'avaient visé.

**Annuler.** « Effacer », tant qu'un point est sélectionné, annule la sélection sans effacer la mesure.

**Alterner.** On peut glisser à nouveau sur la carte (le point repart à la position lâchée et le plan est redéfini), puis viser à nouveau, autant de fois que nécessaire.

### 5.7 Ce que montre la visionneuse

Chaque repère est **reprojeté** là où la caméra voit le point 3D :

- un point glissé **le long de sa visée** (plus près ou plus loin) ne bouge pas dans l'image : la caméra le voit toujours dans la même direction, seule la valeur change ;
- un point glissé **de côté** se déplace dans l'image, et le trait avec lui ;
- l'étiquette prend toujours la nouvelle valeur.

Le message du panneau rappelle le nombre de points recalés et visés à nouveau, et chaque résultat se termine par « Point mal placé : glissez-le sur la carte. »

### 5.8 Déroulés types

**Hauteur d'une porte dont le pied est caché par une voiture.**

1. Mode « Hauteur d'un objet » : cliquer le pied (sur la voiture, faute de mieux), puis le haut de la porte.
2. Sur la carte, glisser le pied sur le bord du bâtiment, devant la porte.
3. Dans la visionneuse, cliquer le repère du pied (cercle jaune), puis viser le seuil de la porte.

Sur scène simulée (porte de 2,10 m dans un mur à 6 m, hauteur de caméra fausse de 40 cm) : 1,69 m avec les clics bruts, 1,97 m une fois le pied glissé sur la carte à 30 cm près, puis **2,11 m** après avoir visé le seuil, le pied étant placé à 3 cm de sa vraie position.

**Largeur d'une fenêtre ou d'un portail le long d'un mur.**

1. Mode « Longueur parallèle à la route » : cliquer les deux coins au pied.
2. Sur la carte, glisser **les deux** points sur le bord du bâtiment : le plan calé est le mur.
3. Viser à nouveau chaque coin dans la photo.

Sur scène simulée (fenêtre de 2,00 m au pied d'un mur à 6 m, hauteur de caméra fausse de 40 cm) : 1,60 m avec les clics bruts ; après avoir glissé les deux points sur le mur à 30 cm près puis visé chaque coin, **2,00 m**, les points étant à 1 à 8 cm de leur vraie position.

**Mesures en série sur une façade.**

1. Mesure libre, surface « Façade » : « Définir la façade », deux clics au pied du mur.
2. Sur la carte, glisser les deux extrémités de la façade sur le bord du bâtiment.
3. Mesurer fenêtres, portes et portails : toutes les mesures sont dans le plan de la façade, et suivent si on la recale encore.

Sur scène simulée (pieds de mur masqués, cliqués 15 cm trop haut, hauteur de caméra fausse) : une fenêtre de 2,00 m est mesurée 1,73 m sur la façade définie dans la photo, et exactement 2,00 m une fois ses extrémités recalées.

**Poteau dont le pied est cliqué trop haut.** Glisser le pied à sa place sur la carte suffit : sur scène simulée (pied cliqué 20 cm trop haut, caméra fausse de 40 cm), le pied passe de 5,3 m à 6,0 m de la photo et la hauteur entre le point cliqué et le sommet est retrouvée exactement (2,80 m). Pour la hauteur depuis le vrai pied, viser ensuite ce pied dans la photo.

### 5.9 Précision et limites

- **Incertitude.** Chaque point glissé est supposé placé à ±0,5 m sur la carte : sur une hauteur à 6 m, cela fait environ 8 %. Viser à nouveau ne la réduit que dans le plan (position latérale et hauteur) ; la profondeur reste celle de la carte. Elle est d'autant meilleure que l'orthophoto est précise et bien calée, et que le bord du bâtiment y est net.
- **Bord de toiture.** Sur l'orthophoto, on voit souvent le **débord de toit**, pas le pied du mur ; le cadastre ou la BD TOPO donnent mieux l'emprise au sol. Un débord de 50 cm à 6 m fausse une hauteur d'environ 8 %.
- **Décalage de l'orthophoto.** Une orthophoto non vraie (« ortho » classique) décale les bâtiments hauts, leur sommet étant vu de biais.
- **Plan vertical uniquement.** Le plan calé est vertical : il ne convient pas à un objet incliné (toiture, talus).
- **Pas de reprise après fermeture.** « Enregistrer les mesures » écrit le résultat dans une couche (segment et valeurs, mis à jour après un recalage), mais pas les clics ni les points recalés : une mesure ne peut plus être recalée après la fermeture de QGIS. Le calage des photos, lui, est conservé dans le profil QGIS.

---

## 6. Le calage des photos

Les calages mesurent les défauts propres à une photo ou une séquence, à partir d'éléments connus de la scène. Ils s'appliquent ensuite automatiquement à toutes les mesures et visées, sont rappelés sous les boutons de mesure, et sont **conservés dans le profil QGIS**, d'une session et d'un projet à l'autre, jusqu'à ce qu'ils soient refaits ou effacés (seules les observations sont stockées : verticales et repères cliqués, clics des références de hauteur ; les corrections en sont recalculées). Le calage d'un projet enregistré par une version précédente, qui le stockait dans le projet, est repris à l'ouverture du projet pour les photos et séquences qui n'en ont pas encore, puis retiré du projet. « Effacer » supprime le calage du mode affiché. Les mesures déjà faites ne sont pas recalculées après un nouveau calage.

Le recalage d'une mesure sur la carte (section 5) et le calage des photos sont complémentaires : le premier corrige **une mesure** après coup ; le second corrige **la photo ou la séquence** pour toutes les mesures à venir.

### 6.1 Inclinaison (objets verticaux), par photo

**But.** Trouver la vraie verticale dans le repère de la photo. Un véhicule penche (dévers d'environ 2 %, soit 1,1°, freinage, trottoir), et une caméra est rarement de niveau : 0,5° suffit à fausser d'un mètre un point au sol à 15 m. Le roulis fausse surtout les mesures prises sur le côté du véhicule.

**Clics.** Pied puis sommet d'objets bien verticaux (poteau, angle de façade, montant de portail), montant d'au moins 3° dans l'image, pied et sommet à moins de 30° de cap l'un de l'autre.

**Calcul.** Les directions du pied `b` et du sommet `s` définissent un plan contenant la vraie verticale, de normale `n = b × s`. La verticale `v = (a, b, 1)` doit vérifier `n · v = 0` pour chaque objet ; ces équations sont résolues par moindres carrés (avec un amortissement minime qui donne la plus petite correction compatible). Toute direction est ensuite tournée (formule de Rodrigues) de la rotation qui ramène `v` au zénith.

**Un ou deux objets.** Un objet ne corrige que l'inclinaison vue de côté dans sa direction (correction partielle, signalée) ; deux objets à environ 90° l'un de l'autre corrigent l'inclinaison complète ; au-delà, le résidu contrôle la cohérence. Une inclinaison de plus de 10° est refusée.

**Vérification.** Caméra simulée penchée de 2,5° : retrouvée exactement avec deux poteaux, un point au sol étant ensuite corrigé au centième de degré.

Le calage à la main sur deux objets ou plus l'emporte sur l'inclinaison automatique (section 6.1 bis). Sur **un seul objet**, il la complète au lieu de l'écraser : l'inclinaison automatique sert d'a priori (pondéré par son incertitude), l'objet corrige la composante qu'il voit, l'autre reste celle du calcul automatique. « Effacer » retire le calage à la main et rend la main au calcul automatique.

### 6.1 bis Inclinaison automatique, par photo

**Constat.** Diagnostic du 12/10/2026 sur 28 photos imajbox du CD21 (Pouilly-en-Auxois) : les panoramas ne sont pas mis à niveau. La caméra penche de 1 à 3° avec le véhicule ; le tangage suit la pente de la route (corrélation 0,95), le roulis son dévers. Sans correction, un point au sol à 6 m est déplacé d'environ 37 cm par degré.

**Quand.** Dès que la mesure est active, pour chaque photo 360° affichée qui n'a pas d'inclinaison calée à la main sur deux objets ou plus : le plugin télécharge la photo HD (7 à 8 Mo pour l'imajbox) et calcule en arrière-plan (3 à 5 s). Seule la photo affichée est analysée : quittée avant la fin, son téléchargement est interrompu et rien n'est enregistré (elle sera reprise si on y revient). Le résultat est conservé dans le profil QGIS, dans un fichier à part (`visionneuse_panoramax/inclinaisons.json`, une entrée par photo analysée) : une photo n'est analysée qu'une fois. Les clics faits sur la photo avant la fin du calcul sont corrigés à son arrivée, et leurs mesures recalculées.

**1. Verticales de la photo** (`core/autotilt.py`). Sur une photo de niveau, les arêtes verticales du monde (angles de façades, poteaux, encadrements) restent verticales dans l'image équirectangulaire ; la caméra penchée, elles s'inclinent selon une loi connue. La photo est décodée à 6 144 px de large, dans la bande de 3 à 45° au-dessus de l'horizon (le sol, les véhicules et la voiture de prise de vue sont en dessous). Par bandes de 2,5° : sommes du gradient horizontal le long de droites de pentes variées (transformée de Radon locale) pour repérer les arêtes rectilignes, puis position de l'arête sur chaque ligne (centre du gradient) et droite des moindres carrés. Chaque segment est traité comme un objet vertical cliqué (6.1) ; moindres carrés sur toutes, avec rejet itératif à 3 écarts-types (arbres, toits, objets penchés). Pentes cherchées jusqu'à ±6,8° : une verticale vue à 45° au-dessus de l'horizon penche de deux fois l'inclinaison de la caméra. Résultat retenu avec au moins 40 segments et une incertitude de moins de 0,25°, calculée dans la direction la moins contrainte (verticales toutes du même côté : une composante mal connue) et en comptant les arêtes distinctes, une même arête donnant un segment par bande.

**2. Pente et dévers du LiDAR HD**, faute de verticales (campagne). Plan du sol ajusté sur 15 points du LiDAR HD sous le véhicule (1,5 m devant et derrière, 0,8 m de chaque côté, sous les roues) : le tangage suit la pente, le roulis le dévers. Cette estimation n'est utilisée que sur une séquence où **au moins trois photos calées par leurs verticales** confirment que la caméra suit le véhicule (écart-type de moins de 0,8°) : une caméra portée à pied ou stabilisée n'est pas concernée. Le décalage moyen entre verticales et LiDAR (fixation de la caméra) est ajouté.

**Validation (Pouilly-en-Auxois, photos réelles).**

| | Résultat |
|---|---|
| Inclinaison imposée à une photo réelle (1° en long, 1° en travers) | retrouvée à 0,05–0,2° près |
| Photos calées par leurs verticales | 19 sur 28 à Pouilly (centre-ville), 21 sur 28 autour de Dijon (bourgs et quartiers pavillonnaires, ciel couvert) ; ±0,05 à 0,24° annoncés |
| Comparaison avec un détecteur de segments de référence (OpenCV LSD) | 0,11° d'écart médian, 0,48° au plus |
| LiDAR comparé aux verticales, en validation croisée (18 photos) | 0,4° en long, 0,5° en travers ; décalage de fixation +0,2° et −0,2° |
| Photos de campagne (10 sur 28) | estimées par le LiDAR, ±0,5° |

**Limites.** Calcul seulement pour les photos 360° (équirectangulaires) ; numpy est nécessaire (fourni avec QGIS) ; sans numpy, seule l'estimation LiDAR reste possible, et seulement si la séquence a déjà été validée. Une photo sans verticales sur une séquence non validée reste non calée : la ligne d'état le signale.

### 6.2 Hauteur de caméra (longueur connue), par séquence

**But.** Mesurer la hauteur réelle de la caméra, dont les mesures au sol dépendent proportionnellement.

**Référence.** « au sol » (deux extrémités : place de stationnement de 2,30 à 2,50 m, bande de passage piéton de 0,50 m, trait de marquage, longueur mesurée sur l'orthophoto) ou « en hauteur » (pied puis sommet d'un objet de hauteur connue), avec sa longueur réelle.

**Calcul.** Recherche par dichotomie, entre 0,3 et 6 m, de la hauteur qui redonne la longueur connue, avec le vrai profil de terrain et les clics corrigés de l'inclinaison. Incertitude : inclinaison de la photo (commune aux deux clics, voir 3.5) et visée de chaque clic (±0,1°), en somme quadratique. Plusieurs références se combinent (moyenne pondérée par `1/σ²`).

**Recalculée.** Les clics bruts, leur profil de terrain et la longueur connue sont conservés : la hauteur est recalculée quand le calage de la photo change (inclinaison automatique arrivée ensuite, calage refait). Un calage fait par une version précédente, qui n'enregistrait que la valeur, reste utilisé tel quel ; le rappel du calage invite à le refaire.

**Conseils.** Référence proche (moins de 10–15 m) et, au sol, **en travers** de la vue : dans l'axe, une longueur est la différence de deux distances et elle est bien moins précise.

**Effet.** La hauteur calée remplace la valeur saisie de `h` pour les mesures de la séquence, sauf pour les photos à position vérifiée précise, dont la hauteur vient de l'altitude GPS (section 2.7) (l'altitude du sol `z_sol` reste celle du profil de terrain, section 2.5), et elle est reportée et enregistrée dans le réglage « Caméra à ». Sur scène simulée (caméra à 2,30 m, panneau à 1,90 m) : 2,30 m retrouvés, et une voie de 3,50 m mesurée 3,50 m au lieu de 2,89 m.

### 6.3 Cap et position (repères sur la carte), par séquence

**But.** Corriger le cap de la séquence et, avec assez de repères, la position GPS des photos, par **relèvement**.

**Clics.** Un repère net (poteau, angle de bâtiment, borne) cliqué dans la photo, puis sur la carte (glisser pour déplacer la carte, Échap pour annuler).

**Calcul.** Trois inconnues communes aux photos des repères, le décalage de cap `θ` et le décalage de position `(sx, sy)`, ajustées par moindres carrés (Gauss-Newton) sur

```
gisement(Mᵢ − Pᵢ − s) = yᵢ + θ
```

avec un **a priori** sur chaque inconnue : cap juste à ±0,5° (matériel de relevé) ou ±3° (autres appareils), position GPS juste à ±sa précision. Chaque repère est pondéré par la précision de sa visée (clic à ±0,1°, repère sur la carte à ±0,5 m).

**Conséquences.**

- **Un repère** corrige surtout le cap, sans jamais le dégrader ; pour le cap, choisir un repère **lointain** (à 100 m, 2 m d'erreur GPS faussent déjà le cap de plus de 1°).
- **Trois repères ou plus, bien répartis** autour de la photo recalent aussi la **position** ; des repères **proches** y sont alors utiles.
- À partir du quatrième, un repère incohérent avec ce que prévoient les autres (plus de quatre écarts-types et de 1°) est refusé.
- Le recalage ne vaut que pour les photos à **moins de 300 m** de celles qui l'ont servi (le GPS dérive au fil d'une séquence). Il part toujours de la position GPS d'origine.

**Ordre de grandeur (simulation).** GPS faussé de 1,7 m et cap de 1°, cinq repères à 30–120 m pointés à ±0,5 m : erreur de position médiane ramenée de 1,70 m à 0,29 m sur 200 tirages, aucun bon repère refusé. Un objet triangulé ensuite depuis deux photos passe de 1,40 m à 0,44 m d'erreur.

### 6.4 Ordre conseillé

1. **Inclinaison**, qui intervient dans tous les autres calculs : automatique en général (6.1 bis) ; à caler à la main quand la ligne d'état l'indique non calée.
2. **Hauteur de caméra**, une fois pour la séquence.
3. **Cap et position**, si l'on triangule ou reporte des points sur la carte.
4. Puis mesurer, et **recaler sur la carte** les mesures qui le demandent (section 5).

---

## 7. Affichage et gestion des mesures

- **Mesures conservées.** Une mesure terminée reste affichée dans la visionneuse (sur la photo où elle a été prise) et sur la carte quand on en commence une autre, qu'on change de mode ou qu'on arrête de mesurer.
- **Effacer.** Supprime la mesure en cours (ou annule la sélection d'un point, ou supprime le calage du mode affiché). **Tout effacer** supprime toutes les mesures affichées, la façade et les visées de triangulation.
- **Enregistrer les mesures.** Ajoute les mesures terminées affichées à la couche « Panoramax – mesures » (segment mesuré et attributs du résultat, dont la décomposition et l'incertitude) ; une mesure déjà enregistrée puis recalée y est mise à jour. Effacer une mesure de l'écran ne la retire pas de la couche.
- **Visionneuse.** Croix jaune vif (`#ffeb00`) aux points cliqués, bien visibles sur la peinture routière, trait de mesure rouge (`#ff5f52`, 2,5 px) devant les croix, étiquette de la valeur (à côté du trait pour une hauteur), cercle jaune autour d'un point sélectionné.
- **Carte.** Mêmes croix jaunes aux points cliqués, cernées de sombre pour rester lisibles sur l'orthophoto, trait rouge de la mesure et étiquette. Pour la largeur perpendiculaire, le trait tracé perpendiculairement à la route, à mi-chemin des deux bords, est bleu clair : il ne relie pas les points cliqués, contrairement au trait rouge de la visionneuse, et porte l'étiquette « perpendiculaire à la route » (masquée quand le trait est trop court à l'échelle de la carte) ; des pointillés le relient aux deux bords cliqués. Trait « façade » au pied du mur.
- **Couches.** Mesures et points triangulés s'enregistrent dans « Panoramax – mesures » et « Panoramax – points triangulés » : au premier enregistrement, dans un fichier GeoPackage choisi (tables `mesures` et `points_triangules`, conservé à la fermeture de QGIS) ou en couche temporaire (à sauvegarder).

---

## 8. Sources d'erreur et ordres de grandeur

| Source | Ordre de grandeur | Effet principal | Remède dans le plugin |
|---|---|---|---|
| Pied masqué ou cliqué au-dessus du sol | quelques dizaines de cm à plusieurs mètres de profondeur | point au sol derrière le mur, distance et hauteur trop grandes | glisser le point sur la carte, viser à nouveau (section 5) |
| Hauteur de caméra | ±10 à 20 % si non calée | largeurs et hauteurs proportionnelles | calage de hauteur (6.2) ; recalage sur la carte, qui l'élimine (5.3) |
| Inclinaison de la caméra | 0,5 à 3° (1 à 3° sur l'imajbox) | mesures au sol : `δd ≈ d²/h · δe`, surtout sur le côté (roulis) | inclinaison automatique (6.1 bis : ±0,1 à 0,2°, ±0,5° par le LiDAR) ; calage d'inclinaison (6.1) ; recalage sur la carte |
| Terrain | creux, bosses, ponts, bordures | distances au sol | pente de Theil-Sen (3.1) ; recalage sur la carte |
| Position GPS de la photo | 2 m (relevé) à 5 m et plus | position absolue des points ; peu d'effet sur les longueurs prises sur une photo | recalage par repères (6.3), pondération (4.4) |
| Cap de la photo | 0,3° à plusieurs degrés ; +0,5° si arrondi | triangulation : `t · tan ε` de décalage latéral | cap EXIF précis (2.4), recalage du cap (6.3) |
| Position sur la carte | ±0,5 m (orthophoto), débord de toit | profondeur d'un point recalé | choisir le cadastre ou la BD TOPO plutôt que le bord de toit |
| Incidence rasante sur un plan | erreur en `1/sin(incidence)` | mesures sur façade ou plan horizontal | photo plus en face ; avertissement sous 15° |
| Précision du clic | 1 pixel = 0,03° sur une image de 12 288 px, bien plus sans zoom | toutes les mesures | zoomer avant de cliquer |
| Assemblage 360° | quelques pixels près des raccords | décalages locaux | éviter les raccords |
| Décalage temporel GPS / prise de vue | à 50 km/h, 10 ms = 14 cm | position le long de la route | recalage par repères |

---

## 9. Pistes d'amélioration des calculs

### Exploiter mieux les données existantes

- **Inclinaison fournie par la caméra** : lire `PosePitchDegrees` et `PoseRollDegrees` (XMP GPano) quand ils sont renseignés et non nuls.
- **Inclinaison lissée le long de la séquence** : l'inclinaison du véhicule varie lentement d'une photo à l'autre (5 m) ; combiner les photos voisines réduirait le bruit des photos pauvres en verticales.
- **Cap lissé** : combiner le cap EXIF avec la direction de la trajectoire et un décalage de montage constant.
- **Axe de la route** ajusté sur plusieurs photos, ou cliqué par l'utilisateur.

### Recalage sur la carte

- **Accrochage** : accrocher les points et les extrémités de façade glissés aux sommets et aux arêtes des couches de bâtiments (cadastre, BD TOPO), avec les outils d'accrochage de QGIS.
- **Plan calé depuis une couche** : prendre directement le plan dans l'arête de bâtiment la plus proche, sans glisser.
- **Plan incliné** défini par trois points connus en 3D (triangulés), pour les toitures, talus et rampes.

### Mesures sans hypothèse de sol ni de hauteur de caméra

- **Plus de deux visées par point** en triangulation 3D, pondérées, avec l'écart des visées comme contrôle.
- **Points réutilisables** : relier les points mesurés entre eux (polyligne, surface, angle) et les enregistrer avec leur altitude.

### Modèles et données plus riches

- **LiDAR HD de l'IGN** : MNT à 50 cm (ponts et ouvrages mieux traités) et nuage de points ou modèle de surface, pour intersecter un clic avec la façade ou l'objet réellement visé.
- **Ajustement en bloc** de toutes les photos d'un tronçon à partir de points homologues détectés automatiquement (structure à partir du mouvement), contraint par le GPS et quelques repères.
- **Dérive GPS modélisée** le long de la séquence au lieu d'une portée fixe de 300 m.
- **Décalage temporel** estimé par séquence.

### Incertitudes et contrôle

- **Propagation complète** des covariances (terrain, axe de la route, corrélations entre sources) au-delà de la somme quadratique d'écarts à ±1 écart-type.
- **Clic assisté** : accrochage sur un bord détecté dans l'image, affinage sous-pixel.
- **Contrôle croisé** automatique sur deux photos voisines.

---

## 10. Pistes d'amélioration de la prise de vues

### Matériel

- **GNSS corrigé** (RTK ou PPK, centimétrique) : le plus grand gain pour la triangulation et le report sur la carte.
- **Centrale inertielle** enregistrant cap, roulis et tangage dans les métadonnées (`GPSImgDirection` au centième, `PosePitchDegrees`, `PoseRollDegrees`), plutôt qu'une boussole perturbée par la carrosserie.
- **Caméra haute définition** à l'assemblage soigné (faible parallaxe entre objectifs).
- **Synchronisation** des horloges de la caméra et du GNSS.

### Installation

- **Fixation rigide et de niveau**, vérifiée au niveau à bulle : l'inclinaison est la première source d'erreur des mesures au sol.
- **Hauteur connue et constante**, mesurée au mètre ruban et indiquée dans la description de la séquence.
- **Raccords d'assemblage** orientés vers des zones sans intérêt pour la mesure.

### Acquisition

- **Espacement de 2 à 5 m** entre photos, pour des visées sous des angles variés.
- **Vitesse modérée** : moins de flou, moins de décalage temporel.
- **Plusieurs passages** (aller et retour, voies différentes) pour des visées croisées et des GPS indépendants.
- **Bonnes conditions** : lumière diffuse, chaussée sèche ; éviter les assemblages HDR qui créent des images fantômes.
- **Repères de contrôle** visibles : bornes géodésiques, marques de dimensions connues, points levés au GNSS.
- **Pieds de façade dégagés** autant que possible (éviter les rues encombrées de véhicules), pour des pieds visibles au contact du sol.

### Publication

- **Conserver les métadonnées précises** à l'envoi : cap au centième, précision GPS, inclinaison.
- **Pleine résolution.**
- **Décrire la séquence** : caméra, hauteur de montage, support, GNSS.

---

## 11. Annexe : paramètres du code

| Paramètre | Valeur | Rôle | Fichier |
|---|---|---|---|
| `DEFAULT_CAMERA_HEIGHT` | 1,90 m | hauteur de caméra par défaut | `measure.py` |
| `AIM_ERROR`, `UNCALIBRATED`, `OBJECT_ERROR` | 0,1°, 2°, 0,2° | visée d'un clic ; inclinaison d'une photo non calée ; écart d'un objet vertical cliqué | `core/calibration.py` |
| `CAMERA_ERRORS` | 5 cm (altitude GPS), 15 cm (saisie) | incertitude de la hauteur de caméra | `core/measures.py` |
| `WALL_ERROR`, `WALL_ERROR_UNCHECKED` | 0,5 m, 1 m | façade du cadastre confirmée ou non par le LiDAR | `core/measures.py` |
| `MAX_DISTANCE` | 60 m | portée maximale d'un point au sol | `core/ground.py` |
| `MAX_TOP_OFFSET` | 45° | écart de cap maximal entre pied et sommet d'une hauteur | `core/ground.py` |
| `LINE_FROM`, `LINE_TO` | −30 m, 90 m | fenêtre du profil de terrain | `core/terrain.py` |
| `MAX_COARSE_SLOPE` | 12 % | pente maximale du service de secours | `core/terrain.py` |
| `MAX_RANGE` | 100 m | portée maximale d'un point sur un plan | `core/geometry.py` |
| `GRAZING` | 15° | incidence sous laquelle une visée sur un plan est signalée | `core/geometry.py` |
| `MIN_CROSSING` | 3° | croisement minimal en triangulation 3D | `core/geometry.py` |
| `PLANE_HEIGHT_ERROR` | 0,05 m | incertitude de la hauteur du plan horizontal | `core/measures.py` |
| `SELECT_ANGLE` | 1° | écart maximal entre un clic et un repère pour le sélectionner | `core/measures.py` |
| poignées sur la carte | 10 px | distance de saisie d'un point à glisser | `plugin.py` |
| `CLICK_TOLERANCE` | 4 px | au-delà, un clic sur la carte est un glisser | `plugin.py` |
| `MAP_ERROR` | 0,5 m | précision d'un point placé sur la carte (recalage, repères, façade) | `core/calibration.py` |
| `AIM_ERROR` | 0,1° | précision d'un clic dans la photo zoomée | `core/calibration.py` |
| `HEADING_ERROR` | 1° | erreur de visée supposée en triangulation | `core/triangulation.py` |
| `SURVEY_HEADING_ERROR`, `SURVEY_ACCURACY` | 0,5°, 2 m | matériel de relevé | `core/triangulation.py` |
| `GPS_ACCURACY`, `GPS_WARNING` | 3 m, 5 m | précision GPS supposée, seuil d'avertissement | `core/triangulation.py` |
| `MIN_ANGLE`, `MAX_DISTANCE` | 3°, 500 m | croisement minimal et distance maximale d'un point triangulé | `core/triangulation.py` |
| `MAX_TILT`, `MIN_SPAN`, `MAX_LEAN` | 10°, 3°, 30° | contrôles du calage d'inclinaison | `core/calibration.py` |
| `MAX_DISTANCE`, `MIN_CROSSING`, `TANGENT` | 15 m, 45°, ±10 m | choix du tronçon BD TOPO et direction locale | `core/roads.py` |
| `MIN_RANGE`, `MAX_RANGE` | 1 m, 60 m | façades du cadastre cherchées le long de la visée | `core/buildings.py` |
| `BUILT`, `LIDAR_BEFORE`, `LIDAR_AFTER`, `SEARCH_BEFORE` | 2 m, 0,5 m, 2 m, 3 m | bâti du LiDAR et contrôle de la façade | `core/buildings.py` |
| `CELL`, `TRACK_RADIUS`, `RETRY` | 0,001° (~100 m), 20 m, 30 s | maille de lecture des tronçons, photos de la trajectoire, nouvel essai après une panne | `core/roads.py` |
| `ELEV_MIN`, `ELEV_MAX`, `BAND_DEG` | 3°, 45°, 2,5° | bande et bandes d'analyse des verticales | `core/autotilt.py` |
| `MAX_SLOPE`, `EDGE_BIN` | 0,12 (6,8°), 0,3° | pente maximale d'une verticale dans l'image ; segments d'une même arête | `core/autotilt.py` |
| `WIDTH` | 6 144 px | largeur de décodage de la photo | `core/autotilt.py` |
| `MIN_SEGMENTS`, `MAX_SIGMA` | 40, 0,25° | conditions pour retenir l'inclinaison des verticales | `core/autotilt.py` |
| `LIDAR_ALONG`, `LIDAR_ACROSS` | 1,5 m, 0,8 m | grille LiDAR sous le véhicule | `core/calibration.py` |
| `LIDAR_MIN_PHOTOS`, `LIDAR_MAX_SIGMA` | 3, 0,8° | validation du modèle LiDAR d'une séquence | `core/calibration.py` |
| `SURVEY_PRIOR`, `PRIOR` | 0,5°, 3° | précision a priori du cap | `core/calibration.py` |
| `REACH`, `OUTLIER` | 300 m, 4 | portée du recalage, seuil de refus d'un repère | `core/calibration.py` |
| `CAMERA_MIN`, `CAMERA_MAX` | 0,3 m, 6 m | plage de recherche de la hauteur de caméra | `core/calibration.py` |
