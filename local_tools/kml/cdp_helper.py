# -*- coding: utf-8 -*-
"""按《流程文档-0819》机制补写的最小 CDP 客户端（等价于原 kling_cdp.py 的用法）。"""
import json
import urllib.parse
import urllib.request
import websocket

DEBUG_HOST = "http://127.0.0.1:9222"


def http_get_json(path):
    with urllib.request.urlopen(DEBUG_HOST + path) as r:
        return json.load(r)


def open_tab(url="about:blank"):
    """新开独立标签页并连接，返回 (CDP, tab_id)。
    辅助脚本（salvage/检查类）必须用独立页，避免与正在批量轮询的主页面互抢。"""
    req = urllib.request.Request(
        f"{DEBUG_HOST}/json/new?{urllib.parse.quote(url, safe='')}", method="PUT")
    with urllib.request.urlopen(req) as r:
        tab = json.load(r)
    return CDP(tab["webSocketDebuggerUrl"]), tab["id"]


def close_tab(tab_id):
    urllib.request.urlopen(f"{DEBUG_HOST}/json/close/{tab_id}")


class CDP:
    def __init__(self, ws_url):
        # Chrome 若未加 --remote-allow-origins 会拒绝带 Origin 头的连接，故不发送 Origin
        self.ws = websocket.create_connection(ws_url, timeout=180, suppress_origin=True)
        self._id = 0

    def call(self, method, params=None):
        self._id += 1
        msg_id = self._id
        self.ws.send(json.dumps({"id": msg_id, "method": method, "params": params or {}}))
        while True:
            resp = json.loads(self.ws.recv())
            if resp.get("id") == msg_id:
                if "error" in resp:
                    raise RuntimeError(resp["error"])
                return resp.get("result", {})

    def evaluate(self, expr, await_promise=False):
        res = self.call("Runtime.evaluate", {
            "expression": expr,
            "returnByValue": True,
            "awaitPromise": await_promise,
        })
        if res.get("exceptionDetails"):
            raise RuntimeError(res["exceptionDetails"])
        return res.get("result", {}).get("value")

    def close(self):
        self.ws.close()


def connect(url_contains="chatgpt"):
    for tab in http_get_json("/json/list"):
        if tab.get("type") == "page" and url_contains in tab.get("url", ""):
            return CDP(tab["webSocketDebuggerUrl"])
    raise RuntimeError(f"no tab matching {url_contains!r}")
