# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Cédric COCHART
# SPDX-License-Identifier: GPL-2.0-or-later
"""Capture « virtuelle » Full HD de la vue affichée : conversion des images Qt et
cartouche d'attribution ; le calcul de la vue est dans core/capture.py."""

import numpy as np
from qgis.PyQt.QtCore import QRectF, Qt
from qgis.PyQt.QtGui import QColor, QFont, QImage, QPainter

from .core.capture import _tilted_rays, caption_for, render_view

REEXPORTED = (render_view, caption_for, _tilted_rays)  # utilisées par le panneau et les tests


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
