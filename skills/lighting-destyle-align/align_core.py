# -*- coding: utf-8 -*-
"""几何对齐核心：ORB + 相似变换（缩放/旋转/平移），禁止补边。

放大插值可选：upscale_interp 默认 cv2.INTER_LINEAR（旧行为）；像素风批次由 align_batch.py
传入 cv2.INTER_NEAREST，白描/线稿批次传入 cv2.INTER_CUBIC，且仅在「AI 图需要放大」时生效，缩小仍用双线性。
特征域可选：feature_mode="gray"（旧行为）| "edge"（Sobel 梯度图）| "auto"（两路取 inliers 多者，白描规则）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

import cv2
import numpy as np


@dataclass
class AlignResult:
    category: str  # ok | crop | unaligned
    reason: str
    orig_crop: Optional[np.ndarray]  # 预览用（可与 AI 同尺寸）
    orig_hires: Optional[np.ndarray]  # 导出用：最大分辨率裁切/原图，无损保存
    copy_orig_file: bool  # True=直接复制原文件，不做重编码
    ai_aligned: Optional[np.ndarray]  # BGR, export AI
    preview_orig: Optional[np.ndarray]  # 同尺寸预览原图
    cover: float
    inliers: int
    matches: int
    confidence: float
    crop_box: Optional[Tuple[int, int, int, int]]  # x,y,w,h on original
    feature_used: str = "gray"  # gray | edge：最终采用的特征域（白描规则会尝试梯度图）


def _to_gray(img: np.ndarray) -> np.ndarray:
    if img.ndim == 2:
        return img
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


def _resize_max(img: np.ndarray, max_side: int) -> Tuple[np.ndarray, float]:
    h, w = img.shape[:2]
    m = max(w, h)
    if m <= max_side:
        return img, 1.0
    s = max_side / m
    nw, nh = max(8, int(round(w * s))), max(8, int(round(h * s)))
    return cv2.resize(img, (nw, nh), interpolation=cv2.INTER_AREA), s


def _pick_interp(src_w: float, src_h: float, dst_w: int, dst_h: int, upscale_interp: int) -> int:
    """目标像素数多于源像素数（放大）时用 upscale_interp，否则保持双线性（旧行为）。"""
    if upscale_interp == cv2.INTER_LINEAR:
        return cv2.INTER_LINEAR
    if dst_w * dst_h > max(1.0, float(src_w) * float(src_h)):
        return int(upscale_interp)
    return cv2.INTER_LINEAR


def _edge_gray(gray: np.ndarray) -> np.ndarray:
    """边缘图（uint8）：Canny → 3×3 膨胀 → 5×5 高斯。白描/线稿与彩色原图在灰度上差异很大，
    但“线在哪里”一致；两边都转成边缘图后 ORB 同构，匹配点数是灰度域的 10 倍以上（实测 135 vs 3 inliers）。
    Sobel 幅值图在线稿上会出双脊，效果反而差，故用 Canny。"""
    c = cv2.Canny(cv2.GaussianBlur(gray, (3, 3), 0), 50, 150)
    c = cv2.dilate(c, np.ones((3, 3), np.uint8))
    return cv2.GaussianBlur(c, (5, 5), 0)


def _orb_similarity(ai_g: np.ndarray, orig_g: np.ndarray, max_feat: int = 2500, ratio: float = 0.75):
    """在给定特征域上跑 ORB + 相似变换，返回 (M_work, inliers, nmatch)；失败返回 (None, 0, nmatch)。"""
    k1, k2, good = _orb_matches(ai_g, orig_g, max_feat=max_feat, ratio=ratio)
    nmatch = len(good) if good else 0
    if not good or nmatch < 8:
        return None, 0, nmatch
    ai_pts = np.float32([k1[m.queryIdx].pt for m in good])
    orig_pts = np.float32([k2[m.trainIdx].pt for m in good])
    M_work, inliers = _estimate_similarity(ai_pts, orig_pts)
    if M_work is None or inliers < 8:
        return None, int(inliers or 0), nmatch
    return M_work, int(inliers), nmatch


def _orb_matches(ai_gray: np.ndarray, orig_gray: np.ndarray, max_feat: int = 2500, ratio: float = 0.75):
    orb = cv2.ORB_create(nfeatures=max_feat, scaleFactor=1.2, nlevels=8)
    k1, d1 = orb.detectAndCompute(ai_gray, None)
    k2, d2 = orb.detectAndCompute(orig_gray, None)
    if d1 is None or d2 is None or len(k1) < 8 or len(k2) < 8:
        return None, None, []
    bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False)
    raw = bf.knnMatch(d1, d2, k=2)
    good = []
    for pair in raw:
        if len(pair) < 2:
            continue
        m, n = pair
        if m.distance < ratio * n.distance:
            good.append(m)
    if len(good) < 8:
        return k1, k2, []
    return k1, k2, good


def _estimate_similarity(ai_pts, orig_pts):
    """AI 坐标 → 原图坐标，2x3 相似变换。"""
    M, inliers = cv2.estimateAffinePartial2D(
        ai_pts.reshape(-1, 1, 2),
        orig_pts.reshape(-1, 1, 2),
        method=cv2.RANSAC,
        ransacReprojThreshold=3.0,
        maxIters=4000,
        confidence=0.995,
        refineIters=10,
    )
    if M is None or inliers is None:
        return None, 0
    return M, int(inliers.sum())


def _apply_affine(M: np.ndarray, x: float, y: float) -> Tuple[float, float]:
    a, b, tx = M[0]
    c, d, ty = M[1]
    return a * x + b * y + tx, c * x + d * y + ty


def _invert_affine(M: np.ndarray) -> Optional[np.ndarray]:
    A = M[:, :2]
    t = M[:, 2]
    det = float(A[0, 0] * A[1, 1] - A[0, 1] * A[1, 0])
    if abs(det) < 1e-12:
        return None
    invA = np.array([[A[1, 1], -A[0, 1]], [-A[1, 0], A[0, 0]]], dtype=np.float64) / det
    inv_t = -invA @ t
    out = np.zeros((2, 3), dtype=np.float64)
    out[:, :2] = invA
    out[:, 2] = inv_t
    return out


def _rect_inside_ai(R, Aw: int, Ah: int, inv, n: int = 12) -> bool:
    x, y, w, h = R
    for i in range(n + 1):
        for j in range(n + 1):
            px = x + (w - 1) * (i / n)
            py = y + (h - 1) * (j / n)
            ax, ay = _apply_affine(inv, px, py)
            if ax < 0.5 or ay < 0.5 or ax > Aw - 1.5 or ay > Ah - 1.5:
                return False
    return True


def _black_border_widths(img: np.ndarray, *, thresh: int = 2, min_frac: float = 0.92) -> Tuple[int, int, int, int]:
    """
    检测 remap 填色造成的纯黑边（BORDER_CONSTANT→近似 0）。
    阈值压得很低，避免夜景/暗水面被误裁。
    返回 (left, top, right, bottom)，单位像素。
    """
    if img is None or img.size == 0:
        return 0, 0, 0, 0
    if img.ndim == 3:
        dark = np.all(img <= thresh, axis=2)
    else:
        dark = img <= thresh
    h, w = dark.shape[:2]
    left = top = right = bottom = 0
    for x in range(w):
        if dark[:, x].mean() >= min_frac:
            left += 1
        else:
            break
    for x in range(w - 1, -1, -1):
        if dark[:, x].mean() >= min_frac:
            right += 1
        else:
            break
    for y in range(h):
        if dark[y, :].mean() >= min_frac:
            top += 1
        else:
            break
    for y in range(h - 1, -1, -1):
        if dark[y, :].mean() >= min_frac:
            bottom += 1
        else:
            break
    # 整图都黑时不要误判成四边吃光
    if left + right >= w or top + bottom >= h:
        return 0, 0, 0, 0
    return left, top, right, bottom


def _remap_ai_to_rect(
    ai_bgr: np.ndarray,
    inv: np.ndarray,
    rx: int,
    ry: int,
    rw: int,
    rh: int,
    out_w: int,
    out_h: int,
    upscale_interp: int = cv2.INTER_LINEAR,
) -> np.ndarray:
    """把 AI 按 inv 采样到原图矩形 R，输出 out_w×out_h。禁止依赖黑边补全。"""
    # 原图矩形 R 映到 AI 空间约为 rw*s × rh*s（s 为 inv 的等比缩放因子）；据此判断是否放大
    det = float(inv[0, 0] * inv[1, 1] - inv[0, 1] * inv[1, 0])
    s = float(np.sqrt(abs(det)))
    interp = _pick_interp(rw * s, rh * s, out_w, out_h, upscale_interp)
    ys = (np.arange(out_h, dtype=np.float64) + 0.5) * rh / out_h - 0.5 + ry
    xs = (np.arange(out_w, dtype=np.float64) + 0.5) * rw / out_w - 0.5 + rx
    grid_x, grid_y = np.meshgrid(xs, ys)
    a, b, tx = inv[0]
    c, d, ty = inv[1]
    map_x = (a * grid_x + b * grid_y + tx).astype(np.float32)
    map_y = (c * grid_x + d * grid_y + ty).astype(np.float32)
    return cv2.remap(
        ai_bgr,
        map_x,
        map_y,
        interpolation=interp,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(0, 0, 0),
    )


def _find_full_cover_crop(Ow: int, Oh: int, Aw: int, Ah: int, M: np.ndarray):
    """在原图上找可被 AI 完全覆盖的最大轴对齐裁剪框。"""
    inv = _invert_affine(M)
    if inv is None:
        return None

    # 将原图四角映到 AI，再映回，估计大致重叠
    corners = [(0, 0), (Ow - 1, 0), (Ow - 1, Oh - 1), (0, Oh - 1)]
    mapped = [_apply_affine(inv, x, y) for x, y in corners]
    # 原图上可行区域：从边界向内收缩搜索
    best = None
    best_area = 0
    # 多级边距搜索
    for margin_frac in [0.0, 0.02, 0.05, 0.08, 0.12, 0.18, 0.25, 0.35]:
        mx = int(Ow * margin_frac)
        my = int(Oh * margin_frac)
        if Ow - 2 * mx < 16 or Oh - 2 * my < 16:
            continue
        R = (mx, my, Ow - 2 * mx, Oh - 2 * my)
        if _rect_inside_ai(R, Aw, Ah, inv):
            area = R[2] * R[3]
            if area > best_area:
                best_area = area
                best = R
            break  # 越大越好，找到较小 margin 即可

    if best is not None:
        # 尝试再略放大（减小 margin）
        x, y, w, h = best
        for step in range(8):
            nx = max(0, x - 2)
            ny = max(0, y - 2)
            nw = min(Ow - nx, w + 4)
            nh = min(Oh - ny, h + 4)
            cand = (nx, ny, nw, nh)
            if _rect_inside_ai(cand, Aw, Ah, inv):
                best = cand
                x, y, w, h = best
            else:
                break
        return best

    # 回退：用映射点包围盒裁剪
    xs = [p[0] for p in mapped]
    ys = [p[1] for p in mapped]
    # 实际上需要原图坐标下的重叠：采样网格找有效点
    ys_ok, xs_ok = [], []
    step = max(4, min(Ow, Oh) // 40)
    for yy in range(0, Oh, step):
        for xx in range(0, Ow, step):
            ax, ay = _apply_affine(inv, xx, yy)
            if 0.5 <= ax <= Aw - 1.5 and 0.5 <= ay <= Ah - 1.5:
                xs_ok.append(xx)
                ys_ok.append(yy)
    if len(xs_ok) < 20:
        return None
    x0, x1 = min(xs_ok), max(xs_ok)
    y0, y1 = min(ys_ok), max(ys_ok)
    R = (x0, y0, max(1, x1 - x0), max(1, y1 - y0))
    # 再向内缩直到完全在内
    for shrink in range(0, 40, 2):
        rx = x0 + shrink
        ry = y0 + shrink
        rw = (x1 - x0) - 2 * shrink
        rh = (y1 - y0) - 2 * shrink
        if rw < 16 or rh < 16:
            break
        cand = (rx, ry, rw, rh)
        if _rect_inside_ai(cand, Aw, Ah, inv):
            return cand
    return None


def _output_size(tw: int, th: int, aw: int, ah: int, *, allow_downscale: bool) -> Tuple[int, int]:
    """
    allow_downscale=False：保持裁剪/原图目标分辨率（同比例成对时不压分辨率）。
    allow_downscale=True：仍不超过 AI 原生像素（避免大幅放大）。
    """
    if not allow_downscale:
        return max(1, int(tw)), max(1, int(th))
    s = min(1.0, aw / max(1, tw), ah / max(1, th))
    return max(1, int(round(tw * s))), max(1, int(round(th * s)))


def _same_aspect(w1: int, h1: int, w2: int, h2: int, tol: float = 0.02) -> bool:
    if min(h1, h2) <= 0:
        return False
    return abs((w1 / h1) - (w2 / h2)) <= tol


def _is_near_identity(M: np.ndarray, *, px_tol: float = 2.0, scale_tol: float = 0.015, rot_tol: float = 0.02) -> bool:
    """相似变换是否接近单位变换（几乎无需几何纠正）。"""
    a, b, tx = float(M[0, 0]), float(M[0, 1]), float(M[0, 2])
    c, d, ty = float(M[1, 0]), float(M[1, 1]), float(M[1, 2])
    if abs(tx) > px_tol or abs(ty) > px_tol:
        return False
    if abs(a - 1.0) > scale_tol or abs(d - 1.0) > scale_tol:
        return False
    if abs(b) > rot_tol or abs(c) > rot_tol:
        return False
    return True


def _passthrough_ok(
    orig_bgr: np.ndarray, ai_bgr: np.ndarray, reason: str, *, upscale_interp: int = cv2.INTER_LINEAR
) -> AlignResult:
    """已对齐：不几何扭曲，仅统一到原图像素尺寸；若 AI 自带纯黑边则双方同裁掉。"""
    Oh, Ow = orig_bgr.shape[:2]
    Ah, Aw = ai_bgr.shape[:2]
    if (Ah, Aw) == (Oh, Ow):
        ai_out = ai_bgr.copy()
        orig_out = orig_bgr
    else:
        ai_out = cv2.resize(ai_bgr, (Ow, Oh), interpolation=_pick_interp(Aw, Ah, Ow, Oh, upscale_interp))
        orig_out = orig_bgr

    bl, bt, br, bb = _black_border_widths(ai_out)
    cropped = False
    if max(bl, bt, br, bb) > 0:
        h, w = ai_out.shape[:2]
        y0, y1 = bt, h - bb
        x0, x1 = bl, w - br
        if x1 - x0 >= 16 and y1 - y0 >= 16:
            ai_out = ai_out[y0:y1, x0:x1].copy()
            orig_out = orig_out[y0:y1, x0:x1].copy()
            cropped = True
            reason = reason + f"；去掉 AI 纯黑边后裁为 {x1-x0}×{y1-y0}"

    return AlignResult(
        category="crop" if cropped else "ok",
        reason=reason,
        orig_crop=orig_out,
        orig_hires=orig_out,
        copy_orig_file=not cropped,
        ai_aligned=ai_out,
        preview_orig=orig_out,
        cover=(orig_out.shape[0] * orig_out.shape[1]) / float(Oh * Ow),
        inliers=0,
        matches=0,
        confidence=1.0,
        crop_box=(0, 0, orig_out.shape[1], orig_out.shape[0]) if not cropped else (bl, bt, orig_out.shape[1], orig_out.shape[0]),
    )


def align_images(
    orig_bgr: np.ndarray, ai_bgr: np.ndarray, *, upscale_interp: int = cv2.INTER_LINEAR, feature_mode: str = "gray"
) -> AlignResult:
    """feature_mode: gray=灰度 ORB（旧行为）| edge=梯度图 ORB | auto=两路都算取 inliers 多的（白描规则）。"""
    Oh, Ow = orig_bgr.shape[:2]
    Ah, Aw = ai_bgr.shape[:2]

    # 同尺寸时用 NCC 判断是否已像素对齐；已对齐则直出。
    # 真正的错位来自后面「微边距裁剪再拉满画布」，不是特征算不出来。
    if abs(Ow - Aw) <= 2 and abs(Oh - Ah) <= 2:
        og = _to_gray(orig_bgr).astype(np.float64)
        ag_img = ai_bgr if (Ah, Aw) == (Oh, Ow) else cv2.resize(ai_bgr, (Ow, Oh), interpolation=cv2.INTER_AREA)
        ag = _to_gray(ag_img).astype(np.float64)
        og0, ag0 = og - og.mean(), ag - ag.mean()
        ncc = float((og0 * ag0).sum() / (np.sqrt((og0**2).sum() * (ag0**2).sum()) + 1e-9))
        if ncc >= 0.98:
            return _passthrough_ok(
                orig_bgr,
                ai_bgr,
                reason=f"同尺寸且内容已对齐(NCC={ncc:.4f})，直出",
                upscale_interp=upscale_interp,
            )

    ai_s, ai_scale = _resize_max(ai_bgr, 960)
    orig_s, orig_scale = _resize_max(orig_bgr, 960)
    ai_g = _to_gray(ai_s)
    orig_g = _to_gray(orig_s)

    base_conf = 0.0
    inliers = 0
    nmatch = 0
    feature_used = "gray"
    cands = []
    if feature_mode in ("gray", "auto"):
        cands.append(("gray",) + _orb_similarity(ai_g, orig_g))
    if feature_mode in ("edge", "auto"):
        # 边缘域点更多但更弱，放宽 ratio 并多取特征（实测 5000/0.8 最稳）
        cands.append(("edge",) + _orb_similarity(_edge_gray(ai_g), _edge_gray(orig_g), max_feat=5000, ratio=0.8))
    # 取 inliers 最多的一路；同分优先灰度（旧行为）
    cands.sort(key=lambda c: (-(c[2] if c[1] is not None else -1), 0 if c[0] == "gray" else 1))
    M_work = None
    if cands:
        feature_used, M_work, inliers, nmatch = cands[0]

    M_full = None
    if M_work is not None:
        if True:
            sx, sy = ai_scale, ai_scale
            sdx, sdy = orig_scale, orig_scale
            M_full = np.zeros((2, 3), dtype=np.float64)
            M_full[0, 0] = M_work[0, 0] * sx / sdx
            M_full[0, 1] = M_work[0, 1] * sy / sdx
            M_full[0, 2] = M_work[0, 2] / sdx
            M_full[1, 0] = M_work[1, 0] * sx / sdy
            M_full[1, 1] = M_work[1, 1] * sy / sdy
            M_full[1, 2] = M_work[1, 2] / sdy
            base_conf = inliers / max(1, nmatch)

    # ECC 回退（同构图妆面很常见）
    if M_full is None:
        try:
            warp = np.eye(2, 3, dtype=np.float32)
            ai_r = cv2.resize(ai_g, (orig_g.shape[1], orig_g.shape[0]), interpolation=cv2.INTER_AREA)
            cc, warp = cv2.findTransformECC(
                orig_g.astype(np.float32) / 255.0,
                ai_r.astype(np.float32) / 255.0,
                warp,
                cv2.MOTION_EUCLIDEAN,
                (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 80, 1e-5),
                None,
                1,
            )
            ow_w, ow_h = orig_g.shape[1], orig_g.shape[0]
            M_work = warp.astype(np.float64)
            M_full = np.zeros((2, 3), dtype=np.float64)
            M_full[0, 0] = M_work[0, 0] * (ow_w / Aw) / orig_scale
            M_full[0, 1] = M_work[0, 1] * (ow_h / Ah) / orig_scale
            M_full[0, 2] = M_work[0, 2] / orig_scale
            M_full[1, 0] = M_work[1, 0] * (ow_w / Aw) / orig_scale
            M_full[1, 1] = M_work[1, 1] * (ow_h / Ah) / orig_scale
            M_full[1, 2] = M_work[1, 2] / orig_scale
            inliers = max(inliers, 12)
            nmatch = max(nmatch, 12)
            base_conf = float(cc) if cc == cc else 0.5
        except cv2.error:
            M_full = None

    if M_full is not None and _is_near_identity(M_full):
        return _passthrough_ok(
            orig_bgr,
            ai_bgr,
            reason="估计变换接近单位阵，跳过几何变换，避免误扭曲",
            upscale_interp=upscale_interp,
        )

    if M_full is None or inliers < 8:
        return AlignResult(
            category="unaligned",
            reason="相似变换匹配不足，无法仅用缩放/旋转/裁剪对齐",
            orig_crop=None,
            orig_hires=None,
            copy_orig_file=True,
            ai_aligned=None,
            preview_orig=None,
            cover=0.0,
            inliers=inliers,
            matches=nmatch,
            confidence=base_conf,
            crop_box=None,
            feature_used=feature_used,
        )

    R = _find_full_cover_crop(Ow, Oh, Aw, Ah, M_full)
    if R is None:
        return AlignResult(
            category="unaligned",
            reason="找不到双方完全重叠的裁剪区域（禁止填色补边）",
            orig_crop=None,
            orig_hires=None,
            copy_orig_file=True,
            ai_aligned=None,
            preview_orig=None,
            cover=0.0,
            inliers=inliers,
            matches=nmatch,
            confidence=base_conf,
            crop_box=None,
            feature_used=feature_used,
        )

    rx, ry, rw, rh = R
    area_ratio = (rw * rh) / float(Ow * Oh)
    if area_ratio < 0.2:
        return AlignResult(
            category="unaligned",
            reason=f"可重叠区域过小（仅原图 {100 * area_ratio:.0f}%）",
            orig_crop=None,
            orig_hires=None,
            copy_orig_file=True,
            ai_aligned=None,
            preview_orig=None,
            cover=area_ratio,
            inliers=inliers,
            matches=nmatch,
            confidence=base_conf,
            crop_box=R,
            feature_used=feature_used,
        )

    inv = _invert_affine(M_full)
    assert inv is not None

    # 仅当「整幅原图」确实完全落在 AI 内时，才允许全画布采样。
    # 旧逻辑用 nearly_full 边距启发式强行拉满 Ow×Oh，越界处 BORDER_CONSTANT 填黑 → 黑边。
    full_inside = _rect_inside_ai((0, 0, Ow, Oh), Aw, Ah, inv, n=16)
    if full_inside:
        rx, ry, rw, rh = 0, 0, Ow, Oh
        R = (rx, ry, rw, rh)
        cropped_orig = False
        area_ratio = 1.0
    else:
        cropped_orig = True

    # 同宽高比：不压分辨率
    same_ar = _same_aspect(Ow, Oh, Aw, Ah) or _same_aspect(rw, rh, Aw, Ah)
    if not cropped_orig:
        out_w, out_h = Ow, Oh
        allow_downscale = False
    elif same_ar:
        out_w, out_h = rw, rh
        allow_downscale = False
    else:
        out_w, out_h = _output_size(rw, rh, Aw, Ah, allow_downscale=True)
        allow_downscale = True

    ai_aligned = _remap_ai_to_rect(ai_bgr, inv, rx, ry, rw, rh, out_w, out_h, upscale_interp)

    # 兜底：若仍出现黑边（插值/数值边界），向内收缩 R 重采样，禁止带黑边导出
    for _shrink_try in range(6):
        bl, bt, br, bb = _black_border_widths(ai_aligned)
        if max(bl, bt, br, bb) <= 1:
            # ≤1px 直接裁掉，避免细黑线
            if max(bl, bt, br, bb) == 1:
                y0, y1 = bt, ai_aligned.shape[0] - bb
                x0, x1 = bl, ai_aligned.shape[1] - br
                if x1 - x0 >= 16 and y1 - y0 >= 16:
                    ai_aligned = ai_aligned[y0:y1, x0:x1].copy()
                    # 同步收缩原图矩形
                    rx2 = rx + int(round(bl * rw / out_w))
                    ry2 = ry + int(round(bt * rh / out_h))
                    rw2 = max(16, int(round((x1 - x0) * rw / out_w)))
                    rh2 = max(16, int(round((y1 - y0) * rh / out_h)))
                    rx, ry, rw, rh = rx2, ry2, min(rw2, Ow - rx2), min(rh2, Oh - ry2)
                    R = (rx, ry, rw, rh)
                    out_w, out_h = ai_aligned.shape[1], ai_aligned.shape[0]
                    if (rx, ry, rw, rh) != (0, 0, Ow, Oh):
                        cropped_orig = True
                        area_ratio = (rw * rh) / float(Ow * Oh)
            break
        # 按黑边比例向内收缩，并验证仍在 AI 内
        pad_x = max(bl, br, 2)
        pad_y = max(bt, bb, 2)
        # 映射到原图坐标的收缩量
        sx = max(2, int(round(pad_x * rw / max(1, out_w))))
        sy = max(2, int(round(pad_y * rh / max(1, out_h))))
        nx, ny = rx + sx, ry + sy
        nw, nh = rw - 2 * sx, rh - 2 * sy
        if nw < 16 or nh < 16:
            return AlignResult(
                category="unaligned",
                reason="对齐后存在黑边且可收缩区域过小（禁止填色补边）",
                orig_crop=None,
                orig_hires=None,
                copy_orig_file=True,
                ai_aligned=None,
                preview_orig=None,
                cover=area_ratio,
                inliers=inliers,
                matches=nmatch,
                confidence=base_conf,
                crop_box=R,
                feature_used=feature_used,
            )
        cand = (nx, ny, nw, nh)
        if not _rect_inside_ai(cand, Aw, Ah, inv, n=16):
            # 再多缩一点
            nx, ny = nx + 2, ny + 2
            nw, nh = nw - 4, nh - 4
            cand = (nx, ny, nw, nh)
            if nw < 16 or nh < 16 or not _rect_inside_ai(cand, Aw, Ah, inv, n=16):
                return AlignResult(
                    category="unaligned",
                    reason="对齐后存在黑边，无法找到无黑边裁剪（禁止填色补边）",
                    orig_crop=None,
                    orig_hires=None,
                    copy_orig_file=True,
                    ai_aligned=None,
                    preview_orig=None,
                    cover=area_ratio,
                    inliers=inliers,
                    matches=nmatch,
                    confidence=base_conf,
                    crop_box=R,
                    feature_used=feature_used,
                )
        rx, ry, rw, rh = cand
        R = cand
        cropped_orig = True
        area_ratio = (rw * rh) / float(Ow * Oh)
        if same_ar:
            out_w, out_h = rw, rh
        else:
            out_w, out_h = _output_size(rw, rh, Aw, Ah, allow_downscale=allow_downscale)
        ai_aligned = _remap_ai_to_rect(ai_bgr, inv, rx, ry, rw, rh, out_w, out_h, upscale_interp)
    else:
        bl, bt, br, bb = _black_border_widths(ai_aligned)
        if max(bl, bt, br, bb) > 1:
            return AlignResult(
                category="unaligned",
                reason="对齐后仍有黑边，已放弃导出（禁止填色补边）",
                orig_crop=None,
                orig_hires=None,
                copy_orig_file=True,
                ai_aligned=None,
                preview_orig=None,
                cover=area_ratio,
                inliers=inliers,
                matches=nmatch,
                confidence=base_conf,
                crop_box=R,
                feature_used=feature_used,
            )

    # 预览原图（同尺寸，仅审核用）
    if not cropped_orig:
        preview_orig = orig_bgr if (out_w, out_h) == (Ow, Oh) else cv2.resize(
            orig_bgr, (out_w, out_h), interpolation=cv2.INTER_AREA
        )
    else:
        preview_orig = cv2.resize(
            orig_bgr[ry : ry + rh, rx : rx + rw], (out_w, out_h), interpolation=cv2.INTER_AREA
        )

    # 导出原图：最大分辨率；ok 时直接复制原文件，crop 时无损 PNG 保存裁切区
    if cropped_orig:
        orig_hires = orig_bgr[ry : ry + rh, rx : rx + rw].copy()
        copy_orig_file = False
        orig_export_preview = preview_orig
    else:
        orig_hires = orig_bgr
        copy_orig_file = True
        orig_export_preview = preview_orig

    category = "crop" if cropped_orig else "ok"
    reason = (
        f"AI 无法整幅无黑边覆盖，已裁公共区域 {rw}×{rh}（约占原图 {100 * area_ratio:.0f}%）"
        if cropped_orig
        else "整幅对齐且无黑边"
    )
    if not allow_downscale and cropped_orig:
        reason = reason + "；同比例未压分辨率"

    return AlignResult(
        category=category,
        reason=reason,
        orig_crop=orig_export_preview,
        orig_hires=orig_hires,
        copy_orig_file=copy_orig_file,
        ai_aligned=ai_aligned,
        preview_orig=preview_orig,
        cover=area_ratio if cropped_orig else 1.0,
        inliers=inliers,
        matches=nmatch,
        confidence=base_conf,
        crop_box=R,
        feature_used=feature_used,
    )


def align_pair_paths(
    orig_path: str, ai_path: str, *, upscale_interp: int = cv2.INTER_LINEAR, feature_mode: str = "gray"
) -> AlignResult:
    orig = cv2.imdecode(np.fromfile(orig_path, dtype=np.uint8), cv2.IMREAD_COLOR)
    ai = cv2.imdecode(np.fromfile(ai_path, dtype=np.uint8), cv2.IMREAD_COLOR)
    if orig is None or ai is None:
        return AlignResult(
            category="unaligned",
            reason="图片读取失败",
            orig_crop=None,
            orig_hires=None,
            copy_orig_file=True,
            ai_aligned=None,
            preview_orig=None,
            cover=0.0,
            inliers=0,
            matches=0,
            confidence=0.0,
            crop_box=None,
        )
    return align_images(orig, ai, upscale_interp=upscale_interp, feature_mode=feature_mode)
