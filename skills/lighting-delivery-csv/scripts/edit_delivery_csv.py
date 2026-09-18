# -*- coding: utf-8 -*-
"""Modify an existing lighting delivery CSV in place: switch colormatch variants
per tag, drop or keep tags, enforce a minimum output count."""
import argparse
import collections
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (OUTPUT_COLS, Report, is_junk, read_delivery_csv, resolve_batch_layout,
                    stdout_utf8, parse_tag_list, variants, write_delivery_csv)


def main():
    stdout_utf8()
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--batch-dir", required=True, help="用于确认目标文件确实存在")
    ap.add_argument("--out", help="默认原地覆盖，并留一份 .bak")
    ap.add_argument("--report")
    ap.add_argument("--plain-tags", help="改用原始 -rN.png 的标签：default | all | 逗号分隔")
    ap.add_argument("--colormatch-tags", help="改回 _colormatched.png 的标签：all | 逗号分隔")
    ap.add_argument("--drop-tags", help="删除这些标签的行")
    ap.add_argument("--keep-tags", help="只保留这些标签的行")
    ap.add_argument("--min-outputs", type=int, help="输出数少于此值的行删除")
    args = ap.parse_args()

    batch_dir = os.path.abspath(args.batch_dir)
    out_path = args.out or args.csv
    report_path = args.report or os.path.splitext(out_path)[0] + "_edit_report.txt"
    p = Report(report_path)

    layout = resolve_batch_layout(batch_dir)
    disk_out = {}
    for d in layout["output_dirs"]:
        for fn in os.listdir(os.path.join(batch_dir, d)):
            if not is_junk(fn):
                disk_out[fn] = d

    fields, records = read_delivery_csv(args.csv)
    p("输入 csv :", args.csv, "|", len(records), "行")

    to_plain = parse_tag_list(args.plain_tags) if args.plain_tags else set()
    to_cm = parse_tag_list(args.colormatch_tags) if args.colormatch_tags else set()
    switched = collections.Counter()
    unavailable = []

    for rec in records:
        tag = rec["标签"]
        if to_plain == "ALL" or (to_plain != "ALL" and tag in to_plain):
            target = "plain"
        elif to_cm == "ALL" or (to_cm != "ALL" and tag in to_cm):
            target = "colormatched"
        else:
            continue
        for col in OUTPUT_COLS:
            if not rec[col]:
                continue
            plain, cm = variants(rec[col])
            want = plain if target == "plain" else cm
            if want in disk_out:
                new = disk_out[want] + "/" + want
                if new != rec[col]:
                    rec[col] = new
                    switched[tag + " -> " + target] += 1
            else:
                unavailable.append((tag, want))

    before = len(records)
    if args.keep_tags:
        keep = parse_tag_list(args.keep_tags)
        records = [r for r in records if r["标签"] in keep]
    if args.drop_tags:
        drop = parse_tag_list(args.drop_tags)
        records = [r for r in records if r["标签"] not in drop]
    if args.min_outputs is not None:
        records = [r for r in records
                   if sum(1 for c in OUTPUT_COLS if r[c]) >= args.min_outputs]

    if out_path == args.csv and os.path.isfile(args.csv):
        shutil.copy2(args.csv, args.csv + ".bak")
        p("已备份 :", args.csv + ".bak")

    write_delivery_csv(out_path, records)

    p("路径切换 :", sum(switched.values()))
    for k, v in sorted(switched.items()):
        p("    ", k, v)
    p("目标文件不存在，已保持原样 :", len(unavailable), unavailable[:5])
    p("行数 :", before, "->", len(records))
    p("已写出 :", out_path)
    p.close()


if __name__ == "__main__":
    main()
