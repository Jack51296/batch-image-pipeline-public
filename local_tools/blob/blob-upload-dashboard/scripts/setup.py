# -*- coding: utf-8 -*-
"""一次性初始化：把这套上传 + 看板流程部署到某个人自己的目录。

    python setup.py --root D:\\我的项目\\upload_content --prefix 我的前缀

干四件事：
    1. 建好工作目录，把主程序和看板页面拷过去
    2. 写 blob_config.json（以后改配置只改它，不动 .py）
    3. 连一下桶，确认网络和前缀都通
    4. 打印接下来的三条命令

不给参数就问你要。已经存在的配置会读出来当默认值，回车即保留。
"""

import argparse
import io
import json
import os
import shutil
import sys

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_NAME = "blob_config.json"

# 跟着一起部署的文件。html 用通配是因为看板页面允许改名
ASSETS = ["auto_upload_to_blob.py", "dashboard_server.py"]

DEFAULTS = {
    "bucket": "bucket-b",
    "endpoint": "https://blobstore.example.com",
    "public_host": "https://blobstore.example.com",
    "service_header": "grpc_onlineEarningGenRpcService",
}

TEMPLATE_NOTE = [
    "这套上传 + 看板流程的全部配置，改这里，不要改 .py。",
    "local_root  本地要上传的根目录，它下面每个子文件夹算一个批次",
    "key_prefix  线上前缀，最终地址是 <public_host>/<bucket>/<key_prefix>/<批次>/...",
    "换人用的时候只需要改 local_root 和 key_prefix 两项。",
]


def ask(prompt, default=""):
    tip = "%s[%s]: " % (prompt, default) if default else "%s: " % prompt
    try:
        got = input(tip).strip().strip('"').strip("'")
    except (EOFError, KeyboardInterrupt):
        print("")
        sys.exit(1)
    return got or default


def html_assets():
    return [n for n in os.listdir(SKILL_DIR) if n.lower().endswith(".html")]


def read_existing(work_dir):
    path = os.path.join(work_dir, CONFIG_NAME)
    if not os.path.isfile(path):
        return {}
    try:
        with io.open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def check_bucket(work_dir):
    """连一下桶，把结果说清楚。返回 True 表示通了。"""
    sys.path.insert(0, work_dir)
    for mod in ("auto_upload_to_blob",):
        sys.modules.pop(mod, None)
    try:
        import auto_upload_to_blob as up
    except ImportError as e:
        print("  [×] 导入失败：%s" % e)
        print("      多半是缺 boto3，装一下：pip install boto3 openpyxl")
        return False
    try:
        client = up.get_blob_s3_client()
        prefix = up.KEY_PREFIX.strip("/") + "/"
        resp = client.list_objects_v2(Bucket=up.BUCKET, Prefix=prefix, MaxKeys=5)
        n = resp.get("KeyCount", 0)
        if n:
            print("  [√] 连通，%s 下已经有东西了" % prefix)
        else:
            print("  [√] 连通，%s 现在还是空的（第一次用就该是空的）" % prefix)
        return True
    except Exception as e:
        print("  [×] 连不上：%s" % e)
        print("      检查：是不是在内网/办公网？endpoint 对不对？")
        return False


def main():
    p = argparse.ArgumentParser(description="部署上传 + 看板流程到你自己的目录")
    p.add_argument("--root", help="本地要上传的根目录，如 D:\\我的项目\\upload_content")
    p.add_argument("--prefix", help="线上前缀，如 zhangsan_data")
    p.add_argument("--bucket", default=DEFAULTS["bucket"], help="桶名")
    p.add_argument("--work-dir", default=None,
                   help="脚本装到哪，默认装到 root 的上一层")
    p.add_argument("--no-check", action="store_true", help="跳过连通性自检")
    args = p.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    print("=" * 70)
    print("部署「上传到 BS3 + 数据看板」")
    print("=" * 70)

    root = args.root or ask("\n本地要上传的根目录（把文件夹拖进来）")
    if not root:
        print("[×] 没给目录，退出")
        return 1
    root = os.path.abspath(os.path.expanduser(root))

    work_dir = args.work_dir or os.path.dirname(root)
    work_dir = os.path.abspath(os.path.expanduser(work_dir))
    old = read_existing(work_dir)

    prefix = args.prefix or ask(
        "线上前缀（你在桶里的文件夹名，别和同事重名）",
        old.get("key_prefix", ""))
    if not prefix:
        print("[×] 没给前缀，退出")
        return 1
    prefix = prefix.strip("/")

    print("")
    print("  本地根目录: %s" % root)
    print("  脚本装到  : %s" % work_dir)
    print("  线上位置  : %s/%s/%s/"
          % (DEFAULTS["public_host"], args.bucket, prefix))
    print("")

    os.makedirs(root, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)

    print("拷贝主程序：")
    for name in ASSETS + html_assets():
        src = os.path.join(SKILL_DIR, name)
        dst = os.path.join(work_dir, name)
        if not os.path.isfile(src):
            print("  [跳过] 技能目录里没有 %s" % name)
            continue
        shutil.copy2(src, dst)
        print("  %s" % name)

    cfg = dict(DEFAULTS)
    cfg.update({k: v for k, v in old.items() if not k.startswith("_")})
    cfg["local_root"] = root.replace("\\", "/")
    cfg["key_prefix"] = prefix
    cfg["bucket"] = args.bucket
    ordered = {"_说明": TEMPLATE_NOTE}
    for k in ("local_root", "bucket", "key_prefix",
              "endpoint", "public_host", "service_header"):
        ordered[k] = cfg[k]

    cfg_path = os.path.join(work_dir, CONFIG_NAME)
    with io.open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(ordered, f, ensure_ascii=False, indent=2)
        f.write(u"\n")
    print("\n配置已写入 %s" % cfg_path)

    if not args.no_check:
        print("\n连通性自检：")
        check_bucket(work_dir)

    print("\n" + "=" * 70)
    print("装好了。接下来三条命令，都在 %s 里跑：" % work_dir)
    print("=" * 70)
    print("""
  1. 把素材按批次放进 %s
     每个子文件夹算一个批次，比如 0806\\、0812\\

  2. 上传（先看要传什么，确认了再真传）
       python auto_upload_to_blob.py --once --dry-run
       python auto_upload_to_blob.py --once

  3. 看板
       python auto_upload_to_blob.py --publish     # 发布线上版
       python dashboard_server.py                  # 或本地起服务看
""" % root)
    return 0


if __name__ == "__main__":
    sys.exit(main())
