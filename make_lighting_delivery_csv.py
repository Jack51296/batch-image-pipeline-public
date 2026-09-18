# -*- coding: utf-8 -*-
"""
按 lighting-delivery-csv skill 生成交付 CSV，支持三批。

用法:
  python3 -u make_lighting_delivery_csv.py --batch 0818 --list-tags
  python3 -u make_lighting_delivery_csv.py --batch all --verify
"""

from __future__ import print_function

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR / "skills" / "lighting-delivery-csv"
SKILL_SCRIPTS = SKILL_DIR / "scripts"
BATCH_INDEX = SCRIPT_DIR / "batches" / "index.json"


def load_batches(spec):
    index = json.loads(BATCH_INDEX.read_text(encoding="utf-8"))
    if spec.strip().lower() == "all":
        return list(index.values())
    ids = [x.strip() for x in spec.split(",") if x.strip()]
    missing = [i for i in ids if i not in index]
    if missing:
        sys.exit("未知 batch: %s；可选 %s" % (missing, list(index)))
    return [index[i] for i in ids]


def ensure_source_link(cfg, batch_dir, source_dir):
    """出图在 chatgpt-ketu/光照，原图在光影光效根下时，在 work_dir 建软链供交付 CSV 索引。"""
    link = batch_dir / source_dir
    if link.exists():
        return
    target = cfg.get("image_root") or cfg.get("kml_image_root") or ""
    if not target:
        return
    target_path = Path(target)
    if not target_path.is_dir():
        print("[%s] 警告: 原图目录不存在，无法建软链: %s" % (cfg["id"], target_path))
        return
    batch_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.symlink(str(target_path), str(link))
        print("[%s] 已软链原图: %s -> %s" % (cfg["id"], link, target_path))
    except OSError as e:
        print("[%s] 软链失败 (%s)，交付校验可能找不到原图" % (cfg["id"], e))


def run_one(cfg, args):
    batch_dir = Path(cfg["work_dir"])
    out = args.out or str(SCRIPT_DIR / cfg["delivery_csv"])
    Path(out).parent.mkdir(parents=True, exist_ok=True)

    # 出图在 work_dir；原图可能在别处，source_dir = 原图文件夹名
    source_dir = args.source_dir or cfg.get("source_dir") or "source_images"
    ensure_source_link(cfg, batch_dir, source_dir)
    pipeline_name = cfg.get("pipeline_csv") or "Prompt_with_images.csv"
    pipeline = args.pipeline_file or str(batch_dir / Path(pipeline_name).name)

    cmd = [
        sys.executable,
        str(SKILL_SCRIPTS / "build_delivery_csv.py"),
        "--batch-dir", str(batch_dir),
        "--source-dir", source_dir,
        "--pipeline-file", pipeline,
        "--out", out,
        "--no-colormatch-tags", args.no_colormatch_tags,
    ]
    if args.min_outputs is not None:
        cmd.extend(["--min-outputs", str(args.min_outputs)])
    if args.path_style:
        cmd.extend(["--path-style", args.path_style])
    if args.list_tags:
        cmd.append("--list-tags")

    print("[%s] %s" % (cfg["id"], " ".join(cmd)))
    rc = subprocess.call(cmd, cwd=str(SKILL_DIR))
    if rc != 0:
        return rc

    if args.list_tags or not args.verify:
        if not args.list_tags:
            print("[%s] 交付 CSV: %s" % (cfg["id"], out))
        return 0

    vcmd = [
        sys.executable,
        str(SKILL_SCRIPTS / "verify_delivery_csv.py"),
        "--csv", out,
        "--batch-dir", str(batch_dir),
    ]
    print("[%s] verify: %s" % (cfg["id"], " ".join(vcmd)))
    return subprocess.call(vcmd, cwd=str(SKILL_DIR))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", default="all", help="0818 | 0819 | 0819b | all")
    ap.add_argument("--pipeline-file")
    ap.add_argument("--source-dir")
    ap.add_argument("--out")
    ap.add_argument("--no-colormatch-tags", default="default")
    ap.add_argument("--min-outputs", type=int)
    ap.add_argument("--list-tags", action="store_true")
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--path-style", choices=["relative", "absolute", "unc"])
    args = ap.parse_args()

    batches = load_batches(args.batch)
    if args.out and len(batches) > 1:
        sys.exit("--out 只能用于单个 batch")

    rc = 0
    for cfg in batches:
        r = run_one(cfg, args)
        if r != 0:
            rc = r
    sys.exit(rc)


if __name__ == "__main__":
    main()
