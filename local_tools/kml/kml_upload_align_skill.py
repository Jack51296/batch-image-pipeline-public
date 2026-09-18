# -*- coding: utf-8 -*-
"""把包内 remote-skills/lighting-destyle-align 整体上传到开发机代码目录，并 md5 校验。

用法: python kml_upload_align_skill.py [远端代码目录]
默认远端代码目录: /mnt/kfs/bob/光影光效2
（换新开发机/新代码目录时传参即可。）
"""
import sys, os, time
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kml_term import Kml
from kml_upload import upload

PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(PKG, "remote-skills", "lighting-destyle-align")
CODE_DIR = sys.argv[1] if len(sys.argv) > 1 else "/mnt/kfs/bob/光影光效2"
DST = f"{CODE_DIR}/skills/lighting-destyle-align"
FILES = ["align_batch.py", "align_core.py", "config.json",
         "requirements-batch.txt", "index.html", "SKILL.md", "使用说明-批处理.txt"]

k = Kml()
assert k.focus_term(), "终端未聚焦"
k.paste_text(f"mkdir -p '{DST}' && cd '{DST}' && pwd")
k.enter(); time.sleep(1.5)

expect = {}
for f in FILES:
    local = os.path.join(SRC, f)
    print(f"上传 {f} ({os.path.getsize(local):,}B)…", flush=True)
    expect[f] = upload(k, local, f"{DST}/{f}")

lst = " ".join(f"'{DST}/{f}'" for f in FILES)
k.paste_text(f"clear; md5sum {lst}")
k.enter(); time.sleep(2.5)
k.screenshot(os.path.join(PKG, "_kml_screen.png"))
print("\n本地 md5（与截图 _kml_screen.png 逐行比对，必须全部一致）:")
for f, m in expect.items():
    print(f"  {m}  {f}")
