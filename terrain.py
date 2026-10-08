# -*- coding: utf-8 -*-
"""Profils d'altitude du terrain le long d'une visée (pour ground.intersect).

Sources, dans l'ordre :
1. une couche raster MNT du projet, choisie dans le panneau (rapide, hors ligne) ;
2. le service d'altimétrie de la Géoplateforme IGN (RGE ALTI, France entière) ;
3. à défaut, un sol plat et horizontal.
Un profil est une liste [(distance horizontale en m, altitude)], depuis la photo.
"""

from urllib.parse import urlencode

from qgis.core import QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsPointXY, QgsProject, QgsRasterLayer

from . import api, ground, triangulation

WGS84 = QgsCoordinateReferenceSystem("EPSG:4326")
STEP = 0.25  # pas d'échantillonnage du profil (m)
SAMPLES = int(ground.MAX_DISTANCE / STEP) + 1  # 241 : maximum accepté en une requête par le service IGN
IGN_URL = "https://data.geopf.fr/altimetrie/1.0/calcul/alti/rest/elevationLine.json"
IGN_RESOURCE = "ign_rge_alti_wld"
IGN_LABEL = "RGE ALTI (IGN)"
NODATA_BELOW = -9999.0  # le service renvoie -99999 hors de sa couverture


class TerrainProvider:
    def __init__(self):
        self.layer = None  # QgsRasterLayer choisi, ou None : service IGN

    def set_layer(self, layer):
        self.layer = layer if isinstance(layer, QgsRasterLayer) and layer.isValid() else None

    def profile(self, lon, lat, yaw, callback):
        """Profil le long de la visée. callback(profil, libellé de la source, avertissement | None)."""
        warning = None
        if self.layer is not None:
            prof = self._raster_profile(lon, lat, yaw)
            if prof is not None:
                callback(prof, "MNT « {} »".format(self.layer.name()), None)
                return
            warning = "La couche « {} » ne couvre pas la visée (ou n'est pas interrogeable)".format(self.layer.name())
        self._ign_profile(lon, lat, yaw, callback, warning)

    # ------------------------------------------------------------------
    def _raster_profile(self, lon, lat, yaw):
        layer = self.layer
        provider = layer.dataProvider()
        ct = QgsCoordinateTransform(WGS84, layer.crs(), QgsProject.instance())
        prof = []
        for i in range(SAMPLES):
            s = i * STEP
            plon, plat = triangulation.offset(lon, lat, yaw, s)
            try:
                value, ok = provider.sample(ct.transform(QgsPointXY(plon, plat)), 1)
            except Exception:  # hors du domaine du SCR, fournisseur sans échantillonnage
                return None
            if not ok or value != value:  # nodata ou NaN
                if i == 0:
                    return None  # pas d'altitude sous la photo : profil inutilisable
                break  # bord du MNT : on garde le début du profil
            prof.append((s, float(value)))
        return prof if len(prof) >= 2 else None

    def _ign_profile(self, lon, lat, yaw, callback, warning):
        end = triangulation.offset(lon, lat, yaw, ground.MAX_DISTANCE)
        params = {
            "lon": "{:.8f}|{:.8f}".format(lon, end[0]),
            "lat": "{:.8f}|{:.8f}".format(lat, end[1]),
            "resource": IGN_RESOURCE,
            "sampling": SAMPLES,
            "profile_mode": "simple",
        }
        url = IGN_URL + "?" + urlencode(params, safe="|")

        def done(data, error):
            prof = None
            if not error and isinstance(data, dict):
                prof = self._parse_ign(data, lon, lat)
            if prof is not None:
                callback(prof, IGN_LABEL, warning)
                return
            reason = "service d'altimétrie IGN indisponible ({})".format(error) if error else \
                "pas d'altitude IGN à cet endroit (hors de France ?)"
            callback(ground.flat_profile(), ground.FLAT, "{}{}".format(warning + " ; " if warning else "", reason))

        api.fetch_json(url, done)

    @staticmethod
    def _parse_ign(data, lon, lat):
        prof = []
        for pt in data.get("elevations") or []:
            try:
                z = float(pt["z"])
                x, y = triangulation._local(float(pt["lon"]), float(pt["lat"]), lon, lat)
            except (KeyError, TypeError, ValueError):
                return None
            if z <= NODATA_BELOW:
                if not prof:
                    return None
                break
            prof.append(((x * x + y * y) ** 0.5, z))
        prof.sort()
        return prof if len(prof) >= 2 else None
