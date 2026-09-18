# -*- coding: utf-8 -*-
"""扫描 6 个 4-*张 目录，找内容级重复（md5），生成：
  光影光效2/dup_groups_4档.csv   —— 全部重复组明细（每文件一行，保留/跳过标记）
  光影光效2/dup_skip_list.txt   —— 跳过清单（每组除第 1 张外的绝对路径）
只读数据目录；输出写到自己的工作区 光影光效2/。Python 3.6 可跑。
"""
import os, hashlib, collections, csv, io, sys

BASE = '/mnt/kfs/bob/光影光效'
OUT_DIR = '/mnt/kfs/bob/光影光效2'

dirs = []
for b in sorted(os.listdir(BASE)):
    p = os.path.join(BASE, b)
    if not os.path.isdir(p) or not b.startswith('2608'):
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
print('总文件数:', total)

def md5(fp):
    h = hashlib.md5()
    with open(fp, 'rb') as f:
        for c in iter(lambda: f.read(1 << 20), b''):
            h.update(c)
    return h.hexdigest()

byhash = collections.defaultdict(list)
for v in bysize.values():
    if len(v) < 2:
        continue
    for fp in v:
        byhash[md5(fp)].append(fp)
realdup = {k: sorted(v) for k, v in byhash.items() if len(v) > 1}
print('内容完全相同组:', len(realdup), ' 涉及文件:', sum(len(v) for v in realdup.values()))

csv_path = os.path.join(OUT_DIR, 'dup_groups_4档.csv')
skip_path = os.path.join(OUT_DIR, 'dup_skip_list.txt')
skips = []
with io.open(csv_path, 'w', newline='', encoding='utf-8-sig') as f:
    w = csv.writer(f)
    w.writerow(['组号', 'md5', '处置', '相对路径'])
    for gi, (k, paths) in enumerate(sorted(realdup.items()), 1):
        for j, fp in enumerate(paths):
            act = 'KEEP' if j == 0 else 'SKIP'
            if j > 0:
                skips.append(fp)
            w.writerow([gi, k, act, fp.replace(BASE + '/', '')])
with io.open(skip_path, 'w', encoding='utf-8') as f:
    f.write('\n'.join(skips) + '\n')

print('已写出:', csv_path)
print('已写出:', skip_path, ' 跳过条数:', len(skips))
print('MAKE_DUP_DONE')
