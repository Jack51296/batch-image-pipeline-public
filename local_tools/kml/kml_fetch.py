# -*- coding: utf-8 -*-
"""通过 code-server 的 /vscode-remote-resource 接口把 KML 上的文件下载到本地（复用浏览器登录态 cookie）。
用法:
  python kml_fetch.py <remote_abs_path> <local_path>            单文件
  python kml_fetch.py --list <remote_list_txt> <local_root> <remote_root>   按清单批量（清单每行一个远端绝对路径）
"""
import hashlib, json, os, sys, time, urllib.parse
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import requests
from kml_term import Kml

HOST = "https://devbox.example.com"


def session_from_browser() -> requests.Session:
    k = Kml()
    res = k.c.call("Network.getCookies", {"urls": [HOST + "/"]})
    s = requests.Session()
    for c in res["cookies"]:
        s.cookies.set(c["name"], c["value"], domain=c.get("domain") or None, path=c.get("path") or "/")
    s.headers["User-Agent"] = k.c.evaluate("navigator.userAgent")
    s.headers["Referer"] = HOST + "/"
    return s


def fetch(s: requests.Session, remote: str, local: str, retries: int = 4) -> int:
    url = HOST + "/vscode-remote-resource?path=" + urllib.parse.quote(remote, safe="")
    for i in range(retries):
        try:
            r = s.get(url, timeout=(15, 600), stream=True)
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code}: {r.text[:120]}")
            os.makedirs(os.path.dirname(os.path.abspath(local)) or ".", exist_ok=True)
            tmp = local + ".part"
            n = 0
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk)
                    n += len(chunk)
            cl = r.headers.get("Content-Length")
            if cl and int(cl) != n:
                raise RuntimeError(f"short read {n}/{cl}")
            os.replace(tmp, local)
            return n
        except Exception as e:
            if i == retries - 1:
                raise
            print(f"  retry {i+1}: {e}")
            time.sleep(2 * (i + 1))


def main():
    s = session_from_browser()
    if sys.argv[1] == "--list":
        lst, local_root, remote_root = sys.argv[2], sys.argv[3], sys.argv[4].rstrip("/")
        paths = [l.strip() for l in open(lst, encoding="utf-8") if l.strip()]
        total = 0
        t0 = time.time()
        for i, p in enumerate(paths, 1):
            assert p.startswith(remote_root + "/"), p
            rel = p[len(remote_root) + 1:]
            local = os.path.join(local_root, rel.replace("/", os.sep))
            if os.path.exists(local) and os.path.getsize(local) > 0:
                print(f"[{i}/{len(paths)}] skip exists {rel}")
                continue
            n = fetch(s, p, local)
            total += n
            print(f"[{i}/{len(paths)}] {n/1e6:8.2f} MB  {rel}")
        print(f"done: {len(paths)} files, {total/1e9:.2f} GB, {time.time()-t0:.0f}s")
    else:
        remote, local = sys.argv[1], sys.argv[2]
        n = fetch(s, remote, local)
        md5 = hashlib.md5(open(local, "rb").read()).hexdigest()
        print(f"{n} bytes  md5={md5}  -> {local}")


if __name__ == "__main__":
    main()
