# -*- coding: utf-8 -*-
"""隐藏侧栏+面板后用 QuickOpen 打开文件并截图（比 kml_view_img.py 显示区域更大），完毕后恢复布局。
用法: python kml_view_big.py <文件名关键字> <截图输出路径>
"""
import sys, os, time
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kml_term import Kml

name, out = sys.argv[1], sys.argv[2]
k = Kml()
k.escape(2)
k.key("b", "KeyB", 66, modifiers=2)   # Ctrl+B 隐藏侧栏
time.sleep(0.5)
k.key("j", "KeyJ", 74, modifiers=2)   # Ctrl+J 隐藏面板
time.sleep(0.8)
k.key("P", "KeyP", 80, modifiers=2)   # Ctrl+P
time.sleep(1.0)
k.insert(name)
time.sleep(1.5)
rows = k.c.evaluate(
    "Array.from(document.querySelectorAll('.quick-input-widget .monaco-list-row'))"
    ".map(r=>r.textContent.trim()).slice(0,3)")
print("候选:", rows)
k.enter()
time.sleep(3.5)
print("screenshot:", k.screenshot(out))
k.escape(1)
k.key("b", "KeyB", 66, modifiers=2)   # 恢复侧栏
time.sleep(0.5)
k.key("j", "KeyJ", 74, modifiers=2)   # 恢复面板
time.sleep(0.5)
