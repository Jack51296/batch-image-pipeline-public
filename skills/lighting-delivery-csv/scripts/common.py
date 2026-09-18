# -*- coding: utf-8 -*-
"""Shared helpers for the lighting delivery CSV scripts."""
import csv
import io
import json
import os
import re
import sys

csv.field_size_limit(10 ** 9)

FIELDS = ["id", "prompt", "input_image_path", "output_image_path",
          "output_image_path_1", "output_image_path_2", "标签"]
OUTPUT_COLS = ["output_image_path", "output_image_path_1", "output_image_path_2"]

JUNK_NAMES = {".ds_store", "thumbs.db", "desktop.ini"}
JUNK_EXTS = {".db", ".ini", ".jsonl", ".csv", ".txt"}

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def stdout_utf8():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


class Report(object):
    """Writes to a UTF-8 file and echoes to stdout."""

    def __init__(self, path):
        self.path = path
        self.fh = io.open(path, "w", encoding="utf-8") if path else None

    def __call__(self, *parts):
        line = " ".join(str(x) for x in parts)
        if self.fh:
            self.fh.write(line + "\n")
        print(line)

    def close(self):
        if self.fh:
            self.fh.close()


def load_defaults():
    path = os.path.join(SKILL_DIR, "defaults.json")
    with io.open(path, encoding="utf-8") as f:
        return json.load(f)


def is_junk(name):
    return name.lower() in JUNK_NAMES or os.path.splitext(name)[1].lower() in JUNK_EXTS


def rel_from_batch(remote_path, batch_dir):
    """Convert a pipeline path (/ytech_milm/.../0820/<dir>/...) into a path
    relative to batch_dir, by locating the first component that exists there."""
    parts = [p for p in remote_path.replace("\\", "/").split("/") if p]
    for i, part in enumerate(parts):
        if os.path.isdir(os.path.join(batch_dir, part)):
            return "/".join(parts[i:])
    return None


def index_sources(batch_dir, source_root):
    """(relative_dir, stem) -> [filenames] for every image under source_root.
    Keys are relative to batch_dir so they line up with rel_from_batch output."""
    index = {}
    for dirpath, _, filenames in os.walk(os.path.join(batch_dir, source_root)):
        rel = os.path.relpath(dirpath, batch_dir).replace("\\", "/")
        rel = "" if rel == "." else rel
        for fn in filenames:
            if is_junk(fn):
                continue
            index.setdefault((rel, os.path.splitext(fn)[0]), []).append(fn)
    return index


def pick_source(candidates):
    """Prefer a directly usable format when one stem has several files."""
    order = [".jpg", ".jpeg", ".png", ".webp", ".avif", ".heic", ".arw"]

    def key(name):
        ext = os.path.splitext(name)[1].lower()
        return (order.index(ext) if ext in order else len(order), name)

    return sorted(candidates, key=key)[0]


def tag_of(rel_path):
    """Tag = top-level light-effect folder under the input root, minus the -N张 suffix.
    rel_path looks like '<batch>/<input_root>/<tag_dir>[/<sub>]/<file>'."""
    parts = rel_path.split("/")
    if len(parts) < 4:
        return ""
    return re.sub(r"-\d+张$", "", parts[2])


def round_of(row_id):
    m = re.search(r"-r(\d+)$", row_id or "")
    return int(m.group(1)) if m else 99


def variants(colormatched_path):
    """Return (plain, colormatched) filenames for an output path."""
    name = colormatched_path.replace("\\", "/").split("/")[-1]
    if name.endswith("_colormatched.png"):
        return name[:-len("_colormatched.png")] + ".png", name
    stem = name[:-4] if name.lower().endswith(".png") else name
    return name, stem + "_colormatched.png"


def parse_tag_list(value, defaults_key="no_colormatch_tags"):
    """--no-colormatch-tags accepts: default | none | all | comma-separated tags."""
    if value is None:
        return set()
    v = value.strip()
    if v in ("", "none"):
        return set()
    if v == "all":
        return "ALL"
    if v == "default":
        return set(load_defaults()[defaults_key])
    return set(t.strip() for t in v.split(",") if t.strip())


def wants_plain(tag, no_cm):
    return no_cm == "ALL" or (no_cm != "ALL" and tag in no_cm)


def read_delivery_csv(path):
    with io.open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        return reader.fieldnames, list(reader)


def write_delivery_csv(path, records):
    with io.open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(records)


def resolve_batch_layout(batch_dir):
    """Locate the source-image folder, the round output folder and the pipeline file."""
    entries = os.listdir(batch_dir)
    dirs = [d for d in entries if os.path.isdir(os.path.join(batch_dir, d))]
    out_dirs = sorted(d for d in dirs if d.endswith("-qt-round1") or "-round" in d)
    src_dirs = sorted(d for d in dirs if d not in out_dirs and not d.startswith("."))
    pipeline = []
    for rel in [""] + dirs:
        base = os.path.join(batch_dir, rel) if rel else batch_dir
        try:
            names = os.listdir(base)
        except OSError:
            continue
        for f in names:
            if "Prompt_with_images" in f and os.path.splitext(f)[1].lower() in (".csv", ".jsonl"):
                pipeline.append(os.path.join(rel, f) if rel else f)
    return {
        "source_dirs": src_dirs,
        "output_dirs": out_dirs,
        "pipeline_files": sorted(pipeline),
    }


def load_tasks(path):
    """Normalize a pipeline CSV or JSONL into records with a common shape.

    JSONL is only a task list: it carries no status and no output path, so the
    outputs have to be inferred from the id and confirmed against disk."""
    ext = os.path.splitext(path)[1].lower()
    tasks = []
    if ext == ".jsonl":
        with io.open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                d = json.loads(line)
                tasks.append({
                    "id": d["id"],
                    "prompt": d["prompt"],
                    "input_image_path": d["image_path"],
                    "status": "success",
                    "status_known": False,
                    "out_plain": d["id"] + ".png",
                    "out_cm": d["id"] + "_colormatched.png",
                    "light_type": d.get("light_type", ""),
                })
        return tasks
    with io.open(path, encoding="utf-8-sig", newline="") as f:
        for d in csv.DictReader(f):
            plain, cm = variants(d.get("output_image_path") or d.get("ai_output_image_path") or "")
            tasks.append({
                "id": d["id"],
                "prompt": d["prompt"],
                "input_image_path": d["input_image_path"],
                "status": d.get("image_status", "success"),
                "status_known": True,
                "out_plain": plain,
                "out_cm": cm,
                "light_type": d.get("light_type", ""),
            })
    return tasks
