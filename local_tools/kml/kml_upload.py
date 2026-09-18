# -*- coding: utf-8 -*-
"""经 xterm 合成 paste 通道上传本地文件到开发机（base64 分块 + md5 校验）。

用法: python kml_upload.py <本地文件> <远端绝对路径>
校验: 结尾打印本地 md5，与远端 md5sum（截图里 UPMD5=…）比对。
"""
import sys, os, time, base64, hashlib
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kml_term import Kml

CHUNK = 24000  # base64 字符/块


def upload(k, local, remote):
    data = open(local, "rb").read()
    md5 = hashlib.md5(data).hexdigest()
    b64 = base64.b64encode(data).decode()
    tmp = "/tmp/_up_cdp.b64"
    k.paste_text(f": > {tmp}")
    k.enter(); time.sleep(0.5)
    n = (len(b64) + CHUNK - 1) // CHUNK
    for i in range(n):
        seg = b64[i*CHUNK:(i+1)*CHUNK]
        k.paste_text(f"printf '%s' '{seg}' >> {tmp}")
        k.enter()
        time.sleep(1.2)
        print(f"  chunk {i+1}/{n}", flush=True)
    k.paste_text(
        f"base64 -d {tmp} > '{remote}' && "
        f"echo UPMD5=$(md5sum '{remote}' | cut -d' ' -f1)")
    k.enter()
    time.sleep(2.5)
    return md5


if __name__ == "__main__":
    k = Kml()
    assert k.focus_term(), "终端未聚焦"
    local, remote = sys.argv[1], sys.argv[2]
    md5 = upload(k, local, remote)
    print(f"local md5: {md5}  ({os.path.basename(local)} -> {remote})")
