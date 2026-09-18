# -*- coding: utf-8 -*-
"""Convert every image under a source tree to PNG in a parallel output tree.

Lossless and at the original resolution unless --max-long-edge is given.
Subdirectory layout is mirrored. Safe to re-run: files that already exist in
the destination are skipped, so an interrupted network write can just be
restarted.
"""
import argparse
import collections
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (Report, index_by_stem, load_defaults, pick_source,
                    stdout_utf8)


def target_mode(im):
    if "A" in im.mode or im.info.get("transparency") is not None:
        return "RGBA"
    return "RGB"


def main():
    stdout_utf8()
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--dst", required=True, help="平行新目录，惯例是 <源目录名>-png")
    ap.add_argument("--max-long-edge", type=int,
                    help="长边上限，超出等比缩小；不给则保持原分辨率")
    ap.add_argument("--compress-level", type=int, default=6)
    ap.add_argument("--report")
    args = ap.parse_args()

    from PIL import Image, ImageOps
    Image.MAX_IMAGE_PIXELS = None

    cfg = load_defaults()
    limit_mb = cfg["max_image_mb"]
    src, dst = os.path.abspath(args.src), os.path.abspath(args.dst)
    p = Report(args.report or os.path.splitext(dst)[0] + "_convert_report.txt")
    p("src :", src)
    p("dst :", dst)
    p("长边上限 :", args.max_long_edge or "不限（保持原分辨率）")
    p("")

    # One stem can exist as .jpg and .webp in the same folder; both would map to
    # the same .png name, so decide up front which source wins.
    index = index_by_stem(src)
    todo = []
    collisions = []
    for (rel, stem), names in index.items():
        chosen = pick_source(names, cfg["source_ext_priority"])
        if len(names) > 1:
            collisions.append((rel + "/" + stem, names, chosen))
        todo.append((rel, chosen, stem))

    p("唯一主干 :", len(index), "| 同名多格式 :", len(collisions))
    for stem, names, chosen in collisions:
        p("    %s : %s -> %s" % (stem, sorted(names), chosen))
    p("")

    stats = collections.Counter()
    failures, oversize = [], []
    src_bytes = dst_bytes = 0
    started = time.time()

    for i, (rel, fn, stem) in enumerate(todo, 1):
        s = os.path.join(src, rel.replace("/", os.sep), fn) if rel else os.path.join(src, fn)
        out_dir = os.path.join(dst, rel.replace("/", os.sep)) if rel else dst
        out = os.path.join(out_dir, stem + ".png")
        try:
            if os.path.isfile(out) and os.path.getsize(out) > 0:
                stats["已存在跳过"] += 1
                src_bytes += os.path.getsize(s)
                dst_bytes += os.path.getsize(out)
                continue
            if not os.path.isdir(out_dir):
                os.makedirs(out_dir)
            with Image.open(s) as im:
                # No-op when orientation is 1/absent, but keeps the output
                # visually identical if EXIF-rotated files ever show up.
                im = ImageOps.exif_transpose(im)
                im = im.convert(target_mode(im))
                if args.max_long_edge and max(im.size) > args.max_long_edge:
                    r = args.max_long_edge / float(max(im.size))
                    im = im.resize((max(1, int(im.size[0] * r)),
                                    max(1, int(im.size[1] * r))), Image.LANCZOS)
                    stats["已缩小"] += 1
                tmp = out + ".part"
                im.save(tmp, "PNG", compress_level=args.compress_level)
            os.replace(tmp, out)
            ss, ds = os.path.getsize(s), os.path.getsize(out)
            src_bytes += ss
            dst_bytes += ds
            stats["已转换"] += 1
            stats["源" + os.path.splitext(fn)[1].lower()] += 1
            if ds > limit_mb * 1048576:
                oversize.append((ds / 1048576.0, (rel + "/" if rel else "") + fn))
        except Exception as ex:
            stats["失败"] += 1
            failures.append(((rel + "/" if rel else "") + fn, repr(ex)))
            p("失败 :", (rel + "/" if rel else "") + fn, repr(ex))

        if i % 25 == 0 or i == len(todo):
            el = time.time() - started
            p("进度 %d/%d  已用 %.0f 分钟  预计剩余 %.0f 分钟"
              % (i, len(todo), el / 60, (el / i) * (len(todo) - i) / 60))

    p("")
    p("统计 :", dict(sorted(stats.items())))
    if src_bytes:
        p("源体积 %.2f GB -> png %.2f GB（%.1f 倍）"
          % (src_bytes / 1073741824.0, dst_bytes / 1073741824.0,
             dst_bytes / float(src_bytes)))
    p("")
    p("!! 超 %d MB 的 png : %d 张，这些图 pipeline 会直接拒绝" % (limit_mb, len(oversize)))
    for mb, name in sorted(oversize, reverse=True):
        p("    %6.1f MB  %s" % (mb, name))
    p("")
    p("失败 :", len(failures))
    for name, err in failures:
        p("    ", name, err)
    p("")
    p("耗时 %.1f 分钟" % ((time.time() - started) / 60))
    p.close()
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
