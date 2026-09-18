#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按「交付记录汇总表」里的 image1/image2 两列，把每一行对应的 src / target 图片
从 BlobStore 拉到本地，并把汇总表本身也拷一份放到目标目录下。

是 download_dir_from_blob.py 的变体：那个是按 key 前缀拉一整棵目录，这个是按
Excel 表里列出的、分散在不同前缀下的具体文件列表来拉，且明确分成 src/target
两个子目录（而不是照搬远端目录结构，因为远端各批次目录命名不统一，直接拉
容易在同名文件如 0_src.png 上互相覆盖）。

远端路径格式：Excel 里 image1/image2 列存的是 "mmu:<bucket>:<key>"，例如
    mmu:aiplatform-temp:label_data/20260827_AI六妆转秋冬妆_labkit/0_src.png
实际调用 S3 时 bucket 要加 "mmu-" 前缀，即 bucket="bucket-a"
（已用 head_object / list_objects_v2 验证过，见下方 BUCKET_PREFIX_MAP 逻辑）。

本地命名规则（避免不同来源文件之间的文件名冲突）：
    {全局行号:04d}_{category_raw}_{metadataId}_src{原始后缀}
    {全局行号:04d}_{category_raw}_{metadataId}_target{原始后缀}
    category_raw 中的 "/" 等文件系统非法字符会被替换成 "_"。

可重入：本地已存在且大小一致的文件跳过；下载中先写 .part，成功后改名，中断
    不会留下会被误判成完整的残缺文件。失败清单写到 <dest>/_failed_downloads.tsv。

用法：
    python3 download_0903_makeup_images.py                # 裸跑，用下面 DEFAULT_* 配置
    python3 download_0903_makeup_images.py --dry-run       # 只列文件不落盘，先确认数量/大小
    python3 download_0903_makeup_images.py --limit 20      # 抽样验证
    python3 download_0903_makeup_images.py --table ... --dest ...   # 临时改输入/输出
"""

import argparse
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import boto3
import openpyxl
from botocore import UNSIGNED
from botocore.config import Config

ENDPOINT_CORP = "http://blobstore.example.com/"
ENDPOINT_INTERNAL = "http://blobstore.example.internal"

# ============================================================
# 只改这里：裸跑 `python3 download_0903_makeup_images.py` 时用的配置。
# 命令行给了同名参数就以命令行为准。
DEFAULT_TABLE = "/mnt/kfs/carol/ketu/frank/图生图/0903/美妆图生图_交付记录_汇总_0903.xlsx"
DEFAULT_SHEET = "汇总"
DEFAULT_DEST = "/mnt/kfs/carol/ketu/frank/图生图/0903"
DEFAULT_WORKERS = 16
# ============================================================

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass

_thread_local = threading.local()
_opts = None  # argparse.Namespace, assigned in main()


def get_client():
    """botocore client 不保证跨线程共享安全，每个线程持有独立实例。"""
    client = getattr(_thread_local, "client", None)
    if client is not None:
        return client
    client = boto3.client(
        service_name="s3",
        endpoint_url=_opts.endpoint,
        use_ssl=False,
        config=Config(
            region_name="HB1",
            signature_version=UNSIGNED,
            s3={"addressing_style": "path"},
            max_pool_connections=_opts.workers * 2 + 8,
            connect_timeout=15,
            read_timeout=120,
            retries={"max_attempts": 5, "mode": "standard"},
        ),
    )
    service = _opts.service
    client.meta.events.register(
        "before-sign.*.*",
        lambda request, **kw: request.headers.add_header("service", service),
    )
    _thread_local.client = client
    return client


def pick_endpoint(probe_bucket: str) -> str:
    """自动选端点。IDC 机器走 corp 域名会被拒（DenyIdcAccessByCorp），办公网又访问
    不到 .internal，两边都有可能，所以挨个试一次真实请求，谁通用谁。

    注意：探测专门用短超时、不重试的临时 client，避免在网络单通的机器上，因为
    正式下载用的重试配置（5 次重试、15s 连接超时）而在不通的那个端点上卡很久。"""
    for candidate in (ENDPOINT_INTERNAL, ENDPOINT_CORP):
        probe_client = boto3.client(
            service_name="s3",
            endpoint_url=candidate,
            use_ssl=False,
            config=Config(
                region_name="HB1",
                signature_version=UNSIGNED,
                s3={"addressing_style": "path"},
                connect_timeout=5,
                read_timeout=8,
                retries={"max_attempts": 1, "mode": "standard"},
            ),
        )
        probe_client.meta.events.register(
            "before-sign.*.*",
            lambda request, **kw: request.headers.add_header("service", _opts.service),
        )
        try:
            probe_client.list_objects_v2(Bucket=probe_bucket, MaxKeys=1)
            print("端点自动选定: %s" % candidate)
            _opts.endpoint = candidate
            _thread_local.client = None
            return candidate
        except Exception as e:
            print("端点 %s 不可用（%s），换下一个" % (candidate, type(e).__name__))
    raise SystemExit("两个端点都连不上，请用 --endpoint 手动指定。")


MMU_RE = re.compile(r"^mmu:([^:]+):(.+)$")
ILLEGAL_FS_CHARS = re.compile(r"[\\/:*?\"<>|]")


def parse_mmu_path(raw):
    """'mmu:aiplatform-temp:label_data/xxx/0_src.png'
    -> ('bucket-a', 'label_data/xxx/0_src.png')
    实际 S3 bucket 名字要在 mmu 段前面加 'mmu-' 前缀（已用 head_object 验证过）。"""
    if not raw:
        return None
    raw = str(raw).strip()
    m = MMU_RE.match(raw)
    if not m:
        return None
    bucket, key = m.group(1), m.group(2)
    return "mmu-" + bucket, key


def safe_stem(s: str) -> str:
    s = "" if s is None else str(s)
    return ILLEGAL_FS_CHARS.sub("_", s).strip() or "unknown"


def load_table(table_path: Path, sheet_name: str):
    """读取汇总表，返回 [(idx, bucket1, key1, out1, bucket2, key2, out2), ...] 以及行数统计。
    idx 从 1 开始，是汇总表里的行号（不含表头），用来保证本地文件名全局唯一。"""
    wb = openpyxl.load_workbook(table_path, data_only=True)
    if sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
    else:
        ws = wb.worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    header = [str(c) if c is not None else "" for c in rows[0]]
    idx_map = {h: i for i, h in enumerate(header)}

    required = ["category_raw", "metadataId", "image1", "image2"]
    missing = [c for c in required if c not in idx_map]
    if missing:
        raise SystemExit("表里缺少必需列: %s（现有列: %s）" % (missing, header))

    tasks = []
    skipped_no_path = 0
    for i, row in enumerate(rows[1:], start=1):
        if row is None or all(c is None for c in row):
            continue
        category_raw = safe_stem(row[idx_map["category_raw"]])
        metadata_id = row[idx_map["metadataId"]]
        img1_raw = row[idx_map["image1"]]
        img2_raw = row[idx_map["image2"]]

        p1 = parse_mmu_path(img1_raw)
        p2 = parse_mmu_path(img2_raw)
        if not p1 or not p2:
            skipped_no_path += 1
            continue

        bucket1, key1 = p1
        bucket2, key2 = p2
        ext1 = Path(key1).suffix or ".png"
        ext2 = Path(key2).suffix or ".png"
        stem = "%04d_%s_%s" % (i, category_raw, metadata_id)
        out1 = "target_real/%s_src%s" % (stem, ext1)
        out2 = "source_aigc/%s_target%s" % (stem, ext2)
        tasks.append((i, bucket1, key1, out1, bucket2, key2, out2))
    wb.close()
    return tasks, skipped_no_path


class Progress:
    def __init__(self, total_files: int) -> None:
        self.total_files = total_files
        self.done_files = 0
        self.done_bytes = 0
        self.lock = threading.Lock()
        self.start = time.time()
        self.last_draw = 0.0

    def add_bytes(self, n: int) -> None:
        with self.lock:
            self.done_bytes += n
            self._maybe_draw()

    def finish_file(self) -> None:
        with self.lock:
            self.done_files += 1
            self._maybe_draw(force=True)

    def _maybe_draw(self, force: bool = False) -> None:
        now = time.time()
        if not force and now - self.last_draw < 0.5:
            return
        self.last_draw = now
        elapsed = now - self.start
        pct = self.done_files / self.total_files * 100 if self.total_files else 100.0
        speed = self.done_bytes / 1024 ** 2 / elapsed if elapsed > 0 else 0.0
        filled = int(30 * self.done_files / self.total_files) if self.total_files else 30
        sys.stdout.write(
            "\r|%s| %5.1f%% | %d/%d 文件 | %.1f MB | %.1f MB/s   "
            % ("#" * filled + "-" * (30 - filled), pct, self.done_files, self.total_files,
               self.done_bytes / 1024 ** 2, speed)
        )
        sys.stdout.flush()

    def log(self, msg: str) -> None:
        with self.lock:
            sys.stdout.write("\r" + " " * 110 + "\r" + msg + "\n")
            sys.stdout.flush()
            self.last_draw = 0.0


def download_one(bucket, key, dest, rel_out, progress):
    out = dest / rel_out
    tag = "%s:%s -> %s" % (bucket, key, rel_out)
    try:
        size = None
        try:
            head = get_client().head_object(Bucket=bucket, Key=key)
            size = head.get("ContentLength")
        except Exception:
            pass  # 拿不到就不做“已存在且大小一致”跳过判断，直接下

        if size is not None and out.exists() and out.stat().st_size == size:
            progress.add_bytes(size)
            progress.finish_file()
            return tag, "skipped"

        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_name(out.name + ".part")
        get_client().download_file(bucket, key, str(tmp), Callback=progress.add_bytes)
        os.replace(tmp, out)
        progress.finish_file()
        return tag, "ok"
    except Exception as e:
        tmp = out.with_name(out.name + ".part")
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        progress.finish_file()
        return tag, "%s: %s" % (type(e).__name__, e)


def fmt_seconds(seconds: float) -> str:
    seconds = int(max(seconds, 0))
    if seconds < 60:
        return "%d秒" % seconds
    if seconds < 3600:
        return "%d分%d秒" % (seconds // 60, seconds % 60)
    return "%d小时%d分" % (seconds // 3600, (seconds % 3600) // 60)


def copy_table_with_local_paths(table_path, sheet_name, dest, tasks):
    """把汇总表拷一份到 dest 下，并追加两列本地相对路径，方便对照。"""
    wb = openpyxl.load_workbook(table_path, data_only=False)
    if sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
    else:
        ws = wb.worksheets[0]

    header_row = 1
    max_col = ws.max_column
    ws.cell(row=header_row, column=max_col + 1, value="本地src路径")
    ws.cell(row=header_row, column=max_col + 2, value="本地target路径")

    dest_str = str(dest).rstrip('/\\')
    by_idx = {t[0]: (t[3], t[6]) for t in tasks}  # 行号 -> (rel_out1, rel_out2)
    for i in range(2, ws.max_row + 1):
        local = by_idx.get(i - 1)
        if local:
            ws.cell(row=i, column=max_col + 1, value=dest_str + '/' + local[0])
            ws.cell(row=i, column=max_col + 2, value=dest_str + '/' + local[1])

    out_path = dest / table_path.name
    wb.save(out_path)
    wb.close()
    return out_path


def main() -> int:
    global _opts
    p = argparse.ArgumentParser(description="按汇总表拉取 src/target 图片到本地")
    p.add_argument("--table", default=DEFAULT_TABLE, help="汇总表 xlsx 路径")
    p.add_argument("--sheet", default=DEFAULT_SHEET, help="汇总表里数据所在的 sheet 名")
    p.add_argument("--dest", default=DEFAULT_DEST, help="本地目标目录，会在下面建 src/ target/")
    p.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    p.add_argument("--endpoint", default=None,
                   help="S3 端点；默认自动在 %s 与 %s 之间选可用的那个"
                        % (ENDPOINT_INTERNAL, ENDPOINT_CORP))
    p.add_argument("--service", default="local_test")
    p.add_argument("--limit", type=int, help="只下前 N 行，用于抽样验证")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--skip-table-copy", action="store_true", help="不把汇总表拷到 dest 下")
    _opts = p.parse_args()
    _opts.workers = max(1, _opts.workers)

    table_path = Path(_opts.table).expanduser()
    dest = Path(_opts.dest).expanduser()
    if not table_path.exists():
        raise SystemExit("汇总表不存在: %s" % table_path)

    print("汇总表: %s" % table_path)
    print("目标目录: %s\n" % dest)

    tasks, skipped_no_path = load_table(table_path, _opts.sheet)
    if skipped_no_path:
        print("警告：%d 行的 image1/image2 无法解析为 mmu:bucket:key 格式，已跳过。" % skipped_no_path)
    if _opts.limit:
        tasks = tasks[:_opts.limit]
        print("（已限制为前 %d 行）" % len(tasks))

    total_files = len(tasks) * 2
    print("共 %d 行，%d 个文件（src+target）\n" % (len(tasks), total_files))

    if not tasks:
        print("没有可下载的行。")
        return 1

    if _opts.endpoint is None:
        pick_endpoint(tasks[0][1])
    else:
        _opts.endpoint = _opts.endpoint

    if _opts.dry_run:
        for i, b1, k1, o1, b2, k2, o2 in tasks[:20]:
            print("  #%04d  %s:%s  ->  %s" % (i, b1, k1, o1))
            print("        %s:%s  ->  %s" % (b2, k2, o2))
        if len(tasks) > 20:
            print("  ... 其余 %d 行省略" % (len(tasks) - 20))
        return 0

    dest.mkdir(parents=True, exist_ok=True)
    (dest / "target_real").mkdir(parents=True, exist_ok=True)
    (dest / "source_aigc").mkdir(parents=True, exist_ok=True)

    progress = Progress(total_files)
    ok = skipped = 0
    failures = []  # list of (tag, error_str)
    print("开始下载，并发 %d\n" % _opts.workers)
    with ThreadPoolExecutor(max_workers=_opts.workers) as pool:
        futures = []
        for i, b1, k1, o1, b2, k2, o2 in tasks:
            futures.append(pool.submit(download_one, b1, k1, dest, o1, progress))
            futures.append(pool.submit(download_one, b2, k2, dest, o2, progress))
        for future in as_completed(futures):
            tag, status = future.result()
            if status == "ok":
                ok += 1
            elif status == "skipped":
                skipped += 1
            else:
                failures.append((tag, status))
                progress.log("失败: %s - %s" % (tag, status))

    elapsed = time.time() - progress.start
    speed = progress.done_bytes / 1024 ** 2 / elapsed if elapsed > 0 else 0.0
    print("\n" + "=" * 78)
    print("新下载 %d，已存在跳过 %d，失败 %d（共 %d）" % (ok, skipped, len(failures), total_files))
    print("传输 %.1f MB，耗时 %s，平均 %.1f MB/s" % (progress.done_bytes / 1024 ** 2, fmt_seconds(elapsed), speed))
    print("=" * 78)

    if failures:
        log_path = dest / "_failed_downloads.tsv"
        with log_path.open("w", encoding="utf-8") as f:
            for tag, err in failures:
                f.write("%s\t%s\n" % (tag, err))
        print("失败清单: %s" % log_path)
        print("重跑本命令会跳过已下完的文件，只补失败项。")

    if not _opts.skip_table_copy:
        out_table = copy_table_with_local_paths(table_path, _opts.sheet, dest, tasks)
        print("汇总表已拷贝并附加本地路径列: %s" % out_table)

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
