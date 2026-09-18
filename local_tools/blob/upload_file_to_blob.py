#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把本地目录整棵树上传到 BlobStore（S3 协议网关），保持目录结构。

与 datablob_batch_download.py 是一对：那边按 key 拉下来，这边按目录推上去。

鉴权不走 AK/SK：签名版本为 UNSIGNED，靠 before-sign 钩子塞的 service 头做调用方
识别，值由 --service 指定。端点按运行位置选：内网 Linux 机器用 blobstore.example.internal，
办公网/VPN 的机器用 blobstore.example.com（默认）。

可重入：每个文件先 head_object 比对大小，远端已有同样大小的就跳过，所以中断后
直接重跑即可，只补没传完的部分。失败的文件会写进 _failed_uploads.tsv。

用法：
    python upload_file_to_blob.py --src <本地目录> --bucket <桶> --prefix <key前缀>
    python upload_file_to_blob.py --src ... --bucket ... --prefix ... --dry-run
    python upload_file_to_blob.py --src ... --bucket ... --prefix ... --workers 16
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import boto3
from botocore import UNSIGNED
from botocore.config import Config
from botocore.exceptions import ClientError
from boto3.s3.transfer import TransferConfig

ENDPOINT_CORP = "http://blobstore.example.com/"
ENDPOINT_INTERNAL = "http://blobstore.example.internal"
DEFAULT_WORKERS = 8
# 单个大文件的分片并发。总连接数约为 workers * 该值，别和连接池配置脱节。
PER_FILE_CONCURRENCY = 4
MULTIPART_CHUNK = 16 * 1024 * 1024

# GBK 控制台编码不了进度条里的方块字符，降级替换而不是让进程崩掉
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass

_thread_local = threading.local()
_opts: argparse.Namespace


def get_client() -> "boto3.client":
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
            max_pool_connections=_opts.workers * PER_FILE_CONCURRENCY + 8,
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


TRANSFER = TransferConfig(
    multipart_threshold=MULTIPART_CHUNK,
    multipart_chunksize=MULTIPART_CHUNK,
    max_concurrency=PER_FILE_CONCURRENCY,
    use_threads=True,
)


def scan(src: Path, prefix: str) -> list[tuple[Path, str, int]]:
    """走一遍目录树，返回 (本地路径, s3 key, 字节数)。只走一遍，大目录上省一半元数据开销。"""
    prefix = prefix.strip("/")
    items: list[tuple[Path, str, int]] = []
    for root, _, files in os.walk(src):
        rel_dir = os.path.relpath(root, src)
        for name in sorted(files):
            path = Path(root) / name
            try:
                size = path.stat().st_size
            except OSError:
                continue  # 扫描期间文件被删，跳过而不是中断整轮
            rel = name if rel_dir == "." else os.path.join(rel_dir, name)
            key = "%s/%s" % (prefix, rel.replace(os.sep, "/")) if prefix else rel.replace(os.sep, "/")
            items.append((path, key, size))
    return items


def remote_size(client, bucket: str, key: str) -> int | None:
    try:
        return client.head_object(Bucket=bucket, Key=key)["ContentLength"]
    except ClientError as e:
        if e.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
            return None
        raise


class Progress:
    """全局字节进度。并发上传下逐文件进度条没意义，这里只维护一条总进度。"""

    def __init__(self, total_bytes: int, total_files: int) -> None:
        self.total_bytes = total_bytes
        self.total_files = total_files
        self.done_bytes = 0
        self.done_files = 0
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
        pct = self.done_bytes / self.total_bytes * 100 if self.total_bytes else 100.0
        speed = self.done_bytes / 1024 ** 2 / elapsed if elapsed > 0 else 0.0
        if self.done_bytes > 0 and elapsed > 0:
            eta = fmt_seconds((self.total_bytes - self.done_bytes) / (self.done_bytes / elapsed))
        else:
            eta = "--"
        filled = int(30 * self.done_bytes / self.total_bytes) if self.total_bytes else 30
        bar = "#" * filled + "-" * (30 - filled)
        sys.stdout.write(
            "\r|%s| %5.1f%% | %d/%d 文件 | %.1f/%.1f GB | %.1f MB/s | 剩余 %s   "
            % (bar, pct, self.done_files, self.total_files,
               self.done_bytes / 1024 ** 3, self.total_bytes / 1024 ** 3, speed, eta)
        )
        sys.stdout.flush()

    def log(self, msg: str) -> None:
        with self.lock:
            sys.stdout.write("\r" + " " * 110 + "\r" + msg + "\n")
            sys.stdout.flush()
            self.last_draw = 0.0


def upload_one(item: tuple[Path, str, int], bucket: str, progress: Progress) -> tuple[str, str]:
    """返回 (key, status)，status 为 'ok' / 'skipped' / 错误信息。"""
    path, key, size = item
    client = get_client()
    try:
        if remote_size(client, bucket, key) == size:
            progress.add_bytes(size)
            progress.finish_file()
            return key, "skipped"
        client.upload_file(
            str(path), bucket, key,
            Config=TRANSFER,
            Callback=progress.add_bytes,
        )
        progress.finish_file()
        return key, "ok"
    except Exception as e:
        progress.finish_file()
        return key, "%s: %s" % (type(e).__name__, e)


def fmt_seconds(seconds: float) -> str:
    seconds = int(max(seconds, 0))
    if seconds < 60:
        return "%d秒" % seconds
    if seconds < 3600:
        return "%d分%d秒" % (seconds // 60, seconds % 60)
    return "%d小时%d分" % (seconds // 3600, (seconds % 3600) // 60)


def fmt_bytes(n: int) -> str:
    return "%.2f GB" % (n / 1024 ** 3) if n >= 1024 ** 3 else "%.1f MB" % (n / 1024 ** 2)


def run(items: list[tuple[Path, str, int]], bucket: str, workers: int) -> int:
    total_bytes = sum(s for _, _, s in items)
    progress = Progress(total_bytes, len(items))
    ok = skipped = 0
    failures: list[tuple[str, str]] = []

    print("开始上传 %d 个文件（%s），并发 %d\n" % (len(items), fmt_bytes(total_bytes), workers))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(upload_one, it, bucket, progress) for it in items]
        for future in as_completed(futures):
            key, status = future.result()
            if status == "ok":
                ok += 1
            elif status == "skipped":
                skipped += 1
            else:
                failures.append((key, status))
                progress.log("失败: %s - %s" % (key, status))

    elapsed = time.time() - progress.start
    speed = progress.done_bytes / 1024 ** 2 / elapsed if elapsed > 0 else 0.0
    print("\n" + "=" * 78)
    print("新上传 %d，已存在跳过 %d，失败 %d（共 %d）" % (ok, skipped, len(failures), len(items)))
    print("传输 %s，耗时 %s，平均 %.1f MB/s" % (fmt_bytes(progress.done_bytes), fmt_seconds(elapsed), speed))
    print("=" * 78)

    if failures:
        log_path = Path(_opts.src) / "_failed_uploads.tsv"
        with log_path.open("w", encoding="utf-8") as f:
            for key, err in failures:
                f.write("%s\t%s\n" % (key, err))
        print("失败清单: %s" % log_path)
        print("重跑本命令会自动跳过已传完的文件，只补失败项。")
    return 1 if failures else 0


def main() -> int:
    global _opts
    p = argparse.ArgumentParser(description="BlobStore 目录上传工具")
    p.add_argument("--src", required=True, help="本地目录")
    p.add_argument("--bucket", required=True, help="目标桶名")
    p.add_argument("--prefix", default="", help="key 前缀，目录结构拼在它后面")
    p.add_argument("--workers", type=int, default=DEFAULT_WORKERS, help="并发上传线程数")
    p.add_argument("--endpoint", default=ENDPOINT_CORP,
                   help="S3 端点；内网 Linux 机器用 %s" % ENDPOINT_INTERNAL)
    p.add_argument("--service", default="local_test", help="service 头的值，服务端据此识别调用方")
    p.add_argument("--limit", type=int, help="只传前 N 个，用于抽样验证")
    p.add_argument("--dry-run", action="store_true", help="只打印将要上传的内容，不实际传输")
    p.add_argument("--manifest", help="把 key 与字节数写到该 tsv，供下载侧校验")
    _opts = p.parse_args()
    _opts.workers = max(1, _opts.workers)

    src = Path(_opts.src).expanduser()
    if not src.is_dir():
        print("目录不存在: %s" % src)
        return 2

    print("扫描 %s ..." % src)
    items = scan(src, _opts.prefix)
    if not items:
        print("目录里没有文件，无事可做。")
        return 0
    if _opts.limit:
        items = items[:_opts.limit]
        print("（已限制为前 %d 个）" % len(items))

    total = sum(s for _, _, s in items)
    print("找到 %d 个文件，合计 %s" % (len(items), fmt_bytes(total)))
    print("目标: %s/%s" % (_opts.bucket, _opts.prefix.strip("/")))
    print("端点: %s  service: %s" % (_opts.endpoint, _opts.service))

    if _opts.manifest:
        with Path(_opts.manifest).open("w", encoding="utf-8") as f:
            for _, key, size in items:
                f.write("%s\t%d\n" % (key, size))
        print("清单已写入: %s" % _opts.manifest)

    if _opts.dry_run:
        print("\n--dry-run，以下为前 20 条 key：")
        for _, key, size in items[:20]:
            print("  %-70s %s" % (key, fmt_bytes(size)))
        if len(items) > 20:
            print("  ... 其余 %d 条省略" % (len(items) - 20))
        return 0

    return run(items, _opts.bucket, _opts.workers)


if __name__ == "__main__":
    raise SystemExit(main())
