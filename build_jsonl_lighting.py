# -*- coding: utf-8 -*-
"""
按 lighting-batch-jsonl skill 为光影批次生成 Prompt_with_images.jsonl。

支持三批一起跑:
  0818  = 260818-光影光效2 (1426)
  0819  = 0819-光影光效 (1151)
  0819b = 260819-光影光效2 (1501)

用法:
  python3 -u build_jsonl_lighting.py --batch 0818
  python3 -u build_jsonl_lighting.py --batch all
  python3 -u build_jsonl_lighting.py --batch 0819,0819b
"""

from __future__ import print_function

import argparse
import collections
import json
import os
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_SCRIPTS = SCRIPT_DIR / "skills" / "lighting-batch-jsonl" / "scripts"
sys.path.insert(0, str(SKILL_SCRIPTS))

from common import (  # noqa: E402
    Report,
    is_image,
    load_defaults,
    load_prompt_table,
    tag_of_dir,
    write_jsonl,
)

BATCH_INDEX = SCRIPT_DIR / "batches" / "index.json"
PROMPT_XLSX = SCRIPT_DIR / "光影光效2-prompts.xlsx"
PROMPT_JSON = SCRIPT_DIR / "prompts.json"
TIERS = ("3.5", "4")


def load_batch_index():
    return json.loads(BATCH_INDEX.read_text(encoding="utf-8"))


def resolve_batches(spec):
    index = load_batch_index()
    if spec.strip().lower() == "all":
        return [index[k] for k in index]
    ids = [x.strip() for x in spec.split(",") if x.strip()]
    missing = [i for i in ids if i not in index]
    if missing:
        sys.exit("未知 batch id: %s；可选: %s" % (missing, list(index)))
    return [index[i] for i in ids]


def load_prompt_table_json(path):
    """prompts.json: tag -> [{effect, prompt}, ...] -> tag -> [(target, prompt)]"""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    table = collections.OrderedDict()
    empty = []
    for tag, items in raw.items():
        pairs = []
        seen = set()
        for it in items or []:
            target = (it.get("effect") or "").strip()
            prompt = (it.get("prompt") or "").strip()
            if not prompt:
                empty.append((tag, target))
                continue
            if target in seen:
                continue
            seen.add(target)
            pairs.append((target, prompt))
        if pairs:
            table[tag] = pairs
    return table, empty


def load_prompts(skill_cfg, xlsx_arg=None, json_arg=None):
    """优先 prompts.json（KML 无外网、无 openpyxl 也能跑）；有 openpyxl 再读 xlsx。"""
    json_path = Path(json_arg) if json_arg else PROMPT_JSON
    if json_path.is_file():
        table, empty = load_prompt_table_json(json_path)
        return table, empty, "json:%s" % json_path

    xlsx = Path(xlsx_arg) if xlsx_arg else (
        PROMPT_XLSX if PROMPT_XLSX.is_file() else Path(skill_cfg["prompt_xlsx"])
    )
    if not xlsx.is_file():
        sys.exit("找不到 prompts.json 或 prompt Excel（%s）" % xlsx)
    try:
        table, empty = load_prompt_table(str(xlsx), skill_cfg.get("prompt_sheet", "有prompt"))
    except ImportError:
        sys.exit(
            "读 Excel 需要 openpyxl，但当前环境没有且无外网安装。\n"
            "请把本机 prompts.json 放到脚本同目录后再跑。"
        )
    return table, empty, "xlsx:%s" % xlsx


def resolve_tier_dirs(root):
    """匹配 3.5 / 4，以及盘上常见的 3.5-460张、4-966张 这类命名。"""
    root = Path(root)
    by_tier = {}
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        name = child.name
        for tier in TIERS:
            if name == tier or name.startswith(tier + "-") or name.startswith(tier + "_"):
                by_tier.setdefault(tier, []).append(child)
                break
    ordered = []
    for tier in TIERS:
        ordered.extend(by_tier.get(tier, []))
    return ordered


def list_tag_dirs(root):
    root = Path(root)
    if not root.is_dir():
        raise FileNotFoundError("图片目录不存在: %s" % root)
    found = []
    for tier_dir in resolve_tier_dirs(root):
        tier_name = tier_dir.name  # 写入 JSONL 的真实目录名
        for tag_dir in sorted(tier_dir.iterdir()):
            if not tag_dir.is_dir():
                continue
            images = sorted(
                p.name for p in tag_dir.iterdir() if p.is_file() and is_image(p.name)
            )
            if not images:
                for sub in sorted(tag_dir.iterdir()):
                    if not sub.is_dir():
                        continue
                    sub_imgs = sorted(
                        p.name for p in sub.iterdir() if p.is_file() and is_image(p.name)
                    )
                    if sub_imgs:
                        rel = "%s/%s/%s" % (tier_name, tag_dir.name, sub.name)
                        found.append((tag_of_dir(tag_dir.name), rel, sub_imgs))
                continue
            rel = "%s/%s" % (tier_name, tag_dir.name)
            found.append((tag_of_dir(tag_dir.name), rel, images))
    return found


def resolve_image_root(cfg):
    """优先用配置里的绝对 image_root / kml 路径；否则用本机相对目录。"""
    for key in ("image_root", "kml_image_root"):
        raw = cfg.get(key) or ""
        if raw and Path(raw).is_absolute():
            return Path(raw)
    local = cfg.get("local_image_root") or ""
    if local:
        p = Path(local)
        return p if p.is_absolute() else SCRIPT_DIR / p
    raise FileNotFoundError("未配置 image_root / local_image_root")


def build_one(cfg, table, skill_cfg, runs, missing_prompt, report):
    alias = dict(skill_cfg.get("tag_aliases") or {})
    local_root = resolve_image_root(cfg)
    out_jsonl = SCRIPT_DIR / cfg["jsonl"]
    out_jsonl.parent.mkdir(parents=True, exist_ok=True)
    report_path = str(out_jsonl).replace(".jsonl", "_report.txt")
    p = Report(report_path)

    p("=" * 48)
    p("batch:", cfg["id"], "|", cfg["root_name"], "|", cfg["total_images"], "张")
    p("扫描目录:", local_root, "| exists:", local_root.is_dir())
    p("KML 前缀:", cfg["kml_image_root"])
    p("work_dir:", cfg["work_dir"])
    p("输出 JSONL:", out_jsonl)
    p("")

    try:
        tag_groups = list_tag_dirs(local_root)
    except FileNotFoundError as e:
        p("!!", e)
        p("请把原图放到:", local_root, "结构: 3.5|4/类目文件夹/图片")
        p.close()
        return 0

    if not tag_groups:
        p("!! 未找到图片:", local_root)
        p.close()
        return 0

    records = []
    per_tag = []
    stats = collections.Counter()
    generic_tags, skipped_tags = [], []

    for raw_tag, rel_dir, images in tag_groups:
        tag = alias.get(raw_tag, raw_tag)
        targets = table.get(tag)
        if not targets:
            if missing_prompt == "skip":
                skipped_tags.append((raw_tag, len(images)))
                stats["跳过的图片"] += len(images)
                continue
            targets = [("去光", skill_cfg["generic_delight_prompt"])]
            generic_tags.append((raw_tag, len(images)))
            stats["用通用 prompt 的图片"] += len(images)

        buckets = collections.OrderedDict((t, []) for t, _ in targets)
        for i, img in enumerate(images):
            buckets[targets[i % len(targets)][0]].append(img)

        pmap = dict(targets)
        for target, imgs in buckets.items():
            kind = "去光" if target == "去光" else "布光"
            out_target = "自然光" if target == "去光" else target
            for img in imgs:
                stem = os.path.splitext(img)[0]
                path = "%s/%s/%s" % (cfg["kml_image_root"].rstrip("/"), rel_dir, img)
                for run in range(1, runs + 1):
                    records.append(collections.OrderedDict([
                        ("id", "%s-r%d" % (stem, run)),
                        ("image_path", path),
                        ("prompt", pmap[target]),
                        ("light_type", raw_tag),
                        ("prompt_kind", kind),
                        ("prompt_target", out_target),
                        ("run", run),
                        ("batch_id", cfg["id"]),
                    ]))
                stats["图片"] += 1
            stats["target:" + out_target] += len(imgs)

        per_tag.append((
            raw_tag,
            len(images),
            [(t if t != "去光" else "去光/自然光", len(v)) for t, v in buckets.items()],
        ))

    write_jsonl(str(out_jsonl), records)

    p("--- 每个标签的分配 ---")
    for tag, n, split in per_tag:
        idle = [s for s in split if s[1] == 0]
        flag = "   <- %d 个目标没分到图" % len(idle) if idle else ""
        p("  %-14s %3d张 -> %s%s"
          % (tag, n, ", ".join("%s %d" % s for s in split), flag))
    p("")
    if generic_tags:
        p("!! 用通用去光 prompt 的标签 %d 个，共 %d 张:"
          % (len(generic_tags), sum(x[1] for x in generic_tags)))
        for t, n in generic_tags:
            p("    %-14s %3d张" % (t, n))
        p("")
    if skipped_tags:
        p("!! 被跳过的标签:", skipped_tags)
        p("")
    p("图片:", stats["图片"], "| 行数:", len(records))
    p("已写出:", out_jsonl)
    p("报告:", report_path)
    p.close()
    report("batch %s: %d 图 / %d 行 -> %s" % (cfg["id"], stats["图片"], len(records), out_jsonl))
    return len(records)


def main():
    skill_cfg = load_defaults()
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", default="all", help="0818 | 0819 | 0819b | all | 逗号列表")
    ap.add_argument("--xlsx", default=None, help="可选；默认优先用同目录 prompts.json")
    ap.add_argument("--prompts-json", default=None, help="默认 scripts 旁 prompts.json")
    ap.add_argument("--runs", type=int, default=skill_cfg.get("runs", 3))
    ap.add_argument("--missing-prompt", choices=["generic", "skip"], default="generic")
    args = ap.parse_args()

    table, empty, src = load_prompts(skill_cfg, args.xlsx, args.prompts_json)
    batches = resolve_batches(args.batch)

    summary = Report(str(SCRIPT_DIR / "batches" / "build_all_report.txt"))
    summary("prompts:", src, "| 标签:", len(table), "| 空目标:", len(empty) or 0)
    summary("batches:", [b["id"] for b in batches])
    summary("")

    total_rows = 0
    for cfg in batches:
        total_rows += build_one(cfg, table, skill_cfg, args.runs, args.missing_prompt, summary)

    summary("")
    summary("合计 JSONL 行数:", total_rows)
    summary("下一步: 各 batch 在对应 work_dir 跑 gen_image_lighting.py --batch <id>")
    summary.close()
    print("合计行数:", total_rows)


if __name__ == "__main__":
    main()
