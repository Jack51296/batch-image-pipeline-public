#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
图生图对齐批处理（并发）+ 可视化审核 + 送标 CSV。

像素风规则：标签/prompt 命中「像素」类关键词的配对，放大 AI 图改用最近邻（见 defaults.json / --pixel-mode）；
白描/线稿规则：命中「白描/线稿/线描/lineart」的配对，放大 AI 图用双三次、特征匹配灰度/梯度两路取优（见 defaults.json / --lineart-mode）；
            其它风格不受影响，导出逐字节与旧版一致。

导出规则：
  - input：ok/unaligned 原样复制；crop 按源格式导出——源为 JPG/JPEG 则最高质量 JPG，源为 PNG 则保留 PNG
  - output：始终写压缩 JPG（含 NCC≥0.98 直出），满足体积预算；不再另存目视 PNG
            预算 = min(同尺寸input→JPEG(q92)×ratio, input原文件×ratio)
            ok 用 ratio=40%（比默认 80% 再压一倍）；crop/unaligned 用 80%
  - 全部写入 processed/ 扁平目录，命名：
      {id}_input_{ok|crop|unaligned}.(原扩展名|jpg|png)
      {id}_output_{ok|crop|unaligned}.jpg
  - 送标 CSV 记录相对路径：aligned_candidates.csv / aligned_for_label.csv

用法：
  python align_batch.py --csv "\\\\10.0.0.12\\...\\final_fixed.csv"
  python align_batch.py --finalize --out "...\\final_fixed_align_out"
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import cv2
cv2.setNumThreads(0)
import numpy as np

# 同目录导入
sys.path.insert(0, str(Path(__file__).resolve().parent))
from align_core import align_pair_paths  # noqa: E402


# ---------------------------------------------------------------------------
# 像素风规则：只对「像素」类标签/提示词命中的配对，放大 AI 图时改用最近邻(INTER_NEAREST)，
# 保持像素块硬边；未命中的配对沿用双线性，与旧行为一致。关键词可在同目录 defaults.json 调整。
# ---------------------------------------------------------------------------
PIXEL_RULE_DEFAULTS: dict = {
    "mode": "auto",  # auto=按标签/prompt 判断 | on=全部最近邻 | off=关闭（全部双线性）
    "tag_fields": ["标签", "tag", "light_type", "prompt_target", "target_style", "style", "aigc生成风格"],
    "tag_keywords": ["像素", "pixel", "8bit", "8-bit", "16bit", "16-bit", "点阵"],
    "prompt_fields": ["prompt"],
    "prompt_keywords": ["像素画", "像素风", "像素艺术", "pixel art", "pixel-art", "pixelart", "8-bit", "16-bit", "点阵"],
}
PIXEL_RULE_FILE = Path(__file__).resolve().parent / "defaults.json"

# 白描/线稿规则：标签/prompt 命中「白描/线稿/线描/lineart…」的配对：放大 AI 图用双三次(INTER_CUBIC)，
# 特征匹配灰度/梯度两路取优（feature_mode=auto）。未命中的配对行为不变。关键词可在 defaults.json 的 lineart_style 段调整。
LINEART_RULE_DEFAULTS: dict = {
    "mode": "auto",
    "tag_fields": ["标签", "tag", "light_type", "prompt_target", "target_style", "style", "aigc生成风格"],
    "tag_keywords": ["白描", "线稿", "线描", "lineart", "line art", "line-art", "勾线", "素描"],
    "prompt_fields": ["prompt"],
    "prompt_keywords": ["白描", "线稿", "线描", "line art", "lineart", "line-art", "纯线条"],
}


def load_lineart_rule(mode: str = "", tags: str = "") -> dict:
    """合并 defaults.json 的 lineart_style 段与命令行覆盖（--lineart-mode / --lineart-tags）。"""
    rule = {k: (list(v) if isinstance(v, list) else v) for k, v in LINEART_RULE_DEFAULTS.items()}
    if PIXEL_RULE_FILE.is_file():
        try:
            cfg = json.loads(PIXEL_RULE_FILE.read_text(encoding="utf-8")).get("lineart_style") or {}
            for k in rule:
                if k in cfg and cfg[k] not in (None, ""):
                    rule[k] = list(cfg[k]) if isinstance(rule[k], list) else cfg[k]
        except Exception as e:
            print(f"警告: 读取 {PIXEL_RULE_FILE.name} 的 lineart_style 失败，使用内置规则: {e}")
    if mode:
        rule["mode"] = mode
    if tags:
        rule["tag_keywords"] = [t.strip() for t in tags.split(",") if t.strip()]
    if rule["mode"] not in ("auto", "on", "off"):
        raise SystemExit(f"--lineart-mode 只能是 auto/on/off，收到: {rule['mode']}")
    return rule


# --upscale-interp 全局覆盖：对所有配对强制指定放大插值（如 新海诚/赛璐璐 用 cubic 保线条锐度），优先于像素风/白描规则
_INTERP_BY_NAME = {
    "nearest": cv2.INTER_NEAREST,
    "linear": cv2.INTER_LINEAR,
    "cubic": cv2.INTER_CUBIC,
    "lanczos": cv2.INTER_LANCZOS4,
}


def load_pixel_rule(mode: str = "", tags: str = "") -> dict:
    """合并 defaults.json 的 pixel_style 段与命令行覆盖（--pixel-mode / --pixel-tags）。"""
    rule = {k: (list(v) if isinstance(v, list) else v) for k, v in PIXEL_RULE_DEFAULTS.items()}
    if PIXEL_RULE_FILE.is_file():
        try:
            cfg = json.loads(PIXEL_RULE_FILE.read_text(encoding="utf-8")).get("pixel_style") or {}
            for k in rule:
                if k in cfg and cfg[k] not in (None, ""):
                    rule[k] = list(cfg[k]) if isinstance(rule[k], list) else cfg[k]
        except Exception as e:  # 配置坏了不应拖垮对齐，退回内置默认
            print(f"警告: 读取 {PIXEL_RULE_FILE.name} 失败，使用内置像素风规则: {e}")
    if mode:
        rule["mode"] = mode
    if tags:
        rule["tag_keywords"] = [t.strip() for t in tags.split(",") if t.strip()]
    if rule["mode"] not in ("auto", "on", "off"):
        raise SystemExit(f"--pixel-mode 只能是 auto/on/off，收到: {rule['mode']}")
    return rule


def detect_pixel_style(row: dict, style: str, rule: dict, forced_label: str = "forced(--pixel-mode on)") -> str:
    """
    返回命中说明（如 '标签=像素' / 'prompt~像素画'），未命中返回 ''。
    标签类字段做关键词子串匹配；prompt 只匹配更强的短语（像素画/像素风/pixel art…），
    避免“不改变像素尺寸”这类描述误伤其它风格。
    """
    mode = rule.get("mode", "auto")
    if mode == "off":
        return ""
    if mode == "on":
        return forced_label
    tag_kw = [str(k).lower() for k in rule.get("tag_keywords", []) if str(k).strip()]
    for f in rule.get("tag_fields", []):
        v = str(row.get(f) or "").strip()
        if not v:
            continue
        low = v.lower()
        for k in tag_kw:
            if k in low:
                return f"{f}={v}"
    if style:
        low = str(style).lower()
        for k in tag_kw:
            if k in low:
                return f"style={style}"
    prompt_kw = [str(k).lower() for k in rule.get("prompt_keywords", []) if str(k).strip()]
    for f in rule.get("prompt_fields", []):
        v = str(row.get(f) or "")
        if not v:
            continue
        low = v.lower()
        for k in prompt_kw:
            if k in low:
                return f"{f}~{k}"
    return ""


class _FilteredStderr:
    """过滤 libpng iCCP 等无害警告，避免刷屏盖住进度。"""

    _DROP = ("iCCP:", "libpng warning", "known incorrect sRGB profile")

    def __init__(self, real):
        self._real = real

    def write(self, s):
        if s and any(t in s for t in self._DROP):
            return len(s)
        return self._real.write(s)

    def writelines(self, lines):
        for line in lines:
            self.write(line)

    def flush(self):
        return self._real.flush()

    def fileno(self):
        return self._real.fileno()

    def isatty(self):
        return self._real.isatty()

    def __getattr__(self, name):
        return getattr(self._real, name)


def silence_libpng_warnings() -> None:
    if getattr(sys.stderr, "_align_libpng_filtered", False):
        return
    wrapper = _FilteredStderr(sys.stderr)
    wrapper._align_libpng_filtered = True  # type: ignore[attr-defined]
    sys.stderr = wrapper  # type: ignore[assignment]
    try:
        cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_ERROR)
    except Exception:
        pass


def silence_native_stderr_for_worker() -> None:
    """
    子进程里 libpng 直接写 C 层 stderr，Python 包装拦不住。
    worker 进度由主进程打印，这里可把 fd=2 丢到空设备。
    """
    if getattr(silence_native_stderr_for_worker, "_done", False):
        return
    try:
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, 2)
        silence_native_stderr_for_worker._fd = devnull  # type: ignore[attr-defined]
        silence_native_stderr_for_worker._done = True  # type: ignore[attr-defined]
    except OSError:
        pass


silence_libpng_warnings()


IMG_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}

# 导出体积上限 = input 文件大小 × 该比例
SIZE_BUDGET_RATIO = 0.80
# output_ok 再压一倍（预算减半）
SIZE_BUDGET_RATIO_OK = SIZE_BUDGET_RATIO * 0.5

# 处理后单目录内命名：{id}_input_{category}.jpg / {id}_output_{category}.jpg
# category: ok | crop | unaligned


def imwrite_unicode(path: Path, img: np.ndarray, ext: str = ".jpg", jpeg_quality: int = 92) -> int:
    """写入图片，返回字节数。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    ext = ext.lower()
    if ext in (".jpg", ".jpeg"):
        params = [int(cv2.IMWRITE_JPEG_QUALITY), int(jpeg_quality)]
    else:
        params = []
    ok, buf = cv2.imencode(ext, img, params)
    if not ok:
        raise RuntimeError(f"encode failed: {path}")
    data = buf.tobytes()
    path.write_bytes(data)
    return len(data)


def encode_under_budget(
    img: np.ndarray,
    max_bytes: int,
    *,
    allow_downscale: bool = False,
) -> tuple[bytes, str, int]:
    """
    将图片压到不超过 max_bytes。
    默认同比例场景只调 JPEG 质量、不降分辨率；仅 allow_downscale=True 时才缩小。
    """
    if max_bytes < 8 * 1024:
        max_bytes = 8 * 1024

    work = img
    scale_rounds = 12 if allow_downscale else 1
    for _scale_try in range(scale_rounds):
        lo, hi = 35, 95
        best = None
        best_q = 75
        while lo <= hi:
            q = (lo + hi) // 2
            ok, buf = cv2.imencode(".jpg", work, [int(cv2.IMWRITE_JPEG_QUALITY), q])
            if not ok:
                hi = q - 1
                continue
            raw = buf.tobytes()
            if len(raw) <= max_bytes:
                best = raw
                best_q = q
                lo = q + 1
            else:
                hi = q - 1
        if best is not None:
            return best, ".jpg", best_q
        if not allow_downscale:
            # 保持分辨率，质量降到 35 仍超标也接受（避免 PNG/JPEG 口径误伤后再砍像素）
            ok, buf = cv2.imencode(".jpg", work, [int(cv2.IMWRITE_JPEG_QUALITY), 35])
            if not ok:
                raise RuntimeError("encode fallback failed")
            return buf.tobytes(), ".jpg", 35
        h, w = work.shape[:2]
        if min(h, w) <= 256:
            ok, buf = cv2.imencode(".jpg", work, [int(cv2.IMWRITE_JPEG_QUALITY), 30])
            if not ok:
                raise RuntimeError("encode fallback failed")
            return buf.tobytes(), ".jpg", 30
        work = cv2.resize(
            work,
            (max(1, int(w * 0.85)), max(1, int(h * 0.85))),
            interpolation=cv2.INTER_AREA,
        )
    ok, buf = cv2.imencode(".jpg", work, [int(cv2.IMWRITE_JPEG_QUALITY), 30])
    return buf.tobytes(), ".jpg", 30


def jpeg_reference_bytes(img: np.ndarray, quality: int = 92) -> int:
    """把参考图（通常是 input 同尺寸内容）压成 JPEG，得到可比的体积基准。"""
    ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)])
    if not ok:
        return max(8 * 1024, img.shape[0] * img.shape[1])
    return len(buf.tobytes())


def path_with_ext(stem: Path, ext: str) -> Path:
    """
    给无扩展名 stem 加上后缀。
    不用 Path.with_suffix：文件名中含小数点（如 3.5）时 with_suffix 会误截断。
    """
    if not ext:
        return stem
    if not ext.startswith("."):
        ext = "." + ext
    return stem.parent / f"{stem.name}{ext}"


def write_bytes_unique(path: Path, data: bytes) -> Path:
    """
    写入文件；若已存在则改用 _2、_3…
    用 O_EXCL 独占创建，减轻多进程同时写同名时的互相覆盖。
    Windows 必须 O_BINARY，否则 \\n 会被改成 \\r\\n，PNG/JPEG 全部损坏。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    suffix = path.suffix
    base = path.name[: -len(suffix)] if suffix else path.name
    parent = path.parent
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    n = 1
    while n <= 100000:
        cand = path if n == 1 else (parent / f"{base}_{n}{suffix}")
        try:
            fd = os.open(str(cand), flags)
        except FileExistsError:
            n += 1
            continue
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
        except Exception:
            try:
                cand.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        return cand
    raise RuntimeError(f"无法写入不重名文件: {path}")


def allocate_unique_safe_id(processed: Path, safe_id: str, cat: str) -> str:
    """
    若 processed 下已有同名 input/output，则返回 safe_id_2、safe_id_3…
    保证一对 input/output 共用同一序号前缀。
    """
    processed.mkdir(parents=True, exist_ok=True)
    n = 1
    while n <= 100000:
        sid = safe_id if n == 1 else f"{safe_id}_{n}"
        conflict = False
        for kind in ("input", "output"):
            if any(processed.glob(f"{sid}_{kind}_{cat}.*")):
                conflict = True
                break
        if not conflict:
            return sid
        n += 1
    raise RuntimeError(f"无法分配唯一 safe_id: {safe_id}")


def save_lossless_png(path: Path, img: np.ndarray) -> dict:
    """无损 PNG 导出（源为 PNG 的 crop input 用）。"""
    path = path_with_ext(path, ".png")
    path.parent.mkdir(parents=True, exist_ok=True)
    ok, buf = cv2.imencode(".png", img)
    if not ok:
        raise RuntimeError(f"PNG encode failed: {path}")
    data = buf.tobytes()
    path = write_bytes_unique(path, data)
    return {"path": path, "bytes": len(data), "budget": None, "quality": "lossless", "ext": ".png"}


def save_cropped_input(dest_stem: Path, img: np.ndarray, src: Path) -> dict:
    """
    crop 后的 input 导出：跟随源图格式。
    - 源为 .jpg/.jpeg → JPEG 质量 100（最高质量转码，避免巨幅 PNG）
    - 源为 .png → 无损 PNG
    - 其他 → 按 JPEG q100（体积友好）
    """
    ext = (src.suffix or "").lower()
    if ext == ".png":
        return save_lossless_png(dest_stem, img)
    # jpg / jpeg / 其他有损或未知：最高质量 JPEG
    out_ext = ".jpg"
    path = path_with_ext(dest_stem, out_ext)
    path.parent.mkdir(parents=True, exist_ok=True)
    ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 100])
    if not ok:
        raise RuntimeError(f"JPEG encode failed: {path}")
    data = buf.tobytes()
    path = write_bytes_unique(path, data)
    return {
        "path": path,
        "bytes": len(data),
        "budget": None,
        "quality": 100,
        "ext": out_ext,
        "format": "jpg_q100",
    }


def copy_input_file(src: Path, dest_stem: Path) -> dict:
    """原样复制 input 文件（保留原始体积与编码，零有损）。"""
    ext = src.suffix.lower() or ".png"
    dest = path_with_ext(dest_stem, ext)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest = write_bytes_unique(dest, src.read_bytes())
    return {
        "path": dest,
        "bytes": dest.stat().st_size,
        "budget": None,
        "quality": "original_copy",
        "ext": ext,
    }


def save_output_fair_budget(
    path: Path,
    out_img: np.ndarray,
    ref_img: np.ndarray,
    *,
    input_bytes: int | None = None,
    allow_downscale: bool = False,
    budget_ratio: float | None = None,
) -> dict:
    """
    output 只写压缩 JPG，满足体积预算。

    预算 = min(
      同尺寸 input 内容 JPEG(q=92) × ratio,
      input 原文件 × ratio（若提供 input_bytes）
    )
    默认 ratio=0.80；output_ok 用 0.40（压缩程度再大一倍）。
    """
    ratio = float(SIZE_BUDGET_RATIO if budget_ratio is None else budget_ratio)
    rh, rw = ref_img.shape[:2]
    oh, ow = out_img.shape[:2]
    if (rh, rw) != (oh, ow):
        ref_for_budget = cv2.resize(ref_img, (ow, oh), interpolation=cv2.INTER_AREA)
    else:
        ref_for_budget = ref_img

    ref_jpeg = jpeg_reference_bytes(ref_for_budget, 92)
    budget = max(8 * 1024, int(ref_jpeg * ratio))
    if input_bytes and input_bytes > 0:
        budget = min(budget, max(8 * 1024, int(input_bytes * ratio)))

    path.parent.mkdir(parents=True, exist_ok=True)
    # path 为无扩展名 stem；禁止 with_suffix（文件名含 3.5 等小数点会截断）
    data, ext, q = encode_under_budget(out_img, budget, allow_downscale=allow_downscale)
    out_path = write_bytes_unique(path_with_ext(path, ext), data)

    return {
        "path": out_path,
        "bytes": len(data),
        "budget": budget,
        "budget_ratio": ratio,
        "ref_jpeg_bytes": ref_jpeg,
        "quality": q,
        "ext": ext,
        "vis_png": None,
        "format": "jpg",
    }


def imread_unicode(path: Path):
    data = np.fromfile(str(path), dtype=np.uint8)
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def read_csv_rows(csv_path: Path) -> list[dict]:
    _, rows = read_csv_bundle(csv_path)
    return rows


def read_csv_bundle(csv_path: Path) -> tuple[list[str], list[dict]]:
    raw = csv_path.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw.decode("utf-8", errors="replace")
    reader = csv.DictReader(text.splitlines())
    fields = list(reader.fieldnames or [])
    return fields, list(reader)


def find_csv(root: Path) -> Path:
    csvs = sorted(root.glob("*.csv"))
    if not csvs:
        raise SystemExit(f"目录下没有 CSV: {root}")
    # 优先 fixed / relative，再退回字母序第一个
    for prefer in ("fixed", "relative"):
        hit = [c for c in csvs if prefer in c.name.lower()]
        if hit:
            return hit[0]
    return csvs[0]


def derive_colormatched(name: str) -> str:
    p = Path(name)
    if "_colormatched" in p.stem.lower():
        return p.name
    return f"{p.stem}_colormatched{p.suffix}"


def strip_colormatched_stem(name: str) -> str:
    stem = Path(name).stem
    return re.sub(r"_colormatched$", "", stem, flags=re.I)


def basename_of(path_str: str) -> str:
    return Path(str(path_str).replace("\\", "/")).name


def infer_style(root: Path, style_arg: str) -> str:
    if style_arg:
        return style_arg.strip()
    name = root.name
    name = re.sub(r"round\s*\d+$", "", name, flags=re.I).strip("_- ")
    return name or root.name


def resolve_under_root(root: Path, path_str: str) -> Path | None:
    """相对 CSV 所在目录解析；已是绝对路径则直接用。"""
    s = (path_str or "").strip().strip('"')
    if not s:
        return None
    p = Path(s)
    # UNC / 盘符绝对路径
    if p.is_absolute() or s.startswith("\\\\") or (len(s) >= 2 and s[1] == ":"):
        cand = Path(s)
    else:
        cand = root / Path(s)
    return cand if cand.is_file() else None


OUTPUT_PATH_KEYS = (
    "output_image_path",
    "output_image_path_1",
    "output_image_path_2",
)


def iter_output_rels(row: dict) -> list[tuple[str, str]]:
    """
    返回 [(column_key, relative_or_abs_path), ...]。
    若存在 _1/_2 列则三列都参与（空值跳过）；否则兼容单列 output / ai_output。
    """
    has_multi = any(k in row for k in ("output_image_path_1", "output_image_path_2"))
    out: list[tuple[str, str]] = []
    if has_multi or "output_image_path" in row:
        for key in OUTPUT_PATH_KEYS:
            if key not in row and key != "output_image_path":
                continue
            v = (row.get(key) or "").strip()
            if v:
                out.append((key, v))
        if out:
            return out
    for key in ("ai_output_image_path", "output"):
        v = (row.get(key) or "").strip()
        if v:
            out.append((key, v))
            break
    return out


def resolve_jobs_from_row(root: Path, row: dict) -> list[dict]:
    """
    一行 CSV 可展开为多对：input × 每个非空 output 列。
    返回 job 片段：id / orig / ai（路径字符串）。
    """
    status = (row.get("image_status") or "").strip()
    if status and not re.match(r"^success", status, re.I):
        return []

    inp = (row.get("input_image_path") or row.get("input") or "").strip()
    if not inp:
        return []

    orig = resolve_under_root(root, inp)
    outputs = iter_output_rels(row)
    if not outputs:
        # 旧布局：仅 input 文件名，推导 *_colormatched
        out_name = derive_colormatched(basename_of(inp))
        outputs = [("output_image_path", out_name)]

    base_id = (row.get("id") or "").strip()
    jobs: list[dict] = []
    for key, out_rel in outputs:
        ai = resolve_under_root(root, out_rel)
        out_base = basename_of(out_rel)
        pid = base_id or strip_colormatched_stem(out_base)
        if not pid:
            pid = strip_colormatched_stem(basename_of(inp))
        # 多 output 时保证 id 不撞车
        if len(outputs) > 1:
            tag = {"output_image_path": "r1", "output_image_path_1": "r2", "output_image_path_2": "r3"}.get(key, key)
            stem_id = strip_colormatched_stem(out_base)
            if stem_id and re.search(r"-r[123]$", stem_id, re.I):
                pid = stem_id
            elif base_id:
                pid = f"{base_id}-{tag}"
            else:
                pid = f"{strip_colormatched_stem(basename_of(inp))}-{tag}"
        jobs.append({
            "id": pid,
            "orig": str(orig) if orig else "",
            "ai": str(ai) if ai else "",
            "output_col": key,
            "missing": not (orig and ai),
        })
    return jobs


def resolve_pair_paths(root: Path, row: dict) -> tuple[str, Path | None, Path | None]:
    """兼容旧调用：只取第一个可用 output。"""
    jobs = resolve_jobs_from_row(root, row)
    if not jobs:
        return "", None, None
    j = jobs[0]
    orig = Path(j["orig"]) if j["orig"] else None
    ai = Path(j["ai"]) if j["ai"] else None
    return j["id"], orig, ai


def _worker(job: dict) -> dict:
    """子进程任务：对齐后写入 processed/；input 无损最大尺寸，output 受 80% 体积约束。"""
    silence_libpng_warnings()
    silence_native_stderr_for_worker()
    pid = job["id"]
    style = job["style"]
    orig_path = job["orig"]
    ai_path = job["ai"]
    out_dir = Path(job["out_dir"])
    pixel_hit = str(job.get("pixel_hit") or "")
    lineart_hit = str(job.get("lineart_hit") or "")
    if pixel_hit:
        upscale_interp, interp_name = cv2.INTER_NEAREST, "nearest"
    elif lineart_hit:
        upscale_interp, interp_name = cv2.INTER_CUBIC, "cubic"
    else:
        upscale_interp, interp_name = cv2.INTER_LINEAR, "linear"
    override = str(job.get("upscale_override") or "").strip().lower()
    if override:
        upscale_interp, interp_name = _INTERP_BY_NAME[override], override
    feature_mode = "auto" if lineart_hit else "gray"
    meta_extra = {
        "row_index": job.get("row_index"),
        "output_col": job.get("output_col", ""),
        "source_row": job.get("source_row") or {},
        "upscale_interp": interp_name,
        "upscale_override": override,
        "pixel_style_hit": pixel_hit,
        "lineart_style_hit": lineart_hit,
        "feature_mode": feature_mode,
    }
    try:
        orig_p = Path(orig_path)
        input_bytes = orig_p.stat().st_size
        result = align_pair_paths(orig_path, ai_path, upscale_interp=upscale_interp, feature_mode=feature_mode)
        meta_extra["feature_used"] = getattr(result, "feature_used", "gray")
        cat = result.category
        # 仅替换 Windows 非法文件名字符；保留小数点等（扩展名由 path_with_ext 追加）
        safe_id = re.sub(r'[<>:"/\\|?*]', "_", pid)
        processed = out_dir / "processed"

        if cat == "unaligned" or result.ai_aligned is None:
            cat = "unaligned"
            orig_img = imread_unicode(orig_p)
            ai_img = imread_unicode(Path(ai_path))
            if orig_img is None or ai_img is None:
                return {
                    "id": pid,
                    "ok": False,
                    "category": "unaligned",
                    "reason": result.reason or "读取失败",
                    "error": True,
                    **meta_extra,
                }
            safe_id = allocate_unique_safe_id(processed, safe_id, cat)
            # input：原样复制；output：JPEG 口径体积约束（相对 input 同尺寸 JPEG×80%）
            stem_in = processed / f"{safe_id}_input_{cat}"
            stem_out = processed / f"{safe_id}_output_{cat}"
            meta_o = copy_input_file(orig_p, stem_in)
            meta_a = save_output_fair_budget(
                stem_out, ai_img, orig_img, input_bytes=input_bytes, allow_downscale=False,
                budget_ratio=SIZE_BUDGET_RATIO_OK if cat == "ok" else SIZE_BUDGET_RATIO,
            )
            cover = 0.0
            reason = result.reason
        else:
            safe_id = allocate_unique_safe_id(processed, safe_id, cat)
            stem_in = processed / f"{safe_id}_input_{cat}"
            stem_out = processed / f"{safe_id}_output_{cat}"
            # input：ok 原样复制；crop 跟随源格式（JPG→q100 JPG，PNG→PNG）
            if result.copy_orig_file or cat == "ok":
                meta_o = copy_input_file(orig_p, stem_in)
                ref_for_budget = imread_unicode(orig_p)
            else:
                hires = result.orig_hires
                if hires is None:
                    meta_o = copy_input_file(orig_p, stem_in)
                    ref_for_budget = imread_unicode(orig_p)
                else:
                    meta_o = save_cropped_input(stem_in, hires, orig_p)
                    ref_for_budget = hires
            # output：只写压缩 JPG；ok 预算再减半（压缩再大一倍）
            allow_down = cat not in ("ok", "crop")
            meta_a = save_output_fair_budget(
                stem_out,
                result.ai_aligned,
                ref_for_budget if ref_for_budget is not None else result.ai_aligned,
                input_bytes=input_bytes,
                allow_downscale=allow_down,
                budget_ratio=SIZE_BUDGET_RATIO_OK if cat == "ok" else SIZE_BUDGET_RATIO,
            )
            cover = result.cover
            reason = result.reason

        p_orig = meta_o["path"]
        p_ai = meta_a["path"]

        rel_o = f"processed/{p_orig.name}"
        rel_a = f"processed/{p_ai.name}"
        return {
            "id": pid,
            "ok": True,
            "safe_id": safe_id,
            "style": style,
            "category": cat,
            "reason": reason,
            "cover": round(float(cover), 4),
            "inliers": result.inliers,
            "matches": result.matches,
            "confidence": round(float(result.confidence), 4),
            "input_bytes": input_bytes,
            "budget_bytes": meta_a.get("budget"),
            "ref_jpeg_bytes": meta_a.get("ref_jpeg_bytes"),
            "orig_bytes": meta_o["bytes"],
            "ai_bytes": meta_a["bytes"],
            "orig_encode": meta_o.get("quality"),
            "ai_jpeg_quality": meta_a.get("quality"),
            "output_format": meta_a.get("format"),
            "vis_png": "",
            "orig_rel": rel_o,
            "ai_rel": rel_a,
            "review_orig": rel_o,
            "review_ai": rel_a,
            "discarded": False,
            **meta_extra,
        }
    except Exception as e:
        return {
            "id": pid,
            "ok": False,
            "category": "unaligned",
            "reason": str(e),
            "error": True,
            **meta_extra,
        }


REVIEW_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>对齐结果审核</title>
<style>
:root { --bg:#0f172a; --card:#1e293b; --text:#e2e8f0; --muted:#94a3b8; }
* { box-sizing: border-box; }
body { margin:0; font-family:"Microsoft YaHei",sans-serif; background:var(--bg); color:var(--text); }
header { position:sticky; top:0; z-index:20; background:#0b1220ee; backdrop-filter:blur(8px); border-bottom:1px solid #334155; padding:12px 16px; }
h1 { margin:0 0 8px; font-size:18px; }
.bar { display:flex; flex-wrap:wrap; gap:8px; align-items:center; }
.bar button, .bar select { padding:6px 12px; border-radius:6px; border:1px solid #475569; background:#1e293b; color:var(--text); cursor:pointer; }
.bar button.primary { background:#2563eb; border-color:#2563eb; }
.bar button.danger { background:#b91c1c; border-color:#b91c1c; }
.stats { color:var(--muted); font-size:12px; margin-top:6px; }
.grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(300px,1fr)); gap:12px; padding:16px; }
.card { background:var(--card); border:1px solid #334155; border-radius:10px; overflow:hidden; cursor:pointer; }
.card.discarded { opacity:.45; outline:2px solid #ef4444; }
.card .meta { padding:8px 10px; font-size:12px; display:flex; justify-content:space-between; gap:8px; flex-wrap:wrap; }
.tag { padding:2px 8px; border-radius:999px; font-size:11px; }
.tag.ok { background:#14532d; color:#86efac; }
.tag.crop { background:#1e3a8a; color:#93c5fd; }
.tag.unaligned { background:#7f1d1d; color:#fecaca; }
.wipe { position:relative; height:260px; background:#020617; overflow:hidden; user-select:none; }
.wipe img { position:absolute; inset:0; width:100%; height:100%; object-fit:contain; pointer-events:none; }
.wipe .top { clip-path: inset(0 calc(100% - var(--split,50%)) 0 0); }
.wipe .handle {
  position:absolute; top:0; bottom:0; left:var(--split,50%); width:28px; margin-left:-14px;
  z-index:3; cursor:ew-resize; touch-action:none;
}
.wipe .handle::before {
  content:""; position:absolute; left:50%; top:0; bottom:0; width:2px; margin-left:-1px;
  background:#38bdf8; box-shadow:0 0 0 1px #0ea5e922;
}
.wipe .handle::after {
  content:""; position:absolute; left:50%; top:50%; width:18px; height:18px; margin:-9px 0 0 -9px;
  border-radius:50%; background:#38bdf8; border:2px solid #fff; box-shadow:0 1px 4px #0008;
}
.wipe input[type=range] { position:absolute; left:8px; right:8px; bottom:6px; width:auto; z-index:2; }
.actions { display:flex; gap:6px; padding:8px 10px 10px; }
.actions button { flex:1; padding:6px; border-radius:6px; border:1px solid #475569; background:#0f172a; color:var(--text); cursor:pointer; }
.actions button.keep { border-color:#166534; }
.actions button.dump { border-color:#991b1b; }
.pager { display:flex; flex-wrap:wrap; gap:8px; align-items:center; justify-content:center; padding:8px 16px 20px; }
.pager button { padding:6px 12px; border-radius:6px; border:1px solid #475569; background:#1e293b; color:var(--text); cursor:pointer; }
.pager button:disabled { opacity:.4; cursor:not-allowed; }
.pager .page-info { color:var(--muted); font-size:12px; min-width:140px; text-align:center; }
.pager select { padding:6px 8px; border-radius:6px; border:1px solid #475569; background:#1e293b; color:var(--text); }
dialog.hint { border:1px solid #475569; border-radius:10px; background:#1e293b; color:var(--text); max-width:520px; }
/* 放大对比层 */
.lb { display:none; position:fixed; inset:0; z-index:100; background:#020617f2; flex-direction:column; }
.lb.show { display:flex; }
.lb-head { display:flex; align-items:center; gap:10px; padding:10px 14px; border-bottom:1px solid #334155; flex-wrap:wrap; }
.lb-head h2 { margin:0; font-size:15px; flex:1; min-width:140px; }
.lb-head button, .lb-head label { padding:6px 10px; border-radius:6px; border:1px solid #475569; background:#1e293b; color:var(--text); cursor:pointer; font-size:12px; }
.lb-stage { flex:1; position:relative; overflow:hidden; cursor:grab; touch-action:none; }
.lb-stage.dragging { cursor:grabbing; }
.lb-world { position:absolute; left:50%; top:50%; transform-origin:center center; will-change:transform; }
.lb-wipe { position:relative; background:#000; box-shadow:0 0 0 1px #334155; }
.lb-wipe img { display:block; max-width:none; user-select:none; pointer-events:none; }
.lb-wipe .base { position:relative; }
.lb-wipe .over { position:absolute; left:0; top:0; clip-path: inset(0 calc(100% - var(--split,50%)) 0 0); }
.lb-handle {
  position:absolute; top:0; bottom:0; left:var(--split,50%); width:36px; margin-left:-18px;
  z-index:6; cursor:ew-resize; touch-action:none;
}
.lb-handle::before {
  content:""; position:absolute; left:50%; top:0; bottom:0; width:3px; margin-left:-1.5px;
  background:#38bdf8; box-shadow:0 0 8px #38bdf8aa;
}
.lb-handle::after {
  content:"⟷"; position:absolute; left:50%; top:50%; width:28px; height:28px; margin:-14px 0 0 -14px;
  border-radius:50%; background:#0ea5e9; border:2px solid #fff; color:#fff;
  font-size:12px; line-height:24px; text-align:center; box-shadow:0 2px 8px #000a;
}
.lb-slider { position:absolute; left:12px; right:12px; bottom:12px; z-index:5; }
.lb-hint { padding:8px 14px; font-size:12px; color:var(--muted); border-top:1px solid #334155; }
</style>
</head>
<body>
<header>
  <h1>对齐结果审核 · <span id="styleName"></span></h1>
  <div class="bar">
    <select id="filter">
      <option value="all">全部</option>
      <option value="ok">已对齐</option>
      <option value="crop">已裁原图</option>
      <option value="unaligned">无法对齐</option>
      <option value="keep">未废弃</option>
      <option value="discarded">已废弃</option>
    </select>
    <button type="button" id="btnKeepAllOk">保留全部 ok/crop</button>
    <button type="button" id="btnDumpUn">废弃全部无法对齐</button>
    <button type="button" class="primary" id="btnExport">下载 decisions.json</button>
    <button type="button" class="danger" id="btnFinalizeHint">如何生成送标 CSV？</button>
  </div>
  <div class="stats" id="stats"></div>
  <div class="stats">分页加载 processed（input 原样/PNG，output 压缩 JPG），避免一次撑爆内存。点击卡片放大：滚轮缩放 · 拖拽平移 · 拖分割线对比。</div>
</header>
<div class="pager" id="pagerTop"></div>
<div class="grid" id="grid"></div>
<div class="pager" id="pagerBottom"></div>
<dialog class="hint" id="hintDlg">
  <p><strong>送标 CSV</strong></p>
  <p>1. 点「下载 decisions.json」保存到输出根目录（如 <code>final_fixed_align_out/</code>）。<br>
     2. 运行：<code>python align_batch.py --finalize --out &lt;输出根目录&gt;</code><br>
     3. 生成 <code>aligned_for_label.csv</code>。</p>
  <form method="dialog"><button>知道了</button></form>
</dialog>

<div class="lb" id="lightbox" aria-hidden="true">
  <div class="lb-head">
    <h2 id="lbTitle">对比</h2>
    <button type="button" id="lbZoomOut">－</button>
    <button type="button" id="lbZoomReset">100%</button>
    <button type="button" id="lbZoomIn">＋</button>
    <label><input type="checkbox" id="lbSwap"> 对调上下层</label>
    <button type="button" id="lbKeep">保留</button>
    <button type="button" id="lbDump">废弃</button>
    <button type="button" id="lbClose">关闭 (Esc)</button>
  </div>
  <div class="lb-stage" id="lbStage">
    <div class="lb-world" id="lbWorld">
      <div class="lb-wipe" id="lbWipe" style="--split:50%">
        <img class="base" id="lbBase" alt="base">
        <img class="over" id="lbOver" alt="over">
        <div class="lb-handle" id="lbHandle" title="拖动分割线对比"></div>
        <input class="lb-slider" id="lbSplit" type="range" min="0" max="100" value="50">
      </div>
    </div>
  </div>
  <div class="lb-hint">拖动蓝线左右对比 · 滚轮放大看细节 · 空白处拖拽平移 · 也可拖底部滑杆</div>
</div>

<script>
const MANIFEST = /*__MANIFEST__*/[];
const state = Object.fromEntries(MANIFEST.map(x => [x.id, { discarded: !!x.discarded }]));
const $ = id => document.getElementById(id);
$('styleName').textContent = (MANIFEST[0] && MANIFEST[0].style) || '';

let pageSize = 20;
let pageIndex = 0;

function toReviewUrl(rel) {
  if (!rel) return '';
  // review/index.html → ../processed/...
  return rel.startsWith('../') ? rel : ('../' + rel.replace(/^\/+/, ''));
}

function pairUrls(it) {
  const orig = toReviewUrl(it.review_orig || it.orig_rel);
  const ai = toReviewUrl(it.review_ai || it.ai_rel);
  return { orig, ai };
}

function filtered() {
  const f = $('filter').value;
  return MANIFEST.filter(it => {
    const d = state[it.id].discarded;
    if (f === 'all') return true;
    if (f === 'discarded') return d;
    if (f === 'keep') return !d;
    return it.category === f;
  });
}

function refreshStats() {
  const total = MANIFEST.length;
  const dumped = MANIFEST.filter(x => state[x.id].discarded).length;
  const ok = MANIFEST.filter(x => x.category==='ok' && !state[x.id].discarded).length;
  const crop = MANIFEST.filter(x => x.category==='crop' && !state[x.id].discarded).length;
  const un = MANIFEST.filter(x => x.category==='unaligned' && !state[x.id].discarded).length;
  const list = filtered();
  const pages = Math.max(1, Math.ceil(list.length / pageSize) || 1);
  $('stats').textContent = `共 ${total} · 可用 ${ok+crop}（ok ${ok} / crop ${crop}）· 未对齐保留审 ${un} · 已废弃 ${dumped} · 当前筛选 ${list.length} · 第 ${Math.min(pageIndex+1, pages)}/${pages} 页`;
}

function unloadGridImages(grid) {
  grid.querySelectorAll('img').forEach(img => {
    img.removeAttribute('src');
    img.src = '';
  });
  grid.innerHTML = '';
}

function bindWipe(wipeEl, rangeEl, handleEl) {
  const apply = (pct) => {
    const v = Math.max(0, Math.min(100, pct));
    wipeEl.style.setProperty('--split', v + '%');
    if (rangeEl) rangeEl.value = String(Math.round(v));
  };
  if (rangeEl) {
    rangeEl.addEventListener('input', () => apply(Number(rangeEl.value)));
    rangeEl.addEventListener('click', e => e.stopPropagation());
    rangeEl.addEventListener('pointerdown', e => e.stopPropagation());
  }
  const startDrag = (e) => {
    e.preventDefault();
    e.stopPropagation();
    const move = (ev) => {
      const rect = wipeEl.getBoundingClientRect();
      if (rect.width <= 0) return;
      apply(((ev.clientX - rect.left) / rect.width) * 100);
    };
    const up = () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
    };
    move(e);
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
  };
  if (handleEl) {
    handleEl.addEventListener('pointerdown', startDrag);
    handleEl.addEventListener('click', e => e.stopPropagation());
  }
}

function renderPager(el, list) {
  const pages = Math.max(1, Math.ceil(list.length / pageSize) || 1);
  if (pageIndex >= pages) pageIndex = pages - 1;
  if (pageIndex < 0) pageIndex = 0;
  el.innerHTML = `
    <button type="button" data-go="first" ${pageIndex<=0?'disabled':''}>首页</button>
    <button type="button" data-go="prev" ${pageIndex<=0?'disabled':''}>上一页</button>
    <span class="page-info">第 ${pageIndex+1} / ${pages} 页 · 本页 ${Math.min(pageSize, Math.max(0, list.length - pageIndex*pageSize))} 条</span>
    <button type="button" data-go="next" ${pageIndex>=pages-1?'disabled':''}>下一页</button>
    <button type="button" data-go="last" ${pageIndex>=pages-1?'disabled':''}>末页</button>
    <label style="font-size:12px;color:#94a3b8">每页
      <select data-ps>
        <option value="12"${pageSize===12?' selected':''}>12</option>
        <option value="20"${pageSize===20?' selected':''}>20</option>
        <option value="40"${pageSize===40?' selected':''}>40</option>
      </select>
    </label>`;
  el.querySelectorAll('[data-go]').forEach(btn => {
    btn.onclick = () => {
      const pages2 = Math.max(1, Math.ceil(filtered().length / pageSize) || 1);
      const go = btn.getAttribute('data-go');
      if (go === 'first') pageIndex = 0;
      else if (go === 'prev') pageIndex = Math.max(0, pageIndex - 1);
      else if (go === 'next') pageIndex = Math.min(pages2 - 1, pageIndex + 1);
      else if (go === 'last') pageIndex = pages2 - 1;
      window.scrollTo({ top: 0, behavior: 'smooth' });
      render();
    };
  });
  const ps = el.querySelector('[data-ps]');
  if (ps) {
    ps.onchange = () => {
      pageSize = Number(ps.value) || 20;
      pageIndex = 0;
      render();
    };
  }
}

function render() {
  const list = filtered();
  const pages = Math.max(1, Math.ceil(list.length / pageSize) || 1);
  if (pageIndex >= pages) pageIndex = pages - 1;
  const start = pageIndex * pageSize;
  const pageItems = list.slice(start, start + pageSize);

  renderPager($('pagerTop'), list);
  renderPager($('pagerBottom'), list);

  const grid = $('grid');
  unloadGridImages(grid);
  if (!list.length) { grid.innerHTML = '<div class="empty">没有条目</div>'; refreshStats(); return; }
  for (const it of pageItems) {
    const d = state[it.id].discarded;
    const urls = pairUrls(it);
    const card = document.createElement('div');
    card.className = 'card' + (d ? ' discarded' : '');
    card.innerHTML = `
      <div class="wipe" style="--split:50%">
        <img src="${urls.ai}" alt="ai" loading="lazy" decoding="async">
        <img class="top" src="${urls.orig}" alt="orig" loading="lazy" decoding="async">
        <div class="handle" title="拖动分割线"></div>
        <input type="range" min="0" max="100" value="50">
      </div>
      <div class="meta">
        <span>${it.id}</span>
        <span class="tag ${it.category}">${it.category}</span>
      </div>
      <div class="meta" style="color:#94a3b8">${it.reason || ('cover ' + ((it.cover||0)*100).toFixed(0) + '%')}</div>
      <div class="actions">
        <button type="button" class="keep" data-act="keep">保留</button>
        <button type="button" class="dump" data-act="dump">废弃</button>
      </div>`;
    const wipe = card.querySelector('.wipe');
    const range = card.querySelector('input[type=range]');
    const handle = card.querySelector('.handle');
    bindWipe(wipe, range, handle);
    card.querySelector('[data-act=keep]').onclick = (e) => { e.stopPropagation(); state[it.id].discarded = false; render(); };
    card.querySelector('[data-act=dump]').onclick = (e) => { e.stopPropagation(); state[it.id].discarded = true; render(); };
    card.addEventListener('click', () => openLb(it));
    grid.appendChild(card);
  }
  refreshStats();
}

/* ===== lightbox zoom ===== */
let lbItem = null;
let scale = 1, tx = 0, ty = 0;
let dragging = false, lx = 0, ly = 0;
let splitting = false;

function applyView() {
  $('lbWorld').style.transform = `translate(calc(-50% + ${tx}px), calc(-50% + ${ty}px)) scale(${scale})`;
}

function loadLbImages(it) {
  const urls = pairUrls(it);
  const swap = $('lbSwap').checked;
  const base = $('lbBase');
  const over = $('lbOver');
  base.src = swap ? urls.orig : urls.ai;
  over.src = swap ? urls.ai : urls.orig;
  const fit = () => {
    const st = $('lbStage');
    const w = base.naturalWidth || 1;
    const h = base.naturalHeight || 1;
    const sw = st.clientWidth * 0.92;
    const sh = st.clientHeight * 0.92;
    const fitScale = Math.min(sw / w, sh / h, 1);
    scale = fitScale;
    tx = 0; ty = 0;
    $('lbWipe').style.width = w + 'px';
    $('lbWipe').style.height = h + 'px';
    over.style.width = w + 'px';
    over.style.height = h + 'px';
    applyView();
  };
  if (base.complete && base.naturalWidth) fit();
  else base.onload = fit;
}

function openLb(it) {
  lbItem = it;
  $('lbTitle').textContent = `${it.id} · ${it.category}`;
  $('lightbox').classList.add('show');
  $('lightbox').setAttribute('aria-hidden', 'false');
  setSplit(50);
  loadLbImages(it);
}

function closeLb() {
  $('lightbox').classList.remove('show');
  $('lightbox').setAttribute('aria-hidden', 'true');
  lbItem = null;
  splitting = false;
  // 释放大图解码内存
  $('lbBase').removeAttribute('src');
  $('lbBase').src = '';
  $('lbOver').removeAttribute('src');
  $('lbOver').src = '';
}

function setSplit(v) {
  const n = Math.max(0, Math.min(100, Number(v)));
  $('lbWipe').style.setProperty('--split', n + '%');
  $('lbSplit').value = String(Math.round(n));
}

function splitFromClientX(clientX) {
  const rect = $('lbWipe').getBoundingClientRect();
  if (rect.width <= 0) return 50;
  return ((clientX - rect.left) / rect.width) * 100;
}

$('lbHandle').addEventListener('pointerdown', (e) => {
  e.preventDefault();
  e.stopPropagation();
  splitting = true;
  dragging = false;
  setSplit(splitFromClientX(e.clientX));
  $('lbHandle').setPointerCapture(e.pointerId);
});
$('lbHandle').addEventListener('pointermove', (e) => {
  if (!splitting) return;
  e.preventDefault();
  e.stopPropagation();
  setSplit(splitFromClientX(e.clientX));
});
$('lbHandle').addEventListener('pointerup', (e) => {
  splitting = false;
  try { $('lbHandle').releasePointerCapture(e.pointerId); } catch (_) {}
});
$('lbHandle').addEventListener('pointercancel', () => { splitting = false; });

$('lbClose').onclick = closeLb;
$('lbZoomIn').onclick = () => { scale = Math.min(12, scale * 1.25); applyView(); };
$('lbZoomOut').onclick = () => { scale = Math.max(0.1, scale / 1.25); applyView(); };
$('lbZoomReset').onclick = () => { if (lbItem) loadLbImages(lbItem); };
$('lbSwap').onchange = () => { if (lbItem) loadLbImages(lbItem); };
$('lbSplit').oninput = () => setSplit($('lbSplit').value);
$('lbSplit').addEventListener('pointerdown', (e) => e.stopPropagation());
$('lbKeep').onclick = () => { if (!lbItem) return; state[lbItem.id].discarded = false; render(); };
$('lbDump').onclick = () => { if (!lbItem) return; state[lbItem.id].discarded = true; closeLb(); render(); };

const stage = $('lbStage');
stage.addEventListener('wheel', (e) => {
  if (!$('lightbox').classList.contains('show')) return;
  e.preventDefault();
  const rect = stage.getBoundingClientRect();
  const cx = e.clientX - rect.left - rect.width / 2;
  const cy = e.clientY - rect.top - rect.height / 2;
  const prev = scale;
  const factor = e.deltaY < 0 ? 1.12 : 1 / 1.12;
  scale = Math.min(12, Math.max(0.1, scale * factor));
  tx = cx - (cx - tx) * (scale / prev);
  ty = cy - (cy - ty) * (scale / prev);
  applyView();
}, { passive: false });

stage.addEventListener('pointerdown', (e) => {
  if (splitting) return;
  if (e.target && (e.target.id === 'lbHandle' || e.target.closest && e.target.closest('#lbHandle'))) return;
  if (e.target && e.target.tagName === 'INPUT') return;
  dragging = true;
  stage.classList.add('dragging');
  lx = e.clientX; ly = e.clientY;
  stage.setPointerCapture(e.pointerId);
});
stage.addEventListener('pointermove', (e) => {
  if (splitting || !dragging) return;
  tx += e.clientX - lx;
  ty += e.clientY - ly;
  lx = e.clientX; ly = e.clientY;
  applyView();
});
stage.addEventListener('pointerup', () => { dragging = false; stage.classList.remove('dragging'); });
stage.addEventListener('pointercancel', () => { dragging = false; stage.classList.remove('dragging'); });

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && $('lightbox').classList.contains('show')) closeLb();
});

$('filter').onchange = () => { pageIndex = 0; render(); };
$('btnKeepAllOk').onclick = () => {
  MANIFEST.forEach(it => { if (it.category === 'ok' || it.category === 'crop') state[it.id].discarded = false; });
  render();
};
$('btnDumpUn').onclick = () => {
  MANIFEST.forEach(it => { if (it.category === 'unaligned') state[it.id].discarded = true; });
  render();
};
$('btnExport').onclick = () => {
  const decisions = {
    generated_at: new Date().toISOString(),
    items: MANIFEST.map(it => ({
      id: it.id,
      style: it.style,
      category: it.category,
      discarded: !!state[it.id].discarded,
      orig_rel: it.orig_rel,
      ai_rel: it.ai_rel,
    })),
  };
  const blob = new Blob([JSON.stringify(decisions, null, 2)], {type:'application/json'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'decisions.json';
  a.click();
};
$('btnFinalizeHint').onclick = () => $('hintDlg').showModal();
render();
</script>
</body>
</html>
"""


def write_csv_rows_atomic(path: Path, fieldnames: list[str], rows: list[dict]) -> Path:
    """
    原子/可重试写 CSV。网络盘上文件被 Excel 占用时常见 PermissionError。
    策略：写临时文件 → replace；失败则重试；仍失败则改用带时间戳的新文件名。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")

    def _dump(target: Path) -> None:
        with target.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)

    last_err: Exception | None = None
    for attempt in range(6):
        try:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
            _dump(tmp)
            try:
                os.replace(str(tmp), str(path))
            except PermissionError:
                # 目标被锁：直接尝试覆盖写；再不行换新名
                try:
                    _dump(path)
                except PermissionError:
                    raise
            return path
        except PermissionError as e:
            last_err = e
            time.sleep(0.4 * (attempt + 1))
        except OSError as e:
            last_err = e
            time.sleep(0.4 * (attempt + 1))
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass

    alt = path.with_name(f"{path.stem}_{time.strftime('%Y%m%d_%H%M%S')}{path.suffix}")
    try:
        _dump(alt)
        print(f"警告: 无法写入 {path.name}（可能被 Excel 占用），已改写为 {alt.name}")
        return alt
    except Exception as e:
        raise PermissionError(
            f"无法写入 CSV: {path}\n"
            f"请关闭占用该文件的程序（如 Excel）后重试。\n原始错误: {last_err or e}"
        ) from e


def write_label_csv(out_dir: Path, items: list[dict], filename: str, *, usable_only: bool) -> Path:
    """
    扁平等对明细 CSV（一行一对），便于排查。
    usable_only=True 时仅 ok/crop 且未废弃。
    """
    path = out_dir / filename
    fields = [
        "id",
        "style",
        "category",
        "row_index",
        "output_col",
        "input_image_path",
        "output_image_path",
        "cover",
        "confidence",
        "inliers",
        "input_bytes",
        "orig_export_bytes",
        "output_export_bytes",
        "budget_bytes",
        "ref_jpeg_bytes",
        "output_format",
        "upscale_interp",
        "pixel_style_hit",
        "lineart_style_hit",
        "feature_used",
        "reason",
    ]
    rows = []
    for it in items:
        if not it.get("ok"):
            continue
        if it.get("discarded"):
            continue
        cat = it.get("category") or ""
        if usable_only and cat not in ("ok", "crop"):
            continue
        rows.append({
            "id": it.get("id", ""),
            "style": it.get("style", ""),
            "category": cat,
            "row_index": it.get("row_index", ""),
            "output_col": it.get("output_col", ""),
            "input_image_path": it.get("orig_rel", ""),
            "output_image_path": it.get("ai_rel", ""),
            "cover": it.get("cover", ""),
            "confidence": it.get("confidence", ""),
            "inliers": it.get("inliers", ""),
            "input_bytes": it.get("input_bytes", ""),
            "orig_export_bytes": it.get("orig_bytes", ""),
            "output_export_bytes": it.get("ai_bytes", ""),
            "budget_bytes": it.get("budget_bytes", ""),
            "ref_jpeg_bytes": it.get("ref_jpeg_bytes", ""),
            "output_format": it.get("output_format", ""),
            "upscale_interp": it.get("upscale_interp", ""),
            "pixel_style_hit": it.get("pixel_style_hit", ""),
            "lineart_style_hit": it.get("lineart_style_hit", ""),
            "feature_used": it.get("feature_used", ""),
            "reason": it.get("reason") or "",
        })
    return write_csv_rows_atomic(path, fields, rows)


def write_source_shaped_csv(
    out_dir: Path,
    items: list[dict],
    filename: str,
    source_fields: list[str],
    *,
    usable_only: bool,
) -> Path:
    """
    按原 CSV 表头写出：一行对应原 CSV 一行。
    - 单 output：input / output 一一对应
    - 多 output：保留 output_image_path / _1 / _2，路径改为 processed 结果
    """
    path = out_dir / filename
    fields = list(source_fields) if source_fields else [
        "input_image_path",
        "output_image_path",
    ]

    by_row: dict[int, list[dict]] = {}
    orphans: list[dict] = []
    for it in items:
        if not it.get("ok"):
            continue
        idx = it.get("row_index")
        if idx is None:
            orphans.append(it)
            continue
        by_row.setdefault(int(idx), []).append(it)

    def _apply_pair(row: dict, it: dict) -> bool:
        """写入一对路径；返回该对是否算 usable。"""
        if it.get("discarded"):
            return False
        cat = it.get("category") or ""
        usable = cat in ("ok", "crop")
        if usable_only and not usable:
            return False
        col = it.get("output_col") or "output_image_path"
        orig_rel = it.get("orig_rel") or ""
        ai_rel = it.get("ai_rel") or ""
        if "input_image_path" in row and orig_rel:
            row["input_image_path"] = orig_rel
        elif "input" in row and orig_rel:
            row["input"] = orig_rel
        if col in row:
            row[col] = ai_rel
        elif col == "output_image_path" and "output" in row:
            row["output"] = ai_rel
        elif col == "output_image_path" and "ai_output_image_path" in row:
            row["ai_output_image_path"] = ai_rel
        return usable

    out_rows: list[dict] = []
    for idx in sorted(by_row.keys()):
        group = by_row[idx]
        src = dict(group[0].get("source_row") or {})
        row = {k: src.get(k, "") for k in fields}
        # 先清空会改写的路径列，避免残留源路径
        for k in fields:
            if k in (
                "input_image_path",
                "input",
                "output_image_path",
                "output_image_path_1",
                "output_image_path_2",
                "output",
                "ai_output_image_path",
            ):
                row[k] = ""
        any_usable = False
        for it in group:
            if _apply_pair(row, it):
                any_usable = True
            elif not usable_only:
                # 全量模式：unaligned 也写入路径
                col = it.get("output_col") or "output_image_path"
                if it.get("orig_rel") and "input_image_path" in row and not row["input_image_path"]:
                    row["input_image_path"] = it["orig_rel"]
                if col in row and it.get("ai_rel"):
                    row[col] = it["ai_rel"]
                if it.get("category") in ("ok", "crop") and not it.get("discarded"):
                    any_usable = True
        if usable_only and not any_usable:
            continue
        # 候选：至少有一个可用 output；若某 output 列仍空（失败/废弃）保持空
        if usable_only:
            # 去掉完全没有 output 路径的行
            has_out = any(
                (row.get(k) or "").strip()
                for k in (
                    "output_image_path",
                    "output_image_path_1",
                    "output_image_path_2",
                    "output",
                    "ai_output_image_path",
                )
                if k in row
            )
            if not has_out:
                continue
        out_rows.append(row)

    # 无 row_index 的旧结果：退回扁平行（仍尽量填原表头）
    for it in orphans:
        if it.get("discarded"):
            continue
        cat = it.get("category") or ""
        if usable_only and cat not in ("ok", "crop"):
            continue
        row = {k: "" for k in fields}
        if "input_image_path" in row:
            row["input_image_path"] = it.get("orig_rel", "")
        if "output_image_path" in row:
            row["output_image_path"] = it.get("ai_rel", "")
        out_rows.append(row)

    return write_csv_rows_atomic(path, fields, out_rows)


def write_review_html(out_dir: Path, items: list[dict]) -> Path:
    review_dir = out_dir / "review"
    review_dir.mkdir(parents=True, exist_ok=True)
    slim = []
    for it in items:
        if not it.get("ok"):
            continue
        orig_rel = it.get("orig_rel", "")
        ai_rel = it.get("ai_rel", "")
        # 审核对比用送标 JPG（不再用 vis_png）
        slim.append({
            "id": it["id"],
            "style": it.get("style", ""),
            "category": it.get("category", "unaligned"),
            "reason": it.get("reason") or "",
            "cover": it.get("cover", 0),
            "inliers": it.get("inliers", 0),
            "orig_rel": orig_rel,
            "ai_rel": ai_rel,
            "vis_png": "",
            "review_orig": it.get("review_orig") or orig_rel,
            "review_ai": ai_rel,
            "discarded": False,
        })
    html = REVIEW_HTML.replace("/*__MANIFEST__*/[]", json.dumps(slim, ensure_ascii=False))
    path = review_dir / "index.html"
    path.write_text(html, encoding="utf-8")
    (review_dir / "manifest.json").write_text(json.dumps(slim, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def rebuild_review(out_dir: Path) -> Path:
    results_path = out_dir / "results.json"
    if not results_path.is_file():
        raise SystemExit(f"缺少 results.json: {results_path}")
    results = json.loads(results_path.read_text(encoding="utf-8"))
    for it in results:
        if not it.get("ok"):
            continue
        if not it.get("review_orig"):
            it["review_orig"] = it.get("orig_rel", "")
        if not it.get("review_ai"):
            it["review_ai"] = it.get("ai_rel", "")
    path = write_review_html(out_dir, results)
    print(f"已重建审核页: {path}")
    return path


def run_batch(
    csv_path: Path,
    root: Path,
    out_dir: Path,
    style: str,
    workers: int,
    limit: int = 0,
    pixel_rule: dict | None = None,
    lineart_rule: dict | None = None,
    upscale_override: str = "",
) -> None:
    upscale_override = (upscale_override or "").strip().lower()
    if upscale_override and upscale_override not in _INTERP_BY_NAME:
        raise SystemExit(f"--upscale-interp 只能是 nearest/linear/cubic/lanczos，收到: {upscale_override}")
    pixel_rule = pixel_rule or load_pixel_rule()
    lineart_rule = lineart_rule or load_lineart_rule()
    source_fields, rows = read_csv_bundle(csv_path)
    print(f"CSV: {csv_path}")
    print(f"相对路径根目录: {root}")
    print(f"行数: {len(rows)} · 表头: {source_fields}")
    print(f"风格类别: {style}")
    print(f"输出: {out_dir}")
    print(f"体积口径: ok≤JPEG(q92)×{SIZE_BUDGET_RATIO_OK:.0%}；crop/unaligned≤×{SIZE_BUDGET_RATIO:.0%}；同比例不压分辨率")
    print("每行将对 output_image_path / _1 / _2（非空列）分别与 input 对齐；导出 CSV 对齐原表头")
    print(
        f"像素风规则: mode={pixel_rule['mode']} · 标签字段={pixel_rule['tag_fields']} · "
        f"标签关键词={pixel_rule['tag_keywords']} · prompt 短语={pixel_rule['prompt_keywords']}"
    )
    print(
        f"白描/线稿规则: mode={lineart_rule['mode']} · 标签关键词={lineart_rule['tag_keywords']} · "
        f"prompt 短语={lineart_rule['prompt_keywords']} → 命中时放大用双三次(INTER_CUBIC)、特征匹配灰度/梯度两路取优"
    )
    if upscale_override:
        print(f"放大插值全局覆盖(--upscale-interp): {upscale_override} → 本批所有配对放大时统一用该插值（优先于像素风/白描规则；缩小仍双线性）")

    jobs = []
    skip = 0
    miss = 0
    for row_index, r in enumerate(rows):
        expanded = resolve_jobs_from_row(root, r)
        if not expanded:
            skip += 1
            continue
        src_snap = {k: (r.get(k) if r.get(k) is not None else "") for k in source_fields}
        pixel_hit = detect_pixel_style(r, style, pixel_rule)
        lineart_hit = "" if pixel_hit else detect_pixel_style(r, style, lineart_rule, "forced(--lineart-mode on)")
        for j in expanded:
            if j.get("missing"):
                miss += 1
                continue
            jobs.append({
                "id": j["id"],
                "style": style,
                "orig": j["orig"],
                "ai": j["ai"],
                "out_dir": str(out_dir),
                "output_col": j.get("output_col", ""),
                "row_index": row_index,
                "source_row": src_snap,
                "pixel_hit": pixel_hit,
                "lineart_hit": lineart_hit,
                "upscale_override": upscale_override,
            })
            if limit and len(jobs) >= limit:
                break
        if limit and len(jobs) >= limit:
            break

    print(f"可处理 {len(jobs)} 对 · 跳过行 {skip} · 缺文件配对 {miss}")
    n_pixel = sum(1 for j in jobs if j.get("pixel_hit"))
    if n_pixel:
        hits = sorted({j["pixel_hit"] for j in jobs if j.get("pixel_hit")})
        print(
            f"像素风命中 {n_pixel}/{len(jobs)} 对 → 放大用最近邻(INTER_NEAREST)；"
            f"命中依据: {hits[:6]}{' …' if len(hits) > 6 else ''}"
        )
    else:
        print("像素风命中 0 对 → 全部沿用双线性放大（与旧行为一致）")
    n_line = sum(1 for j in jobs if j.get("lineart_hit"))
    if n_line:
        hits = sorted({j["lineart_hit"] for j in jobs if j.get("lineart_hit")})
        print(
            f"白描/线稿命中 {n_line}/{len(jobs)} 对 → 放大用双三次(INTER_CUBIC)，特征匹配灰度/梯度取优；"
            f"命中依据: {hits[:6]}{' …' if len(hits) > 6 else ''}"
        )
    if not jobs:
        raise SystemExit("没有可处理的配对")

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "processed").mkdir(exist_ok=True)
    (out_dir / "source_meta.json").write_text(
        json.dumps(
            {
                "source_csv": str(csv_path),
                "source_fields": source_fields,
                "pixel_rule": pixel_rule,
                "pixel_hits": n_pixel,
                "lineart_rule": lineart_rule,
                "lineart_hits": n_line,
                "upscale_override": upscale_override,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    results: list[dict] = []
    t0 = time.time()
    workers = max(1, workers)
    print(f"并发 workers={workers}")

    if workers == 1:  # SEQ_MODE_PATCH
        done = 0
        for job in jobs:
            res = _worker(job)
            results.append(res)
            done += 1
            if done % 10 == 0 or done == len(jobs):
                elapsed = time.time() - t0
                rate = done / max(1e-6, elapsed)
                print(f'  [{done}/{len(jobs)}] {rate:.2f} 对/秒 · 最近: {res.get("id")} ({res.get("category")})')
    else:
        with ProcessPoolExecutor(max_workers=workers, mp_context=__import__('multiprocessing').get_context('spawn')) as ex:
            futs = {ex.submit(_worker, job): job["id"] for job in jobs}
            done = 0
            for fut in as_completed(futs):
                done += 1
                res = fut.result()
                results.append(res)
                if done % 10 == 0 or done == len(jobs):
                    elapsed = time.time() - t0
                    rate = done / max(1e-6, elapsed)
                    print(f"  [{done}/{len(jobs)}] {rate:.2f} 对/秒 · 最近: {res.get('id')} ({res.get('category')})")


    results.sort(key=lambda x: (x.get("row_index") is None, x.get("row_index", 0), str(x.get("id", ""))))
    (out_dir / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    review = write_review_html(out_dir, results)
    # 对齐原表头的送标 CSV + 扁平等对明细（写失败不丢整批结果）
    cand = all_csv = pair_csv = None
    try:
        cand = write_source_shaped_csv(
            out_dir, results, "aligned_candidates.csv", source_fields, usable_only=True
        )
    except Exception as e:
        print(f"警告: 写入 aligned_candidates.csv 失败: {e}")
    try:
        all_csv = write_source_shaped_csv(
            out_dir, results, "align_all_results.csv", source_fields, usable_only=False
        )
    except Exception as e:
        print(f"警告: 写入 align_all_results.csv 失败: {e}")
    try:
        pair_csv = write_label_csv(out_dir, results, "align_pair_details.csv", usable_only=False)
    except Exception as e:
        print(f"警告: 写入 align_pair_details.csv 失败: {e}")
        print("  （图片与 results.json / 审核页已生成；请关闭 Excel 占用后执行 --rebuild-csv）")

    ok = sum(1 for x in results if x.get("category") == "ok")
    crop = sum(1 for x in results if x.get("category") == "crop")
    un = sum(1 for x in results if x.get("category") == "unaligned")
    print("\n完成")
    print(f"  ok={ok} crop={crop} unaligned={un}")
    n_near = sum(1 for x in results if x.get("upscale_interp") == "nearest")
    n_cubic = sum(1 for x in results if x.get("upscale_interp") == "cubic")
    n_lanczos = sum(1 for x in results if x.get("upscale_interp") == "lanczos")
    print(f"  放大插值: 最近邻 {n_near} 对 · 双三次 {n_cubic} 对 · Lanczos {n_lanczos} 对 · 双线性 {len(results) - n_near - n_cubic - n_lanczos} 对"
          + (f"（--upscale-interp {upscale_override} 全局覆盖）" if upscale_override else ""))
    if n_cubic:
        n_edge = sum(1 for x in results if x.get("feature_used") == "edge")
        print(f"  白描/线稿特征域: 梯度图胜出 {n_edge} 对 · 灰度 {n_cubic - n_edge} 对")
    print(f"  图片目录: {out_dir / 'processed'}")
    print(f"  候选送标 CSV（原表头）: {cand}")
    print(f"  全量结果 CSV（原表头）: {all_csv}")
    print(f"  等对明细 CSV: {pair_csv}")
    print(f"  审核页: {review}")
    print("  审核后下载 decisions.json，再运行:")
    print(f'  python align_batch.py --finalize --out "{out_dir}"')


def rebuild_csv(out_dir: Path) -> None:
    """根据已有 results.json / source_meta.json 重写 CSV（无需重跑对齐）。"""
    results_path = out_dir / "results.json"
    if not results_path.is_file():
        raise SystemExit(f"缺少 results.json: {results_path}")
    results = json.loads(results_path.read_text(encoding="utf-8"))
    source_fields: list[str] = []
    meta_path = out_dir / "source_meta.json"
    if meta_path.is_file():
        try:
            source_fields = list(json.loads(meta_path.read_text(encoding="utf-8")).get("source_fields") or [])
        except Exception:
            source_fields = []
    if not source_fields:
        for it in results:
            src = it.get("source_row") or {}
            if src:
                source_fields = list(src.keys())
                break
    if not source_fields:
        source_fields = ["input_image_path", "output_image_path"]
    cand = write_source_shaped_csv(
        out_dir, results, "aligned_candidates.csv", source_fields, usable_only=True
    )
    all_csv = write_source_shaped_csv(
        out_dir, results, "align_all_results.csv", source_fields, usable_only=False
    )
    pair_csv = write_label_csv(out_dir, results, "align_pair_details.csv", usable_only=False)
    print(f"已重写:\n  {cand}\n  {all_csv}\n  {pair_csv}")


def finalize(out_dir: Path, decisions_path: Path | None) -> None:
    """按审核结果写出最终送标 CSV（相对路径）；文件已在 processed/ 无需再分文件夹。"""
    dec_path = decisions_path or (out_dir / "decisions.json")
    if not dec_path.is_file():
        alt = out_dir / "review" / "decisions.json"
        if alt.is_file():
            dec_path = alt
        else:
            raise SystemExit(f"找不到 decisions.json，请先从审核页下载并放到:\n  {out_dir}\\decisions.json")

    data = json.loads(dec_path.read_text(encoding="utf-8"))
    decisions = data.get("items") or data
    by_id = {str(it.get("id")): it for it in decisions}

    # 合并 results.json 中的路径与体积信息
    results_path = out_dir / "results.json"
    if not results_path.is_file():
        raise SystemExit(f"缺少 results.json: {results_path}")
    results = json.loads(results_path.read_text(encoding="utf-8"))

    merged = []
    n_dump = 0
    for it in results:
        if not it.get("ok"):
            continue
        d = by_id.get(str(it.get("id")), {})
        discarded = bool(d.get("discarded", False))
        row = dict(it)
        row["discarded"] = discarded
        if discarded:
            n_dump += 1
        merged.append(row)

    source_fields: list[str] = []
    meta_path = out_dir / "source_meta.json"
    if meta_path.is_file():
        try:
            source_fields = list(json.loads(meta_path.read_text(encoding="utf-8")).get("source_fields") or [])
        except Exception:
            source_fields = []
    if not source_fields:
        for it in merged:
            src = it.get("source_row") or {}
            if src:
                source_fields = list(src.keys())
                break
    if not source_fields:
        source_fields = ["input_image_path", "output_image_path"]

    final_csv = write_source_shaped_csv(
        out_dir,
        merged,
        "aligned_for_label.csv",
        source_fields,
        usable_only=True,
    )
    write_label_csv(out_dir, merged, "align_pair_details_final.csv", usable_only=True)
    # 也可写出废弃清单便于追溯
    dumped = [x for x in merged if x.get("discarded")]
    dump_csv = out_dir / "discarded.csv"
    with dump_csv.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["id", "style", "category", "input_image_path", "output_image_path", "reason"])
        w.writeheader()
        for it in dumped:
            w.writerow({
                "id": it.get("id", ""),
                "style": it.get("style", ""),
                "category": it.get("category", ""),
                "input_image_path": it.get("orig_rel", ""),
                "output_image_path": it.get("ai_rel", ""),
                "reason": it.get("reason") or "discarded_in_review",
            })

    n_keep = sum(1 for x in merged if not x.get("discarded") and x.get("category") in ("ok", "crop"))
    print(f"最终送标: 可用等对 {n_keep} · 废弃 {n_dump}")
    print(f"CSV（原表头）: {final_csv}")
    print("表头与源 CSV 对齐；多 output 仍为多列")
    print(f"图片仍在: {out_dir / 'processed'}  （命名 {{id}}_input_{{cat}}.* / {{id}}_output_{{cat}}.jpg）")
    print(f"废弃清单: {dump_csv}")


def main() -> int:
    silence_libpng_warnings()
    ap = argparse.ArgumentParser(description="图生图对齐批处理 + 审核导出")
    ap.add_argument("--csv", default="", help="输入配对 CSV 路径（相对路径以 CSV 所在目录为根）")
    ap.add_argument("--root", default="", help="可选：相对路径根目录（默认=CSV 所在目录）；兼容旧用法时也可只传目录")
    ap.add_argument("--out", default="", help="输出目录（默认 CSV目录/{csv名}_align_out）")
    ap.add_argument("--style", default="", help="风格类别名（默认从 CSV 所在文件夹名推断）")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 4) - 1))
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 对（试跑）")
    ap.add_argument(
        "--pixel-mode",
        default="",
        choices=["", "auto", "on", "off"],
        help="像素风规则：auto=按标签/prompt 自动判断（默认，见 defaults.json）| on=全部最近邻放大 | off=关闭",
    )
    ap.add_argument("--pixel-tags", default="", help="覆盖像素风标签关键词，逗号分隔，如 像素,pixel")
    ap.add_argument(
        "--lineart-mode",
        default="",
        choices=["", "auto", "on", "off"],
        help="白描/线稿规则：auto=按标签/prompt 自动判断（默认，见 defaults.json）| on=全部双三次放大+梯度匹配 | off=关闭",
    )
    ap.add_argument("--lineart-tags", default="", help="覆盖白描/线稿标签关键词，逗号分隔，如 白描,线稿")
    ap.add_argument(
        "--upscale-interp",
        default="",
        choices=["", "nearest", "linear", "cubic", "lanczos"],
        help="全局覆盖放大插值（优先于像素风/白描规则）：新海诚/赛璐璐等细线平涂风格建议 cubic；不传则按规则自动",
    )
    ap.add_argument("--finalize", action="store_true", help="按 decisions.json 生成 aligned_for_label.csv")
    ap.add_argument("--rebuild-review", action="store_true", help="仅根据 results.json 重建 review/index.html")
    ap.add_argument("--rebuild-csv", action="store_true", help="仅根据 results.json 重写送标/明细 CSV（无需重跑对齐）")
    ap.add_argument("--decisions", default="", help="decisions.json 路径")
    args = ap.parse_args()

    if args.rebuild_review:
        out = Path(args.out).expanduser().resolve() if args.out else None
        if not out:
            raise SystemExit("rebuild-review 需要 --out 指向批处理输出目录")
        rebuild_review(out)
        return 0

    if args.rebuild_csv:
        out = Path(args.out).expanduser().resolve() if args.out else None
        if not out:
            raise SystemExit("rebuild-csv 需要 --out 指向批处理输出目录")
        rebuild_csv(out)
        return 0

    if args.finalize:
        out = Path(args.out).expanduser().resolve() if args.out else None
        if not out:
            raise SystemExit("finalize 需要 --out 指向批处理输出目录")
        finalize(out, Path(args.decisions).expanduser().resolve() if args.decisions else None)
        return 0

    csv_s = (args.csv or "").strip().strip('"')
    root_s = (args.root or "").strip().strip('"')

    if not csv_s and not root_s:
        print("请输入配对 CSV 路径（可拖拽文件），或把路径作为 --csv 传入：")
        try:
            csv_s = input("> ").strip().strip('"')
        except EOFError:
            csv_s = ""
        if not csv_s:
            raise SystemExit("未提供 --csv")

    # 兼容：只传了目录 → 在目录内找 CSV
    if csv_s and Path(csv_s).is_dir() and not root_s:
        root_s = csv_s
        csv_s = ""

    if not csv_s and root_s:
        root = Path(root_s).expanduser()
        if not root.is_dir():
            raise SystemExit(f"目录不存在: {root}")
        csv_path = find_csv(root)
    else:
        csv_path = Path(csv_s).expanduser()
        if not csv_path.is_file():
            raise SystemExit(f"CSV 不存在: {csv_path}")
        root = Path(root_s).expanduser() if root_s else csv_path.parent

    if not root.is_dir():
        raise SystemExit(f"相对路径根目录不存在: {root}")

    out = Path(args.out).expanduser().resolve() if args.out else (csv_path.parent / f"{csv_path.stem}_align_out")
    style = infer_style(root, args.style)
    run_batch(
        csv_path, root, out, style, args.workers, args.limit,
        load_pixel_rule(args.pixel_mode, args.pixel_tags),
        load_lineart_rule(args.lineart_mode, args.lineart_tags),
        args.upscale_interp,
    )
    return 0


if __name__ == "__main__":
    # Windows 多进程需要
    raise SystemExit(main())