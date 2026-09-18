# -*- coding: utf-8 -*-
"""只读扫描 carol 光影光效P0 下所有 4-* 目录，找内容级重复（md5）。
输出（写到自己的工作区，不动他人数据）：
  /mnt/kfs/bob/光影光效2/dup_groups_P0.csv
Python 3.6 可跑。
"""
import os, hashlib, collections, csv, io

BASE = '/mnt/kfs/carol/ketu/grace/260710可送标/文生图/光影光效P0'
OUT_CSV = '/mnt/kfs/bob/光影光效2/dup_groups_P0.csv'

dirs = []
for b in sorted(os.listdir(BASE)):
    p = os.path.join(BASE, b)
    if not os.path.isdir(p):
        continue
    for sub in sorted(os.listdir(p)):
        if sub.startswith('4-') and os.path.isdir(os.path.join(p, sub)):
            dirs.append(os.path.join(p, sub))
print('4-目录数:', len(dirs))

bysize = collections.defaultdict(list)
total = 0
for d in dirs:
    for root, _, files in os.walk(d):
        for f in sorted(files):
            if f.startswith('.') or f == 'Thumbs.db':
                continue
            fp = os.path.join(root, f)
            total += 1
            try:
                bysize[os.path.getsize(fp)].append(fp)
            except OSError:
                pass
cand = sum(len(v) for v in bysize.values() if len(v) > 1)
print('总文件数:', total, ' 同大小候选:', cand)

def md5(fp):
    h = hashlib.md5()
    with open(fp, 'rb') as f:
        for c in iter(lambda: f.read(1 << 20), b''):
            h.update(c)
    return h.hexdigest()

byhash = collections.defaultdict(list)
done = 0
for v in bysize.values():
    if len(v) < 2:
        continue
    for fp in v:
        byhash[md5(fp)].append(fp)
        done += 1
        if done % 500 == 0:
            print('  md5进度 %d/%d' % (done, cand))
realdup = {k: sorted(v) for k, v in byhash.items() if len(v) > 1}
nfiles = sum(len(v) for v in realdup.values())
print('内容完全相同组:', len(realdup), ' 涉及文件:', nfiles)

with io.open(OUT_CSV, 'w', newline='', encoding='utf-8-sig') as f:
    w = csv.writer(f)
    w.writerow(['组号', 'md5', '处置', '相对路径'])
    for gi, (k, paths) in enumerate(sorted(realdup.items()), 1):
        for j, fp in enumerate(paths):
            w.writerow([gi, k, 'KEEP' if j == 0 else 'SKIP',
                        fp.replace(BASE + '/', '')])
print('已写出:', OUT_CSV)

# 摘要：跨目录组数 / 同目录组数，并给几组示例
cross = same = 0
examples = []
for k, paths in sorted(realdup.items()):
    tops = set(p.replace(BASE + '/', '').split('/')[0] for p in paths)
    if len(tops) > 1:
        cross += 1
    else:
        same += 1
    if len(examples) < 5:
        examples.append([p.replace(BASE + '/', '') for p in paths])
print('跨批次重复组:', cross, ' 同批次内重复组:', same)
for ex in examples:
    print('EX:', ' <=> '.join(ex))
print('MAKE_DUP_P0_DONE')
