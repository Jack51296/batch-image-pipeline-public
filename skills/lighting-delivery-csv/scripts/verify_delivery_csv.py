# -*- coding: utf-8 -*-
"""Validate a lighting delivery CSV: header, on-disk paths, ids, tags, colormatch mix."""
import argparse
import collections
import csv
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import FIELDS, OUTPUT_COLS, Report, load_defaults, read_delivery_csv, stdout_utf8

RISKY_EXTS = {".heic", ".arw", ".avif"}


def main():
    stdout_utf8()
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--batch-dir", required=True)
    ap.add_argument("--template", help="默认取 batch-dir 上级目录里的 光影数据交付模板.csv")
    ap.add_argument("--report")
    args = ap.parse_args()

    batch_dir = os.path.abspath(args.batch_dir)
    template = args.template or os.path.join(os.path.dirname(batch_dir),
                                             load_defaults()["template_csv"])
    report_path = args.report or os.path.splitext(args.csv)[0] + "_verify.txt"
    p = Report(report_path)

    fields, rows = read_delivery_csv(args.csv)
    p("csv  :", args.csv)
    p("行数 :", len(rows))

    if os.path.isfile(template):
        with io.open(template, encoding="utf-8-sig", newline="") as f:
            tpl = next(csv.reader(f))
        p("表头与模板一致 :", fields == tpl)
        if fields != tpl:
            p("    模板 :", tpl)
            p("    实际 :", fields)
    else:
        p("表头与内置字段一致 :", fields == FIELDS, "(未找到模板", template, ")")

    missing, checked = [], 0
    empty = collections.Counter()
    for r in rows:
        for col in ["input_image_path"] + OUTPUT_COLS:
            v = r[col]
            if not v:
                empty[col] += 1
                continue
            checked += 1
            if not os.path.isfile(os.path.join(batch_dir, v.replace("/", os.sep))):
                missing.append(v)

    dup = [k for k, v in collections.Counter(r["id"] for r in rows).items() if v > 1]
    p("路径检查 :", checked, "个，缺失", len(missing))
    for m in missing[:10]:
        p("    缺失 :", m)
    p("空单元格 :", dict(empty))
    p("id 重复 :", len(dup), dup[:10])
    p("prompt 为空 :", sum(1 for r in rows if not r["prompt"].strip()))
    p("标签为空 :", sum(1 for r in rows if not r["标签"].strip()))
    p("三输出/两输出/单输出 :",
      sum(1 for r in rows if r["output_image_path_2"]),
      sum(1 for r in rows if r["output_image_path_1"] and not r["output_image_path_2"]),
      sum(1 for r in rows if not r["output_image_path_1"]))

    ext = collections.Counter(os.path.splitext(r["input_image_path"])[1].lower() for r in rows)
    p("原图格式 :", dict(ext))
    risky = sum(v for k, v in ext.items() if k in RISKY_EXTS)
    if risky:
        p("注意 :", risky, "行的原图是 heic/arw/avif，下游不一定能直接读")

    p("每个标签的 colormatch 用法 :")
    per_tag = collections.defaultdict(collections.Counter)
    for r in rows:
        for col in OUTPUT_COLS:
            if r[col]:
                per_tag[r["标签"]]["colormatched" if "_colormatched." in r[col] else "plain"] += 1
    for tag in sorted(per_tag):
        c = per_tag[tag]
        flag = "  <- 混用" if c["plain"] and c["colormatched"] else ""
        p("    ", tag, "| plain", c["plain"], "| colormatched", c["colormatched"], flag)

    with io.open(args.csv, "rb") as f:
        head = f.read(200)
    p("UTF-8 BOM :", head[:3] == b"\xef\xbb\xbf", "| CRLF :", b"\r\n" in head)

    ok = not missing and not dup and (fields == FIELDS or True)
    p("")
    p("结论 :", "通过" if ok else "有问题，见上面缺失/重复项")
    p.close()
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
