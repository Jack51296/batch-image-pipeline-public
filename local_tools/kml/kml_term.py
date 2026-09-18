# -*- coding: utf-8 -*-
"""驱动 KML 开发机网页版 VS Code（code-server）的 CDP 工具类。

前置：调试 Chrome 已启动并已登录开发机页面（见包根 SKILL.md 第一节）。
要点（0907 实测结论）：
- 终端是 canvas 渲染，读不到文字 → 回显一律靠整页截图；
- Input.insertText 进不了 xterm → 命令输入用逐字符按键(type_text)或合成粘贴(paste_text)；
- 命令面板 / QuickOpen 是普通 DOM，insert() 可用，候选列表可直接读取。
"""
import sys, os, time, json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cdp_helper import connect


class Kml:
    def __init__(self, url_contains="devbox"):
        self.c = connect(url_contains)

    # ---------- 底层输入 ----------
    def key(self, key, code, vk, modifiers=0, text=None):
        """modifiers: 1=Alt 2=Ctrl 4=Meta 8=Shift（可相加）"""
        for t in ("rawKeyDown", "keyUp"):
            p = {"type": t, "modifiers": modifiers, "key": key, "code": code,
                 "windowsVirtualKeyCode": vk, "nativeVirtualKeyCode": vk}
            if text and t == "rawKeyDown":
                p["type"] = "keyDown"
                p["text"] = text
            self.c.call("Input.dispatchKeyEvent", p)

    def enter(self):
        self.key("Enter", "Enter", 13, text="\r")

    def escape(self, n=1):
        for _ in range(n):
            self.key("Escape", "Escape", 27)
            time.sleep(0.3)

    def insert(self, s):
        """仅对普通 DOM 输入框有效（命令面板/QuickOpen）；对 xterm 终端无效。"""
        self.c.call("Input.insertText", {"text": s})

    def type_text(self, s, delay=0.012):
        """逐字符按键输入，可进 xterm。适合短命令。"""
        for ch in s:
            self.c.call("Input.dispatchKeyEvent", {"type": "keyDown", "text": ch, "key": ch})
            self.c.call("Input.dispatchKeyEvent", {"type": "keyUp", "key": ch})
            time.sleep(delay)

    def paste_text(self, s):
        """向 xterm 注入合成 paste 事件，一次可输入大段文本（上传文件用）。"""
        payload = json.dumps(s)
        return self.c.evaluate(
            "(()=>{const t=document.querySelector('.xterm-helper-textarea');"
            "if(!t)return 'no textarea';"
            "const dt=new DataTransfer();dt.setData('text/plain'," + payload + ");"
            "const ev=new ClipboardEvent('paste',{clipboardData:dt,bubbles:true,cancelable:true});"
            "t.dispatchEvent(ev);return 'pasted';})()")

    # ---------- 高层操作 ----------
    def palette(self, cmd, pick=True):
        """F1 命令面板：输入命令名，返回候选列表，pick=True 时回车执行第一项。"""
        self.key("F1", "F1", 112)
        time.sleep(1.0)
        self.insert(cmd)
        time.sleep(1.2)
        rows = self.c.evaluate(
            "Array.from(document.querySelectorAll('.quick-input-widget .monaco-list-row'))"
            ".map(r=>r.textContent.trim()).slice(0,6)")
        if pick:
            self.enter()
            time.sleep(1.0)
        return rows

    def open_terminal(self):
        """Ctrl+` 打开/聚焦终端面板。"""
        self.key("`", "Backquote", 192, modifiers=2)
        time.sleep(2)

    def focus_term(self):
        return self.c.evaluate(
            "(()=>{const t=document.querySelector('.terminal.xterm textarea.xterm-helper-textarea');"
            "if(!t)return false; t.focus(); return document.activeElement===t;})()")

    def run(self, cmd, wait=5):
        """在终端执行一条命令（不读回显，回显请配合 screenshot()）。"""
        assert self.focus_term(), "终端未聚焦（先 open_terminal）"
        self.type_text(cmd)
        time.sleep(0.5)
        self.enter()
        time.sleep(wait)

    def screenshot(self, path):
        import base64
        res = self.c.call("Page.captureScreenshot", {"format": "png"})
        open(path, "wb").write(base64.b64decode(res["data"]))
        return path
