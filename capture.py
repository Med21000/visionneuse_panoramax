# -*- coding: utf-8 -*-
"""Capture « virtuelle » Full HD de la vue affichée.

Plutôt qu'une capture d'écran (limitée à la taille du panneau), on recalcule la
vue à partir de la photo originale en haute définition, avec le même cadrage
que la visionneuse : cap, inclinaison et champ de vision.

- Photo 360° (équirectangulaire) : projection perspective de la sphère.
- Photo classique : reprojection de l'image plane selon la vue demandée.
"""

import math

import numpy as np
from qgis.PyQt.QtCore import QRectF, Qt
from qgis.PyQt.QtGui import QColor, QFont, QImage, QPainter

OUT_W, OUT_H = 1920, 1080
BACKGROUND = (40, 40, 40)


def qimage_to_array(img):
    img = img.convertToFormat(QImage.Format.Format_RGB888)
    w, h, bpl = img.width(), img.height(), img.bytesPerLine()
    ptr = img.constBits()
    ptr.setsize(h * bpl)
    arr = np.frombuffer(ptr, np.uint8).reshape(h, bpl)[:, :w * 3].reshape(h, w, 3)
    return arr.copy()


def array_to_qimage(arr):
    arr = np.ascontiguousarray(arr, dtype=np.uint8)
    h, w, _ = arr.shape
    return QImage(arr.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()


def _view_rays(heading, pitch, vfov, width, height):
    """Directions (unitaires) de chaque pixel de la vue, en repère monde.

    Repère : x = est, y = haut, z = nord. heading en degrés (0 = nord),
    pitch en degrés (positif = vers le haut), vfov = champ vertical en degrés.
    """
    f = (height / 2.0) / math.tan(math.radians(vfov) / 2.0)
    u = (np.arange(width, dtype=np.float64) + 0.5 - width / 2.0) / f
    v = (height / 2.0 - np.arange(height, dtype=np.float64) - 0.5) / f
    x, y = np.meshgrid(u, v)
    z = np.ones_like(x)
    # inclinaison (rotation autour de l'axe x)
    p = math.radians(pitch)
    y2 = y * math.cos(p) + z * math.sin(p)
    z2 = -y * math.sin(p) + z * math.cos(p)
    # cap (rotation autour de l'axe vertical, sens horaire depuis le nord)
    h = math.radians(heading)
    x3 = x * math.cos(h) + z2 * math.sin(h)
    z3 = -x * math.sin(h) + z2 * math.cos(h)
    n = np.sqrt(x3 * x3 + y2 * y2 + z3 * z3)
    return x3 / n, y2 / n, z3 / n


def _bilinear(src, xs, ys, valid=None):
    h, w, _ = src.shape
    x0 = np.floor(xs).astype(np.int64)
    y0 = np.floor(ys).astype(np.int64)
    fx = (xs - x0)[..., None]
    fy = (ys - y0)[..., None]
    x1, y1 = x0 + 1, y0 + 1
    if valid is None:  # 360° : bouclage horizontal
        x0 %= w
        x1 %= w
    else:
        x0 = np.clip(x0, 0, w - 1)
        x1 = np.clip(x1, 0, w - 1)
    y0 = np.clip(y0, 0, h - 1)
    y1 = np.clip(y1, 0, h - 1)
    s = src.astype(np.float32)
    out = (s[y0, x0] * (1 - fx) * (1 - fy) + s[y0, x1] * fx * (1 - fy)
           + s[y1, x0] * (1 - fx) * fy + s[y1, x1] * fx * fy)
    if valid is not None:
        out[~valid] = BACKGROUND
    return out


def render_view(src, azimuth, is360, heading, pitch, vfov, photo_hfov=None,
                width=OUT_W, height=OUT_H):
    """Calcule la vue (tableau numpy RGB) à partir de la photo source."""
    dx, dy, dz = _view_rays(heading, pitch, vfov, width, height)
    sh, sw, _ = src.shape
    a = math.radians(azimuth or 0.0)
    # Directions exprimées dans le repère de la photo (centre de l'image = azimut)
    px = dx * math.cos(a) - dz * math.sin(a)
    pz = dx * math.sin(a) + dz * math.cos(a)
    py = dy
    if is360:
        lon = np.arctan2(px, pz)  # -pi..pi, 0 = centre de l'image
        lat = np.arcsin(np.clip(py, -1, 1))
        xs = (lon / (2 * math.pi) + 0.5) * sw - 0.5
        ys = (0.5 - lat / math.pi) * sh - 0.5
        return _bilinear(src, xs, ys)
    # Photo classique : plan image perpendiculaire à l'axe de prise de vue
    hfov = photo_hfov or 70.0
    f = (sw / 2.0) / math.tan(math.radians(hfov) / 2.0)
    with np.errstate(divide="ignore", invalid="ignore"):
        xs = sw / 2.0 + f * px / pz - 0.5
        ys = sh / 2.0 - f * py / pz - 0.5
    valid = (pz > 1e-6) & (xs >= 0) & (xs <= sw - 1) & (ys >= 0) & (ys <= sh - 1)
    xs = np.where(valid, xs, 0)
    ys = np.where(valid, ys, 0)
    return _bilinear(src, xs, ys, valid)


def add_caption(img, text):
    """Ajoute un cartouche discret (attribution) en bas à droite."""
    if not text:
        return img
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    font = QFont()
    font.setPixelSize(max(14, img.height() // 54))
    p.setFont(font)
    metrics = p.fontMetrics()
    pad = font.pixelSize() // 2
    tw = metrics.horizontalAdvance(text) + 2 * pad
    th = metrics.height() + pad
    rect = QRectF(img.width() - tw - pad, img.height() - th - pad, tw, th)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(0, 0, 0, 140))
    p.drawRoundedRect(rect, pad / 2, pad / 2)
    p.setPen(QColor(255, 255, 255))
    p.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
    p.end()
    return img


def caption_for(item, heading):
    """Texte d'attribution : auteur, date, cap, licence (obligation des licences Panoramax)."""
    props = item.get("properties", {}) or {}
    providers = props.get("providers") or item.get("providers") or []
    names = [p.get("name") for p in providers if isinstance(p, dict) and p.get("name")]
    author = names[0] if names else None
    date = (props.get("datetime") or "")[:10]
    lic = None
    for link in item.get("links", []) or []:
        if link.get("rel") == "license":
            lic = link.get("title") or _license_label(link.get("href", ""))
            break
    if not lic:
        lic = props.get("license") or item.get("license")
    parts = ["Panoramax"]
    if author:
        parts.append("© " + author)
    if date:
        parts.append(date)
    if heading is not None:
        parts.append("cap {:.0f}°".format(heading % 360))
    if lic:
        parts.append(str(lic))
    return "  ·  ".join(parts)


def _license_label(href):
    h = href.lower()
    if "by-sa/4.0" in h:
        return "CC BY-SA 4.0"
    if "by/4.0" in h:
        return "CC BY 4.0"
    if "etalab" in h or "licence-ouverte" in h:
        return "Licence Ouverte 2.0"
    return href
