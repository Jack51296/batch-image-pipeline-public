# -*- coding: utf-8 -*-
"""Validate a Prompt_with_images JSONL before handing it to the pipeline.

With --png-dir it also maps every image_path back to a local file, which is
the only way to catch a wrong --kml-prefix before the pipeline fails the whole
batch with 找不到原图.
"""
import argparse
import collections
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (JSONL_FIELDS, Report, load_defaults, read_jsonl,
                    stdout_utf8, tag_of_dir)


def main():
    stdout_utf8()
    ap = argparse.ArgumentParser()
    ap.add_argument("--jsonl", required=True)
    ap.add_argument("--png-dir", help="本地 png 根目录，给了才做存在性和体积检查")
    ap.add_argument("--kml-prefix", help="与 --png-dir 对应的 KML 前缀")
    ap.add_argument("--runs", type=int)
    ap.add_argument("--report")
    args = ap.parse_args()

    cfg = load_defaults()
    runs = args.runs or cfg["runs"]
    limit_mb = cfg["max_image_mb"]
    p = Report(args.report or os.path.splitext(args.jsonl)[0] + "_verify.txt")
    p("jsonl :", args.jsonl)
    p("")

    errors, warns = [], []

    def err(n, msg):
        errors.append("行 %s: %s" % (n, msg))

    def warn(msg):
        warns.append(msg)

    try:
        rows = read_jsonl(args.jsonl)
    except (ValueError, json.JSONDecodeError) as ex:
        p("!! JSONL 解析失败 :", repr(ex))
        p.close()
        sys.exit(1)

    ids = collections.Counter()
    per_image = collections.Counter()
    tags = collections.Counter()
    targets = collections.Counter()
    prefixes = collections.Counter()
    paths = collections.OrderedDict()

    for n, r in enumerate(rows, 1):
        if list(r.keys()) != JSONL_FIELDS:
            err(n, "字段不符，应为 %s，实为 %s" % (JSONL_FIELDS, list(r.keys())))
            continue

        path, run, rid = r["image_path"], r["run"], r["id"]
        stem = os.path.splitext(path.rsplit("/", 1)[-1])[0]
        ids[rid] += 1
        per_image[path] += 1
        tags[r["light_type"]] += 1
        targets[r["prompt_target"]] += 1
        prefixes[path.rsplit("/", 2)[0]] += 1
        paths.setdefault(path, n)

        if rid != "%s-r%d" % (stem, run):
            err(n, "id 与文件名/run 不一致: %s vs %s-r%d" % (rid, stem, run))
        if not path.startswith("/"):
            err(n, "image_path 不是绝对路径: %s" % path)
        if not path.lower().endswith(".png"):
            err(n, "image_path 不是 png: %s" % path)
        if not isinstance(run, int) or not 1 <= run <= runs:
            err(n, "run 越界: %r" % (run,))
        if not r["prompt"] or not r["prompt"].strip():
            err(n, "prompt 为空")
        elif r["prompt"] != r["prompt"].strip() or "\n" in r["prompt"]:
            err(n, "prompt 首尾有空白或含换行")
        if r["prompt_kind"] not in ("去光", "布光"):
            err(n, "prompt_kind 非法: %r" % (r["prompt_kind"],))
        if (r["prompt_kind"] == "去光") != (r["prompt_target"] == "自然光"):
            err(n, "去光 必须配 自然光，实为 %s/%s" % (r["prompt_kind"], r["prompt_target"]))

        dir_tag = tag_of_dir(path.rsplit("/", 2)[-2])
        if dir_tag != r["light_type"]:
            err(n, "light_type %s 与目录 %s 不符" % (r["light_type"], dir_tag))

    for rid, c in ids.items():
        if c > 1:
            err("-", "id 重复 %d 次: %s" % (c, rid))
    for path, c in per_image.items():
        if c != runs:
            err(paths[path], "该图有 %d 行，应为 %d 行: %s" % (c, runs, path))

    if len(prefixes) > 1:
        warn("image_path 出现 %d 种上级目录前缀，确认是否预期: %s"
             % (len(prefixes), dict(prefixes)))

    p("行数 :", len(rows), "| 图片 :", len(per_image), "| runs :", runs)
    p("标签 :", len(tags))
    p("prompt_target 分布 :", dict(targets.most_common()))
    p("")
    p("路径前缀 :")
    for k, v in prefixes.most_common(5):
        p("    %s  (%d 行)" % (k, v))
    p("")

    if args.png_dir:
        prefix = args.kml_prefix
        if not prefix:
            # Every row shares the batch dir; derive it from the tag dir level.
            prefix = os.path.commonprefix(list(per_image)).rsplit("/", 1)[0]
            p("未给 --kml-prefix，按公共前缀推断为 :", prefix)
        prefix = prefix.rstrip("/")
        missing, oversize = [], []
        for path in per_image:
            if not path.startswith(prefix + "/"):
                missing.append((path, "前缀不匹配"))
                continue
            local = os.path.join(args.png_dir,
                                 path[len(prefix) + 1:].replace("/", os.sep))
            if not os.path.isfile(local):
                missing.append((path, "本地不存在"))
            else:
                mb = os.path.getsize(local) / 1048576.0
                if mb > limit_mb:
                    oversize.append((mb, path))
        if missing:
            errors.append("有 %d 张图在本地对不上，--kml-prefix 层级很可能写错" % len(missing))
            for path, why in missing[:15]:
                p("    对不上 :", why, path)
            p("")
        else:
            p("全部 %d 张图都能在本地找到，路径层级正确。" % len(per_image))
        if oversize:
            warn("%d 张 png 超过 %d MB，pipeline 会返回 400 file_above_max_size"
                 % (len(oversize), limit_mb))
            for mb, path in sorted(oversize, reverse=True)[:15]:
                p("    %6.1f MB  %s" % (mb, path))
        p("")

    for w in warns:
        p("警告 :", w)
    p("")
    if errors:
        p("!! 校验失败，%d 个问题 :" % len(errors))
        for e in errors[:50]:
            p("    ", e)
        if len(errors) > 50:
            p("     ... 还有 %d 个" % (len(errors) - 50))
    else:
        p("校验通过。")
    p.close()
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
