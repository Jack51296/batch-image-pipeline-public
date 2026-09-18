# -*- coding: utf-8 -*-
"""Build a Prompt_with_images JSONL for a lighting batch.

One row per (image, run). Within each light-effect tag the prompts available
for that tag in 光照种类-提示词总表.xlsx are handed out round-robin across the
images, so 横屏/竖屏 and different scenes spread evenly over the targets.
Rows are emitted grouped by target, matching the layout of earlier batches.
"""
import argparse
import collections
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (Report, load_defaults, load_prompt_table, stdout_utf8,
                    tag_of_dir, write_jsonl)


def main():
    stdout_utf8()
    ap = argparse.ArgumentParser()
    ap.add_argument("--png-dir", required=True, help="转换后的 png 根目录")
    ap.add_argument("--xlsx", required=True)
    ap.add_argument("--kml-prefix", required=True,
                    help="KML 上对应 --png-dir 的绝对路径。层级必须实测确认，"
                         "不同批次不一样，写错会整批 找不到原图")
    ap.add_argument("--out", required=True)
    ap.add_argument("--runs", type=int)
    ap.add_argument("--missing-prompt", choices=["generic", "skip"], default="generic",
                    help="总表里没有 prompt 的标签：用通用去光文案 / 整个标签跳过")
    ap.add_argument("--tag-alias", action="append", default=[],
                    help="目录标签=总表标签，如 单光照射=单光照明，可重复")
    ap.add_argument("--report")
    args = ap.parse_args()

    cfg = load_defaults()
    runs = args.runs or cfg["runs"]
    alias = dict(a.split("=", 1) for a in args.tag_alias)
    p = Report(args.report or os.path.splitext(args.out)[0] + "_report.txt")

    table, empty = load_prompt_table(args.xlsx, cfg["prompt_sheet"])
    p("总表标签数 :", len(table), "| runs :", runs)
    p("因 prompt 为空被丢弃的目标 :", empty or "无")
    if alias:
        p("标签别名 :", alias)
    p("")

    tag_dirs = sorted(d for d in os.listdir(args.png_dir)
                      if os.path.isdir(os.path.join(args.png_dir, d)))

    records, per_tag = [], []
    stats = collections.Counter()
    generic_tags, skipped_tags = [], []

    for tag_dir in tag_dirs:
        raw_tag = tag_of_dir(tag_dir)
        tag = alias.get(raw_tag, raw_tag)
        images = sorted(f for f in os.listdir(os.path.join(args.png_dir, tag_dir))
                        if f.lower().endswith(".png"))
        if not images:
            continue

        targets = table.get(tag)
        if not targets:
            if args.missing_prompt == "skip":
                skipped_tags.append((raw_tag, len(images)))
                stats["跳过的图片"] += len(images)
                continue
            targets = [("去光", cfg["generic_delight_prompt"])]
            generic_tags.append((raw_tag, len(images)))
            stats["用通用 prompt 的图片"] += len(images)

        # Round-robin so every target gets a mixed slice of the tag's images
        # rather than one orientation or one shoot.
        buckets = collections.OrderedDict((t, []) for t, _ in targets)
        for i, img in enumerate(images):
            buckets[targets[i % len(targets)][0]].append(img)

        pmap = dict(targets)
        for target, imgs in buckets.items():
            kind = "去光" if target == "去光" else "布光"
            out_target = "自然光" if target == "去光" else target
            for img in imgs:
                stem = os.path.splitext(img)[0]
                path = "%s/%s/%s" % (args.kml_prefix.rstrip("/"), tag_dir, img)
                for run in range(1, runs + 1):
                    records.append(collections.OrderedDict([
                        ("id", "%s-r%d" % (stem, run)),
                        ("image_path", path),
                        ("prompt", pmap[target]),
                        ("light_type", raw_tag),
                        ("prompt_kind", kind),
                        ("prompt_target", out_target),
                        ("run", run),
                    ]))
                stats["图片"] += 1
            stats["target:" + out_target] += len(imgs)

        per_tag.append((raw_tag, len(images),
                        [(t if t != "去光" else "去光/自然光", len(v))
                         for t, v in buckets.items()]))

    write_jsonl(args.out, records)

    p("--- 每个标签的分配 ---")
    for tag, n, split in per_tag:
        idle = [s for s in split if s[1] == 0]
        flag = "   <- %d 个目标没分到图（图少于目标数）" % len(idle) if idle else ""
        p("  %-14s %3d张 -> %s%s"
          % (tag, n, ", ".join("%s %d" % s for s in split), flag))
    p("")
    if generic_tags:
        p("!! 用通用去光 prompt 的标签 %d 个，共 %d 张 :"
          % (len(generic_tags), sum(x[1] for x in generic_tags)))
        for t, n in generic_tags:
            p("    %-14s %3d张" % (t, n))
        p("")
    if skipped_tags:
        p("!! 被跳过的标签 %d 个，共 %d 张 :"
          % (len(skipped_tags), sum(x[1] for x in skipped_tags)))
        for t, n in skipped_tags:
            p("    %-14s %3d张" % (t, n))
        p("")
    p("图片 :", stats["图片"], "| 行数 :", len(records))
    p("")
    p("prompt_target 分布 :")
    for k, v in sorted(((k[7:], v) for k, v in stats.items()
                        if k.startswith("target:")), key=lambda x: -x[1]):
        p("    %-14s %3d 张 (%d 行)" % (k, v, v * runs))
    p("")
    p("已写出 :", args.out)
    p("下一步务必跑 verify_jsonl.py，尤其是 --kml-prefix 的层级。")
    p.close()


if __name__ == "__main__":
    main()
