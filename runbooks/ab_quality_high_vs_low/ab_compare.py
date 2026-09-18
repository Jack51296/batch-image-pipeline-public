# -*- coding: utf-8 -*-
"""quality=low vs quality=high A/B：客观指标 + 拼图（原图 | low | high）。"""
import json
from pathlib import Path

import cv2
import numpy as np

W = Path('/mnt/kfs/alice/work')
SRC_DIR = Path('/mnt/kfs/alice/source/风格/20260904-非写实风格化-4148-qt-2072-s35-2076/4分-2072张/二次元平涂-61张')
NAME = '二次元平涂-4-横屏-1'
FILES = {
    'SOURCE': SRC_DIR / (NAME + '.png'),
    'LOW': W / 'style0911' / '20260904-非写实风格化-4148-qt-2072-s35-2076-qt-round1' / (NAME + '-r1.png'),
    'HIGH': W / 'style0911_hqtest' / '20260904-非写实风格化-4148-qt-2072-s35-2076-qt-hq-test' / (NAME + '-r1.png'),
}
OUT = W / 'style0911_hqtest' / 'compare'
OUT.mkdir(parents=True, exist_ok=True)

# 主角脸部/上半身所在区域（相对坐标），用于放大对比像素颗粒
FACE = (0.55, 0.12, 0.25, 0.42)  # rx, ry, rw, rh


def stats(p):
    im = cv2.imread(str(p), cv2.IMREAD_COLOR)
    assert im is not None, p
    h, w = im.shape[:2]
    g = cv2.cvtColor(im, cv2.COLOR_BGR2GRAY)
    lap = cv2.Laplacian(g, cv2.CV_64F).var()
    flat = im.reshape(-1, 3)
    if flat.shape[0] > 4_000_000:
        flat = flat[::4]
    uniq = len(np.unique(flat, axis=0))
    return {'w': w, 'h': h, 'bytes': p.stat().st_size, 'lap_var': round(float(lap), 1), 'uniq_colors_sampled': int(uniq)}, im


def crop_rel(im, r):
    h, w = im.shape[:2]
    rx, ry, rw, rh = r
    return im[int(ry * h):int((ry + rh) * h), int(rx * w):int((rx + rw) * w)]


def fit_width(im, width, upscale_nearest):
    h, w = im.shape[:2]
    s = width / float(w)
    interp = (cv2.INTER_NEAREST if upscale_nearest else cv2.INTER_LINEAR) if s > 1 else cv2.INTER_AREA
    return cv2.resize(im, (width, max(1, int(round(h * s)))), interpolation=interp)


def label(im, text):
    im = im.copy()
    cv2.rectangle(im, (0, 0), (im.shape[1], 34), (0, 0, 0), -1)
    cv2.putText(im, text, (8, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2, cv2.LINE_AA)
    return im


def hstack_same_h(ims):
    h = min(i.shape[0] for i in ims)
    return np.hstack([i[:h] for i in ims])


report = {}
imgs = {}
for k, p in FILES.items():
    if not p.exists():
        print('MISSING', k, p)
        continue
    report[k], imgs[k] = stats(p)
    print('%-6s %5dx%-5d %8.2f MB  lap_var=%8.1f  uniq_colors=%d' % (
        k, report[k]['w'], report[k]['h'], report[k]['bytes'] / 1e6, report[k]['lap_var'], report[k]['uniq_colors_sampled']))

order = [k for k in ('SOURCE', 'LOW', 'HIGH') if k in imgs]
full = hstack_same_h([label(fit_width(imgs[k], 900, False), '%s %dx%d' % (k, report[k]['w'], report[k]['h'])) for k in order])
cv2.imwrite(str(OUT / 'ab_full.png'), full)

face = hstack_same_h([label(fit_width(crop_rel(imgs[k], FACE), 640, k != 'SOURCE'), k + ' (face crop, nearest x%.1f)' % (
    640.0 / crop_rel(imgs[k], FACE).shape[1])) for k in order])
cv2.imwrite(str(OUT / 'ab_face.png'), face)

(OUT / 'ab_stats.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print('wrote', OUT / 'ab_full.png', full.shape, '|', OUT / 'ab_face.png', face.shape)
