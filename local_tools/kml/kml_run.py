# -*- coding: utf-8 -*-
"""在 KML 终端跑一条命令并截图到包根 _kml_screen.png。

用法:
  python kml_run.py "命令" [等待秒数]
  python kml_run.py @命令文件.txt [等待秒数]   # 命令含引号/$ 时用文件方式，避免转义问题

看结果: 打开包根目录下的 _kml_screen.png（长输出建议命令里加 | tail -25，
终端面板先用命令面板 "View: Toggle Maximized Panel" 最大化，一屏约 50 行）。
"""
import sys, os, time
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kml_term import Kml

PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHOT = os.path.join(PKG, "_kml_screen.png")

cmd = sys.argv[1]
if cmd.startswith("@"):
    cmd = open(cmd[1:], encoding="utf-8").read().strip().replace("\r\n", "\n")
    assert "\n" not in cmd, "命令文件必须是单行（多行会被终端逐行执行）"
wait = float(sys.argv[2]) if len(sys.argv) > 2 else 4

k = Kml()
assert k.focus_term(), "终端未聚焦（页面上先 Ctrl+` 打开终端，或重登开发机）"
k.type_text(cmd)
time.sleep(0.5)
k.enter()
time.sleep(wait)
k.screenshot(SHOT)
print("done:", cmd)
print("screenshot:", SHOT)
