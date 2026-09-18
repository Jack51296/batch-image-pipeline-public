# -*- coding: utf-8 -*-
"""统计当前 0814/0815 任务清单中，与已跑完批次(0818/0819)内容相同的图。Python 3.6 可跑。"""
import io, csv, json, os, collections

ROOT = '/mnt/kfs/bob/光影光效2'
CSV = os.path.join(ROOT, 'dup_groups_4档.csv')
DONE_PREFIX = ('260818', '260819')   # 已在 chatgpt-ketu 完成的批次
NEW_PREFIX = ('260814', '260815-光影光效-1598')

groups = collections.defaultdict(list)
for row in csv.reader(io.open(CSV, encoding='utf-8-sig')):
    if len(row) >= 4 and row[0] != '组号':
        groups[row[0]].append(row[3])

# 与已完成批次同内容的 0814/0815 相对路径
overlap = set()
for g, paths in groups.items():
    has_done = any(p.startswith(DONE_PREFIX) for p in paths)
    if not has_done:
        continue
    for p in paths:
        if p.startswith(NEW_PREFIX):
            overlap.add(p)
print('与0818/0819重复的 0814/0815 图片数:', len(overlap))

def tail3(p):
    return '/'.join(p.replace('\\', '/').rstrip('/').split('/')[-3:])
ov_tails = set(tail3(p) for p in overlap)

for bid in ('0814', '0815'):
    jp = os.path.join(ROOT, 'batches', bid, 'Prompt_with_images.4dedup.jsonl')
    if not os.path.exists(jp):
        print(bid, '无 4dedup.jsonl'); continue
    rows = hit = 0
    imgs = set()
    for line in io.open(jp, encoding='utf-8'):
        line = line.strip()
        if not line:
            continue
        d = json.loads(line)
        ip = d.get('image_path', '')
        rows += 1
        if tail3(ip) in ov_tails:
            hit += 1
            imgs.add(tail3(ip))
    print('批次%s: 任务%d行, 其中与已完成批次重复 %d 行 (%d 张图)' % (bid, rows, hit, len(imgs)))
    for t in sorted(imgs)[:8]:
        print('  例:', t)
print('OVERLAP_DONE')
