# Mesures et calage : méthodes, précision et pistes d'amélioration

Ce document décrit en détail comment la Visionneuse Panoramax mesure à partir des photos 360°, comment fonctionnent les fonctions de calage, quelles sont les sources d'erreur, et comment améliorer la précision, côté calculs comme côté prise de vues. Le mode d'emploi résumé se trouve dans le [README](../README.md).

Sommaire :

1. [Principes communs](#1-principes-communs)
2. [Méthodes de mesure](#2-méthodes-de-mesure)
3. [Fonctions de calage](#3-fonctions-de-calage)
4. [Sources d'erreur et ordres de grandeur](#4-sources-derreur-et-ordres-de-grandeur)
5. [Pistes d'amélioration des calculs](#5-pistes-damélioration-des-calculs)
6. [Pistes d'amélioration de la prise de vues](#6-pistes-damélioration-de-la-prise-de-vues)
7. [Annexe : paramètres du code](#7-annexe--paramètres-du-code)

---

## 1. Principes communs

### 1.1 Ce que fournit une photo

Toutes les mesures reposent sur trois informations par photo, fournies par Panoramax :

| Information | Source | Qualité typique |
|---|---|---|
| Position (lon, lat) | GPS de la caméra, `geometry` de la photo | 2 m (matériel de relevé, GPS corrigé) à 4–5 m (caméras grand public, téléphones), indiquée dans `quality:horizontal_accuracy` |
| Cap du centre de l'image | `view:azimuth` (arrondi au degré par l'API), ou EXIF `GPSImgDirection` (au centième de degré quand l'appareil le fournit) | 0,3° (centrale inertielle de relevé) à plusieurs degrés (boussole de téléphone) |
| Image équirectangulaire | photo 360° assemblée | chaque pixel correspond à une direction (cap, élévation) |

Le plugin ne connaît **ni la hauteur de la caméra au-dessus du sol, ni son inclinaison** : il suppose une caméra de niveau, à la hauteur indiquée dans le panneau (1,90 m par défaut). Les fonctions de calage (section 3) servent précisément à mesurer ces inconnues et à corriger le cap et la position.

### 1.2 Repère et directions

- Les calculs se font dans un **plan tangent local** (est, nord) centré sur la zone de mesure : sur quelques centaines de mètres, l'erreur due à la projection est négligeable devant la précision du GPS.
- Une **direction** est un couple (cap absolu, élévation) en degrés : cap 0 = nord, sens horaire ; élévation positive vers le haut. Un vecteur unitaire (est, nord, haut) lui correspond : `(sin cap · cos élév, cos cap · cos élév, sin élév)`.

### 1.3 D'un clic à une direction

- **Visionneuse web** (Photo Sphere Viewer) : la visionneuse calcule elle-même, par lancer de rayon sur la sphère, la position du clic dans la photo (angle dans la photo et élévation). Le cap absolu vaut cet angle plus le cap de la photo. Un second calcul, à partir de la position du clic à l'écran, du champ de vision et de l'orientation de la vue, sert de contrôle (journal QGIS « Panoramax »).
- **Visionneuse de secours** (sans QtWebEngine) : l'image équirectangulaire est affichée directement ; la direction d'un pixel s'en déduit linéairement.
- **Visée au réticule** (triangulation) : direction du centre de la vue.

### 1.4 Cap précis

L'API Panoramax arrondit `view:azimuth` au degré, soit jusqu'à 0,5° d'erreur de visée (17 cm à 20 m, 87 cm à 100 m). Quand l'EXIF de la photo contient `GPSImgDirection` (au centième de degré sur le matériel de relevé), le plugin l'utilise à la place, mais seulement s'il concorde à moins de 1° avec `view:azimuth` : il s'agit alors bien de la même mesure non arrondie, et non d'un cap que Panoramax aurait corrigé. La visionneuse web, elle, s'oriente sur la valeur arrondie : le plugin corrige l'écart dans ses calculs.

Sur les photos IMAJING imajbox 360 HD du Conseil départemental de la Côte-d'Or, le cap EXIF suit la trajectoire du véhicule à 0,33° près (écart-type sur 700 photos en ligne droite). Il s'en écarte de 3,2° de façon constante, parce que la caméra est montée un peu de biais : l'EXIF décrit bien l'orientation de l'image (vérifié sur le point de fuite d'une route droite).

### 1.5 Ordre d'application des corrections

Pour chaque clic ou visée, le plugin applique dans cet ordre :

1. **inclinaison** de la photo (calage par objets verticaux) ;
2. **décalage de cap** de la séquence (calage par repères) ;
3. **position recalée** de la photo (calage par repères, à partir de trois repères) ;
4. **hauteur de caméra** calée pour la séquence, dans les mesures au sol.

---

## 2. Méthodes de mesure

### 2.1 Point au sol par lancer de rayon

C'est la brique des mesures de largeur et de hauteur.

**Principe.** La caméra est à une hauteur `h` au-dessus du sol. Un clic donne une direction (cap, élévation `e`, négative vers le sol). On suit ce rayon jusqu'à ce qu'il passe sous le terrain : le point d'intersection est le point au sol cliqué.

Sur un sol plat, la distance horizontale du point vaut :

```
d = h / tan(−e)
```

**Profil du terrain.** Le sol n'est pas forcément plat : le plugin demande l'altitude du terrain le long de la visée, de 30 m derrière la photo à 90 m devant, à l'une des sources suivantes, essayées dans l'ordre (5 s maximum chacune) :

1. **service d'altimétrie de l'IGN** (RGE ALTI 1 m, France), 241 points tous les 0,5 m, si la case « Service d'altimétrie IGN » est cochée ;
2. **OpenTopoData EU-DEM 25 m** (Europe), puis **Open-Meteo Copernicus 90 m** (monde) : modèles grossiers, dont seule la pente générale a un sens ;
3. à défaut, **sol plat**.

**Pente de la route plutôt que profil brut.** Le plugin n'utilise pas le profil tel quel, mais la **droite de pente** du terrain autour de la photo, ajustée par la méthode de **Theil-Sen** : la pente retenue est la médiane des pentes entre toutes les paires de points, et l'ordonnée la médiane des résidus. Un modèle de terrain nu ignore les ponts (il donne le fond du cours d'eau sous le tablier) et contient fossés, talus et bruit ; or un creux d'un mètre sous la visée allonge les distances de moitié. La médiane ignore ces accidents tant qu'ils couvrent moins d'un tiers de la fenêtre, et garde la vraie pente d'une rue. Les sources de secours, qui sont des modèles de surface (bâtiments et arbres compris), sont écartées si leur pente dépasse 12 %.

**Intersection.** Le terrain étant linéaire entre deux échantillons, l'intersection rayon–terrain est calculée exactement. Un point qui ne touche pas le sol dans les 60 m est refusé.

**Sensibilité.** La distance dépend fortement de l'élévation du clic, donc de l'inclinaison de la caméra et de la précision du clic :

```
δd ≈ d² / h · δe     (δe en radians)
```

Avec une caméra à 2 m, une erreur de 0,5° déplace un point situé à 8 m de 0,28 m, et un point à 15 m de 0,98 m. D'où la consigne : **mesurer à moins de 15–20 m**, et caler l'inclinaison (section 3.2).

### 2.2 Largeur perpendiculaire à la chaussée et largeur parallèle à la route

Deux modes partagent les mêmes clics au sol mais pas le même calcul. Dans les deux cas, chaque clic est prolongé jusqu'au sol (section 2.1) : on obtient deux points au sol `P` et `Q`.

**Largeur parallèle à la route** (distance directe).

- **Clics.** Les deux extrémités de l'objet, au sol, typiquement le long de la route sur le côté du véhicule : façade, portail, place de stationnement. Le calcul étant une distance directe, il reste juste dans n'importe quelle direction.
- **Calcul.** Longueur de `PQ` en trois dimensions : distance horizontale et dénivelé entre les deux points, qui suit donc la pente. Le dénivelé est indiqué à partir de 5 cm.

**Largeur perpendiculaire à la chaussée**.

- **Clics.** Pied d'un bord (bordure, marquage, limite de chaussée), puis pied du bord opposé, sur la même photo ou sur deux photos.
- **Usage.** En travers de la route uniquement. Le long de la route (deux points d'un mur sur le côté du véhicule, par exemple), la composante perpendiculaire est presque nulle : c'est la largeur parallèle à la route qu'il faut alors utiliser.
- **Calcul :**

1. L'**axe de la route** est la direction de la séquence, calculée entre la photo précédente et la photo suivante. À défaut (photos voisines inconnues ou confondues lors d'un arrêt), c'est l'orientation de la photo.
2. La **largeur** est la composante de `PQ` perpendiculaire à l'axe : les deux clics n'ont pas besoin d'être exactement en face l'un de l'autre. Le point dessiné en face du premier est le pied de la perpendiculaire. La distance en biais `|PQ|` est aussi indiquée.

**Incertitude affichée.** Écart maximal du résultat quand l'élévation de chaque clic varie de ±0,5°. Elle ne tient pas compte de l'erreur de hauteur de caméra : une hauteur fausse de 10 % fausse la largeur de 10 % environ (section 3.4).

### 2.3 Hauteur d'un objet

**Clics.** Pied de l'objet (au sol), puis sommet, sur la même photo.

**Calcul.**

1. Le pied est prolongé jusqu'au sol (section 2.1) : distance horizontale `d`, altitude du sol `z_sol`.
2. Le sommet est supposé à la **même distance horizontale** (objet vertical) : son altitude vaut `z_caméra + d · tan(e_sommet)`.
3. Hauteur = altitude du sommet − `z_sol`.

Sur sol plat, cela revient à `H = h · (1 + tan(e_sommet) / tan(−e_pied))` : la hauteur est **proportionnelle à la hauteur de caméra**. Un sommet sous le pied est refusé (clics inversés).

**Incertitude affichée.** Comme pour la largeur : ±0,5° sur l'élévation de chaque clic.

### 2.4 Triangulation d'un objet

**But.** Positionner sur la carte un objet visible de plusieurs photos (panneau, poteau, regard…), sans hypothèse sur le sol ni sur la hauteur de caméra.

**Visées.** Dans chaque photo, on place le réticule sur l'objet puis « 🎯 Viser ». Une visée est une demi-droite partant de la position de la photo dans la direction du réticule (cap uniquement : la triangulation est planimétrique).

**Calcul (moindres carrés pondérés).**

Le point retenu `X` minimise la somme pondérée des carrés des distances perpendiculaires aux droites de visée :

```
X = argmin Σ wᵢ · dist(X, visée i)²
```

Ce qui donne un système linéaire 2×2 (équations normales) :

```
Σ wᵢ (I − uᵢ uᵢᵀ) X = Σ wᵢ (I − uᵢ uᵢᵀ) Pᵢ
```

avec `Pᵢ` la position de la photo, `uᵢ` la direction de visée unitaire.

**Pondération.** Le poids d'une visée est l'inverse du carré de son écart latéral probable :

```
σᵢ² = (tᵢ · tan εᵢ)² + aᵢ²       wᵢ = 1 / σᵢ²
```

- `tᵢ` : distance de la photo au point le long de la visée ;
- `εᵢ` : erreur de visée supposée, 1° en général, 0,5° pour le matériel de relevé (GPS à 2 m ou mieux, cap EXIF précis), ou l'erreur issue du calage du cap ;
- `aᵢ` : précision de la position GPS de la photo (3 m supposés si inconnue), ou celle de la position recalée.

Une visée lointaine ou une photo mal positionnée compte donc moins. Comme les distances `tᵢ` dépendent du point, le calcul est itéré (4 passes, la première sans pondération).

**Contrôles.** Visées presque parallèles (croisement < 3°), point derrière une photo (visées qui ne se croisent pas devant), point à plus de 500 m : refusés avec un message.

**Résultats affichés.** Distances à chaque photo, meilleur angle de croisement, **incertitude** (grand axe de l'ellipse de covariance `(Σ wᵢ (I − uᵢ uᵢᵀ))⁻¹`), **écart des visées** (écart quadratique moyen des visées au point, à partir de 3 visées), précision GPS, avertissement si le GPS est moins bon que ±5 m ou si le croisement est inférieur à 15°.

**Enregistrement.** « Enregistrer le point » l'ajoute à la couche temporaire « Panoramax – points triangulés » (nombre de visées, angle, incertitude, écart, distance maximale, précision GPS, photos utilisées, date).

**Géométrie favorable.** L'erreur de position vaut à peu près `σ / sin θ`, avec `θ` l'angle de croisement : viser avec au moins 30° de croisement, idéalement 60 à 90°, depuis des photos proches de l'objet. Une troisième visée améliore et contrôle le résultat.

---

## 3. Fonctions de calage

### 3.1 Pourquoi caler

Les mesures supposent une caméra de niveau, à une hauteur connue, avec une position et un cap justes. En pratique :

- un véhicule penche (dévers, freinage, montée sur un trottoir) et une caméra est rarement fixée parfaitement de niveau : **0,5° d'inclinaison** suffit à fausser d'un mètre un point au sol à 15 m ;
- la **hauteur de caméra** varie selon le support (toit de voiture, perche, casque, sac à dos) : une hauteur fausse de 10 % fausse largeurs et hauteurs de 10 % ;
- le **cap** peut être faux de plusieurs degrés (boussole, trajectoire GPS), et la **position** de plusieurs mètres.

Les calages mesurent ces défauts à partir d'éléments connus de la scène. Ils s'appliquent ensuite automatiquement à toutes les mesures, sont rappelés sous les boutons de mesure, et sont **enregistrés dans le projet QGIS** : seules les observations (clics, repères) sont stockées, les corrections en sont recalculées à l'ouverture. « Effacer » supprime le calage du mode affiché.

### 3.2 Inclinaison (objets verticaux), par photo

**But.** Trouver la vraie verticale dans le repère de la photo, pour corriger l'élévation et le cap de tous les clics.

**Clics.** Pied puis sommet d'objets bien verticaux : poteau, angle de façade, montant de portail. Chaque objet doit monter d'au moins 3° dans l'image, pied et sommet ne doivent pas s'écarter de plus de 30° en cap.

**Calcul.**

1. Pour chaque objet, les directions du pied `b` et du sommet `s` définissent un plan passant par la caméra, qui contient l'objet, donc la vraie verticale. Sa normale est `n = b × s`.
2. La vraie verticale `v` doit être perpendiculaire à toutes ces normales : `n · v = 0`. En posant `v = (a, b, 1)` (petite inclinaison), chaque objet donne une équation linéaire en `(a, b)`, résolue par moindres carrés. Un amortissement minime donne la plus petite correction compatible quand les équations ne suffisent pas.
3. Toute direction cliquée est ensuite tournée (formule de Rodrigues) de la rotation qui ramène `v` au zénith.

**Un ou deux objets.** Un objet ne contraint que l'inclinaison **vue de côté** dans sa direction : la correction est partielle, et le plugin le signale. Deux objets à environ **90° l'un de l'autre** déterminent l'inclinaison complète. Au-delà, le résidu (écart des objets à la verticale retenue) contrôle la cohérence des clics. Une inclinaison trouvée de plus de 10° est refusée (objet penché ou clics faux).

**Portée.** La photo seule : l'inclinaison d'un véhicule change d'une photo à l'autre.

### 3.3 Cap et position (repères sur la carte), par séquence

**But.** Corriger le cap de la séquence et, avec assez de repères, la position GPS des photos. C'est un **relèvement**, comme en topographie : on retrouve la position et l'orientation d'un appareil à partir des directions sous lesquelles il voit des points connus.

**Clics.** Un repère net, visible aussi sur la carte ou l'orthophoto (poteau, angle de bâtiment, borne), est cliqué dans la photo, puis sur la carte. Pendant le pointage, la carte reste libre : glisser pour la déplacer, molette pour zoomer, Échap pour annuler.

**Calcul (moindres carrés avec a priori, Gauss-Newton).** Trois inconnues communes aux photos des repères : le décalage de cap `θ` et le décalage de position `(sx, sy)`. Pour chaque repère `Mᵢ` vu depuis la photo `Pᵢ` sous le cap mesuré `yᵢ` (corrigé de l'inclinaison) :

```
gisement(Mᵢ − Pᵢ − s) = yᵢ + θ
```

Les résidus sont pondérés par la précision de la visée : clic à ±0,1° et repère pointé sur la carte à ±0,5 m, soit un angle d'autant plus grand que le repère est proche. Chaque inconnue garde son **a priori** :

- décalage de cap nul, à ±0,5° pour le matériel de relevé ou ±3° pour les autres appareils ;
- position GPS juste, à ±sa précision.

Le calcul est itéré jusqu'à convergence (10 passes au plus). L'inverse de la matrice normale donne les incertitudes du cap et de la position.

**Conséquences.**

- **Un repère** corrige surtout le cap, et ne le dégrade jamais : un repère proche, où l'erreur GPS de la photo pèse lourd, ne le corrige que peu. Pour le cap seul, choisir un repère **lointain** : à 100 m, 2 m d'erreur GPS faussent déjà le cap de plus de 1°.
- **Trois repères ou plus, bien répartis autour de la photo** (devant, derrière, sur les côtés) recalent aussi la **position**. Pour la position, des repères **proches** sont au contraire les plus utiles. La position n'est déclarée recalée qu'à partir de trois repères, et si son incertitude tombe sous 70 % de la précision GPS.
- **Contrôle.** À partir du quatrième, un repère est comparé à la direction que prévoient les autres seuls ; s'il s'en écarte de plus de quatre écarts-types (et de plus de 1°), il est refusé (repère mal pointé).

**Portée.** Le décalage GPS dérive au fil d'une longue séquence : le recalage ne s'applique qu'aux photos situées à **moins de 300 m** des photos qui l'ont servi. Le calcul part toujours de la position GPS d'origine, jamais d'une position déjà recalée.

**Effet.** Les clics et visées utilisent le cap corrigé, la position recalée et sa précision (au lieu de celle du GPS), et l'erreur de visée issue du calage.

**Ordre de grandeur (simulation).** GPS faussé de 1,7 m et cap de 1°, cinq repères à 30–120 m pointés à ±0,5 m : erreur de position médiane ramenée de 1,70 m à 0,29 m sur 200 tirages, cap retrouvé à 0,15° près, aucun bon repère refusé à tort. Un objet triangulé ensuite depuis deux photos passe de 1,40 m à 0,44 m d'erreur.

### 3.4 Hauteur de caméra (longueur connue), par séquence

**But.** Mesurer la hauteur réelle de la caméra au-dessus du sol, dont les largeurs et hauteurs dépendent proportionnellement.

**Référence.** Choisir « au sol » ou « en hauteur » et indiquer la longueur réelle :

- **au sol**, deux extrémités cliquées au sol : largeur d'une place de stationnement (2,30 à 2,50 m), bande de passage piéton (0,50 m), trait de marquage, ou longueur mesurée sur l'orthophoto avec l'outil de mesure de QGIS ;
- **en hauteur**, pied puis sommet d'un objet de hauteur connue.

**Calcul.** La longueur mesurée croît avec la hauteur de caméra supposée (proportionnellement sur sol plat). Le plugin cherche par **dichotomie**, entre 0,3 et 6 m, la hauteur qui redonne la longueur connue, avec le vrai profil de terrain et les clics corrigés de l'inclinaison. L'incertitude est l'écart obtenu en faisant varier l'élévation de chaque clic de ±0,5°. Plusieurs références se combinent par moyenne pondérée (poids `1/σ²`).

**Conseils.** Référence **proche** (moins de 10–15 m). Au sol, la prendre plutôt **en travers** de la vue : une longueur dans l'axe est la différence de deux distances, chacune sensible à l'élévation du clic, et elle est donc bien moins précise.

**Effet.** La hauteur calée remplace la valeur saisie pour les mesures de la séquence, et elle est reportée et enregistrée dans le réglage « Caméra à » du panneau.

### 3.5 Ordre de calage conseillé

1. **Inclinaison** d'abord : elle intervient dans tous les autres calculs, y compris les autres calages.
2. **Hauteur de caméra** ensuite, une fois pour la séquence (le support ne change pas).
3. **Cap et position** si l'on triangule ou si l'on reporte des points sur la carte.
4. Puis mesurer. Les mesures déjà faites ne sont pas recalculées après un nouveau calage.

---

## 4. Sources d'erreur et ordres de grandeur

| Source | Ordre de grandeur | Effet principal | Remède dans le plugin |
|---|---|---|---|
| Position GPS de la photo | 2 m (relevé) à 5 m et plus (téléphone) | position absolue des points triangulés ; peu d'effet sur largeurs et hauteurs prises sur une photo | recalage par repères (3.3), pondération (2.4) |
| Cap de la photo | 0,3° (relevé) à plusieurs degrés (boussole) ; +0,5° si arrondi | triangulation : `t · tan ε` de décalage latéral | cap EXIF précis (1.4), recalage du cap (3.3) |
| Inclinaison de la caméra | 0,5 à 3° | mesures au sol : `δd ≈ d²/h · δe` | calage d'inclinaison (3.2) |
| Hauteur de caméra | ±10 à 20 % si non calée | largeurs et hauteurs proportionnelles | calage de hauteur (3.4) |
| Terrain | creux, bosses, ponts, devers | distances au sol | pente de Theil-Sen (2.1) |
| Précision du clic | 1 pixel = 0,03° sur une image de 12 288 px, bien plus sans zoom | toutes les mesures | zoomer avant de cliquer |
| Assemblage 360° | parallaxe entre objectifs, surtout près de la caméra et sur les lignes de raccord | décalages locaux de quelques pixels | éviter les raccords |
| Décalage temporel GPS / prise de vue | à 50 km/h, 10 ms = 14 cm le long de la trajectoire | position le long de la route | recalage par repères |

---

## 5. Pistes d'amélioration des calculs

### Exploiter mieux les données existantes

- **Inclinaison fournie par la caméra.** Lire `PosePitchDegrees` et `PoseRollDegrees` (XMP GPano) quand ils sont renseignés et non nuls (caméras avec centrale inertielle), pour une correction automatique, sans objets verticaux.
- **Inclinaison par séquence.** Décomposer l'inclinaison d'une photo en un défaut de montage constant pour la séquence, plus la pente et le dévers de la route (tirés du MNT) : caler deux ou trois photos suffirait pour toute la séquence.
- **Cap lissé.** Sur le matériel de relevé, combiner le cap EXIF avec la direction de la trajectoire plus un décalage de montage constant (−3,2° sur les imajbox du CD21) pour réduire son bruit.
- **Axe de la route.** Ajuster l'axe sur plusieurs photos de part et d'autre, plutôt que sur les deux voisines, ou laisser l'utilisateur cliquer l'axe.

### Mesures sans hypothèse de sol ni de hauteur de caméra

- **Largeur par triangulation.** Trianguler chaque extrémité ou chaque bord depuis deux ou trois photos : la largeur ne dépend plus ni de la hauteur de caméra, ni de l'inclinaison, ni du terrain.
- **Triangulation 3D.** Utiliser aussi l'élévation des visées : on obtiendrait l'altitude des points et la hauteur d'un objet par triangulation de son pied et de son sommet, sans hauteur de caméra.
- **Mesures entre photos.** Relier deux points triangulés pour mesurer une distance quelconque (longueur d'un mur, entraxe de poteaux).

### Modèles et données plus riches

- **LiDAR HD de l'IGN.** Utiliser le MNT LiDAR HD (50 cm, ponts et ouvrages mieux traités) et le nuage de points ou le modèle de surface, pour intersecter un clic avec la façade ou l'objet réellement visé, et non seulement avec le sol.
- **Repères automatiques.** Proposer comme repères les objets de la BD TOPO ou d'OpenStreetMap (bâtiments, poteaux, bornes), ou accrocher le clic sur la carte aux sommets de ces objets.
- **Ajustement en bloc de la séquence.** Estimer conjointement la position et l'orientation de toutes les photos d'un tronçon à partir de points homologues détectés automatiquement entre photos voisines (structure à partir du mouvement), contraints par le GPS et quelques repères : c'est la méthode de référence des relevés mobiles, au prix d'un traitement d'image lourd.
- **Dérive GPS modélisée.** Remplacer la portée fixe de 300 m par un décalage qui varie lentement le long de la séquence (par exemple linéaire par morceaux), ajusté sur des repères répartis.
- **Décalage temporel.** Estimer, par séquence, le retard entre la prise de vue et la position GPS (décalage le long de la trajectoire proportionnel à la vitesse).

### Incertitudes et contrôle

- **Propagation complète.** Propager rigoureusement les covariances (inclinaison calée, hauteur de caméra, position recalée, profil de terrain) jusqu'au résultat, au lieu de l'écart à ±0,5° actuel.
- **Clic assisté.** Accrocher le clic à un bord détecté dans l'image (gradient), ou affiner au sous-pixel, pour les bordures et marquages.
- **Contrôle croisé.** Mesurer automatiquement le même objet sur deux photos voisines et signaler les écarts.

---

## 6. Pistes d'amélioration de la prise de vues

### Matériel

- **Positionnement GNSS corrigé** (RTK ou post-traitement PPK, centimétrique) : c'est le gain le plus important pour la triangulation et le report de points sur la carte. À défaut, un GNSS externe double fréquence plutôt que celui d'un téléphone.
- **Centrale inertielle** enregistrant le cap, le roulis et le tangage dans les métadonnées (`GPSImgDirection` au centième, `PosePitchDegrees`, `PoseRollDegrees`), plutôt qu'une boussole magnétique, perturbée par la carrosserie d'un véhicule.
- **Caméra haute définition** : sur une image de 12 288 px de large, un pixel couvre 0,03°. Préférer une caméra dont l'assemblage est soigné (faible parallaxe entre objectifs).
- **Synchronisation** de l'horloge de la caméra et du GNSS, pour que la position corresponde à l'instant exact de la prise de vue.

### Installation

- **Fixation rigide et de niveau**, vérifiée au niveau à bulle : l'inclinaison est la première source d'erreur des mesures au sol. Une fixation stable permettrait aussi de caler l'inclinaison une fois pour toute une séquence (voir section 5).
- **Hauteur connue et constante** : mesurer la hauteur de l'objectif au-dessus du sol au mètre ruban, et l'indiquer dans la description de la séquence. Plus haut (2 à 2,5 m sur un toit de voiture), on voit mieux par-dessus les véhicules ; plus bas, les mesures au sol proches sont plus précises.
- **Raccords d'assemblage** orientés vers des zones sans intérêt pour la mesure : connaître l'emplacement des raccords de sa caméra et éviter d'y faire passer les bords de chaussée.

### Acquisition

- **Espacement de 2 à 5 m** entre photos : plus de photos proches des objets, et des visées sous des angles variés pour la triangulation.
- **Vitesse modérée** : moins de flou de bougé, moins d'effet du décalage temporel, et un GPS plus stable.
- **Plusieurs passages** (aller et retour, voies différentes) : ils donnent des visées croisées sous des angles favorables, et des positions GPS indépendantes qui se compensent.
- **Bonnes conditions** : lumière diffuse, sans contre-jour ni ombres portées marquées, chaussée sèche pour des marquages lisibles. Éviter les assemblages HDR qui créent des images fantômes sur les objets en mouvement.
- **Repères de contrôle** : quelques points connus (bornes géodésiques, marques peintes de dimensions connues, points levés au GNSS) visibles dans les photos permettent de caler et de contrôler les mesures.

### Publication

- **Conserver les métadonnées précises** à l'envoi sur Panoramax : cap au centième, précision GPS, inclinaison. Certains logiciels de traitement arrondissent ou suppriment ces champs.
- **Envoyer les images en pleine résolution.**
- **Décrire la séquence** : modèle de caméra, hauteur de montage, type de support et de GNSS. Ces informations servent directement au calage.

---

## 7. Annexe : paramètres du code

| Paramètre | Valeur | Rôle | Fichier |
|---|---|---|---|
| `PITCH_ERROR` | 0,5° | erreur d'élévation supposée pour l'incertitude des mesures au sol | `ground.py` |
| `MAX_DISTANCE` | 60 m | portée maximale d'un point au sol | `ground.py` |
| `DEFAULT_CAMERA_HEIGHT` | 1,90 m | hauteur de caméra par défaut | `measure.py` |
| `LINE_FROM`, `LINE_TO` | −30 m, 90 m | fenêtre du profil de terrain le long de la visée | `terrain.py` |
| `MAX_COARSE_SLOPE` | 12 % | pente maximale admise des sources de secours | `terrain.py` |
| `HEADING_ERROR` | 1° | erreur de visée supposée (triangulation) | `triangulation.py` |
| `SURVEY_HEADING_ERROR`, `SURVEY_ACCURACY` | 0,5°, 2 m | matériel de relevé : erreur de visée et précision GPS maximale | `triangulation.py` |
| `GPS_ACCURACY` | 3 m | précision GPS supposée si la photo ne l'indique pas | `triangulation.py` |
| `GPS_WARNING` | 5 m | seuil d'avertissement de précision GPS | `triangulation.py` |
| `MIN_ANGLE`, `MAX_DISTANCE` | 3°, 500 m | croisement minimal et distance maximale d'un point triangulé | `triangulation.py` |
| `MAX_TILT`, `MIN_SPAN`, `MAX_LEAN` | 10°, 3°, 30° | contrôles du calage d'inclinaison | `calibration.py` |
| `AIM_ERROR`, `MAP_ERROR` | 0,1°, 0,5 m | précision d'un clic dans la photo et d'un repère sur la carte | `calibration.py` |
| `SURVEY_PRIOR`, `PRIOR` | 0,5°, 3° | précision a priori du cap (relevé, autres appareils) | `calibration.py` |
| `REACH` | 300 m | portée du recalage autour des photos utilisées | `calibration.py` |
| `OUTLIER` | 4 | seuil de refus d'un repère incohérent (écarts-types) | `calibration.py` |
| `CAMERA_MIN`, `CAMERA_MAX` | 0,3 m, 6 m | plage de recherche de la hauteur de caméra | `calibration.py` |
