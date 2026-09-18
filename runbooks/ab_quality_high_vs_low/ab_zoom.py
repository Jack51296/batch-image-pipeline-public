# -*- coding: utf-8 -*-
"""A/B 细节放大图（SOURCE | LOW | HIGH 三列，每列 500px）。
ab_zoom_face.png  : 脸部区域（近原生 1:1）
ab_zoom_detail.png: 眼部 + 手/杯子 两行，nearest 放大约 3.7x，看像素颗粒
"""
from pathlib import Path

import cv2
import numpy as np

W = Path('/mnt/kfs/alice/work')
SRC = Path('/mnt/kfs/alice/source/风格/20260904-非写实风格化-4148-qt-2072-s35-2076/4分-2072张/二次元平涂-61张/二次元平涂-4-横屏-1.png')
NAME = '二次元平涂-4-横屏-1'
FILES = [
    ('SOURCE', SRC),
    ('LOW', W / 'style0911' / '20260904-非写实风格化-4148-qt-2072-s35-2076-qt-round1' / (NAME + '-r1.png')),
    ('HIGH', W / 'style0911_hqtest' / '20260904-非写实风格化-4148-qt-2072-s35-2076-qt-hq-test' / (NAME + '-r1.png')),
]
OUT = W / 'style0911_hqtest' / 'compare'
PANEL_W = 500
FACE_ROWS = [('face', (0.55, 0.13, 0.20, 0.30))]
DETAIL_ROWS = [('eye', (0.585, 0.19, 0.08, 0.10)), ('mug/hand', (0.575, 0.40, 0.08, 0.10))]


def crop_rel(im, r):
    h, w = im.shape[:2]
    rx, ry, rw, rh = r
    return im[int(ry * h):int((ry + rh) * h), int(rx * w):int((rx + rw) * w)]


def fit_w(im, width, nearest):
    h, w = im.shape[:2]
    s = width / float(w)
    interp = (cv2.INTER_NEAREST if nearest else cv2.INTER_LINEAR) if s > 1 else cv2.INTER_AREA
    return cv2.resize(im, (width, max(1, int(round(h * s)))), interpolation=interp), s


def label(im, text):
    im = im.copy()
    cv2.rectangle(im, (0, 0), (im.shape[1], 24), (0, 0, 0), -1)
    cv2.putText(im, text, (5, 17), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)
    return im


def build(imgs, rows_spec):
    rows = []
    for rname, r in rows_spec:
        panels = []
        for k, im in imgs:
            c = crop_rel(im, r)
            p, s = fit_w(c, PANEL_W, k != 'SOURCE')
            panels.append(label(p, '%s %s  (%dx%d native, x%.1f)' % (k, rname, c.shape[1], c.shape[0], s)))
        h = min(p.shape[0] for p in panels)
        rows.append(np.hstack([p[:h] for p in panels]))
    return np.vstack(rows)


imgs = [(k, cv2.imread(str(p), cv2.IMREAD_COLOR)) for k, p in FILES]
assert all(im is not None for _, im in imgs)
for name, spec in (('ab_zoom_face.png', FACE_ROWS), ('ab_zoom_detail.png', DETAIL_ROWS)):
    g = build(imgs, spec)
    cv2.imwrite(str(OUT / name), g)
    print('wrote', name, g.shape[1], 'x', g.shape[0])
