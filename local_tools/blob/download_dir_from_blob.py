#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按 key 前缀把 BlobStore 上的一整棵目录拉到本地，保持目录结构。

是 upload_file_to_blob.py 的反向操作：那边 --prefix 推上去，这边同一个 --prefix
拉下来，相对路径原样还原。与 datablob_batch_download.py 的区别是不需要 Excel 清单、
且不把结果拍平成一层，直接照搬远端的目录层级。

可重入：本地已存在且大小一致的文件跳过；下载中先写 .part，成功后改名，中断不会
留下会被误判成完整的残缺文件。失败清单写到 <dest>/_failed_downloads.tsv。

用法：
    python3 download_dir_from_blob.py            # 裸跑，用下面 DEFAULT_* 的配置
    python3 download_dir_from_blob.py --dry-run  # 只列对象不落盘，先确认数量对得上
    python3 download_dir_from_blob.py --prefix ... --dest ...   # 临时改目标
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

ENDPOINT_CORP = "http://blobstore.example.com/"
ENDPOINT_INTERNAL = "http://blobstore.example.internal"

# ============================================================
# 只改这里：裸跑 `python3 download_dir_from_blob.py` 时用的配置。
# 命令行给了同名参数就以命令行为准。
DEFAULT_BUCKET = "bucket-c"
DEFAULT_PREFIX = "frank/0904wan_distill/wave13"
DEFAULT_DEST = "/ytech_milm/frank/0904wan蒸馏结果/wave13"
DEFAULT_WORKERS = 16
# ============================================================

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass

_thread_local = threading.local()
_opts: argparse.Namespace


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


def pick_endpoint(bucket: str) -> str:
    """自动选端点。IDC 机器走 corp 域名会被拒（DenyIdcAccessByCorp），办公网又访问
    不到 .internal，两边都有可能，所以挨个试一次真实请求，谁通用谁。"""
    for candidate in (ENDPOINT_INTERNAL, ENDPOINT_CORP):
        _opts.endpoint = candidate
        _thread_local.client = None
        try:
            get_client().list_objects_v2(Bucket=bucket, MaxKeys=1)
            print("端点自动选定: %s" % candidate)
            return candidate
        except Exception as e:
            print("端点 %s 不可用（%s），换下一个" % (candidate, type(e).__name__))
    raise SystemExit("两个端点都连不上，请用 --endpoint 手动指定。")


def list_prefix(bucket: str, prefix: str) -> list[tuple[str, int]]:
    client = get_client()
    out: list[tuple[str, int]] = []
    token = None
    while True:
        kw = {"Bucket": bucket, "Prefix": prefix.strip("/") + "/"}
        if token:
            kw["ContinuationToken"] = token
        resp = client.list_objects_v2(**kw)
        for obj in resp.get("Contents", []):
            if not obj["Key"].endswith("/"):
                out.append((obj["Key"], obj["Size"]))
        if not resp.get("IsTruncated"):
            break
        token = resp["NextContinuationToken"]
        print("\r已列出 %d 个对象..." % len(out), end="", flush=True)
    print("\r已列出 %d 个对象   " % len(out))
    return out


class Progress:
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
        sys.stdout.write(
            "\r|%s| %5.1f%% | %d/%d 文件 | %.1f/%.1f GB | %.1f MB/s | 剩余 %s   "
            % ("#" * filled + "-" * (30 - filled), pct, self.done_files, self.total_files,
               self.done_bytes / 1024 ** 3, self.total_bytes / 1024 ** 3, speed, eta)
        )
        sys.stdout.flush()

    def log(self, msg: str) -> None:
        with self.lock:
            sys.stdout.write("\r" + " " * 110 + "\r" + msg + "\n")
            sys.stdout.flush()
            self.last_draw = 0.0


def download_one(key: str, size: int, prefix: str, dest: Path, bucket: str,
                 progress: Progress) -> tuple[str, str]:
    rel = key[len(prefix.strip("/")) + 1:]
    out = dest / rel
    try:
        if out.exists() and out.stat().st_size == size:
            progress.add_bytes(size)
            progress.finish_file()
            return key, "skipped"
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_name(out.name + ".part")
        get_client().download_file(bucket, key, str(tmp), Callback=progress.add_bytes)
        os.replace(tmp, out)
        progress.finish_file()
        return key, "ok"
    except Exception as e:
        tmp = out.with_name(out.name + ".part")
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
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


def main() -> int:
    global _opts
    p = argparse.ArgumentParser(description="按前缀从 BlobStore 拉取整个目录")
    p.add_argument("--bucket", default=DEFAULT_BUCKET)
    p.add_argument("--prefix", default=DEFAULT_PREFIX, help="远端 key 前缀")
    p.add_argument("--dest", default=DEFAULT_DEST, help="本地目标目录，远端相对路径拼在它下面")
    p.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    p.add_argument("--endpoint", default=None,
                   help="S3 端点；默认自动在 %s 与 %s 之间选可用的那个"
                        % (ENDPOINT_INTERNAL, ENDPOINT_CORP))
    p.add_argument("--service", default="local_test")
    p.add_argument("--limit", type=int, help="只下前 N 个，用于抽样验证")
    p.add_argument("--dry-run", action="store_true")
    _opts = p.parse_args()
    _opts.workers = max(1, _opts.workers)

    dest = Path(_opts.dest).expanduser()
    print("桶: %s" % _opts.bucket)
    print("前缀: %s" % _opts.prefix)
    print("目标: %s\n" % dest)

    if _opts.endpoint is None:
        pick_endpoint(_opts.bucket)

    objs = list_prefix(_opts.bucket, _opts.prefix)
    if not objs:
        print("该前缀下没有对象。")
        return 1
    if _opts.limit:
        objs = objs[:_opts.limit]
        print("（已限制为前 %d 个）" % len(objs))
    total = sum(s for _, s in objs)
    print("合计 %s" % fmt_bytes(total))

    if _opts.dry_run:
        for key, size in objs[:20]:
            print("  %-70s %s" % (key, fmt_bytes(size)))
        if len(objs) > 20:
            print("  ... 其余 %d 条省略" % (len(objs) - 20))
        return 0

    dest.mkdir(parents=True, exist_ok=True)
    progress = Progress(total, len(objs))
    ok = skipped = 0
    failures: list[tuple[str, str]] = []
    print("开始下载，并发 %d\n" % _opts.workers)
    with ThreadPoolExecutor(max_workers=_opts.workers) as pool:
        futures = [
            pool.submit(download_one, key, size, _opts.prefix, dest, _opts.bucket, progress)
            for key, size in objs
        ]
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
    print("新下载 %d，已存在跳过 %d，失败 %d（共 %d）" % (ok, skipped, len(failures), len(objs)))
    print("传输 %s，耗时 %s，平均 %.1f MB/s" % (fmt_bytes(progress.done_bytes), fmt_seconds(elapsed), speed))
    print("=" * 78)

    if failures:
        log_path = dest / "_failed_downloads.tsv"
        with log_path.open("w", encoding="utf-8") as f:
            for key, err in failures:
                f.write("%s\t%s\n" % (key, err))
        print("失败清单: %s" % log_path)
        print("重跑本命令会跳过已下完的文件，只补失败项。")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
