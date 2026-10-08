# -*- coding: utf-8 -*-
"""Décodeur minimal de tuiles vectorielles Mapbox (MVT), en Python pur.

Le décodeur de QGIS (QgsVectorTileMVTDecoder) n'est pas exposé à Python :
on lit donc nous-mêmes le protobuf de la tuile. Seul ce qui est utile ici
est géré : couches, attributs, points et lignes (les polygones sont lus
comme des anneaux fermés).

Coordonnées renvoyées en Web Mercator (EPSG:3857).
"""

import struct

WEB_MERCATOR_HALF = 20037508.342789244

GEOM_POINT, GEOM_LINE, GEOM_POLYGON = 1, 2, 3


# --------------------------------------------------------------------------
# Lecture protobuf
# --------------------------------------------------------------------------
def _varint(buf, pos):
    result = shift = 0
    while True:
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, pos
        shift += 7


def _fields(buf):
    """Itère sur (numéro de champ, type, valeur) d'un message protobuf."""
    pos, end = 0, len(buf)
    while pos < end:
        key, pos = _varint(buf, pos)
        num, wtype = key >> 3, key & 7
        if wtype == 0:  # varint
            val, pos = _varint(buf, pos)
        elif wtype == 1:  # 64 bits
            val = buf[pos:pos + 8]
            pos += 8
        elif wtype == 2:  # longueur + octets
            ln, pos = _varint(buf, pos)
            val = buf[pos:pos + ln]
            pos += ln
        elif wtype == 5:  # 32 bits
            val = buf[pos:pos + 4]
            pos += 4
        else:
            raise ValueError("Type protobuf non géré : {}".format(wtype))
        yield num, wtype, val


def _packed(buf):
    out, pos = [], 0
    while pos < len(buf):
        v, pos = _varint(buf, pos)
        out.append(v)
    return out


def _zigzag(n):
    return (n >> 1) ^ -(n & 1)


def _value(buf):
    for num, wtype, val in _fields(buf):
        if num == 1:
            return bytes(val).decode("utf-8", "replace")
        if num == 2:
            return struct.unpack("<f", val)[0]
        if num == 3:
            return struct.unpack("<d", val)[0]
        if num in (4, 5):
            return val if num == 5 or val < (1 << 63) else val - (1 << 64)
        if num == 6:
            return _zigzag(val)
        if num == 7:
            return bool(val)
    return None


# --------------------------------------------------------------------------
# Géométrie
# --------------------------------------------------------------------------
def _parts(commands):
    """Décode la géométrie MVT en liste de parties (liste de (x, y) entiers)."""
    parts, current = [], None
    x = y = 0
    i, n = 0, len(commands)
    while i < n:
        cmd, count = commands[i] & 7, commands[i] >> 3
        i += 1
        if cmd == 1:  # MoveTo
            for _ in range(count):
                x += _zigzag(commands[i])
                y += _zigzag(commands[i + 1])
                i += 2
                current = [(x, y)]
                parts.append(current)
        elif cmd == 2:  # LineTo
            for _ in range(count):
                x += _zigzag(commands[i])
                y += _zigzag(commands[i + 1])
                i += 2
                if current is not None:
                    current.append((x, y))
        elif cmd == 7:  # ClosePath
            if current:
                current.append(current[0])
        else:
            break
    return parts


def decode(data, z, tx, ty, layer_names=None):
    """Décode une tuile.

    Retourne {nom_couche: [(type_geom, parties_en_3857, attributs), ...]}.
    Les parties sont des listes de (x, y) en mètres Web Mercator.
    """
    tile_size = 2 * WEB_MERCATOR_HALF / (2 ** z)
    x0 = -WEB_MERCATOR_HALF + tx * tile_size
    y0 = WEB_MERCATOR_HALF - ty * tile_size

    result = {}
    for num, _, layer_buf in _fields(memoryview(data)):
        if num != 3:
            continue
        name, keys, values, raw_features, extent = None, [], [], [], 4096
        for lnum, _, lval in _fields(layer_buf):
            if lnum == 1:
                name = bytes(lval).decode("utf-8", "replace")
            elif lnum == 2:
                raw_features.append(lval)
            elif lnum == 3:
                keys.append(bytes(lval).decode("utf-8", "replace"))
            elif lnum == 4:
                values.append(_value(lval))
            elif lnum == 5:
                extent = lval
        if layer_names is not None and name not in layer_names:
            continue
        scale = tile_size / float(extent)

        features = result.setdefault(name, [])
        for fbuf in raw_features:
            tags, gtype, geom = [], 0, []
            for fnum, _, fval in _fields(fbuf):
                if fnum == 2:
                    tags = _packed(fval)
                elif fnum == 3:
                    gtype = fval
                elif fnum == 4:
                    geom = _packed(fval)
            attrs = {}
            for k in range(0, len(tags) - 1, 2):
                if tags[k] < len(keys) and tags[k + 1] < len(values):
                    attrs[keys[tags[k]]] = values[tags[k + 1]]
            parts = [[(x0 + px * scale, y0 - py * scale) for px, py in part]
                     for part in _parts(geom)]
            features.append((gtype, parts, attrs))
    return result
