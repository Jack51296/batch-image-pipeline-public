# -*- coding: utf-8 -*-
"""把本地 python 脚本以 heredoc 方式粘贴到 KML 终端执行（等效于上传+运行）。
用法: python kml_heredoc.py <本地py文件> [等待秒] [远端工作目录]
截图输出到包根目录 _kml_screen.png。"""
import sys, os, time
sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from kml_term import Kml

src = open(sys.argv[1], encoding="utf-8").read().replace("\r\n", "\n")
wait = float(sys.argv[2]) if len(sys.argv) > 2 else 15
cwd = sys.argv[3] if len(sys.argv) > 3 else "/mnt/kfs/bob/光影光效2"
text = ("clear; cd " + cwd + " && python3 - <<'PYEOF'\n"
        + src + "\nPYEOF\n")

k = Kml()
assert k.focus_term(), "终端未聚焦"
print(k.paste_text(text))
time.sleep(1.0)
k.enter()
time.sleep(wait)
k.screenshot(os.path.join(os.path.dirname(HERE), "_kml_screen.png"))
print("done, screenshot saved")
