# -*- coding: utf-8 -*-
"""Shared helpers for the lighting batch JSONL scripts."""
import collections
import io
import json
import os
import re
import sys

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff",
              ".avif", ".heic", ".gif"}
JUNK_NAMES = {".ds_store", "thumbs.db", "desktop.ini"}

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

JSONL_FIELDS = ["id", "image_path", "prompt", "light_type",
                "prompt_kind", "prompt_target", "run"]


def stdout_utf8():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


class Report(object):
    """Writes to a UTF-8 file and echoes to stdout.

    The console echo is unreadable on Windows PowerShell (the terminal is not
    UTF-8), so always read the file, not the console.
    """

    def __init__(self, path):
        self.path = path
        self.fh = io.open(path, "w", encoding="utf-8") if path else None

    def __call__(self, *parts):
        line = " ".join(str(x) for x in parts)
        if self.fh:
            self.fh.write(line + "\n")
            self.fh.flush()
        print(line, flush=True)

    def close(self):
        if self.fh:
            self.fh.close()


def load_defaults():
    with io.open(os.path.join(SKILL_DIR, "defaults.json"), encoding="utf-8") as f:
        return json.load(f)


def is_junk(name):
    return name.lower() in JUNK_NAMES


def is_image(name):
    return (not is_junk(name)
            and os.path.splitext(name)[1].lower() in IMAGE_EXTS)


def tag_of_dir(dir_name):
    """'丁达尔光-36张' -> '丁达尔光'. The -N张 count is not trustworthy."""
    return re.sub(r"-\d+张$", "", dir_name)


def pick_source(candidates, priority):
    """One stem can exist as several formats; pick the most usable one.

    Matches lighting-delivery-csv's pick_source ordering so the two skills
    agree on which file is 'the' original.
    """
    def key(name):
        ext = os.path.splitext(name)[1].lower()
        return (priority.index(ext) if ext in priority else len(priority), name)

    return sorted(candidates, key=key)[0]


def index_by_stem(root):
    """(relative_dir, stem) -> [filenames] for every image under root."""
    index = collections.OrderedDict()
    for dirpath, _, filenames in os.walk(root):
        rel = os.path.relpath(dirpath, root).replace("\\", "/")
        rel = "" if rel == "." else rel
        for fn in sorted(filenames):
            if not is_image(fn):
                continue
            index.setdefault((rel, os.path.splitext(fn)[0]), []).append(fn)
    return index


def load_prompt_table(xlsx, sheet="提示词总表"):
    """tag -> [(target, prompt)] from 光照种类-提示词总表.xlsx.

    Columns are 光效图片 / 数据集状态 / 进度 / aigc生成光效 / prompt. A tag can
    appear several times with the same target; the first non-empty prompt wins,
    which is what earlier batches did.
    """
    import openpyxl

    wb = openpyxl.load_workbook(xlsx, data_only=True)
    table = collections.OrderedDict()
    empty = []
    for row in wb[sheet].iter_rows(min_row=2, values_only=True):
        src, _, _, target, prompt = (tuple(row) + (None,) * 5)[:5]
        if not src:
            continue
        src = str(src).strip()
        target = (target or "").strip()
        prompt = (prompt or "").strip()
        if not prompt:
            empty.append((src, target))
            continue
        seen = table.setdefault(src, collections.OrderedDict())
        if target not in seen:
            seen[target] = prompt
    return {k: list(v.items()) for k, v in table.items()}, empty


def read_jsonl(path):
    with io.open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def write_jsonl(path, records):
    with io.open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
