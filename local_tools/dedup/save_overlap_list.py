# -*- coding: utf-8 -*-
"""把 0814/0815 中与已完成批次(0818/0819)内容相同的图写成清单，供交付阶段剔除。"""
import io, csv, os, collections

ROOT = '/mnt/kfs/bob/光影光效2'
CSV = os.path.join(ROOT, 'dup_groups_4档.csv')
OUT = os.path.join(ROOT, 'overlap_done_batches.txt')
DONE_PREFIX = ('260818', '260819')
NEW_PREFIX = ('260814', '260815-光影光效-1598')

groups = collections.defaultdict(list)
for row in csv.reader(io.open(CSV, encoding='utf-8-sig')):
    if len(row) >= 4 and row[0] != '组号':
        groups[row[0]].append(row[3])

lines = []
for g, paths in sorted(groups.items(), key=lambda x: int(x[0])):
    done = [p for p in paths if p.startswith(DONE_PREFIX)]
    if not done:
        continue
    for p in paths:
        if p.startswith(NEW_PREFIX):
            lines.append('%s\t已完成孪生: %s' % (p, done[0]))
with io.open(OUT, 'w', encoding='utf-8') as f:
    f.write('\n'.join(lines) + '\n')
print('写出 %d 条 -> %s' % (len(lines), OUT))
print('SAVE_OVERLAP_DONE')
