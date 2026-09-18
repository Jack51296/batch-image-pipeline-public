# -*- coding: utf-8 -*-
"""给 lighting-destyle-align 加「像素风放大用最近邻」规则。
用法: python patch_align_pixel_rule.py <skill_dir>
对 align_core.py / align_batch.py 做精确锚点替换（每个锚点必须恰好出现预期次数），
新建 defaults.json。保留每个文件原有的换行风格（CRLF/LF）。可重复运行（已打过补丁会跳过）。
"""
import hashlib
import sys
from pathlib import Path

skill = Path(sys.argv[1]).resolve()
assert (skill / "align_core.py").is_file() and (skill / "align_batch.py").is_file(), skill

MARK_CORE = "def _pick_interp("
MARK_BATCH = "def detect_pixel_style("


def apply(path: Path, edits, mark: str) -> str:
    raw = path.read_bytes()
    crlf = b"\r\n" in raw
    text = raw.decode("utf-8").replace("\r\n", "\n")
    if mark in text:
        print(f"skip (already patched): {path.name}")
        return hashlib.md5(raw).hexdigest()
    for old, new, count in edits:
        n = text.count(old)
        assert n == count, f"{path.name}: anchor count {n} != {count}:\n{old[:120]}"
        text = text.replace(old, new)
    out = text.replace("\n", "\r\n") if crlf else text
    path.write_bytes(out.encode("utf-8"))
    md5 = hashlib.md5(path.read_bytes()).hexdigest()
    print(f"patched: {path.name}  edits={len(edits)}  eol={'CRLF' if crlf else 'LF'}  md5={md5}")
    return md5


# ---------------------------------------------------------------- align_core.py
CORE_EDITS = [
    (
        '"""几何对齐核心：ORB + 相似变换（缩放/旋转/平移），禁止补边。"""\n',
        '"""几何对齐核心：ORB + 相似变换（缩放/旋转/平移），禁止补边。\n'
        '\n'
        '放大插值可选：upscale_interp 默认 cv2.INTER_LINEAR（旧行为）；像素风批次由 align_batch.py\n'
        '传入 cv2.INTER_NEAREST，且仅在「AI 图需要放大」时生效，缩小仍用双线性。\n'
        '"""\n',
        1,
    ),
    (
        "def _orb_matches(ai_gray: np.ndarray, orig_gray: np.ndarray, max_feat: int = 2500):\n",
        "def _pick_interp(src_w: float, src_h: float, dst_w: int, dst_h: int, upscale_interp: int) -> int:\n"
        '    """目标像素数多于源像素数（放大）时用 upscale_interp，否则保持双线性（旧行为）。"""\n'
        "    if upscale_interp == cv2.INTER_LINEAR:\n"
        "        return cv2.INTER_LINEAR\n"
        "    if dst_w * dst_h > max(1.0, float(src_w) * float(src_h)):\n"
        "        return int(upscale_interp)\n"
        "    return cv2.INTER_LINEAR\n"
        "\n"
        "\n"
        "def _orb_matches(ai_gray: np.ndarray, orig_gray: np.ndarray, max_feat: int = 2500):\n",
        1,
    ),
    (
        "    out_w: int,\n"
        "    out_h: int,\n"
        ") -> np.ndarray:\n"
        '    """把 AI 按 inv 采样到原图矩形 R，输出 out_w×out_h。禁止依赖黑边补全。"""\n',
        "    out_w: int,\n"
        "    out_h: int,\n"
        "    upscale_interp: int = cv2.INTER_LINEAR,\n"
        ") -> np.ndarray:\n"
        '    """把 AI 按 inv 采样到原图矩形 R，输出 out_w×out_h。禁止依赖黑边补全。"""\n'
        "    # 原图矩形 R 映到 AI 空间约为 rw*s × rh*s（s 为 inv 的等比缩放因子）；据此判断是否放大\n"
        "    det = float(inv[0, 0] * inv[1, 1] - inv[0, 1] * inv[1, 0])\n"
        "    s = float(np.sqrt(abs(det)))\n"
        "    interp = _pick_interp(rw * s, rh * s, out_w, out_h, upscale_interp)\n",
        1,
    ),
    (
        "        interpolation=cv2.INTER_LINEAR,\n"
        "        borderMode=cv2.BORDER_CONSTANT,\n",
        "        interpolation=interp,\n"
        "        borderMode=cv2.BORDER_CONSTANT,\n",
        1,
    ),
    (
        "def _passthrough_ok(orig_bgr: np.ndarray, ai_bgr: np.ndarray, reason: str) -> AlignResult:\n",
        "def _passthrough_ok(\n"
        "    orig_bgr: np.ndarray, ai_bgr: np.ndarray, reason: str, *, upscale_interp: int = cv2.INTER_LINEAR\n"
        ") -> AlignResult:\n",
        1,
    ),
    (
        "        ai_out = cv2.resize(ai_bgr, (Ow, Oh), interpolation=cv2.INTER_LINEAR)\n",
        "        ai_out = cv2.resize(ai_bgr, (Ow, Oh), interpolation=_pick_interp(Aw, Ah, Ow, Oh, upscale_interp))\n",
        1,
    ),
    (
        "def align_images(orig_bgr: np.ndarray, ai_bgr: np.ndarray) -> AlignResult:\n",
        "def align_images(\n"
        "    orig_bgr: np.ndarray, ai_bgr: np.ndarray, *, upscale_interp: int = cv2.INTER_LINEAR\n"
        ") -> AlignResult:\n",
        1,
    ),
    (
        '                reason=f"同尺寸且内容已对齐(NCC={ncc:.4f})，直出",\n'
        "            )\n",
        '                reason=f"同尺寸且内容已对齐(NCC={ncc:.4f})，直出",\n'
        "                upscale_interp=upscale_interp,\n"
        "            )\n",
        1,
    ),
    (
        '            reason="估计变换接近单位阵，跳过几何变换，避免误扭曲",\n'
        "        )\n",
        '            reason="估计变换接近单位阵，跳过几何变换，避免误扭曲",\n'
        "            upscale_interp=upscale_interp,\n"
        "        )\n",
        1,
    ),
    (
        "_remap_ai_to_rect(ai_bgr, inv, rx, ry, rw, rh, out_w, out_h)\n",
        "_remap_ai_to_rect(ai_bgr, inv, rx, ry, rw, rh, out_w, out_h, upscale_interp)\n",
        2,
    ),
    (
        "def align_pair_paths(orig_path: str, ai_path: str) -> AlignResult:\n",
        "def align_pair_paths(orig_path: str, ai_path: str, *, upscale_interp: int = cv2.INTER_LINEAR) -> AlignResult:\n",
        1,
    ),
    (
        "    return align_images(orig, ai)\n",
        "    return align_images(orig, ai, upscale_interp=upscale_interp)\n",
        1,
    ),
]

# ---------------------------------------------------------------- align_batch.py
PIXEL_BLOCK = '''

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


def detect_pixel_style(row: dict, style: str, rule: dict) -> str:
    """
    返回命中说明（如 '标签=像素' / 'prompt~像素画'），未命中返回 ''。
    标签类字段做关键词子串匹配；prompt 只匹配更强的短语（像素画/像素风/pixel art…），
    避免“不改变像素尺寸”这类描述误伤其它风格。
    """
    mode = rule.get("mode", "auto")
    if mode == "off":
        return ""
    if mode == "on":
        return "forced(--pixel-mode on)"
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
'''

BATCH_EDITS = [
    (
        "图生图对齐批处理（并发）+ 可视化审核 + 送标 CSV。\n"
        "\n"
        "导出规则：\n",
        "图生图对齐批处理（并发）+ 可视化审核 + 送标 CSV。\n"
        "\n"
        "像素风规则：标签/prompt 命中「像素」类关键词的配对，放大 AI 图改用最近邻（见 defaults.json / --pixel-mode）；\n"
        "            其它风格不受影响，导出逐字节与旧版一致。\n"
        "\n"
        "导出规则：\n",
        1,
    ),
    (
        "from align_core import align_pair_paths  # noqa: E402\n",
        "from align_core import align_pair_paths  # noqa: E402\n" + PIXEL_BLOCK,
        1,
    ),
    (
        "    meta_extra = {\n"
        '        "row_index": job.get("row_index"),\n'
        '        "output_col": job.get("output_col", ""),\n'
        '        "source_row": job.get("source_row") or {},\n'
        "    }\n"
        "    try:\n"
        "        orig_p = Path(orig_path)\n"
        "        input_bytes = orig_p.stat().st_size\n"
        "        result = align_pair_paths(orig_path, ai_path)\n",
        '    pixel_hit = str(job.get("pixel_hit") or "")\n'
        "    upscale_interp = cv2.INTER_NEAREST if pixel_hit else cv2.INTER_LINEAR\n"
        "    meta_extra = {\n"
        '        "row_index": job.get("row_index"),\n'
        '        "output_col": job.get("output_col", ""),\n'
        '        "source_row": job.get("source_row") or {},\n'
        '        "upscale_interp": "nearest" if pixel_hit else "linear",\n'
        '        "pixel_style_hit": pixel_hit,\n'
        "    }\n"
        "    try:\n"
        "        orig_p = Path(orig_path)\n"
        "        input_bytes = orig_p.stat().st_size\n"
        "        result = align_pair_paths(orig_path, ai_path, upscale_interp=upscale_interp)\n",
        1,
    ),
    (
        '        "output_format",\n'
        '        "reason",\n'
        "    ]\n",
        '        "output_format",\n'
        '        "upscale_interp",\n'
        '        "pixel_style_hit",\n'
        '        "reason",\n'
        "    ]\n",
        1,
    ),
    (
        '            "output_format": it.get("output_format", ""),\n'
        '            "reason": it.get("reason") or "",\n',
        '            "output_format": it.get("output_format", ""),\n'
        '            "upscale_interp": it.get("upscale_interp", ""),\n'
        '            "pixel_style_hit": it.get("pixel_style_hit", ""),\n'
        '            "reason": it.get("reason") or "",\n',
        1,
    ),
    (
        "def run_batch(csv_path: Path, root: Path, out_dir: Path, style: str, workers: int, limit: int = 0) -> None:\n"
        "    source_fields, rows = read_csv_bundle(csv_path)\n",
        "def run_batch(\n"
        "    csv_path: Path,\n"
        "    root: Path,\n"
        "    out_dir: Path,\n"
        "    style: str,\n"
        "    workers: int,\n"
        "    limit: int = 0,\n"
        "    pixel_rule: dict | None = None,\n"
        ") -> None:\n"
        "    pixel_rule = pixel_rule or load_pixel_rule()\n"
        "    source_fields, rows = read_csv_bundle(csv_path)\n",
        1,
    ),
    (
        '    print("每行将对 output_image_path / _1 / _2（非空列）分别与 input 对齐；导出 CSV 对齐原表头")\n',
        '    print("每行将对 output_image_path / _1 / _2（非空列）分别与 input 对齐；导出 CSV 对齐原表头")\n'
        "    print(\n"
        "        f\"像素风规则: mode={pixel_rule['mode']} · 标签字段={pixel_rule['tag_fields']} · \"\n"
        "        f\"标签关键词={pixel_rule['tag_keywords']} · prompt 短语={pixel_rule['prompt_keywords']}\"\n"
        "    )\n",
        1,
    ),
    (
        '        src_snap = {k: (r.get(k) if r.get(k) is not None else "") for k in source_fields}\n'
        "        for j in expanded:\n",
        '        src_snap = {k: (r.get(k) if r.get(k) is not None else "") for k in source_fields}\n'
        "        pixel_hit = detect_pixel_style(r, style, pixel_rule)\n"
        "        for j in expanded:\n",
        1,
    ),
    (
        '                "row_index": row_index,\n'
        '                "source_row": src_snap,\n'
        "            })\n",
        '                "row_index": row_index,\n'
        '                "source_row": src_snap,\n'
        '                "pixel_hit": pixel_hit,\n'
        "            })\n",
        1,
    ),
    (
        '    print(f"可处理 {len(jobs)} 对 · 跳过行 {skip} · 缺文件配对 {miss}")\n',
        '    print(f"可处理 {len(jobs)} 对 · 跳过行 {skip} · 缺文件配对 {miss}")\n'
        '    n_pixel = sum(1 for j in jobs if j.get("pixel_hit"))\n'
        "    if n_pixel:\n"
        '        hits = sorted({j["pixel_hit"] for j in jobs if j.get("pixel_hit")})\n'
        "        print(\n"
        '            f"像素风命中 {n_pixel}/{len(jobs)} 对 → 放大用最近邻(INTER_NEAREST)；"\n'
        "            f\"命中依据: {hits[:6]}{' …' if len(hits) > 6 else ''}\"\n"
        "        )\n"
        "    else:\n"
        '        print("像素风命中 0 对 → 全部沿用双线性放大（与旧行为一致）")\n',
        1,
    ),
    (
        '            {"source_csv": str(csv_path), "source_fields": source_fields},\n',
        "            {\n"
        '                "source_csv": str(csv_path),\n'
        '                "source_fields": source_fields,\n'
        '                "pixel_rule": pixel_rule,\n'
        '                "pixel_hits": n_pixel,\n'
        "            },\n",
        1,
    ),
    (
        '    print(f"  ok={ok} crop={crop} unaligned={un}")\n',
        '    print(f"  ok={ok} crop={crop} unaligned={un}")\n'
        '    n_near = sum(1 for x in results if x.get("upscale_interp") == "nearest")\n'
        '    print(f"  放大插值: 最近邻(像素风) {n_near} 对 · 双线性 {len(results) - n_near} 对")\n',
        1,
    ),
    (
        '    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 对（试跑）")\n',
        '    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 对（试跑）")\n'
        '    ap.add_argument(\n'
        '        "--pixel-mode",\n'
        '        default="",\n'
        '        choices=["", "auto", "on", "off"],\n'
        '        help="像素风规则：auto=按标签/prompt 自动判断（默认，见 defaults.json）| on=全部最近邻放大 | off=关闭",\n'
        "    )\n"
        '    ap.add_argument("--pixel-tags", default="", help="覆盖像素风标签关键词，逗号分隔，如 像素,pixel")\n',
        1,
    ),
    (
        "    run_batch(csv_path, root, out, style, args.workers, args.limit)\n",
        "    run_batch(csv_path, root, out, style, args.workers, args.limit, load_pixel_rule(args.pixel_mode, args.pixel_tags))\n",
        1,
    ),
]

DEFAULTS_JSON = '''{
  "_comment": "像素风规则：标签/prompt 命中下列关键词的配对，对齐导出时放大 AI 图改用最近邻(INTER_NEAREST)，保持像素块硬边；未命中的沿用双线性，与旧行为一致。mode: auto=按标签/prompt 自动判断 | on=全部最近邻 | off=关闭。tag_fields 做子串匹配（如 标签=像素）；prompt 只匹配 prompt_keywords 里的强短语，避免“不改变像素尺寸”之类误伤。命令行 --pixel-mode / --pixel-tags 可临时覆盖。",
  "pixel_style": {
    "mode": "auto",
    "tag_fields": ["标签", "tag", "light_type", "prompt_target", "target_style", "style", "aigc生成风格"],
    "tag_keywords": ["像素", "pixel", "8bit", "8-bit", "16bit", "16-bit", "点阵"],
    "prompt_fields": ["prompt"],
    "prompt_keywords": ["像素画", "像素风", "像素艺术", "pixel art", "pixel-art", "pixelart", "8-bit", "16-bit", "点阵"]
  }
}
'''

apply(skill / "align_core.py", CORE_EDITS, MARK_CORE)
apply(skill / "align_batch.py", BATCH_EDITS, MARK_BATCH)
dj = skill / "defaults.json"
if dj.exists():
    print(f"skip (exists): {dj.name}")
else:
    dj.write_text(DEFAULTS_JSON, encoding="utf-8")
    print(f"created: {dj.name}")
print("done", skill)
