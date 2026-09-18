# -*- coding: utf-8 -*-
"""用 Ctrl+P (QuickOpen) 在网页版 VS Code 里打开文件预览并截图（目检生成图用）。

用法: python kml_view_img.py <文件名关键字>
注意: QuickOpen 只索引工作区（/mnt/kfs/bob）内的文件。
"""
import sys, os, time
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kml_term import Kml

PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
name = sys.argv[1]

k = Kml()
k.escape(2)
k.key("P", "KeyP", 80, modifiers=2)  # Ctrl+P
time.sleep(1.0)
k.insert(name)
time.sleep(1.5)
rows = k.c.evaluate(
    "Array.from(document.querySelectorAll('.quick-input-widget .monaco-list-row'))"
    ".map(r=>r.textContent.trim()).slice(0,5)")
print("候选:", rows)
k.enter()
time.sleep(3)
print("screenshot:", k.screenshot(os.path.join(PKG, "_kml_screen.png")))
