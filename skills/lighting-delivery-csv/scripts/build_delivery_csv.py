# -*- coding: utf-8 -*-
"""Build a lighting delivery CSV from a batch's pipeline CSV plus the images on disk."""
import argparse
import collections
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (FIELDS, Report, index_sources, is_junk, load_defaults, load_tasks,
                    pick_source, rel_from_batch, round_of, resolve_batch_layout,
                    stdout_utf8, tag_of, parse_tag_list, wants_plain, write_delivery_csv)


def main():
    stdout_utf8()
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch-dir", required=True, help="日期批次目录，如 .../光影图生图/0822")
    ap.add_argument("--pipeline-file", help="默认自动查找 *Prompt_with_images*.csv / .jsonl")
    ap.add_argument("--source-dir", help="原图目录名，如 260814-光影光效-6840-qt-1707-s35-5133")
    ap.add_argument("--out", help="输出 csv 路径")
    ap.add_argument("--report", help="报告路径，默认写在 --out 旁边")
    ap.add_argument("--no-colormatch-tags", default="default",
                    help="default | none | all | 逗号分隔标签")
    ap.add_argument("--min-outputs", type=int, help="少于这个输出数的行丢弃，默认 1")
    ap.add_argument("--path-style", choices=["relative", "absolute", "unc"])
    ap.add_argument("--list-tags", action="store_true", help="只统计标签和覆盖率，不写 csv")
    args = ap.parse_args()

    cfg = load_defaults()
    batch_dir = os.path.abspath(args.batch_dir)
    min_outputs = args.min_outputs if args.min_outputs is not None else cfg["min_outputs"]
    path_style = args.path_style or cfg["path_style"]
    no_cm = parse_tag_list(args.no_colormatch_tags)

    layout = resolve_batch_layout(batch_dir)
    pipeline_file = args.pipeline_file
    if not pipeline_file:
        found = layout["pipeline_files"]
        # the CSV carries per-round status, the JSONL is only a task list, so prefer CSV
        preferred = [f for f in found if f.lower().endswith(".csv")] or found
        if len(preferred) != 1:
            sys.exit("找不到唯一的 pipeline 文件，请用 --pipeline-file 指定：%s" % found)
        pipeline_file = os.path.join(batch_dir, preferred[0])
    source_dir = args.source_dir
    if not source_dir:
        if len(layout["source_dirs"]) != 1:
            sys.exit("找不到唯一的原图目录，请用 --source-dir 指定：%s" % layout["source_dirs"])
        source_dir = layout["source_dirs"][0]

    out_path = args.out or os.path.join(batch_dir, os.path.basename(batch_dir) + "_光影_final.csv")
    report_path = args.report or os.path.splitext(out_path)[0] + "_report.txt"
    p = Report(report_path)

    p("batch_dir      :", batch_dir)
    p("pipeline_file  :", pipeline_file)
    p("source_dir     :", source_dir)
    p("output_dirs    :", layout["output_dirs"])
    p("path_style     :", path_style, "| min_outputs:", min_outputs)
    p("no_colormatch  :", "ALL" if no_cm == "ALL" else ("(空)" if not no_cm else ",".join(sorted(no_cm))))
    p("")

    rows = load_tasks(pipeline_file)
    src_index = index_sources(batch_dir, source_dir)
    disk_out = {}
    for d in layout["output_dirs"]:
        for fn in os.listdir(os.path.join(batch_dir, d)):
            if not is_junk(fn):
                disk_out[fn] = d

    by_input = collections.OrderedDict()
    for r in rows:
        by_input.setdefault(r["input_image_path"], []).append(r)

    # fallback for batches whose pipeline paths name a folder that was later renamed
    # (0820 wrote "4-289张-png" while the folder on disk is "4-289张")
    by_stem = collections.defaultdict(list)
    for (rel_dir, stem), names in src_index.items():
        by_stem[stem].append(rel_dir)

    records = []
    stats = collections.Counter()
    unresolved, missing_out, ext_fixed, ambiguous, dir_fixed = [], [], [], [], []
    used_sources = set()
    cm_usage = collections.defaultdict(collections.Counter)

    for inp, group in by_input.items():
        rel = rel_from_batch(inp, batch_dir)
        if rel is None:
            unresolved.append(inp)
            stats["input_path_unmapped"] += 1
            continue
        rel_dir, base = rel.rsplit("/", 1)
        stem = os.path.splitext(base)[0]
        cands = src_index.get((rel_dir, stem))
        if not cands and len(by_stem.get(stem, [])) == 1:
            rel_dir = by_stem[stem][0]
            cands = src_index[(rel_dir, stem)]
            dir_fixed.append((rel, rel_dir))
        if not cands:
            unresolved.append(rel)
            stats["source_missing_on_disk"] += 1
            continue
        if len(cands) > 1:
            ambiguous.append((rel, cands))
        real = pick_source(cands)
        if os.path.splitext(real)[1].lower() != os.path.splitext(base)[1].lower():
            ext_fixed.append((rel, real))
        input_rel = rel_dir + "/" + real
        used_sources.add(input_rel)

        tag = group[0].get("light_type") or tag_of(input_rel)
        plain_first = wants_plain(tag, no_cm)

        outs = []
        for r in sorted(group, key=lambda x: round_of(x["id"])):
            if r["status"] != "success":
                continue
            order = (r["out_plain"], r["out_cm"]) if plain_first else (r["out_cm"], r["out_plain"])
            chosen = next((n for n in order if n in disk_out), None)
            if chosen is None:
                if r["status_known"]:
                    missing_out.append(order[0])
                else:
                    stats["not_generated"] += 1
                continue
            cm_usage[tag]["plain" if chosen == r["out_plain"] else "colormatched"] += 1
            outs.append(disk_out[chosen] + "/" + chosen)

        stats["outputs_%d" % len(outs)] += 1
        if len(outs) < min_outputs:
            continue

        records.append({
            "id": stem,
            "prompt": group[0]["prompt"],
            "input_image_path": input_rel,
            "output_image_path": outs[0],
            "output_image_path_1": outs[1] if len(outs) > 1 else "",
            "output_image_path_2": outs[2] if len(outs) > 2 else "",
            "标签": tag,
        })

    if path_style != "relative":
        prefix = _prefix_for(path_style, rows, batch_dir)
        for rec in records:
            for col in ["input_image_path"] + [c for c in FIELDS if c.startswith("output_image_path")]:
                if rec[col]:
                    rec[col] = prefix + rec[col]

    # coverage: source images on disk that the pipeline CSV never touched
    uncovered = collections.Counter()
    for (rel_dir, stem), names in src_index.items():
        if (rel_dir + "/" + pick_source(names)) not in used_sources:
            uncovered[tag_of(rel_dir + "/x")] += 1

    p("原图（磁盘）  :", sum(len(v) for v in src_index.values()))
    p("pipeline 原图 :", len(by_input))
    p("写出行数      :", len(records))
    p("每图输出数分布:", dict(sorted(stats.items())))
    p("扩展名修正    :", len(ext_fixed), "样例:", ext_fixed[:3])
    p("目录名修正    :", len(dir_fixed), "样例:", dir_fixed[:3])
    p("同名多格式    :", len(ambiguous), ambiguous[:3])
    p("原图无法定位  :", len(unresolved), unresolved[:5])
    p("输出文件缺失  :", len(missing_out), missing_out[:5])
    dup = [k for k, v in collections.Counter(r["id"] for r in records).items() if v > 1]
    p("id 重复       :", len(dup), dup[:10])
    p("")
    p("未被 pipeline 覆盖的原图（按标签）:", sum(uncovered.values()))
    for k, v in uncovered.most_common():
        p("    ", k or "(根目录)", v)
    p("")
    p("每个标签的 colormatch 用法（plain = 未做 colormatch）:")
    for tag in sorted(cm_usage):
        c = cm_usage[tag]
        p("    ", tag, "总计", sum(c.values()), "| plain", c["plain"], "| colormatched", c["colormatched"])

    if args.list_tags:
        p("")
        p("--list-tags 模式，未写出 csv")
    else:
        write_delivery_csv(out_path, records)
        p("")
        p("已写出:", out_path)
    p.close()


def _prefix_for(style, rows, batch_dir):
    if style == "unc":
        return batch_dir.replace("\\", "/").rstrip("/") + "/"
    parts = rows[0]["input_image_path"].replace("\\", "/").split("/")
    for i, part in enumerate(parts):
        if part and os.path.isdir(os.path.join(batch_dir, part)):
            return "/".join(parts[:i]) + "/"
    return ""


if __name__ == "__main__":
    main()
