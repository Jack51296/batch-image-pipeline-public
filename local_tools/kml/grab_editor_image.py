# -*- coding: utf-8 -*-
"""用 QuickOpen 打开 KML 工作区里的一张图，整页截图后自动裁出图片区域（按与编辑器背景色的差异求外接框）。
用法: python grab_editor_image.py <文件名关键字> <输出png>
"""
import sys, os, time
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kml_term import Kml
import numpy as np
from PIL import Image

name, out = sys.argv[1], sys.argv[2]
k = Kml()
k.escape(2)
k.key("P", "KeyP", 80, modifiers=2)
time.sleep(1.0)
k.insert(name)
time.sleep(1.5)
rows = k.c.evaluate(
    "Array.from(document.querySelectorAll('.quick-input-widget .monaco-list-row'))"
    ".map(r=>r.textContent.trim()).slice(0,3)")
print("候选:", rows)
k.enter()
time.sleep(3.5)
# 编辑器区域（排除侧栏/面板/状态栏）
rect = k.c.evaluate(
    "(()=>{const e=document.querySelector('.editor-group-container .editor-container')||document.querySelector('.part.editor');"
    "const r=e.getBoundingClientRect();return JSON.stringify({x:r.x,y:r.y,w:r.width,h:r.height});})()")
import json
r = json.loads(rect)
full = os.path.join(os.path.dirname(out), "_full_screen.png")
k.screenshot(full)
im = np.asarray(Image.open(full).convert("RGB"))
x0, y0, x1, y1 = int(r["x"]) + 2, int(r["y"]) + 2, int(r["x"] + r["w"]) - 2, int(r["y"] + r["h"]) - 2
sub = im[y0:y1, x0:x1].astype(int)
# 背景色取编辑器区域四角的众数
corners = np.array([sub[5, 5], sub[5, -5], sub[-5, 5], sub[-5, -5]])
bg = np.median(corners, axis=0)
mask = np.abs(sub - bg).sum(axis=2) > 80  # 阈值高于水印文字的灰度差，低于黑色标签条(90)
ys, xs = np.where(mask)
if len(xs) == 0:
    print("no image found; editor rect", r)
    sys.exit(1)
bx0, bx1, by0, by1 = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1
crop = im[y0 + by0:y0 + by1, x0 + bx0:x0 + bx1]
Image.fromarray(crop).save(out)
print("editor rect:", r, "bg:", bg.tolist())
print("image bbox in page:", x0 + bx0, y0 + by0, "size:", bx1 - bx0, "x", by1 - by0, "->", out)
