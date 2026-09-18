# -*- coding: utf-8 -*-
"""本地功能测试：像素风规则（检测 + 插值选择 + 与旧版逐字节一致）。"""
import importlib.util
import os
import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np

NEW_DIR = Path(sys.argv[1])          # 打过补丁的目录
OLD_DIR = Path(sys.argv[2])          # 原版目录


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m  # dataclass + from __future__ annotations 需要模块已注册
    spec.loader.exec_module(m)
    return m


new_core = load("new_core", NEW_DIR / "align_core.py")
old_core = load("old_core", OLD_DIR / "align_core.py")
sys.path.insert(0, str(NEW_DIR))
new_batch = load("new_batch", NEW_DIR / "align_batch.py")

# ---------- 1. 检测规则
rule = new_batch.load_pixel_rule()
assert rule["mode"] == "auto", rule
PX_PROMPT = "以输入原图为唯一底图和最高优先级参考，只做像素画法置换，不改动任何内容与形体"
cases = [
    ({"标签": "二次元平涂", "prompt": PX_PROMPT}, "", "prompt~像素画"),
    ({"标签": "像素", "prompt": "x"}, "", "标签=像素"),
    ({"标签": "Pixel-Art", "prompt": "x"}, "", "标签=Pixel-Art"),
    ({"标签": "逆光", "prompt": "把这张图里的特殊光效去掉，改成普通自然的日常光照。只调整光线，不改变图片尺寸"}, "", ""),
    ({"标签": "逆光", "prompt": "保持像素尺寸与像素级细节不变"}, "", ""),
    ({"标签": "二次元平涂", "prompt": "转成 8-bit 游戏画面"}, "", "prompt~8-bit"),
    ({"标签": "", "prompt": ""}, "像素风批次", "style=像素风批次"),
]
for row, style, want in cases:
    got = new_batch.detect_pixel_style(row, style, rule)
    assert got == want, (row, style, got, want)
assert new_batch.detect_pixel_style({"标签": "逆光", "prompt": "x"}, "", dict(rule, mode="on")).startswith("forced")
assert new_batch.detect_pixel_style({"标签": "像素", "prompt": PX_PROMPT}, "", dict(rule, mode="off")) == ""
print("detect_pixel_style: OK (%d cases)" % (len(cases) + 2))

# ---------- 2. 插值选择
pi = new_core._pick_interp
assert pi(100, 100, 300, 300, cv2.INTER_NEAREST) == cv2.INTER_NEAREST
assert pi(300, 300, 100, 100, cv2.INTER_NEAREST) == cv2.INTER_LINEAR   # 缩小仍双线性
assert pi(100, 100, 300, 300, cv2.INTER_LINEAR) == cv2.INTER_LINEAR
print("_pick_interp: OK")

# ---------- 3. 合成图端到端：passthrough（近单位阵）与 remap（裁切）两条路径
rng = np.random.default_rng(0)
Oh, Ow = 500, 800
orig = cv2.GaussianBlur(rng.integers(0, 255, (Oh, Ow, 3), dtype=np.uint8), (0, 0), 3)
for _ in range(60):  # 加些硬边形状，保证 ORB 有角点
    x, y = int(rng.integers(0, Ow - 60)), int(rng.integers(0, Oh - 60))
    cv2.rectangle(orig, (x, y), (x + int(rng.integers(15, 60)), y + int(rng.integers(15, 60))),
                  tuple(int(c) for c in rng.integers(0, 255, 3)), -1)


def pixelize(img, w, h):
    small = cv2.resize(img, (w, h), interpolation=cv2.INTER_AREA)
    return (small // 32) * 32 + 16  # 量化成少量颜色，模拟像素画


def n_colors(img):
    return len(np.unique(img.reshape(-1, 3), axis=0))


tmp = Path(tempfile.mkdtemp())
po = tmp / "orig.png"
cv2.imwrite(str(po), orig)

# 3a. 全幅、缩小 4 倍 → 应走 passthrough（近单位阵）放大
ai_full = pixelize(orig, Ow // 4, Oh // 4)
pa = tmp / "ai_full.png"
cv2.imwrite(str(pa), ai_full)
# 3b. 裁掉四周 8% 再缩小 → 相似变换含缩放/平移 → remap 路径
m = int(Ow * 0.08), int(Oh * 0.08)
ai_crop = pixelize(orig[m[1]:Oh - m[1], m[0]:Ow - m[0]], (Ow - 2 * m[0]) // 4, (Oh - 2 * m[1]) // 4)
pc = tmp / "ai_crop.png"
cv2.imwrite(str(pc), ai_crop)

for label, ai_path, ai_img in (("passthrough", pa, ai_full), ("remap", pc, ai_crop)):
    r_old = old_core.align_pair_paths(str(po), str(ai_path))
    r_def = new_core.align_pair_paths(str(po), str(ai_path))
    r_lin = new_core.align_pair_paths(str(po), str(ai_path), upscale_interp=cv2.INTER_LINEAR)
    r_nn = new_core.align_pair_paths(str(po), str(ai_path), upscale_interp=cv2.INTER_NEAREST)
    assert r_old.category == r_def.category == r_lin.category == r_nn.category, (
        label, r_old.category, r_def.category, r_lin.category, r_nn.category)
    assert r_old.category in ("ok", "crop"), (label, r_old.category, r_old.reason)
    assert np.array_equal(r_old.ai_aligned, r_def.ai_aligned), label + ": 默认行为与旧版不一致"
    assert np.array_equal(r_old.ai_aligned, r_lin.ai_aligned), label + ": 显式 LINEAR 与旧版不一致"
    assert r_nn.ai_aligned.shape == r_lin.ai_aligned.shape, label
    c_ai, c_lin, c_nn = n_colors(ai_img), n_colors(r_lin.ai_aligned), n_colors(r_nn.ai_aligned)
    # 最近邻不产生新颜色（黑边像素除外），双线性会产生大量过渡色
    assert c_nn <= c_ai + 1, (label, c_nn, c_ai)
    assert c_lin > 3 * c_nn, (label, c_lin, c_nn)
    assert not np.array_equal(r_nn.ai_aligned, r_lin.ai_aligned), label
    print(f"{label:11s}: category={r_nn.category:5s} out={r_nn.ai_aligned.shape[1]}x{r_nn.ai_aligned.shape[0]} "
          f"colors ai={c_ai} nearest={c_nn} linear={c_lin}  reason={r_nn.reason[:40]}")

# 3c. AI 比原图大（缩小场景）：像素模式也应保持双线性 → 与旧版一致
big = tmp / "ai_big.png"
cv2.imwrite(str(big), cv2.resize(orig, (Ow * 2, Oh * 2), interpolation=cv2.INTER_CUBIC))
r_old = old_core.align_pair_paths(str(po), str(big))
r_nn = new_core.align_pair_paths(str(po), str(big), upscale_interp=cv2.INTER_NEAREST)
assert r_old.category == r_nn.category and np.array_equal(r_old.ai_aligned, r_nn.ai_aligned), "缩小场景应与旧版一致"
print(f"downscale  : category={r_nn.category} 与旧版逐字节一致 (像素模式下缩小不启用最近邻)")
print("ALL OK")
