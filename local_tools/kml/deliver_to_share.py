# -*- coding: utf-8 -*-
"""按 KML 上生成的 manifest（md5  绝对路径）把交付文件下载到网盘目录，保持相对结构并校验 md5。
用法: python deliver_to_share.py <manifest_local_path> <remote_root> <share_root>
  manifest 里每个路径都必须在 remote_root 之下；落盘到 share_root/<相对路径>。
"""
import hashlib, os, sys, time, urllib.parse
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kml_fetch import session_from_browser, HOST  # noqa: E402
import requests  # noqa: E402

manifest, remote_root, share_root = sys.argv[1], sys.argv[2].rstrip("/"), sys.argv[3]
items = []
for line in open(manifest, encoding="utf-8"):
    line = line.rstrip("\n")
    if not line.strip():
        continue
    md5, path = line[:32], line[34:] if line[32:34] == "  " else line[33:].lstrip()
    items.append((md5.strip(), path.strip()))
print(f"manifest: {len(items)} files")

s = session_from_browser()
ok = skipped = 0
bad = []
total = 0
t0 = time.time()
for i, (md5, remote) in enumerate(items, 1):
    assert remote.startswith(remote_root + "/"), remote
    rel = remote[len(remote_root) + 1:]
    local = os.path.join(share_root, rel.replace("/", os.sep))
    os.makedirs(os.path.dirname(local), exist_ok=True)
    if os.path.exists(local):
        h = hashlib.md5(open(local, "rb").read()).hexdigest()
        if h == md5:
            skipped += 1
            print(f"[{i}/{len(items)}] ok(exists) {rel}")
            continue
    url = HOST + "/vscode-remote-resource?path=" + urllib.parse.quote(remote, safe="")
    done = False
    for attempt in range(4):
        try:
            r = s.get(url, timeout=(15, 600), stream=True)
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code}")
            h = hashlib.md5()
            n = 0
            tmp = local + ".part"
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk)
                    h.update(chunk)
                    n += len(chunk)
            if h.hexdigest() != md5:
                raise RuntimeError(f"md5 mismatch {h.hexdigest()} != {md5} ({n} bytes)")
            os.replace(tmp, local)
            total += n
            ok += 1
            done = True
            print(f"[{i}/{len(items)}] {n/1e6:8.2f} MB  {rel}")
            break
        except Exception as e:
            print(f"  retry {attempt+1}: {e}")
            time.sleep(2 * (attempt + 1))
    if not done:
        bad.append(rel)
print(f"downloaded {ok}, already-ok {skipped}, failed {len(bad)}, {total/1e9:.2f} GB in {time.time()-t0:.0f}s")
for b in bad:
    print("  FAILED:", b)
sys.exit(1 if bad else 0)
