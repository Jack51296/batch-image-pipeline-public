# -*- coding: utf-8 -*-
"""Survey a raw lighting batch before converting anything.

Answers the questions that have bitten past batches: what formats are in
there, which stems collide, how big the PNGs will get relative to the
pipeline's 50MB ceiling, and which tags have no prompt in the spreadsheet.
Read-only.
"""
import argparse
import collections
import ctypes
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (Report, index_by_stem, is_image, is_junk, load_defaults,
                    load_prompt_table, pick_source, stdout_utf8, tag_of_dir)


def free_bytes(path):
    if os.name != "nt":
        st = os.statvfs(path)
        return st.f_bavail * st.f_frsize
    free = ctypes.c_ulonglong(0)
    ctypes.windll.kernel32.GetDiskFreeSpaceExW(
        ctypes.c_wchar_p(path), None, None, ctypes.byref(free))
    return free.value


def main():
    stdout_utf8()
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="原图根目录，如 .../0824/<批次>/4-634张")
    ap.add_argument("--xlsx", help="光照种类-提示词总表.xlsx，给了才做标签覆盖检查")
    ap.add_argument("--report")
    args = ap.parse_args()

    from PIL import Image
    # Batches routinely contain 50-100 MP photos; the default guard would abort.
    Image.MAX_IMAGE_PIXELS = None

    cfg = load_defaults()
    src = os.path.abspath(args.src)
    p = Report(args.report or os.path.join(os.getcwd(), "survey_report.txt"))
    p("src :", src)
    p("")

    ext = collections.Counter()
    junk = collections.Counter()
    per_dir = collections.Counter()
    for dirpath, _, filenames in os.walk(src):
        rel = os.path.relpath(dirpath, src).replace("\\", "/")
        for fn in filenames:
            if is_junk(fn):
                junk[fn.lower()] += 1
            elif is_image(fn):
                ext[os.path.splitext(fn)[1].lower()] += 1
                per_dir[rel] += 1
            else:
                ext["(非图片)" + os.path.splitext(fn)[1].lower()] += 1

    total_images = sum(v for k, v in ext.items() if not k.startswith("("))
    p("图片 :", total_images, dict(ext.most_common()))
    p("垃圾文件 :", sum(junk.values()), dict(junk))
    p("")

    p("--- 标签目录 (目录名里的张数不可信，以实际为准) ---")
    for d in sorted(per_dir):
        if d == ".":
            p("  %-22s %4d  <- 根目录直接放了图片" % ("(根目录)", per_dir[d]))
            continue
        import re
        m = re.search(r"-(\d+)张$", d)
        claim = int(m.group(1)) if m else None
        flag = "" if claim in (None, per_dir[d]) else "  <- 目录名写 %d 张" % claim
        p("  %-22s %4d%s" % (d, per_dir[d], flag))
    p("")

    index = index_by_stem(src)
    collide = [(k, v) for k, v in index.items() if len(v) > 1]
    p("唯一主干 :", len(index), "| 同名多格式 :", len(collide))
    if collide:
        prio = cfg["source_ext_priority"]
        p("  转换后这些主干只会产出一个 png，保留优先级最高的那个：")
        for (rel, stem), names in collide:
            p("    %s/%s -> 保留 %s" % (rel, stem, pick_source(names, prio)))
    p("")

    limit = cfg["max_image_mb"]
    lo, hi = cfg["png_bytes_per_pixel"]
    mp, modes, orient, alpha, weird = [], collections.Counter(), collections.Counter(), 0, []
    src_bytes = 0
    for (rel, stem), names in index.items():
        fn = pick_source(names, cfg["source_ext_priority"])
        full = os.path.join(src, rel.replace("/", os.sep), fn) if rel else os.path.join(src, fn)
        src_bytes += os.path.getsize(full)
        if " " in fn:
            weird.append(rel + "/" + fn)
        try:
            with Image.open(full) as im:
                mp.append((im.size[0] * im.size[1] / 1e6, im.size, rel + "/" + fn))
                modes[im.mode] += 1
                if "A" in im.mode or im.info.get("transparency") is not None:
                    alpha += 1
                try:
                    orient[(im.getexif() or {}).get(274)] += 1
                except Exception:
                    orient[None] += 1
        except Exception as ex:
            p("  读取失败 :", rel + "/" + fn, repr(ex))
    mp.sort(reverse=True)

    p("--- 像素与体积 ---")
    p("源文件合计 : %.2f GB" % (src_bytes / 1073741824.0))
    p("色彩模式 :", dict(modes), "| 带透明通道 :", alpha)
    p("EXIF 方向标记 :", dict(orient),
      "(全为 1 或 None 时转换不会改变尺寸)")
    if mp:
        p("中位 %.1f MP | 最大 %.1f MP %s" % (mp[len(mp) // 2][0], mp[0][0], mp[0][1]))
        est_lo = sum(a for a, _, _ in mp) * 1e6 * lo / 1073741824.0
        est_hi = sum(a for a, _, _ in mp) * 1e6 * hi / 1073741824.0
        p("预计 png 合计 : %.1f - %.1f GB (实测约为源体积的 3-6 倍)" % (est_lo, est_hi))
        thr_lo = limit * 1048576.0 / hi / 1e6
        thr_hi = limit * 1048576.0 / lo / 1e6
        over_hi = sum(1 for a, _, _ in mp if a > thr_lo)
        over_lo = sum(1 for a, _, _ in mp if a > thr_hi)
        p("")
        p("!! 超 %d MB 风险 : %d - %d 张 (%.0f MP 以上必超, %.0f MP 以上可能超)"
          % (limit, over_lo, over_hi, thr_hi, thr_lo))
        p("   pipeline 对超限图直接返回 HTTP 400 file_above_max_size，整张作废。")
        p("   风险最高的 10 张 :")
        for a, size, name in mp[:10]:
            p("     %6.1f MP  %-11s  %s" % (a, "%dx%d" % size, name))
    p("")

    if weird:
        p("文件名含空格 :", len(weird), weird[:10])
        p("")

    try:
        p("目标盘剩余空间 : %.1f TB" % (free_bytes(src) / 1024.0 ** 4))
    except Exception as ex:
        p("目标盘剩余空间 : 查询失败", repr(ex))
    p("")

    if args.xlsx:
        table, empty = load_prompt_table(args.xlsx, cfg["prompt_sheet"])
        p("--- 标签覆盖 (对照总表) ---")
        p("总表里 prompt 为空的条目 :", empty)
        tags = collections.OrderedDict()
        for d in sorted(per_dir):
            if d == ".":
                continue
            tags[tag_of_dir(d.split("/")[0])] = tags.get(tag_of_dir(d.split("/")[0]), 0) + per_dir[d]
        have, miss = [], []
        for t, n in tags.items():
            (have if t in table else miss).append((t, n))
        p("有 prompt 的标签 %d 个 :" % len(have))
        for t, n in have:
            p("    %-14s %3d张  目标: %s"
              % (t, n, ", ".join(x[0] for x in table[t])))
        p("")
        p("!! 总表里没有 prompt 的标签 %d 个，共 %d 张 :" % (len(miss), sum(x[1] for x in miss)))
        for t, n in miss:
            near = [k for k in table if k[:2] == t[:2]]
            p("    %-14s %3d张   总表近似项: %s" % (t, n, near or "无"))
        p("   这些标签要么跳过，要么用 defaults.json 的 generic_delight_prompt。")

    p("")
    p("只读勘查，未改动任何文件。")
    p.close()


if __name__ == "__main__":
    main()
